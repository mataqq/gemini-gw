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
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(HERE, "relay.local.json")

TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO = "https://www.googleapis.com/oauth2/v3/userinfo"
DEFAULT_UPSTREAM = "https://cloudcode-pa.googleapis.com"
GEN_PATH = "/v1internal:generateContent"
STREAM_PATH = "/v1internal:streamGenerateContent?alt=sse"
UA = "antigravity/1.0.0 (relay-py)"

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
    cfg.setdefault("default_model", "gemini-2.5-pro")
    cfg.setdefault("upstream", DEFAULT_UPSTREAM)
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


def build_payload(model, proj, body):
    msgs = body.get("messages") or []
    sys_parts, contents = [], []
    for m in msgs:
        role = m.get("role", "user")
        txt = to_parts(m.get("content", ""))
        if role in ("system", "developer"):
            sys_parts.extend(txt)
        else:
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
    return {"model": model, "project": proj, "request": out}


def pick_text(payload):
    try:
        cand = (payload.get("candidates") or [])[0]
        parts = cand.get("content", {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)
    except Exception:
        return ""


def unwrap(obj):
    if isinstance(obj, dict) and isinstance(obj.get("response"), dict):
        return obj["response"]
    return obj


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
        if p == "/v1/models":
            if not self._authed():
                return self._send(401, {"error": {"message": "invalid api key", "type": "invalid_request_error"}})
            return self._send(200, {"object": "list", "data": [
                {"id": CFG["default_model"], "object": "model", "owned_by": "google"}]})
        return self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
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
            payload = build_payload(model, proj, body)
            if body.get("stream"):
                return self._stream(token, payload, model)
            resp = _http(CFG["upstream"] + GEN_PATH, data=json.dumps(payload).encode(),
                         headers={"Content-Type": "application/json",
                                  "Authorization": "Bearer " + token}, timeout=180)
            j = unwrap(_json_or_text(resp))
            text = pick_text(j)
            return self._send(200, {
                "id": "chatcmpl-" + str(int(time.time())),
                "object": "chat.completion", "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })
        except urllib.error.HTTPError as e:
            d = e.read().decode("utf-8", "replace")
            return self._send(502, {"error": {"message": "upstream %s: %s" % (e.code, d[:400]),
                                              "type": "upstream_error"}})
        except Exception as e:
            return self._send(502, {"error": {"message": str(e)[:400], "type": "gateway_error"}})

    def _stream(self, token, payload, model):
        cid = "chatcmpl-" + str(int(time.time()))
        resp = _http(CFG["upstream"] + STREAM_PATH, data=json.dumps(payload).encode(),
                     headers={"Content-Type": "application/json",
                              "Accept": "text/event-stream",
                              "Authorization": "Bearer " + token}, timeout=180)
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
                    emit(chunk({"content": t}))
            emit(chunk({}, "stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception as e:
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
        return out


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
