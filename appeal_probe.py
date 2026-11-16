import json, re, urllib.request, urllib.error

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
SHORT = "https://forms.gle/hGzM9MEUv2azZsrb9"
OUT = "APPEAL_RESULT.json"

def fetch(url, timeout=45):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.getcode(), r.geturl(), r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        return e.code, url, body, dict(e.headers or {})
    except Exception as e:
        return -1, url, "%s: %s" % (type(e).__name__, str(e)[:200]), {}

res = {}
code, final, body, hdrs = fetch(SHORT)
res["short_status"] = code
res["final_url"] = final
res["body_len"] = len(body)

forms = re.findall(r'action="([^"]*formResponse[^"]*)"', body)
res["form_action"] = forms[0] if forms else None
res["form_id"] = None
m = re.search(r'/forms/d/e/([A-Za-z0-9_-]+)/', final or "")
if m:
    res["form_id"] = m.group(1)
m2 = re.search(r'/forms/d/([A-Za-z0-9_-]+)/', final or "")
if m2 and not res["form_id"]:
    res["form_id"] = m2.group(1)

res["requires_login"] = ("accounts.google.com" in (final or "")) or ("ServiceLogin" in body)
res["entries"] = sorted(set(re.findall(r'entry\.(\d{6,})', body)))
res["is_closed"] = ("This form is no longer accepting responses" in body)
res["title"] = None
t = re.search(r'<title>(.*?)</title>', body, re.S)
if t:
    res["title"] = t.group(1).strip()[:300]

# Try FB_PUBLIC_LOAD_DATA_ blob for structure
blob = None
b = re.search(r'FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\])\s*;?\s*</script>', body, re.S)
if b:
    raw = b.group(1)
    res["load_data_len"] = len(raw)
    try:
        data = json.loads(raw)
        items = data[1][1] if isinstance(data, list) and len(data) > 1 and data[1] and len(data[1]) > 1 else []
        fields = []
        for it in items or []:
            try:
                fields.append({
                    "id": it[0],
                    "title": it[1],
                    "type": it[3],
                    "required": bool(it[4]) if len(it) > 4 else None,
                    "choices": [c[0] for c in (it[6] or []) if c] if len(it) > 6 and it[6] else [],
                })
            except Exception:
                pass
        res["fields"] = fields
        res["form_name"] = data[1][8] if len(data[1]) > 8 else None
    except Exception as e:
        res["load_data_err"] = str(e)[:200]

if not res["entries"]:
    res["snippet"] = re.sub(r"\s+", " ", body[:600])

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False, indent=2)[:4000])
