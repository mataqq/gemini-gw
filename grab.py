# -*- coding: utf-8 -*-
"""Runner-side: discover + download the Antigravity installer, publish as release asset."""
import os, re, json, subprocess, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}

def get(u, tmo=30):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=tmo)
        return r.getcode(), r.read()
    except Exception as e:
        return 0, ("%s: %s" % (type(e).__name__, e)).encode()

cands = []
for page in ("https://antigravity.google/download", "https://antigravity.google/"):
    c, b = get(page, 30)
    print("PAGE %s -> %s len=%d" % (page, c, len(b), flush=True))
    if c != 200:
        continue
    t = b.decode("utf-8", "replace")
    for u in sorted(set(re.findall(r"https?://[^\"'<>\\ ]+", t))):
        low = u.lower()
        if ("storage.googleapis" in low or "dl.google" in low
                or re.search(r"\.(exe|zip|dmg|appimage|deb)(\?|$)", low)):
            cands.append(u)
    print("HINT-URLS", json.dumps(cands[:40], indent=0), flush=True)

GUESS = [
    "https://storage.googleapis.com/antigravity-public/stable/win32-x64/Antigravity-Setup.exe",
    "https://storage.googleapis.com/antigravity-public/latest/win32-x64/Antigravity-Setup.exe",
    "https://storage.googleapis.com/antigravity-public/win32-x64/Antigravity-Setup.exe",
    "https://dl.google.com/antigravity/win/Antigravity-Setup.exe",
    "https://antigravity.google/download/win",
]

hit = None
for u in cands + GUESS:
    if ".exe" not in u.lower():
        continue
    c, b = get(u, 40)
    print("TRY %s -> %s len=%d head=%s" % (u, c, len(b), b[:4], flush=True))
    if c == 200 and len(b) > 20 * 1024 * 1024 and b[:2] == b"MZ":
        open("Antigravity-Setup.exe", "wb").write(b)
        hit = u
        print("GOT", u, len(b), flush=True)
        break

if not hit:
    print("NO-INSTALLER")
    raise SystemExit(0)

# also try to grab the blockmap/yml for later version checks
subprocess.run(["bash", "-lc", "ls -l Antigravity-Setup.exe; file Antigravity-Setup.exe || true"], check=False)

tag = "ag-setup"
subprocess.run(["gh", "release", "delete", tag, "--repo", os.environ["GITHUB_REPOSITORY"],
                "--yes"], check=False, capture_output=True)
r = subprocess.run(["gh", "release", "create", tag,
                    "--repo", os.environ["GITHUB_REPOSITORY"],
                    "Antigravity-Setup.exe",
                    "--title", "Antigravity Setup",
                    "--notes", "mirror for hosts that cannot reach antigravity.google"],
                   capture_output=True, text=True)
print("REL-OUT", r.returncode, r.stdout[-400:], r.stderr[-400:], flush=True)

api = subprocess.run(["gh", "api", "/repos/%s/releases/tags/%s" % (os.environ["GITHUB_REPOSITORY"], tag)],
                     capture_output=True, text=True)
try:
    d = json.loads(api.stdout)
    for a in d.get("assets", []):
        print("ASSET id=%s name=%s size=%s url=%s" % (a["id"], a["name"], a["size"], a["browser_download_url"]), flush=True)
except Exception as e:
    print("ASSET-ERR", e, api.stderr[-200:], flush=True)
