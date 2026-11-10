import json
from pathlib import Path

root = Path(r"C:\Users\QueOne\OneDrive\Desktop\grounded-knowledge-search\eval")

def load(path):
    text = path.read_text(encoding="utf-8")
    return json.JSONDecoder().raw_decode(text)[0]

old = load(root / "results_v4.json")
new = load(root / "results.json")

old_by = {r["id"]: r for r in old["results"]}
new_by = {r["id"]: r for r in new["results"]}
shared = [i for i in old_by if i in new_by]
only_new = [i for i in new_by if i not in old_by]

def stats(rows):
    n = len(rows)
    dh = sum(1 for r in rows if r["document_hit"])
    ph = sum(1 for r in rows if r["page_hit"])
    dm = sum(r["document_mrr"] for r in rows) / n
    pm = sum(r["page_mrr"] for r in rows) / n
    return n, dh, dh / n, ph, ph / n, dm, pm

print("SHARED", len(shared))
on, odh, odr, oph, opr, odm, opm = stats([old_by[i] for i in shared])
nn, ndh, ndr, nph, npr, ndm, npm = stats([new_by[i] for i in shared])
print(f"v4  doc {odh}/{on} ({odr:.3f}) page {oph}/{on} ({opr:.3f}) doc_mrr {odm:.4f} page_mrr {opm:.4f}")
print(f"now doc {ndh}/{nn} ({ndr:.3f}) page {nph}/{nn} ({npr:.3f}) doc_mrr {ndm:.4f} page_mrr {npm:.4f}")
print("ONLY NEW", len(only_new))
if only_new:
    n, dh, dr, ph, pr, dm, pm = stats([new_by[i] for i in only_new])
    print(f"now doc {dh}/{n} ({dr:.3f}) page {ph}/{n} ({pr:.3f}) doc_mrr {dm:.4f} page_mrr {pm:.4f}")
    for i in only_new:
        r = new_by[i]
        print(f"  {i} doc={r['document_hit']} page={r['page_hit']} page_rank={r['page_rank']}")

print("PAGE CHANGES")
for i in shared:
    o, n = old_by[i], new_by[i]
    if o["page_hit"] != n["page_hit"] or o["page_rank"] != n["page_rank"]:
        print(f"  {i} hit {o['page_hit']}->{n['page_hit']} rank {o['page_rank']}->{n['page_rank']}")
print("DOC CHANGES")
for i in shared:
    o, n = old_by[i], new_by[i]
    if o["document_hit"] != n["document_hit"] or o["document_rank"] != n["document_rank"]:
        print(f"  {i} hit {o['document_hit']}->{n['document_hit']} rank {o['document_rank']}->{n['document_rank']}")
