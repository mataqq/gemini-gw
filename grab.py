import json, re, urllib.request, urllib.error, os, base64

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
FORM = "https://docs.google.com/forms/d/e/1FAIpQLScOJWibQ_-hYuVv63kJTgEqlgAaOwRLGFTbXm-QY1gz8U0CGA/viewform"

req = urllib.request.Request(FORM, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
body = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")

out = {}
out["all_entries"] = sorted(set(re.findall(r"entry\.(\d{5,})", body)))

b = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\])\s*;?\s*</script>", body, re.S)
raw = b.group(1)
out["raw_len"] = len(raw)
data = json.loads(raw)

def walk(node, depth=0, path="root"):
    lines = []
    if isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, (list, dict)):
                lines += walk(v, depth + 1, "%s[%d]" % (path, i))
            else:
                s = str(v)
                if len(s) > 200:
                    s = s[:200] + "..."
                lines.append("%s[%d] = %r" % (path, i, s))
    elif isinstance(node, dict):
        for k, v in node.items():
            lines.append("%s{%s} = %r" % (path, k, str(v)[:200]))
    return lines

# structured item dump
items = data[1][1]
out["item_count"] = len(items) if items else 0
out["items"] = []
for it in items or []:
    out["items"].append(it)

out["dump"] = walk(data)[:200]
out["html_inputs"] = re.findall(r'<input[^>]*name="(entry\.\d+)"[^>]*>', body)[:40]
out["choice_labels"] = re.findall(r'data-value="([^"]{1,120})"', body)[:40]

with open("APPEAL_DUMP.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out["items"], ensure_ascii=False)[:3000])
print("ENTRIES", out["all_entries"])
print("INPUTS", out["html_inputs"])
print("CHOICES", out["choice_labels"][:10])
