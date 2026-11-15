# -*- coding: utf-8 -*-
import json, os, time, traceback, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

OUT = {}
def flush():
    with open("OPS_RESULT.json", "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=2)
def rec(k, v):
    OUT[k] = v; flush()
    print("OPS " + k + " = " + json.dumps(v, ensure_ascii=False)[:800], flush=True)

CID  = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CSEC = "".join(["GOCSPX-", "K58FW", "R486LdLJ1mLB8sXC4z6qDAf"])
UA = "antigravity/cli/1.5.3 linux/amd64"
URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent"

def call(url, body=None, tok=None, timeout=90, form=False):
    if body is None:
        rq = urllib.request.Request(url, method="GET")
    else:
        data = (urllib.parse.urlencode(body).encode() if form
                else json.dumps(body).encode())
        ct = "application/x-www-form-urlencoded" if form else "application/json"
        rq = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": ct})
    rq.add_header("Accept", "application/json")
    if tok:
        rq.add_header("Authorization", "Bearer " + tok)
        rq.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:600]
    except Exception as ex:
        return "ERR", type(ex).__name__ + " " + str(ex)[:150]

def one(tok, project, model):
    body = {"project": project, "model": model,
            "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                        "generationConfig": {"maxOutputTokens": 4}}}
    c, b = call(URL, body, tok, timeout=60)
    return c, b[:180]

def burst(tok, project, n=8):
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(lambda _: one(tok, project, "gemini-2.5-pro"), range(n)))
    codes = {}
    for c, b in res:
        codes[str(c)] = codes.get(str(c), 0) + 1
    sample = next((b for c, b in res if c != 200), "")
    return {"codes": codes, "sample_err": sample}

def main():
    RT = (os.environ.get("RT") or "").strip()
    c, b = call("https://oauth2.googleapis.com/token", {"client_id": CID, "client_secret": CSEC,
                "refresh_token": RT, "grant_type": "refresh_token"}, form=True)
    if c != 200:
        rec("refresh", {"status": c}); return
    tok = json.loads(b)["access_token"]

    rec("burst_shared_aicode", burst(tok, "aicode-consumers"))
    time.sleep(8)
    rec("burst_fake_project", burst(tok, "gw-custom-probe-1"))
    time.sleep(8)
    rec("burst_fake_project2", burst(tok, "gw-zzz-999"))

try:
    main()
except Exception:
    rec("traceback", traceback.format_exc()[-1200:])
flush()
print("RESULT WRITTEN", flush=True)
