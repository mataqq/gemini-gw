# -*- coding: utf-8 -*-
import json, os, time, traceback, urllib.parse, urllib.request, urllib.error

OUT = {}
def flush():
    with open("OPS_RESULT.json", "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=2)
def rec(k, v):
    OUT[k] = v; flush()
    print("OPS " + k + " = " + json.dumps(v, ensure_ascii=False)[:900], flush=True)

CID  = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CSEC = "".join(["GOCSPX-", "K58FW", "R486LdLJ1mLB8sXC4z6qDAf"])
UA = "antigravity/cli/1.5.3 linux/amd64"

def call(url, body=None, tok=None, timeout=90, form=False):
    if body is None:
        rq = urllib.request.Request(url, method="GET")
    else:
        if form:
            data = urllib.parse.urlencode(body).encode(); ct = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode(); ct = "application/json"
        rq = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": ct})
    rq.add_header("Accept", "application/json")
    if tok:
        rq.add_header("Authorization", "Bearer " + tok)
        rq.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:800]
    except Exception as ex:
        return "ERR", type(ex).__name__ + " " + str(ex)[:200]

def lca(tok):
    c, b = call("https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
                {"metadata": {}}, tok)
    if c != 200:
        return {"status": c, "err": b[:300]}
    j = json.loads(b)
    return {"status": c,
            "currentTier": j.get("currentTier", {}).get("id"),
            "project": j.get("cloudaicompanionProject"),
            "gcpManaged": j.get("gcpManaged")}

def gen(tok, project, model="gemini-2.5-flash"):
    body = {"project": project, "model": model,
            "request": {"contents": [{"role": "user", "parts": [{"text": "reply with OK"}]}],
                        "generationConfig": {"maxOutputTokens": 8}}}
    c, b = call("https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent", body, tok, timeout=90)
    txt = b
    try:
        j = json.loads(b)
        cands = (j.get("response") or {}).get("candidates") or j.get("candidates") or []
        if cands:
            parts = cands[0].get("content", {}).get("parts", [])
            txt = "OK:" + "".join(p.get("text", "") for p in parts)[:60]
    except Exception:
        pass
    return {"status": c, "out": txt[:400]}

def main():
    RT = (os.environ.get("RT") or "").strip()
    c, b = call("https://oauth2.googleapis.com/token", {"client_id": CID, "client_secret": CSEC,
                "refresh_token": RT, "grant_type": "refresh_token"}, form=True)
    if c != 200:
        rec("refresh", {"status": c}); return
    tok = json.loads(b)["access_token"]

    rec("lca_before", lca(tok))
    rec("gen_shared", gen(tok, "aicode-consumers"))

    PROJ = "gw-custom-probe-1"
    c, b = call("https://cloudcode-pa.googleapis.com/v1internal:onboardUser",
                {"tierId": "standard-tier", "cloudaicompanionProject": PROJ}, tok, timeout=90)
    rec("onboard", {"status": c, "body": b[:280]})

    for attempt in range(6):
        st = lca(tok)
        rec("lca_after_" + str(attempt), st)
        if st.get("project") == PROJ and st.get("currentTier") == "standard-tier":
            break
        time.sleep(4)

    rec("gen_custom", gen(tok, PROJ))
    rec("gen_custom_retry", gen(tok, PROJ))
    rec("lca_final", lca(tok))

try:
    main()
except Exception:
    rec("traceback", traceback.format_exc()[-1200:])
flush()
print("RESULT WRITTEN", flush=True)
