# -*- coding: utf-8 -*-
"""Runner-side: discover + download the Antigravity installer, then publish it as a release asset."""
import os, re, json, subprocess, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}


def get(u, tmo=30):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=tmo)
        return r.getcode(), r.read()
    except Exception as e:
        return 0, ("%s: %s" % (type(e).__name__, e)).encode()


def log(*a):
    print(*a, flush=True)


cands = []
for page in ("https://antigravity.google/download", "https://antigravity.google/"):
    c, b = get(page, 30)
    log("PAGE", page, c, len(b))
    if c != 200:
        log("PAGE-BODY", b[:160])
        continue
    t = b.decode("utf-8", "replace")
    log("PAGE-HEAD", t[:200].replace("\n", " "))
    for u in sorted(set(re.findall(r"https?://[^\"'<>\\ ]+", t))):
        low = u.lower()
        if ("storage.googleapis" in low or "dl.google" in low
                or re.search(r"\.(exe|zip|dmg|appimage|deb)(\?|$)", low)
                or "win32" in low or "x64" in low or "download" in low and "google" in low):
            cands.append(u)
    with open("page.html", "w", encoding="utf-8") as f:
        f.write(t)
log("HINT-URLS", json.dumps(cands[:60]))

GUESS = [
    "https://storage.googleapis.com/antigravity-public/stable/win32-x64/Antigravity-Setup.exe",
    "https://storage.googleapis.com/antigravity-public/latest/win32-x64/Antigravity-Setup.exe",
    "https://storage.googleapis.com/antigravity-public/win32-x64/Antigravity-Setup.exe",
    "https://storage.googleapis.com/antigravity-public/stable/Antigravity-Setup.exe",
    "https://storage.googleapis.com/antigravity-public/win32/Antigravity-Setup.exe",
    "https://dl.google.com/antigravity/Antigravity-Setup.exe",
    "https://dl.google.com/release2/antigravity/Antigravity-Setup.exe",
    "https://update.antigravity.google/win/x64/Antigravity-Setup.exe",
]

hit = None
for u in cands + GUESS:
    if ".exe" not in u.lower():
        continue
    c, b = get(u, 45)
    log("TRY", u, c, len(b), b[:4])
    if c == 200 and len(b) > 20 * 1024 * 1024 and b[:2] == b"MZ":
        with open("Antigravity-Setup.exe", "wb") as f:
            f.write(b)
        hit = u
        log("GOT", u, len(b))
        break

if not hit:
    log("NO-INSTALLER")
    raise SystemExit(0)

tag = "ag-setup"
repo = os.environ["GITHUB_REPOSITORY"]
subprocess.run(["gh", "release", "delete", tag, "--repo", repo, "--yes"], check=False, capture_output=True)
r = subprocess.run(["gh", "release", "create", tag, "--repo", repo, "Antigravity-Setup.exe",
                    "--title", "Antigravity Setup",
                    "--notes", "mirror for hosts that cannot reach antigravity.google"],
                   capture_output=True, text=True)
log("REL-OUT", r.returncode, r.stdout[-300:], r.stderr[-300:])
api = subprocess.run(["gh", "api", "/repos/%s/releases/tags/%s" % (repo, tag)], capture_output=True, text=True)
try:
    d = json.loads(api.stdout)
    for a in d.get("assets", []):
        log("ASSET", a["id"], a["name"], a["size"], a["url"], a["browser_download_url"])
except Exception as e:
    log("ASSET-ERR", e, api.stderr[-200:])
