import json

p = "data/extracted/3M_2015_10K.json"
d = json.load(open(p, encoding="utf-8"))

for page_num in (50, 52, 54):
    page = next(x for x in d["pages"] if x["page"] == page_num)
    print(f"\n{'='*20} PAGE {page_num} {'='*20}")

    for block in page.get("content", []):
        if block.get("type") == "text":
            print(block.get("text", "")[:1800])
            print("-" * 40)
