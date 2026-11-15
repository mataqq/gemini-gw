# -*- coding: utf-8 -*-
import json, os, traceback, urllib.parse, urllib.request, urllib.error

OUT = {}
def flush():
    with open("OPS_RESULT.json", "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=2)
def rec(k, v):
    OUT[k] = v; flush()
    print("OPS " + k + " = " + json.dumps(v, ensure_ascii=False)[:1000], flush=True)

CID  = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CSEC = "".join(["GOCSPX-", "K58FW", "R486LdLJ1mLB8sXC4z6qDAf"])
UA = "antigravity/cli/1.5.3 linux/amd64"

def call(url, body=None, tok=None, timeout=60, form=False):
    if body is None:
        rq = urllib.request.Request(url, method="GET")
    else:
        if form:
            data = urllib.parse.urlencode(body).encode(); ct = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode(); ct = "application/json"
        rq = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": ct})
    h = {"Accept": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    if not form and tok: h["User-Agent"] = UA
    for k, v in h.items():
        rq.add_header(k, v)
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:800]
    except Exception as ex:
        return "ERR", type(ex).__name__ + " " + str(ex)[:200]

def main():
    RT = (os.environ.get("RT") or "").strip()
    c, b = call("https://oauth2.googleapis.com/token", {"client_id": CID, "client_secret": CSEC,
                "refresh_token": RT, "grant_type": "refresh_token"}, form=True)
    if c != 200:
        rec("refresh", {"status": c}); return
    tok = json.loads(b)["access_token"]

    # full raw loadCodeAssist
    c, b = call("https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
                {"metadata": {}}, tok)
    rec("lca_status", c)
    if c == 200:
        try:
            rec("lca_raw", json.loads(b))
        except Exception:
            rec("lca_text", b[:1200])
    else:
        rec("lca_err", b[:400])

    BOGUS = "gw-nonexistent-000001"
    variants = [
        ("std_proj_nometa", {"tierId": "standard-tier", "cloudaicompanionProject": BOGUS}),
        ("std_proj_metaempty", {"tierId": "standard-tier", "metadata": {},
                                "cloudaicompanionProject": BOGUS}),
        ("std_noproj", {"tierId": "standard-tier"}),
    ]
    for label, body in variants:
        c, b = call("https://cloudcode-pa.googleapis.com/v1internal:onboardUser", body, tok, timeout=90)
        rec("onb_" + label, {"status": c, "body": b[:700]})
        if c == 200:
            try:
                j = json.loads(b); resp = j.get("response") or {}
                rec("onb_" + label + "_project", resp.get("cloudaicompanionProject", "none"))
                rec("onb_" + label + "_tier", resp.get("tierId", j.get("tierId", "none")))
            except Exception:
                pass

    # try a couple of plausible project ids to see if any is accepted
    for pid in ("gw-ops-test", "gemini-gw-1", "project-gw1"):
        c, b = call("https://cloudcode-pa.googleapis.com/v1internal:onboardUser",
                    {"tierId": "standard-tier", "cloudaicompanionProject": pid}, tok, timeout=90)
        rec("try_" + pid, {"status": c, "body": b[:350]})

try:
    main()
except Exception:
    rec("traceback", traceback.format_exc()[-1200:])
flush()
print("RESULT WRITTEN", flush=True)
