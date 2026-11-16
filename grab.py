import json, re, urllib.request, urllib.error, urllib.parse, time

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
FORM_ID = "1FAIpQLScOJWibQ_-hYuVv63kJTgEqlgAaOwRLGFTbXm-QY1gz8U0CGA"
VIEW = "https://docs.google.com/forms/d/e/%s/viewform?usp=send_form" % FORM_ID
POST = "https://docs.google.com/forms/d/e/%s/formResponse" % FORM_ID
ENTRY = "172966166"
ANSWER = "Yes, I understand"

out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

req = urllib.request.Request(VIEW, headers={
    "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9"})
r = urllib.request.urlopen(req, timeout=45)
html = r.read().decode("utf-8", "replace")
out["view_status"] = r.getcode()
out["cookies"] = r.headers.get_all("Set-Cookie") or []

fbzx = None
m = re.search(r'name="fbzx"\s+value="(-?\d+)"', html)
if m:
    fbzx = m.group(1)
if not fbzx:
    m = re.search(r'\[\\?"fbzx\\?"\s*,\s*\\?"?(-?\d+)', html)
    if m:
        fbzx = m.group(1)
if not fbzx:
    m = re.search(r'fbzx[^0-9-]{0,20}(-?\d{10,})', html)
    if m:
        fbzx = m.group(1)
out["fbzx"] = fbzx
out["fbzx_near"] = [html[max(0, mm.start()-60):mm.start()+80].replace("\n"," ") for mm in list(re.finditer("fbzx", html))[:3]]

draft = None
m = re.search(r'\[\\?"draftResponse\\?"\s*,\s*(\[.*?\])\s*\]', html)
if not m:
    m = re.search(r'name="draftResponse"\s+value="([^"]*)"', html)
if m:
    draft = m.group(1)
out["has_draft"] = bool(draft)

fields = {
    "entry.%s" % ENTRY: ANSWER,
    "fvv": "1",
    "pageHistory": "0",
    "draftResponse": draft if draft else "[]",
}
if fbzx:
    fields["fbzx"] = fbzx

payload = urllib.parse.urlencode(fields).encode()
req2 = urllib.request.Request(POST, data=payload, method="POST", headers={
    "User-Agent": UA,
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": VIEW,
    "Origin": "https://docs.google.com",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0"})

body = ""
try:
    r2 = urllib.request.urlopen(req2, timeout=45)
    out["post_status"] = r2.getcode(); out["post_final_url"] = r2.geturl()
    body = r2.read().decode("utf-8", "replace")
except urllib.error.HTTPError as e:
    out["post_status"] = e.code
    body = e.read().decode("utf-8", "replace")
except Exception as e:
    out["post_status"] = -1
    out["post_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])

out["body_len"] = len(body)
t = re.search(r"<title>(.*?)</title>", body, re.S)
out["title"] = t.group(1).strip()[:200] if t else None
low = body.lower()
out["markers"] = {
    "thanks": "your response has been recorded" in low,
    "closed": "no longer accepting responses" in low,
    "already": "already responded" in low,
    "notfound": "page not found" in low,
}
if out["post_status"] != 200 or not out["markers"]["thanks"]:
    out["snippet"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))[:400]

with open("APPEAL_SUBMIT.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2)[:4000])
