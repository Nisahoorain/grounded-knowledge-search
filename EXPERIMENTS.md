# Experiments

Each row is one change, measured on `eval/questions.jsonl` at `k = 5`. Page hit means at least one labeled evidence page is in the top 5.

| Change | Hypothesis | Result | Kept |
|---|---|---|---|
| BM25 only, desk filter in the query | Keyword match on labels and years is enough for statements | Document 25/30, page 16/30, page MRR 0.324 | No. It is the baseline. |
| Semantic search fused with BM25 (reciprocal rank fusion) | Embeddings recover paraphrases BM25 misses, and fusion will lift page hit | Document 29/30, page still 16/30, page MRR 0.441 | Yes. Page hit did not move. Document hit and MRR did. |
| Year lock on a named FY, off when the question is a forecast | The same line item in other years was crowding the top 5 | Same page hit. Right period shows up more often on statement questions | Yes. |
| Separate BM25 lists for text and table chunks, plus a statement-heading list | Statement questions were matching prose that only mentions the statement | No gain on the 16 page hits. Heading pages do enter the pool | Yes. The cost is small and the failure mode was real. |
| Drop table-of-contents chunks before fusion | "Item 1" pages were taking a slot from the statement | Contents pages stopped ranking first. Page hit unchanged | Yes. |
| Copy a single-year statement cell instead of asking the model for the digit | The model answered 0 when the row printed a real amount | Fixes the answer when the row was retrieved. Does not fix retrieval | Yes, in `ask` only. |

An earlier hybrid run on this same 30-question file scored page hit 17/30 (`eval/results1.json`). The current fusion scores 16/30 (`eval/results.json`). That one-question swing is why a change has to move MRR or several questions before it stays.

## Three worst failures

### 1. `fb_00735` — PepsiCo legal proceedings

Question: has PepsiCo reported any materially important ongoing legal battles for FY2022 and FY2021? Gold: `PEPSICO_2022_10K` page 26.

Retrieved first: `PEPSICO_2021_10K` page 547 (a signature page), then page 2, then `PEPSICO_2022_10K` page 205.

Root cause: "materially" and "important" are everywhere in certifications and risk boilerplate. The legal-proceedings page does not repeat those words. Fusion ranks the boilerplate higher.

Next: down-rank pages that are only signatures, exhibits, or certifications, and boost the "legal proceedings" heading the way statement headings are boosted.

### 2. `fb_00494` — Boeing production rates

Question: what production rate changes is Boeing forecasting for FY2023? Gold: `BOEING_2022_10K` page 9. This is the only question whose document is also missing.

Retrieved first: `BOEING_2020_10K` page 58, `BOEING_2019_10K` page 88, `BOEING_2015_10K` page 32.

Root cause: "forecasting" turns the year lock off, which is correct, because the evidence is not in a FY2023 filing. Nothing then prefers the latest filing that discusses the next year's rates. Older outlook pages share the same words and win.

Next: when the question forecasts a year, prefer the filing from the previous period instead of searching every year equally.

### 3. `fb_03029` — 3M capital expenditure

Question: FY2018 capital expenditure for 3M, from the cash flow statement, in millions. Gold: `3M_2018_10K` page 60.

Retrieved first: the right document, pages 83 and 46. Page 60 is not in the top 5.

Root cause: the statement says "Purchases of property, plant and equipment", not "capital expenditure". The query expansion and the statement vote get the cash-flow neighborhood, then a higher-overlap page in the same filing takes the slots.

Next: a small alias list from the question phrase to the printed row label, checked only inside the already retrieved document, not across the corpus.
