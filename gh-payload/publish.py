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

def read(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""

def main():
    once = "--once" in sys.argv
    while True:
        url = read("/tmp/public_url.txt").strip()
        diag = read("/tmp/diag.json").strip()
        svo = read("/tmp/serveo_tail.txt").strip()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if url:
            c1, m1 = put("LIVE_URL.txt", url + "\n")
            print("LIVE_URL.txt", c1, m1, flush=True)
        if diag:
            c2, m2 = put("DIAG.json", diag + "\n")
            print("DIAG.json", c2, m2, flush=True)
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
