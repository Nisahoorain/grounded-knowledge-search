## Experiment: Dense vs BM25 vs Hybrid RRF

**Hypothesis:** Combining semantic dense retrieval with lexical BM25 retrieval using Reciprocal Rank Fusion (RRF) will improve retrieval of exact financial evidence.

**Setup:**
- Corpus: 104,327 chunks
- Dense: Chroma with all-MiniLM-L6-v2
- Sparse: SQLite FTS5 BM25
- Fusion: RRF, constant = 60, candidate_k = 50
- Query: "PepsiCo consolidated net revenue 2022"

**Observed results:**
- Dense ranked the 2022 earnings statement (page 9) first and 2022 10-K statement (page 62) 12th.
- BM25 ranked those documents first and second respectively.
- Hybrid ranked them first and second respectively.

**Outcome:** Positive result for this query. Hybrid improved the position of the 2022 10-K evidence from rank 12 to rank 2.

**Limitation:** This is one query, not sufficient to claim overall retrieval improvement. Evaluate on the full evaluation set before making a general performance claim.

## Experiment: Page-level RRF

**Hypothesis:** Fusing on `(doc_id, page)` instead of `chunk_id` would join evidence that dense and BM25 place on the same page under different chunk ids.

**Setup:** Same dev set, n=20, k=10, candidate_k=50, company/year filter on. `hybrid-chunk` scores each chunk. `hybrid-page` adds the two sources' ranks only once per page, so a dense chunk and a different BM25 chunk on one page combine.

**Observed results:**

| mode | Recall | MRR | Hit | Fail |
|---|---:|---:|---:|---:|
| hybrid-chunk | 0.5500 | 0.3885 | 0.6000 | 8 |
| hybrid-page | 0.4500 | 0.3417 | 0.5000 | 10 |

**Outcome:** Tried, hurt. Page fusion promoted pages where any dense chunk and any BM25 chunk co-occurred, and pushed one-sided correct pages down. BM25 had the gold page at rank 1 on `fb_00735`, `fb_00941`, and `fb_02608`. Chunk fusion kept all three in the top 10. Page fusion missed all three, and its only unique recovery was `fb_10285`. Net: two extra misses (8 to 10). Chunk fusion stays the default. If a result list should not repeat a page, dedupe after the chunk scores exist.

## Experiment: Per-document quota

**Hypothesis:** When the company/year filter leaves a few filings, reserving two chunks from each filing would surface the earnings release that a 10-K otherwise crowds out (`fb_01484`).

**Setup:** `doc_quota` on the chunk-level RRF list, `per_doc=2`, `max_docs=4`, k=10. Dev set n=20. Holdout is the ten `gks_` questions in `questions.jsonl`, which were not used to choose this rule.

**Observed results:**

| set | chunk+dedupe1 hit | doc-quota hit | chunk+dedupe1 MRR | doc-quota MRR |
|---|---:|---:|---:|---:|
| dev n=20 | 0.60 | 0.60 | 0.391 | 0.391 |
| holdout n=10 | 1.00 | 0.90 | 0.507 | 0.478 |

**Outcome:** Tried, not adopted. On the dev set every first-gold rank matched `chunk+dedupe1`. `fb_01484` stayed a miss: the reserved earnings chunks were pages 1 and 9, and the gold page is 2, which is a later earnings chunk. On the holdout the quota removed a hit (`gks_003`, four Johnson & Johnson FY2023 filings). Production stays chunk fusion plus page dedupe.