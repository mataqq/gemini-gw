# -*- coding: utf-8 -*-
import json, os, random, string, traceback, urllib.parse, urllib.request, urllib.error

OUT = {}

def flush():
    with open("OPS_RESULT.json", "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=2)

def rec(k, v):
    OUT[k] = v
    flush()
    print("OPS " + k + " = " + json.dumps(v, ensure_ascii=False)[:900], flush=True)

CID   = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CSEC  = "".join(["GOCSPX-", "K58FW", "R486LdLJ1mLB8sXC4z6qDAf"])
CID2  = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
CSEC2 = "".join(["GOCSPX-", "4uHgMP", "m-1o7Sk-geV6Cu5", "clXFsxl"])
UA = "antigravity/cli/1.5.3 linux/amd64"

def post(url, data, hdr=None, timeout=30, form=False):
    if form:
        body = urllib.parse.urlencode(data).encode()
        ct = "application/x-www-form-urlencoded"
    else:
        body = json.dumps(data).encode()
        ct = "application/json"
    h = {"Content-Type": ct}
    h.update(hdr or {})
    rq = urllib.request.Request(url, data=body, method="POST", headers=h)
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:900]
    except Exception as ex:
        return "ERR", type(ex).__name__ + " " + str(ex)[:200]

def get(url, tok=None, timeout=30):
    h = {"Accept": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    rq = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:900]
    except Exception as ex:
        return "ERR", type(ex).__name__ + " " + str(ex)[:200]

def main():
    RT = (os.environ.get("RT") or "").strip()
    rec("rt_present", bool(RT))
    rec("rt_len", len(RT))

    tok = None
    for name, cid, cs in (("antigravity", CID, CSEC), ("gemini-cli", CID2, CSEC2)):
        c, b = post("https://oauth2.googleapis.com/token",
                    {"client_id": cid, "client_secret": cs,
                     "refresh_token": RT, "grant_type": "refresh_token"}, form=True)
        rec("refresh_" + name, {"status": c, "body": ("ok" if c == 200 else b[:200])})
        if c == 200:
            tok = json.loads(b).get("access_token")
            rec("client_used", name)
            break

    if not tok:
        rec("verdict", "no access token; stop")
        return

    c, b = get("https://oauth2.googleapis.com/tokeninfo?access_token=" + tok)
    scopes = []
    if c == 200:
        d = json.loads(b)
        scopes = (d.get("scope") or "").split()
        rec("scope", d.get("scope"))
        rec("aud", d.get("aud") or d.get("azp"))
        rec("has_cloud_platform", any("cloud-platform" in s for s in scopes))
        rec("has_cloudplatformprojects", any("cloudplatformprojects" in s for s in scopes))
    else:
        rec("tokeninfo", {"status": c, "body": b[:300]})

    c, b = get("https://cloudresourcemanager.googleapis.com/v1/projects?pageSize=50", tok)
    listed = []
    if c == 200:
        for p in json.loads(b).get("projects", []):
            listed.append({"projectId": p.get("projectId"), "name": p.get("name"),
                           "lifecycleState": p.get("lifecycleState")})
        rec("projects_list", {"status": c, "count": len(listed), "items": listed[:10]})
    else:
        rec("projects_list", {"status": c, "body": b[:400]})

    pid = "gw-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=9))
    c2, b2 = post("https://cloudresourcemanager.googleapis.com/v1/projects",
                  {"projectId": pid, "name": "gemini-gw"},
                  {"Authorization": "Bearer " + tok})
    rec("project_create", {"status": c2, "projectId": pid, "body": b2[:600]})

    if c2 == 200:
        for tier in ("standard-tier", "free-tier"):
            c3, b3 = post("https://cloudcode-pa.googleapis.com/v1internal:onboardUser",
                          {"tierId": tier, "metadata": {"pluginType": "GEMINI_CLI"},
                           "cloudaicompanionProject": pid},
                          {"Authorization": "Bearer " + tok, "User-Agent": UA}, timeout=45)
            rec("onboard_" + tier, {"status": c3, "project": pid, "body": b3[:700]})
            if c3 == 200:
                break

try:
    main()
except Exception:
    rec("traceback", traceback.format_exc()[-1500:])
flush()
print("RESULT WRITTEN", flush=True)
