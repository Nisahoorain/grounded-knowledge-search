import json

p = "data/extracted/BOEING_2018_10K.json"
d = json.load(open(p, encoding="utf-8"))

page = next(x for x in d["pages"] if x["page"] == 52)

for block in page.get("content", []):
    if block.get("type") == "text":
        print(repr(block.get("text", "")[:1500]))
