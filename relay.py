#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
relay.py - Zero-dependency Gemini(Antigravity) -> OpenAI compatible relay.
Python 3.8+. No pip install needed. Single file.

Run:  python relay.py
Config via relay.local.json or environment variables.
"""
import base64
import json
import os
import re
import ssl
import sys
import time
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(HERE, "relay.local.json")

TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO = "https://www.googleapis.com/oauth2/v3/userinfo"
DEFAULT_UPSTREAM = "https://daily-cloudcode-pa.googleapis.com"
PASS_MAP = {"ccp": "https://daily-cloudcode-pa.googleapis.com",
             "oauth": "https://oauth2.googleapis.com",
             "gapi": "https://www.googleapis.com"}
GEN_PATH = "/v1internal:generateContent"
STREAM_PATH = "/v1internal:streamGenerateContent?alt=sse"
UA = "antigravity/1.0.0 (relay-py)"

# Requested model is always tried first; these are the capacity fallbacks.
# catalog measured live from fetchAvailableModels on a free-tier aicode-consumers project
MODEL_IDS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-thinking",
    "gemini-2.5-pro",
    "gemini-3-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-image",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash-tiered",
    "claude-opus-5-5-high",
    "claude-opus-5-5-medium",
    "claude-opus-5-5-low",
    "claude-sonnet-5-5-high",
    "claude-sonnet-5-5-medium",
    "claude-sonnet-5-5-low",
    "gpt-oss-120b-medium",
    "chat_20706",
    "chat_23310",
    "tab_flash_lite_preview",
    "tab_jump_flash_lite_preview",
]

FALLBACK = ["gemini-2.5-flash", "gemini-3-flash", "gemini-2.5-flash-thinking",
            "gemini-3.5-flash-lite"]
SOFT = (400, 404, 429, 503)   # retry the next model instead of failing the client

CANDIDATES = [
    {"name": "antigravity",
     "client_id": "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com",
     "client_secret": "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"},
    {"name": "gemini-cli",
     "client_id": "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com",
     "client_secret": "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"},
    {"name": "gcloud-legacy",
     "client_id": "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com",
     "client_secret": "d-FL95Q19q7MQmFpd7hHD0Ty"},
    {"name": "cloud-sdk",
     "client_id": "32555940559.apps.googleusercontent.com",
     "client_secret": "ZmssLNjJy2998hD4CTg2ejr2"},
]


def load_cfg():
    cfg = {}
    if os.path.isfile(CFG_FILE):
        try:
            with open(CFG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            sys.stderr.write("warn: bad relay.local.json: %s\n" % e)
    env_map = {
        "GOOGLE_REFRESH_TOKEN": "refresh_token",
        "RELAY_PROXY": "proxy",
        "GOOGLE_CLIENT_ID": "client_id",
        "GOOGLE_CLIENT_SECRET": "client_secret",
        "GOOGLE_PROJECT": "project",
        "LOCAL_KEYS": "local_keys",
        "DEFAULT_MODEL": "default_model",
        "UPSTREAM": "upstream",
        "BIND": "bind",
        "PORT": "port",
    }
    for k, v in env_map.items():
        if os.environ.get(k):
            cfg[v] = os.environ[k]
    cfg.setdefault("refresh_token", "")
    cfg.setdefault("client_id", "")
    cfg.setdefault("client_secret", "")
    cfg.setdefault("project", "")
    cfg.setdefault("local_keys", "sk-gw-dev-9527")
    cfg.setdefault("default_model", "gemini-2.5-flash")
    if cfg["default_model"] == "gemini-2.5-pro":
        cfg["default_model"] = "gemini-2.5-flash"  # pro is capacity-gated on free tier
    cfg.setdefault("upstream", DEFAULT_UPSTREAM)
    if "cloudcode-pa.googleapis.com" in str(cfg.get("upstream", "")) and \
            not str(cfg.get("upstream", "")).split("//")[-1].startswith("daily-"):
        cfg["upstream"] = DEFAULT_UPSTREAM  # free-tier quota lives on daily-
    cfg.setdefault("proxy", os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "")
    cfg.setdefault("proxies", [])
    cfg.setdefault("bind", "0.0.0.0")
    cfg.setdefault("port", 8080)
    cfg["port"] = int(cfg["port"])
    return cfg


CFG = load_cfg()
_ssl_ctx = ssl.create_default_context()
_POOL = list(CFG.get("proxies") or [])
if CFG.get("proxy") and CFG["proxy"] not in _POOL:
    _POOL.insert(0, CFG["proxy"])
_PICK = {"i": 0}


def _opener_for(proxy):
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
        urllib.request.HTTPSHandler(context=_ssl_ctx))


_OPENER = _opener_for(_POOL[0]) if _POOL else None

_TOK = {"value": None, "exp": 0.0, "client": None, "project": None}
_LOCK = threading.Lock()


def _http(url, data=None, headers=None, timeout=60, method=None):
    hdr = {"User-Agent": UA}
    if headers:
        hdr.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    if not _POOL:
        return urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx)
    # rotate through the proxy pool; a dead exit must not kill the request
    last = None
    n = len(_POOL)
    for attempt in range(min(n, 4)):
        with _LOCK:
            idx = _PICK["i"]
            _PICK["i"] = (_PICK["i"] + 1) % n
        proxy = _POOL[idx]
        try:
            return _opener_for(proxy).open(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise  # upstream answered: proxy worked
        except Exception as e:
            last = e
            sys.stderr.write("proxy %s failed: %s | " % (proxy, type(e).__name__))
    raise last if last else RuntimeError("no proxy available")


def _json_or_text(resp):
    raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw}


def _classify(body):
    try:
        err = json.loads(body).get("error", "")
    except Exception:
        err = str(body)[:200]
    e = str(err)
    if "invalid_grant" in e:
        if "revoked" in e or "expired" in e:
            return "client_id matched, but the refresh_token is dead (revoked/expired)"
        return "invalid_grant: client_id does not own this refresh_token"
    if "invalid_client" in e:
        return "invalid_client: bad client_id/client_secret pair"
    if "deleted_client" in e:
        return "deleted_client: this OAuth client was deleted by Google"
    if "unauthorized_client" in e:
        return "unauthorized_client: client not allowed for refresh_token grant"
    return "unknown: " + e[:180]


def candidates():
    out = []
    if CFG["client_id"]:
        out.append({"name": "config", "client_id": CFG["client_id"],
                    "client_secret": CFG.get("client_secret", "")})
    for c in CANDIDATES:
        out.append(dict(c))
    return out


def refresh_with(c):
    data = urllib.parse.urlencode({
        "client_id": c["client_id"],
        "client_secret": c.get("client_secret", ""),
        "refresh_token": CFG["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    try:
        resp = _http(TOKEN_URL, data=data,
                     headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=25)
        j = _json_or_text(resp)
        if j.get("access_token"):
            return {"ok": True, "token": j["access_token"], "ttl": int(j.get("expires_in", 3600))}
        return {"ok": False, "status": 200, "verdict": "no access_token in response: " + str(j)[:150]}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"ok": False, "status": e.code, "verdict": _classify(body), "raw": body[:300]}
    except Exception as e:
        return {"ok": False, "status": 0,
                "verdict": "network: " + type(e).__name__ + ": " + str(e)[:160]}


def resolve_client():
    with _LOCK:
        if _TOK["value"] and _TOK["exp"] > time.time() + 60:
            return _TOK["value"]
        if not CFG["refresh_token"]:
            raise RuntimeError("no refresh_token configured")
        last = None
        for c in candidates():
            r = refresh_with(c)
            if r["ok"]:
                _TOK.update({"value": r["token"], "client": c["name"],
                             "exp": time.time() + max(60, r["ttl"] - 120)})
                return r["token"]
            last = r
            sys.stderr.write("client %-14s -> %s\n" % (c["name"], r.get("verdict", "")[:120]))
        raise RuntimeError("oauth_refresh_failed: no candidate client_id accepted the refresh_token (%s)"
                           % (last or {}).get("verdict", "unknown"))


_PROJ_DBG = {}


def _pick_project(j):
    """Dig a project id out of any response shape Google may use."""
    for key in ("cloudaicompanionProject", "project", "projectId", "id"):
        v = j.get(key) if isinstance(j, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for k2 in ("id", "name", "projectId"):
                v2 = v.get(k2)
                if isinstance(v2, str) and v2.strip():
                    return v2.strip()
    return ""


def _meta_variants():
    return [
        {"pluginType": "GEMINI_CLI"},
        {"pluginType": "GEMINI_CLI", "ideType": "ANTIGRAVITY", "platform": "PLATFORM_UNSPECIFIED"},
        {"pluginType": "ANTIGRAVITY"},
        {"ideType": "ANTIGRAVITY", "pluginType": "IDE_UNSPECIFIED"},
        {},
        None,
    ]


def _v1internal(path, token, payload, timeout=25):
    data = b"" if payload is None else json.dumps(payload).encode()
    resp = _http(CFG["upstream"] + path, data=data,
                 headers={"Content-Type": "application/json",
                          "Authorization": "Bearer " + token}, timeout=timeout)
    return _json_or_text(resp)


def _onboard(token, tier, meta):
    """Create the backend project if the account has none yet."""
    payload = {"tierId": tier}
    if meta is not None:
        payload["metadata"] = meta
    j = _v1internal("/v1internal:onboardUser", token, payload, timeout=30)
    op = j.get("name") if isinstance(j, dict) else None
    ops = {"tier": tier, "op": op, "done": bool(j.get("done")) if isinstance(j, dict) else None,
           "raw": (json.dumps(j)[:400] if isinstance(j, dict) else str(j)[:200])}
    if op and not j.get("done"):
        for _ in range(12):
            time.sleep(3)
            r = _v1internal("/v1internal/operations/" + op.split("/")[-1], token, None, timeout=25)
            if isinstance(r, dict) and r.get("done"):
                j = r
                ops["done"] = True
                break
            if isinstance(r, dict) and r.get("error"):
                ops["error"] = json.dumps(r["error"])[:200]
                break
    p = _pick_project(j.get("response", {}) if isinstance(j, dict) else {}) or _pick_project(j or {})
    ops["project"] = p
    return p, ops


def project(token):
    if CFG["project"]:
        return CFG["project"]
    if _TOK.get("project"):
        return _TOK["project"]

    tiers, allowed, lca_ok, last_err = [], [], False, ""
    for meta in _meta_variants():
        try:
            j = _v1internal("/v1internal:loadCodeAssist", token,
                            None if meta is None else {"metadata": meta}, timeout=25)
        except Exception as e:
            last_err = type(e).__name__ + ": " + str(e)[:120]
            _PROJ_DBG["loadCodeAssist_error"] = last_err
            continue
        lca_ok = True
        _PROJ_DBG["loadCodeAssist"] = json.dumps(j)[:900] if isinstance(j, dict) else str(j)[:400]
        p = _pick_project(j or {})
        if p:
            _TOK["project"] = p
            _PROJ_DBG["via"] = "loadCodeAssist:" + json.dumps(meta or {})[:60]
            return p
        if isinstance(j, dict):
            cur = j.get("currentTier") or {}
            if isinstance(cur, dict) and cur.get("id"):
                tiers.append(cur["id"])
            for t in (j.get("allowedTiers") or []):
                if isinstance(t, dict) and t.get("id"):
                    allowed.append({k: t.get(k) for k in ("id", "name", "isDefault", "userDefinedCloudaicompanionProject")})

    order = []
    for t in tiers + (["free-tier", "standard-tier", "legacy-tier"] if not allowed else []):
        if t and t not in order:
            order.append(t)
    for a in allowed:
        tid = a.get("id")
        if tid and tid not in order and not a.get("userDefinedCloudaicompanionProject"):
            order.insert(0, tid)

    _PROJ_DBG["currentTier"] = tiers
    _PROJ_DBG["allowedTiers"] = allowed
    _PROJ_DBG["onboard_order"] = order[:4]
    if not lca_ok:
        _PROJ_DBG["verdict"] = "loadCodeAssist unreachable"
        return ""

    tries = []
    for tier in order[:4]:
        try:
            p, ops = _onboard(token, tier, {"pluginType": "GEMINI_CLI"})
        except Exception as e:
            tries.append({"tier": tier, "error": type(e).__name__ + ": " + str(e)[:100]})
            continue
        tries.append(ops)
        if p:
            _TOK["project"] = p
            _PROJ_DBG["via"] = "onboardUser:" + tier
            _PROJ_DBG["onboard"] = tries
            return p
    _PROJ_DBG["onboard"] = tries
    _PROJ_DBG["verdict"] = "no project after loadCodeAssist + onboardUser"
    return ""


def to_parts(content):
    if isinstance(content, str):
        return [{"text": content}]
    parts = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                parts.append({"text": str(item)})
                continue
            if item.get("type") == "text":
                parts.append({"text": item.get("text", "")})
            elif item.get("type") == "image_url":
                url = (item.get("image_url") or {}).get("url", "")
                m = re.match(r"data:([^;]+);base64,(.*)", url, re.S)
                if m:
                    parts.append({"inlineData": {"mimeType": m.group(1),
                                                 "data": m.group(2)}})
                elif url:
                    parts.append({"text": "[image] " + url})
    return parts


def to_tools(body):
    """OpenAI tools[] -> Gemini functionDeclarations."""
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        return None
    decls = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if t.get("type", "function") == "function" else None
        if not isinstance(fn, dict):
            fn = t if "name" in t else None
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        d = {"name": str(fn.get("name"))[:64]}
        if fn.get("description"):
            d["description"] = str(fn["description"])[:1024]
        params = fn.get("parameters")
        if isinstance(params, dict):
            params = {k: v for k, v in params.items() if k != "$schema"}
            if "type" not in params:
                params["type"] = "object"
            d["parameters"] = params
        decls.append(d)
    if not decls:
        return None
    return [{"functionDeclarations": decls}]


def to_toolconfig(body):
    tc = body.get("tool_choice")
    mode = None
    if tc == "none":
        mode = "NONE"
    elif tc in ("required", "any"):
        mode = "ANY"
    elif isinstance(tc, dict) and (tc.get("function") or {}).get("name"):
        return {"functionCallingConfig": {"mode": "ANY",
                                         "allowedFunctionNames": [tc["function"]["name"]]}}
    if mode is None:
        return None
    return {"functionCallingConfig": {"mode": mode}}


def _args(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {"value": v}
    return {}


SIG_SKIP = "skip_thought_signature_validator"
_SIG = {}
_SIG_ARGS = {}


def _sig_put(cid, name, args, sig):
    if sig:
        _SIG[cid] = sig
        _SIG_ARGS[(name, json.dumps(args, sort_keys=True))] = sig
    while len(_SIG) > 600:
        _SIG.pop(next(iter(_SIG)))
    while len(_SIG_ARGS) > 600:
        _SIG_ARGS.pop(next(iter(_SIG_ARGS)))


def _sig_get(cid, name, args):
    if cid and cid in _SIG:
        return _SIG[cid]
    return _SIG_ARGS.get((name, json.dumps(args, sort_keys=True))) or SIG_SKIP


def build_payload(model, proj, body):
    msgs = body.get("messages") or []
    sys_parts, contents = [], []
    names = {}
    for m in msgs:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                if tc.get("id") and fn.get("name"):
                    names[tc["id"]] = fn["name"]
    for m in msgs:
        role = m.get("role", "user")
        if role == "tool":
            nm = names.get(m.get("tool_call_id")) or m.get("name") or "tool_result"
            resp = {"result": m.get("content", "")}
            if not isinstance(resp["result"], (str, int, float, bool, list, dict)):
                resp["result"] = str(resp["result"])
            contents.append({"role": "user", "parts": [{"functionResponse": {"name": nm,
                                                                             "response": resp}}]})
            continue
        txt = to_parts(m.get("content"))
        if role in ("system", "developer"):
            sys_parts.extend(txt)
            continue
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            if not fn.get("name"):
                continue
            a = _args(fn.get("arguments"))
            txt.append({"functionCall": {"name": fn["name"], "args": a},
                        "thoughtSignature": _sig_get(tc.get("id"), fn["name"], a)})
        contents.append({"role": "model" if role == "assistant" else "user", "parts": txt})
    out = {"contents": contents}
    if sys_parts:
        out["systemInstruction"] = {"parts": sys_parts}
    gc = {}
    for src, dst in (("temperature", "temperature"), ("top_p", "topP"),
                     ("max_tokens", "maxOutputTokens"),
                     ("stop", "stopSequences"),
                     ("response_format", "responseMimeType")):
        if src in body and body[src] is not None:
            v = body[src]
            if src == "response_format" and isinstance(v, dict):
                v = "application/json"
            gc[dst] = v
    if gc:
        out["generationConfig"] = gc
    tl = to_tools(body)
    if tl:
        out["tools"] = tl
        cfg = to_toolconfig(body)
        if cfg:
            out["toolConfig"] = cfg
    return {"model": model, "project": proj, "request": out}


def pick_text(payload):
    try:
        cand = (payload.get("candidates") or [])[0]
        parts = cand.get("content", {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)
    except Exception:
        return ""


def pick_toolcalls(payload):
    try:
        cand = (payload.get("candidates") or [])[0]
        parts = cand.get("content", {}).get("parts") or []
    except Exception:
        return []
    out, i = [], 0
    for p in parts:
        fc = p.get("functionCall")
        if not isinstance(fc, dict) or not fc.get("name"):
            continue
        cid = "call_%s_%d" % (int(time.time() * 1000), i)
        args = fc.get("args") or {}
        _sig_put(cid, fc["name"], args, p.get("thoughtSignature"))
        out.append({"index": i, "id": cid,
                    "type": "function",
                    "function": {"name": fc["name"],
                                 "arguments": json.dumps(args, ensure_ascii=False)}})
        i += 1
    return out


def unwrap(obj):
    if isinstance(obj, dict) and isinstance(obj.get("response"), dict):
        return obj["response"]
    return obj


_ERRS = []
_LAST = []


def _note_err(where):
    try:
        tb = traceback.format_exc()
        _ERRS.append({"where": where, "tb": tb[-900:]})
        del _ERRS[:-5]
    except Exception:
        pass


def tb_probe(tok, proj):
    """Replay the tool-history shapes straight against upstream and return raw bodies."""
    tools = [{"type": "function", "function": {
        "name": "read_file",
        "description": "read a file",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}}]
    fc = {"id": "c_1", "type": "function",
          "function": {"name": "read_file",
                       "arguments": json.dumps({"path": "/etc/hostname"})}}
    u = {"role": "user", "content": "read /etc/hostname"}
    a = {"role": "assistant", "content": None, "tool_calls": [fc]}
    cases = {
        "tools_only": [u],
        "fc_only": [u, a],
        "fc_result": [u, a, {"role": "tool", "tool_call_id": "c_1", "content": "my-host"}],
    }
    out = {"upstream": CFG["upstream"], "project": proj, "cases": {}, "errs": list(_ERRS)}
    for name, hist in cases.items():
        b = {"model": "gemini-2.5-flash", "tools": tools, "messages": hist}
        try:
            payload = build_payload("gemini-2.5-flash", proj, b)
        except Exception as e:
            out["cases"][name] = {"build_err": type(e).__name__ + ": " + str(e)[:200]}
            continue
        rec = {"payload": json.dumps(payload)[:1200]}
        t0 = time.time()
        try:
            r = _http(CFG["upstream"] + GEN_PATH, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + tok}, timeout=60)
            rec["code"] = 200
            rec["body"] = r.read().decode("utf-8", "replace")[:700]
        except urllib.error.HTTPError as e:
            rec["code"] = e.code
            rec["body"] = e.read().decode("utf-8", "replace")[:700]
        except Exception as e:
            rec["code"] = -1
            rec["err"] = type(e).__name__ + ": " + str(e)[:200]
        rec["ms"] = int((time.time() - t0) * 1000)
        out["cases"][name] = rec
    return out



def tb_probe2(tok, proj):
    """Test which thought_signature strategy upstream accepts on replay."""
    tools = [{"type": "function", "function": {
        "name": "read_file",
        "description": "read a file",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}}]
    u = {"role": "user", "content": "read /etc/hostname then tell me the hostname"}
    out = {"cases": {}}

    def call(contents, model="gemini-2.5-flash"):
        payload = {"model": model, "project": proj,
                   "request": {"contents": contents, "tools": [{"functionDeclarations": [
                       {"name": "read_file", "description": "read a file",
                        "parameters": {"type": "object",
                                       "properties": {"path": {"type": "string"}},
                                       "required": ["path"]}}]}]}}
        t0 = time.time()
        try:
            r = _http(CFG["upstream"] + GEN_PATH, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + tok}, timeout=90)
            body = r.read().decode("utf-8", "replace")
            return {"code": r.getcode(), "ms": int((time.time() - t0) * 1000), "body": body}
        except urllib.error.HTTPError as e:
            return {"code": e.code, "ms": int((time.time() - t0) * 1000),
                    "body": e.read().decode("utf-8", "replace")}
        except Exception as e:
            return {"code": -1, "err": type(e).__name__ + ": " + str(e)[:160]}

    first = call([{"role": "user", "parts": [{"text": u["content"]}]}])
    out["step1_initial"] = {"code": first.get("code"), "ms": first.get("ms"),
                            "body": (first.get("body") or first.get("err") or "")[:400]}
    if first.get("code") != 200:
        return out
    j = unwrap(json.loads(first["body"]))
    parts = (j.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
    fc_part = next((p for p in parts if isinstance(p, dict) and p.get("functionCall")), None)
    out["step1_has_signature"] = bool(fc_part and fc_part.get("thoughtSignature"))
    real_sig = (fc_part or {}).get("thoughtSignature") or ""
    real_fc = (fc_part or {}).get("functionCall") or {"name": "read_file", "args": {"path": "/etc/hostname"}}
    model_turn = {"role": "model", "parts": [{"functionCall": {
        "name": real_fc.get("name"), "args": real_fc.get("args") or {}},
        "thoughtSignature": real_sig}]}
    if not real_sig:
        model_turn["parts"] = [{"functionCall": {"name": "read_file", "args": {"path": "/etc/hostname"}}}]
    sentinel_turn = {"role": "model", "parts": [{"functionCall": {
        "name": "read_file", "args": {"path": "/etc/hostname"}},
        "thoughtSignature": SIG_SKIP}]}
    fresp = {"role": "user", "parts": [{"functionResponse": {"name": "read_file",
                                                             "response": {"result": "my-host-42"}}}]}

    out["cases"]["replay_real_sig"] = call([{"role": "user", "parts": [{"text": u["content"]}]},
                                            model_turn, fresp])
    out["cases"]["replay_sentinel"] = call([{"role": "user", "parts": [{"text": u["content"]}]},
                                            sentinel_turn, fresp])
    out["cases"]["replay_no_sig"] = call([{"role": "user", "parts": [{"text": u["content"]}]},
                                          {"role": "model", "parts": [{"functionCall": {
                                              "name": "read_file", "args": {"path": "/etc/hostname"}}}]},
                                          fresp])
    for k, v in out["cases"].items():
        v["body"] = (v.get("body") or v.get("err") or "")[:420]
    out["errs"] = list(_ERRS)
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "gemini-relay/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def handle(self):
        # client hangs up mid-response (common with mobile clients) must not spam tracebacks
        try:
            BaseHTTPRequestHandler.handle(self)
        except (ConnectionResetError, BrokenPipeError, TimeoutError):
            self.close_connection = True

    def _send(self, code, obj, extra=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "authorization, content-type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        keys = [k.strip() for k in str(CFG["local_keys"]).split(",") if k.strip()]
        if not keys:
            return True
        h = self.headers.get("Authorization", "")
        m = re.match(r"(?i)Bearer\s+(.+)$", h.strip())
        return bool(m) and m.group(1).strip() in keys

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "authorization, content-type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ("/", "/healthz"):
            return self._send(200, {"ok": True, "service": "gemini-relay",
                                    "model": CFG["default_model"], "client": _TOK["client"]})
        if p == "/__diag":
            return self._send(200, self.diag())
        if p == "/__probe":
            try:
                tok = resolve_client()
                return self._send(200, deep_probe(tok))
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})
        if p == "/__lca":
            try:
                tok = resolve_client()
                out = {"raw": {}, "onboard": {}}
                metas = [{"pluginType": "GEMINI_CLI"},
                         {"pluginType": "ANTIGRAVITY", "ideType": "ANTIGRAVITY"},
                         {"pluginType": "IDE_UNSPECIFIED", "ideType": "ANTIGRAVITY"},
                         {"ideType": "ANTIGRAVITY"},
                         {"pluginType": "GEMINI_CLI", "ideType": "GEMINI_CLI"},
                         {}]
                for i, m in enumerate(metas):
                    out["raw"]["v%d" % i] = _full(HOSTS[0], "/v1internal:loadCodeAssist", tok,
                                                  {"metadata": m}, limit=6000)
                r = out["raw"].get("v1") or ""
                try:
                    tier = (json.loads(r).get("allowedTiers") or [])
                except Exception:
                    tier = []
                for t in tier[:3]:
                    tid = t.get("id") if isinstance(t, dict) else t
                    out["onboard"][str(tid)] = _call(
                        HOSTS[0], "/v1internal:onboardUser", tok,
                        {"tierId": tid, "metadata": {"pluginType": "ANTIGRAVITY",
                                                     "ideType": "ANTIGRAVITY"}}, timeout=40)
                return self._send(200, out)
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})
        if p == "/__onb":
            try:
                tok = resolve_client()
                return self._send(200, onboard_all(tok))
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})
        if p == "/__one":
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                ms = qs.get("m") or ["gemini-2.5-pro"]
                tok = resolve_client()
                pj = project(tok)
                out = {}
                for m in ms:
                    out[m] = _raw_gen("https://daily-cloudcode-pa.googleapis.com", tok, m, pj, timeout=60)
                    bb = (out[m].get("b") or "")[:260]
                    out[m]["b"] = bb.replace("\r", " ").replace("\n", " ")
                return self._send(200, out)
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})
        if p == "/__err":
            return self._send(200, {"errs": _ERRS, "last": _LAST})
        if p == "/__e502":
            return self._send(502, {"error": {"message": "synthetic-edge-test",
                                              "type": "upstream_error"}})
        if p == "/__tb2":
            try:
                tok = resolve_client()
                return self._send(200, tb_probe2(tok, project(tok)))
            except Exception as e:
                return self._send(500, {"error": str(e)[:300], "errs": _ERRS})
        if p == "/__tb":
            try:
                tok = resolve_client()
                out = tb_probe(tok, project(tok))
                return self._send(200, out)
            except Exception as e:
                return self._send(500, {"error": str(e)[:300], "errs": _ERRS})
        if p == "/__mx":
            try:
                tok = resolve_client()
                return self._send(200, mx_probe(tok, project(tok)))
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})
        if p == "/__gem":
            try:
                tok = resolve_client()
                return self._send(200, gem_api(tok))
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})
        if p == "/__models":
            try:
                tok = resolve_client()
                return self._send(200, model_catalog(tok, project(tok)))
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})
        if p == "/__gen":
            try:
                tok = resolve_client()
                return self._send(200, gen_matrix(tok, project(tok)))
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})
        if p == "/v1/models":
            if not self._authed():
                return self._send(401, {"error": {"message": "invalid api key", "type": "invalid_request_error"}})
            data = [{"id": m, "object": "model", "owned_by": "google",
                     "created": 1700000000} for m in MODEL_IDS]
            return self._send(200, {"object": "list", "data": data})
        parts = p.strip("/").split("/", 1)
        if parts and parts[0] in PASS_MAP:
            return self._pass(PASS_MAP[parts[0]], "/" + (parts[1] if len(parts) > 1 else ""))
        return self._send(404, {"error": {"message": "not found"}})

    def _pass(self, base, path):
        q = urllib.parse.urlparse(self.path).query
        url = base + path + (("?" + q) if q else "")
        n = int(self.headers.get("Content-Length", "0") or 0)
        data = self.rfile.read(n) if n else None
        hdrs = {"Content-Type": self.headers.get("Content-Type", "application/json"),
                "User-Agent": "gw-pass"}
        auth = self.headers.get("Authorization")
        if auth:
            hdrs["Authorization"] = auth
        try:
            resp = _http(url, data=data, headers=hdrs, timeout=300)
            if isinstance(resp, tuple):
                code, body = resp[0], resp[1]
            elif isinstance(resp, (bytes, bytearray)):
                code, body = 200, bytes(resp)
            elif hasattr(resp, "read"):
                try:
                    code = resp.getcode() or 200
                except Exception:
                    code = 200
                body = resp.read()
            else:
                code, body = 200, str(resp).encode()
            body = bytes(body)
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read()
        except Exception as e:
            code, body = 502, str(e).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        parts = p.strip("/").split("/", 1)
        if parts and parts[0] in PASS_MAP:
            return self._pass(PASS_MAP[parts[0]], "/" + (parts[1] if len(parts) > 1 else ""))
        if p != "/v1/chat/completions":
            return self._send(404, {"error": {"message": "not found"}})
        if not self._authed():
            return self._send(401, {"error": {"message": "invalid api key", "type": "invalid_request_error"}})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")
        except Exception as e:
            return self._send(400, {"error": {"message": "bad json: %s" % e}})
        model = body.get("model") or CFG["default_model"]
        if model.startswith("gemini/"):
            model = model.split("/", 1)[1]
        try:
            token = resolve_client()
            proj = project(token)
            if body.get("stream"):
                return self._stream(token, proj, body, model)
            chain = [model] + [m for m in FALLBACK if m != model]
            last = (0, "")
            for m in chain:
                payload = build_payload(m, proj, body)
                try:
                    resp = _http(CFG["upstream"] + GEN_PATH, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + token}, timeout=180)
                except urllib.error.HTTPError as e:
                    last = (e.code, e.read().decode("utf-8", "replace"))
                    _LAST.append({"m": m, "code": e.code, "b": last[1][:180]})
                    del _LAST[:-12]
                    if (e.code in SOFT and "thought_signature" not in last[1]
                            and "Invalid JSON payload" not in last[1]):
                        continue
                    break
                except Exception as e:
                    _LAST.append({"m": m, "code": -1, "b": type(e).__name__ + ": " + str(e)[:160]})
                    del _LAST[:-12]
                    raise
                j = unwrap(_json_or_text(resp))
                text = pick_text(j)
                tcs = pick_toolcalls(j)
                msg = {"role": "assistant", "content": text}
                if tcs:
                    msg["tool_calls"] = tcs
                    if not text:
                        msg["content"] = None
                um = j.get("usageMetadata") or {}
                return self._send(200, {
                    "id": "chatcmpl-" + str(int(time.time())),
                    "object": "chat.completion", "created": int(time.time()),
                    "model": m,
                    "choices": [{"index": 0, "message": msg,
                                 "finish_reason": "tool_calls" if tcs else "stop"}],
                    "usage": {"prompt_tokens": um.get("promptTokenCount", 0),
                              "completion_tokens": um.get("candidatesTokenCount", 0),
                              "total_tokens": um.get("totalTokenCount", 0)},
                })
            return self._send(502, {"error": {"message": "upstream %s: %s" % (last[0], last[1][:400]),
                                              "type": "upstream_error"},
                                    "tried": chain})
        except urllib.error.HTTPError as e:
            d = e.read().decode("utf-8", "replace")
            return self._send(502, {"error": {"message": "upstream %s: %s" % (e.code, d[:400]),
                                              "type": "upstream_error"}})
        except Exception as e:
            _note_err("do_POST")
            return self._send(502, {"error": {"message": str(e)[:400], "type": "gateway_error"}})

    def _stream(self, token, proj, body, model):
        cid = "chatcmpl-" + str(int(time.time()))
        chain = [model] + [m for m in FALLBACK if m != model]
        resp, last, used = None, (0, ""), model
        for m in chain:
            payload = build_payload(m, proj, body)
            try:
                resp = _http(CFG["upstream"] + STREAM_PATH, data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json",
                                      "Accept": "text/event-stream",
                                      "Authorization": "Bearer " + token}, timeout=180)
                used = m
                break
            except urllib.error.HTTPError as e:
                last = (e.code, e.read().decode("utf-8", "replace"))
                _LAST.append({"m": m, "code": e.code, "b": last[1][:180], "stream": True})
                del _LAST[:-12]
                if (e.code in SOFT and "thought_signature" not in last[1]
                        and "Invalid JSON payload" not in last[1]):
                    continue
                break
        if resp is None:
            return self._send(502, {"error": {"message": "upstream %s: %s" % (last[0], last[1][:300]),
                                              "type": "upstream_error"},
                                    "tried": chain})
        model = used
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True

        def emit(obj):
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()

        def chunk(delta, finish=None):
            return {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        try:
            emit(chunk({"role": "assistant", "content": ""}))
            said, called = False, False
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    j = unwrap(json.loads(data))
                except Exception:
                    continue
                t = pick_text(j)
                if t:
                    said = True
                    emit(chunk({"content": t}))
                for tc in pick_toolcalls(j):
                    called = True
                    emit(chunk({"tool_calls": [tc]}))
            emit(chunk({}, "tool_calls" if called else "stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception as e:
            _note_err("_stream")
            try:
                emit(chunk({"content": "\n[relay-stream-error] " + str(e)[:200]}))
            except Exception:
                pass
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def diag(self):
        out = {"ok": False, "attempts": [], "steps": {}, "config": {
            "upstream": CFG["upstream"],
            "default_model": CFG["default_model"],
            "proxy_env": bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
            "refresh_token_len": len(CFG["refresh_token"]),
            "project_configured": bool(CFG["project"]),
        }}
        good = None
        _PROJ_DBG.clear()
        with _LOCK:
            _TOK["project"] = None
        for c in candidates():
            r = refresh_with(c)
            out["attempts"].append({"client": c["name"], "client_id": c["client_id"],
                                    "ok": r["ok"], "status": r.get("status"),
                                    "verdict": r.get("verdict", "")})
            if r["ok"] and not good:
                good = (c, r)
        if not good:
            out["steps"]["oauth"] = "FAILED"
            return out
        c, r = good
        tok = r["token"]
        with _LOCK:
            _TOK.update({"value": tok, "client": c["name"],
                         "exp": time.time() + max(60, r["ttl"] - 120)})
        out["client"], out["client_id"] = c["name"], c["client_id"]
        out["steps"]["oauth"] = "OK ttl=%ss" % r["ttl"]
        try:
            resp = _http(USERINFO, headers={"Authorization": "Bearer " + tok}, timeout=25)
            u = _json_or_text(resp)
            out["email"] = u.get("email", "")
            out["steps"]["userinfo"] = "OK"
        except Exception as e:
            out["steps"]["userinfo"] = "FAIL " + str(e)[:120]
        p = project(tok)
        out["project"] = p
        out["steps"]["project"] = "OK " + p if p else "FAIL no cloudaicompanionProject"
        if not p:
            out["project_debug"] = dict(_PROJ_DBG)
        payload = build_payload(CFG["default_model"], p,
                                {"messages": [{"role": "user", "content": "reply with exactly: PONG"}]})
        try:
            resp = _http(CFG["upstream"] + GEN_PATH, data=json.dumps(payload).encode(),
                         headers={"Content-Type": "application/json",
                                  "Authorization": "Bearer " + tok}, timeout=120)
            j = unwrap(_json_or_text(resp))
            t = pick_text(j)
            out["reply"] = t
            out["steps"]["generate"] = "OK " + repr(t[:80])
        except urllib.error.HTTPError as e:
            d = e.read().decode("utf-8", "replace")
            out["steps"]["generate"] = "HTTP %s %s" % (e.code, d[:250])
        except Exception as e:
            out["steps"]["generate"] = "FAIL " + str(e)[:160]
        out["ok"] = ("OK" in out["steps"].get("generate", "")) and bool(p)
        out["models"] = probe_models(tok, p)
        return out


MX_MODEL = "gemini-2.5-flash"
MX_ALT = "MODEL_PLACEHOLDER_M18"
MX_REQ = {"contents": [{"role": "user", "parts": [{"text": "say: PONG"}]}]}


def mx_variants():
    import copy
    v = {}
    v["plain"] = {"model": MX_MODEL, "project": "P", "request": copy.deepcopy(MX_REQ)}
    v["reqid"] = {"model": MX_MODEL, "project": "P", "requestId": "req-1",
                  "request": copy.deepcopy(MX_REQ)}
    v["session"] = {"model": MX_MODEL, "project": "P",
                    "request": dict(copy.deepcopy(MX_REQ), sessionId="s-1")}
    v["meta"] = {"model": MX_MODEL, "project": "P",
                 "metadata": {"ideType": "ANTIGRAVITY", "pluginType": "ANTIGRAVITY"},
                 "request": copy.deepcopy(MX_REQ)}
    v["noproj"] = {"model": MX_MODEL, "request": copy.deepcopy(MX_REQ)}
    v["placeholder"] = {"model": MX_ALT, "project": "P", "request": copy.deepcopy(MX_REQ)}
    v["ua"] = {"model": MX_MODEL, "project": "P", "userAgent": "antigravity/1.0.0",
               "request": copy.deepcopy(MX_REQ)}
    v["sysinst"] = {"model": MX_MODEL, "project": "P",
                    "request": dict(copy.deepcopy(MX_REQ),
                                    systemInstruction={"parts": [{"text": "be brief"}]},
                                    generationConfig={"maxOutputTokens": 64})}
    return v


MX_HDRS = {
    "none": None,
    "cli": {"X-Goog-Api-Client": "gl-python/3.11.0",
            "User-Agent": "GeminiCLI/0.1.0 (linux; x64)"},
    "ag": {"X-Goog-Api-Client": "google-genai-sdk/1.0.0 gl-python/3.11.0",
           "User-Agent": "antigravity/1.0.0"},
}


def mx_probe(tok, proj):
    out = {}
    vs = mx_variants()
    eps = [("gen", "/v1internal:generateContent"),
           ("sse", "/v1internal:streamGenerateContent?alt=sse")]
    for htag, host in (("pa", HOSTS[0]), ("daily", HOSTS[1])):
        for etag, ep in eps:
            for vtag, body in vs.items():
                b = json.loads(json.dumps(body).replace('"P"', json.dumps(proj)))
                hdrs = MX_HDRS["cli"] if vtag == "placeholder" else None
                r = _call(host, ep, tok, b, headers=hdrs, timeout=35)
                out["%s|%s|%s" % (htag, etag, vtag)] = r["c"]
    return out


GENLANG = "https://generativelanguage.googleapis.com"


def _bearer(host, path, tok, payload, timeout=45):
    try:
        resp = _http(host + path, data=json.dumps(payload).encode(),
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer " + tok,
                              "x-goog-api-client": "gl-node/22.0.0"}, timeout=timeout)
        return {"c": resp.getcode(), "b": resp.read().decode("utf-8", "replace")[:320].replace("\n", " ")}
    except urllib.error.HTTPError as e:
        return {"c": e.code, "b": e.read().decode("utf-8", "replace")[:260].replace("\n", " ")}
    except Exception as e:
        return {"c": 0, "b": type(e).__name__ + ": " + str(e)[:90]}


def scopes(tok):
    out = {}
    r = _bearer("https://oauth2.googleapis.com", "/tokeninfo?access_token=" + tok, tok, {}, timeout=30)
    out["tokeninfo"] = r
    return out


def gem_api(tok):
    """Hit the public Gemini API with the same bearer: different quota pool."""
    out = {"scopes": scopes(tok).get("tokeninfo", {})}
    payload = {"contents": [{"role": "user", "parts": [{"text": "say: PONG"}]}]}
    for m in ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-3-flash"):
        out["list:" + m] = ""
        out[m + ":gen"] = _bearer(GENLANG, "/v1beta/models/" + m + ":generateContent", tok, payload)
    out["listmodels"] = _bearer(GENLANG, "/v1beta/models?pageSize=5", tok, {}, timeout=40)
    return out


LCA_META = {"ideType": "ANTIGRAVITY"}


def _lca_raw(tok):
    body = {"metadata": LCA_META}
    data = json.dumps(body).encode()
    try:
        resp = _http(HOSTS[0] + "/v1internal:loadCodeAssist", data=data,
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer " + tok}, timeout=40)
        return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return "HTTP%d %s" % (e.code, e.read().decode("utf-8", "replace")[:200])
    except Exception as e:
        return "ERR " + str(e)[:120]


def _gen_stream(host, tok, body, timeout=50):
    import urllib.parse as up
    url = host + "/v1internal:streamGenerateContent?alt=sse"
    data = json.dumps(body).encode()
    try:
        resp = _http(url, data=data,
                     headers={"Content-Type": "application/json",
                              "Accept": "text/event-stream",
                              "Authorization": "Bearer " + tok}, timeout=timeout)
        raw = resp.read().decode("utf-8", "replace")
        return {"c": resp.getcode(), "b": raw[:300].replace("\n", "|")}
    except urllib.error.HTTPError as e:
        return {"c": e.code, "b": e.read().decode("utf-8", "replace")[:220].replace("\n", " ")}
    except Exception as e:
        return {"c": 0, "b": type(e).__name__ + ": " + str(e)[:80]}


def onboard_all(tok):
    out = {"tiers": [], "onboard": {}, "gen": {}}
    raw = _lca_raw(tok)
    try:
        j = json.loads(raw)
    except Exception:
        out["lca_bad"] = raw[:220]
        return out
    out["currentTier"] = (j.get("currentTier") or {}).get("id")
    out["paidTier"] = (j.get("paidTier") or {}).get("id")
    out["project_now"] = _pick_project(j)
    out["gcpManaged"] = j.get("gcpManaged")
    out["tiers"] = [t.get("id") for t in (j.get("allowedTiers") or []) if isinstance(t, dict)]
    cand = ["free-tier", "standard-tier", "legacy-tier"]
    for tid in cand:
        r = _call(HOSTS[0], "/v1internal:onboardUser", tok,
                  {"tierId": tid, "metadata": LCA_META}, timeout=45)
        rec = {"start": r["c"], "body": r["b"][:200]}
        proj = ""
        try:
            jj = json.loads(r["b"] if r["c"] < 300 else "{}")
        except Exception:
            jj = {}
        proj = _pick_project(jj.get("response") or {}) or _pick_project(jj) or ""
        if not proj and jj.get("done") is False:
            name = jj.get("name") or ""
            for k in range(3):
                time.sleep(3)
                rr = _call(HOSTS[0] + "/" if False else "/v1internal/operations/" + name.split("/")[-1],
                           "GET" if False else "POST", tok, {}, timeout=40)
                rec["poll%d" % k] = rr
                try:
                    jp = json.loads(rr["b"] if rr["c"] < 300 else "{}")
                except Exception:
                    jp = {}
                proj = _pick_project(jp.get("response") or {}) or _pick_project(jp) or ""
                if proj:
                    break
        rec["project"] = proj
        out["onboard"][tid] = rec
        if proj:
            for m in ("gemini-2.5-flash", "claude-sonnet-5-5-medium"):
                out["gen"][tid + ":" + m] = _raw_gen(HOSTS[0], tok, m, proj, timeout=45)
    return out


def _raw_gen(host, tok, model, proj, headers=None, timeout=50):
    body = build_payload(model, proj, {"messages": [{"role": "user", "content": "say: PONG"}]})
    return _call(host, "/v1internal:generateContent", tok, body, headers=headers, timeout=timeout)


def model_catalog(tok, proj):
    """Compact view of what the account can actually call, with live quota."""
    cat = {}
    for host in HOSTS[:2]:
        tag = host.split("//")[1].split(".")[0]
        raw = _full(host, "/v1internal:fetchAvailableModels", tok, {"project": proj}, limit=400000)
        try:
            j = json.loads(raw)
        except Exception:
            # fall back to a regex scan: the ids are enough to call
            ids2 = [m for m in re.findall(r'"([a-z0-9][a-z0-9.\-]{2,60})":\s*\{', raw)
                    if any(k in m for k in ("gemini", "claude", "gpt", "flash", "pro"))]
            seen = []
            for m in ids2:
                if m not in seen:
                    seen.append(m)
            cat[tag] = {"count": len(seen), "items": [{"id": m} for m in seen],
                        "note": "regex fallback", "bytes": len(raw)}
            continue
        ms = j.get("models") or j
        items = []
        if isinstance(ms, dict):
            for mid, m in ms.items():
                if not isinstance(m, dict):
                    continue
                qi = m.get("quotaInfo") or {}
                items.append({"id": mid, "name": m.get("displayName"),
                              "frac": qi.get("remainingFraction"), "prov": m.get("apiProvider"),
                              "rec": bool(m.get("recommended"))})
        elif isinstance(ms, list):
            for m in ms:
                if isinstance(m, dict):
                    qi = m.get("quotaInfo") or {}
                    items.append({"id": m.get("id") or m.get("model"), "name": m.get("displayName"),
                                  "frac": qi.get("remainingFraction"), "prov": m.get("apiProvider")})
        cat[tag] = {"count": len(items), "items": items}
    return cat


def gen_matrix(tok, proj):
    """Try every advertised model id exactly once and report the verdict."""
    res = {}
    cat = model_catalog(tok, proj)
    ids = []
    for tag in ("daily-cloudcode-pa", "cloudcode-pa"):
        for it in (cat.get(tag, {}).get("items") or []):
            if it.get("id") and it["id"] not in ids:
                ids.append(it["id"])
    for mid in ids[:14]:
        host = HOSTS[1] if "gemini" in mid else HOSTS[0]
        r = _raw_gen(host, tok, mid, proj, timeout=45)
        res[mid] = {"c": r["c"], "b": r["b"][:200]}
    return {"ids": ids, "results": res}


HOSTS = ["https://cloudcode-pa.googleapis.com",
         "https://daily-cloudcode-pa.googleapis.com",
         "https://autopush-cloudcode-pa.googleapis.com"]


def _call(host, path, tok, payload, headers=None, timeout=30, method=None):
    h = {"Content-Type": "application/json", "Authorization": "Bearer " + tok}
    if headers:
        h.update(headers)
    data = b"" if payload is None else json.dumps(payload).encode()
    try:
        resp = _http(host + path, data=data, headers=h, timeout=timeout, method=method)
        raw = resp.read().decode("utf-8", "replace")
        return {"c": resp.getcode(), "b": raw[:420].replace("\n", " ")}
    except urllib.error.HTTPError as e:
        return {"c": e.code, "b": e.read().decode("utf-8", "replace")[:300].replace("\n", " ")}
    except Exception as e:
        return {"c": 0, "b": type(e).__name__ + ": " + str(e)[:90]}


def deep_probe(tok):
    """Map every plausible upstream host/endpoint/header to see what answers 429."""
    out = {"lca": {}, "models": {}, "gen": {}, "hosts": HOSTS}
    proj = ""
    for host in HOSTS:
        tag = host.split("//")[1].split(".")[0]
        r = _call(host, "/v1internal:loadCodeAssist", tok,
                  {"metadata": {"pluginType": "GEMINI_CLI", "ideType": "ANTIGRAVITY"}})
        out["lca"][tag] = r
        if proj == "" and r["c"] == 200:
            try:
                j = json.loads(r["b"].split(" ...")[0]) if False else None
            except Exception:
                j = None
            p = _pick_project(json.loads(_full(host, "/v1internal:loadCodeAssist", tok) or "{}"))
            if p:
                proj = p
                out["project_from"] = tag
    out["project"] = proj
    for host in HOSTS:
        tag = host.split("//")[1].split(".")[0]
        out["models"][tag + ":fetch"] = _call(host, "/v1internal:fetchAvailableModels", tok,
                                              {"project": proj})
        out["models"][tag + ":list"] = _call(host, "/v1internal:listModels", tok,
                                             {"project": proj})
    body = build_payload("gemini-2.5-flash", proj,
                         {"messages": [{"role": "user", "content": "say: PONG"}]})
    for host in HOSTS:
        tag = host.split("//")[1].split(".")[0]
        out["gen"][tag + ":plain"] = _call(host, "/v1internal:generateContent", tok, body, timeout=45)
        out["gen"][tag + ":cli-hdr"] = _call(
            host, "/v1internal:generateContent", tok, body, timeout=45,
            headers={"X-Goog-Api-Client": "gl-python/3.11.0",
                     "User-Agent": "GeminiCLI/0.1.0 (linux; x64)"})
        out["gen"][tag + ":stream"] = _call(host, "/v1internal:streamGenerateContent?alt=sse",
                                            tok, body, timeout=45,
                                            headers={"Accept": "text/event-stream"})
    out["gen"]["noproj"] = _call("https://cloudcode-pa.googleapis.com",
                                 "/v1internal:generateContent", tok,
                                 build_payload("gemini-2.5-flash", "",
                                               {"messages": [{"role": "user", "content": "say: PONG"}]}),
                                 timeout=45)
    return out


def _full(host, path, tok, payload=None, limit=1500):
    data = b"" if payload is None else json.dumps(payload).encode()
    try:
        resp = _http(host + path, data=data,
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer " + tok}, timeout=30)
        return resp.read().decode("utf-8", "replace")[:limit]
    except Exception:
        return ""


PROBE_MODELS = ["gemini-3-pro-preview", "gemini-2.5-pro", "gemini-2.5-flash",
                "gemini-2.0-flash", "gemini-2.5-flash-lite"]


def _one_gen(tok, proj, model, timeout=60):
    payload = build_payload(model, proj,
                            {"messages": [{"role": "user", "content": "say: PONG"}]})
    try:
        resp = _http(CFG["upstream"] + GEN_PATH, data=json.dumps(payload).encode(),
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer " + tok}, timeout=timeout)
        j = unwrap(_json_or_text(resp))
        return "OK " + repr(pick_text(j)[:40])
    except urllib.error.HTTPError as e:
        d = e.read().decode("utf-8", "replace")
        return "HTTP %s %s" % (e.code, d[:120].replace("\n", " "))
    except Exception as e:
        return "FAIL " + type(e).__name__ + ": " + str(e)[:80]


def probe_models(tok, proj):
    res = {}
    for m in PROBE_MODELS:
        res[m] = _one_gen(tok, proj, m)
    return res


def main():
    srv = ThreadingHTTPServer((CFG["bind"], CFG["port"]), Handler)
    sys.stderr.write("\n[relay] listening on http://%s:%s\n" % (CFG["bind"], CFG["port"]))
    sys.stderr.write("[relay] upstream=%s model=%s\n" % (CFG["upstream"], CFG["default_model"]))
    sys.stderr.write("[relay] health=http://127.0.0.1:%s/healthz  diag=http://127.0.0.1:%s/__diag\n"
                     % (CFG["port"], CFG["port"]))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[relay] bye\n")


if __name__ == "__main__":
    main()
# restart-20261115b
