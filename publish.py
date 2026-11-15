#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish the live tunnel URL + upstream diagnostic into the repo so it can be read
while the workflow is still running (Actions logs are 404 until the job ends)."""
import base64, json, os, sys, time, urllib.request, urllib.error

REPO = os.environ["GITHUB_REPOSITORY"]
TOK = os.environ["GITHUB_TOKEN"]
API = "https://api.github.com/repos/%s/contents/" % REPO
H = {"Authorization": "Bearer " + TOK, "Accept": "application/vnd.github+json",
     "User-Agent": "relay-publisher", "Content-Type": "application/json"}

def put(path, text, tmo=25):
    url = API + path
    sha = None
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=tmo)
        sha = json.loads(r.read().decode()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return e.code, "read " + str(e.code)
    except Exception as e:
        return 0, "read " + type(e).__name__
    body = {"message": "relay status " + path,
            "content": base64.b64encode(text.encode()).decode()}
    if sha:
        body["sha"] = sha
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers=H, method="PUT"), timeout=tmo)
        return r.getcode(), "ok"
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:120]
    except Exception as e:
        return 0, type(e).__name__ + ": " + str(e)[:80]

def fetch_rec():
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8080/__rec", timeout=8)
        items = json.loads(r.read().decode()).get("items") or []
    except Exception:
        return ""
    bad = [i for i in items if i.get("code") != 200]
    keep = bad[-40:]
    tail = items[-25:]
    summary = {"total": len(items), "bad": len(bad),
               "codes": sorted({str(i.get("code")) for i in items}),
               "uagents": sorted({(i.get("ua") or "-")[:40] for i in items})[-6:],
               "last": tail, "bad_items": keep}
    return json.dumps(summary, ensure_ascii=False)

def fetch_err():
    """Snapshot the relay's in-memory error ring + log tail so chain failures
    are inspectable without the tunnel."""
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8080/__err", timeout=8)
        d = json.loads(r.read().decode())
    except Exception:
        return ""
    try:
        with open("/tmp/relay.log", "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.read()[-4000:]
    except Exception:
        tail = ""
    out = {"last": [str(x)[:400] for x in (d.get("last") or [])[-12:]],
           "errs": [str(x)[:600] for x in (d.get("errs") or [])[-5:]],
           "log_tail": tail}
    return json.dumps(out, ensure_ascii=False)

def read(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""

def main():
    once = "--once" in sys.argv
    while True:
        urls = read("/tmp/public_urls.txt").strip() or read("/tmp/public_url.txt").strip()
        lines = [u.strip() for u in urls.splitlines() if u.strip().startswith("http")]
        url = "\n".join(lines[:4])
        diag = read("/tmp/diag.json").strip()
        svo = read("/tmp/serveo_tail.txt").strip()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if url:
            c1, m1 = put("LIVE_URL.txt", url + "\n")  # one host per line, primary first
            print("LIVE_URL.txt", c1, m1, flush=True)
        if diag:
            try:
                clean = json.dumps({k: v for k, v in json.loads(diag).items()
                                    if k in ("ok", "client", "steps", "project")},
                                   ensure_ascii=False)
            except Exception:
                clean = ""
            if clean:
                c2, m2 = put("DIAG.json", clean + "\n")
            print("DIAG.json", c2, m2, flush=True)
        recs = fetch_rec()
        if recs:
            c4, m4 = put("REC.json", recs + "\n")
            print("REC.json", c4, m4, flush=True)
        errs = fetch_err()
        if errs:
            c5, m5 = put("ERR.txt", errs + "\n")
            print("ERR.txt", c5, m5, flush=True)
        if svo:
            c3, m3 = put("SERVEO.txt", svo[-4000:] + "\n")
            print("SERVEO.txt", c3, m3, flush=True)
        if once:
            return
        time.sleep(90)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("publisher died:", type(e).__name__, str(e)[:120], flush=True)
