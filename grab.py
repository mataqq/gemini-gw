import json, re, urllib.request, urllib.error, urllib.parse, time, http.cookiejar

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
FORM_ID = "1FAIpQLScOJWibQ_-hYuVv63kJTgEqlgAaOwRLGFTbXm-QY1gz8U0CGA"
VIEW = "https://docs.google.com/forms/d/e/%s/viewform" % FORM_ID
POST = "https://docs.google.com/forms/d/e/%s/formResponse" % FORM_ID
ENTRY = "172966166"
ANSWER = "Yes, I understand"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.addheaders = [("User-Agent", UA), ("Accept-Language", "en-US,en;q=0.9")]

out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

r = opener.open(urllib.request.Request(VIEW), timeout=45)
html = r.read().decode("utf-8", "replace")
out["view_status"] = r.getcode()
out["cookies_count"] = len(jar)

def grab(name):
    m = re.search(r'name="%s"\s+value="([^"]*)"' % name, html)
    return m.group(1) if m else None

fbzx = grab("fbzx")
ts_field = grab("submissionTimestamp")
out["fbzx"] = fbzx
out["submissionTimestamp"] = ts_field

# capture the full hidden field set the page expects
hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', html))
fields = {}
for k, v in hidden.items():
    if k.startswith("entry.") and k != "entry.%s" % ENTRY:
        continue
    fields[k] = v
fields["entry.%s" % ENTRY] = ANSWER
fields["fvv"] = "1"
fields["pageHistory"] = "0"
if fbzx:
    fields["fbzx"] = fbzx
out["hidden_keys"] = sorted(hidden.keys())

payload = urllib.parse.urlencode(fields).encode()
req = urllib.request.Request(POST, data=payload, method="POST", headers={
    "User-Agent": UA,
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": VIEW,
    "Origin": "https://docs.google.com",
    "Accept": "text/html,application/xhtml+xml"})

body = ""
try:
    r2 = opener.open(req, timeout=45)
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
if not out["markers"]["thanks"]:
    out["snippet"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))[:300]

with open("APPEAL_SUBMIT.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2)[:4000])
