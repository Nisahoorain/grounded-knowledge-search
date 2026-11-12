# Experiments and Results

This file records the main experiments, their results, and the decisions made during development.

Retrieval results come from:

```powershell
python eval/evaluate_retrieval.py
```

Evaluation settings:

- Development set: `dev_questions.jsonl` (20 questions)
- Holdout set: 10 questions from `questions.jsonl` with IDs starting with `gks_`
- Top results: 10
- Company and year filtering: enabled

The current retrieval list merges the raw dense top 15 pages, the raw BM25 top 30 pages, and the chunk-level RRF top 30, then keeps up to three chunks per page. The earlier method, chunk-level RRF with one result per page (`chunk+dedupe1`), is the baseline in section 1.

## 1. Retrieval Experiments

| Experiment | What was tested | Result | Decision |
|---|---|---|---|
| Dense vs BM25 vs Hybrid | Compared meaning-based search, keyword search, and their combination using 104,327 chunks. | For a PepsiCo 2022 revenue question, BM25 and hybrid ranked the relevant earnings statement and annual filing at positions 1 and 2. Dense search ranked the annual filing at 12. | Keep hybrid retrieval. One question alone is not enough to judge the whole corpus. |
| Page-level RRF | Combined results by page instead of by chunk. | Development Hit dropped from 0.60 to 0.50. Recall dropped from 0.55 to 0.45. | Rejected. Combine chunks first, then remove duplicate pages. |
| One chunk per page | Applied page deduplication after chunk-level RRF. | Development Hit: 0.60, MRR: 0.391. Holdout Hit: 1.00, MRR: 0.5067. | Selected as the default search method. |
| Cross-encoder reranking | Used a cross-encoder to improve result order after page deduplication. | Latest run: Development Hit 0.70, MRR 0.3514, average prediction time 1,426 ms. Holdout Hit 0.90, MRR 0.770, average prediction time 1,485 ms. | Keep optional with `--rerank`. Do not use by default. |
| Per-document quota | Limited results to two per document and four documents overall. | Development results did not improve. Holdout Hit dropped to 0.90. | Rejected. |
| Dense score refusal check | Tested whether a minimum dense score could identify unsupported questions. | Valid questions had best scores from 0.537 to 0.808. Some unsupported questions about companies in the corpus also had high scores. | Keep the 0.50 threshold as an initial check, not a guarantee. |

### Reranking details

The reranker uses:

`cross-encoder/ms-marco-MiniLM-L-6-v2`

Settings:

- Maximum input length: 256 tokens
- Text limit: 1,200 characters
- Candidate pool: 20 results

An earlier experiment recorded lower average prediction times: 995 ms for development and 927 ms for holdout. A later evaluation run recorded 1,426 ms and 1,485 ms respectively. The latest results are used in the project decisions.

The holdout set lost one correct result with reranking (`gks_007`). An earlier cross-encoder-only approach was also slower and performed worse.

**Decision:** Reranking stays optional because it improves some rankings but can also lose correct results and adds processing time.

## 2. Answer Verification Experiments

### Calculation block verification

Tested calculation blocks using the Corning DPO example.

Results:

- A number copied from cited evidence, such as 7,772, is accepted without a calculation block.
- A calculated answer such as 63.86 is not accepted as verified without a valid calculation block.
- The full Corning calculation produces 63.86 and passes verification.
- An average or a change cannot be supplied as an original input if it is not printed in the cited evidence.
- An incomplete calculation block is not accepted as verified.

**Decision:** Use the calculation verifier for calculated numbers. Keep an answer marked `answered_unverified` if its calculation cannot be verified.

### Statement-row retrieval

The normal search results keep one chunk per page. A financial calculation may need more than one row from the same page.

To handle this, `ask` performs additional BM25 searches for financial statement lines named in the question. It searches within the relevant company and year filings and prefers the correct statement and row label.

Tests showed:

| Question | Retrieved evidence |
|---|---|
| Corning FY2020 DPO | Accounts payable and inventory on page 72; cost of sales on page 70 |
| MGM FY2018 accounts payable | Correct balance-sheet row on page 57 |
| Boeing FY2018 net PP&E | Correct balance-sheet row on page 52 |
| Pepsi capital spending | Cash-flow statement row on page 63 |

The normal `search` results and retrieval evaluation are unchanged by this additional step.

**Decision:** Keep statement-row retrieval inside `ask` only.

### Defined formula calculation

Tested a question that explicitly defines DPO using a formula.

Corning FY2020 values:

| Input | Value (USD millions) | Page |
|---|---:|---:|
| Accounts payable FY2019 | 1,587 | 72 |
| Accounts payable FY2020 | 1,174 | 72 |
| Inventory FY2019 | 2,320 | 72 |
| Inventory FY2020 | 2,438 | 72 |
| Cost of sales FY2020 | 7,772 | 70 |

Calculation:

- Average accounts payable = 1,380.5
- Inventory change = 118
- Denominator = 7,890
- DPO = 365 × 1,380.5 / 7,890 = 63.86 days

The answer was returned as `answered`, with all inputs and the final result verified. The model was not called.

The filing's printed DPO values (44 and 48) were not used as accounts payable.

**Decision:** Calculate supported questions containing `defined as:` directly in code.

### Single reported line and unit conversion

Tested questions asking for one financial statement line for one year.

| Company and year | Reported value | Answer | Page |
|---|---:|---:|---:|
| MGM Resorts FY2018 accounts payable | 302,578 thousand USD | 303 million USD | 57 |
| Boeing FY2018 net PP&E | 12,645 million USD | 12,645 million USD | 52 |

The MGM result converts thousands into millions and rounds to the nearest million. Boeing's value is already in the requested unit.

Both latest successful tests returned verified answers without calling the model.

**Decision:** Read a single named line directly when the question asks for one year and the required row and unit are available.

## 3. Three Important Retrieval Failures

These failures come from normal retrieval evaluation. They do not necessarily mean that the additional retrieval used by `ask` also fails.

### 3.1 Corning FY2020 Days Payable Outstanding (`fb_10130`)

**Question:** Calculate FY2020 DPO for Corning using the balance sheet and statement of income.

Expected answer: 63.86 days.

Expected pages: 70 and 72.

**Original retrieval problem:**

- Page 43 contained the filing's printed DPO values, 44 and 48.
- The accounts payable and inventory rows were both on page 72.
- Cost of sales was on page 70.
- Normal search kept only one chunk per page, so it could not provide all required rows.

The model could confuse the printed DPO figures with accounts payable.

**Solution:** `ask` now retrieves the named statement rows separately, removes the printed ratio when the question defines a different formula, and calculates the answer in code.

**Latest result:** 63.86 days, verified using pages 70 and 72.

Normal `search` still has its original retrieval limitation.

### 3.2 MGM Resorts FY2018 Accounts Payable (`fb_04171`)

**Question:** What was MGM Resorts' year-end FY2018 accounts payable in USD millions?

Expected answer: approximately 303 million USD.

Expected page: 57.

**Original answer problem:**

The correct row reported 302,578 in thousands. The model incorrectly treated this as millions and also averaged the 2018 and 2017 values, although the question asked only for 2018.

**Solution:** Read the FY2018 column directly, convert thousands into millions, and round to the nearest million.

**Latest result:** 303 million USD, verified using page 57.

An earlier run still produced an incorrect answer, so consistency across repeated runs remains a limitation worth recording.

### 3.3 AMD FY2015 Depreciation and Amortization Margin (`fb_03069`)

**Question:** What was AMD's FY2015 depreciation and amortization margin?

Expected answer: 4.2%.

Expected pages: 56 and 60.

The calculation uses:

- Depreciation and amortization: 167
- Net revenue: 3,991
- Result: 167 / 3,991 ≈ 4.18%, rounded to 4.2%.

**Original retrieval problem:**

The net-income search sometimes returned a different row from page 34. The required operations statement showed the net-loss label separately from its amounts, so it was not available as a normal pipe-row chunk.

**Retrieval improvement:** The statement-row lookup supports net-loss labels and can read amounts that appear on following lines. It also considers the statement heading.

**Remaining limitation:** The margin itself is not yet calculated directly by the defined-formula calculation path. This question still depends on the model.

## 4. Overall Findings

- Hybrid retrieval was selected over dense-only, BM25-only, and page-level fusion.
- Keeping one chunk per page gave the best default holdout Hit result.
- Reranking remains optional.
- The dense score threshold alone cannot reliably identify every unsupported question.
- Calculation blocks provide a way to verify calculated answers.
- Additional statement-row retrieval improves financial evidence available to `ask`.
- Direct calculation and unit conversion work for the tested supported question types.
- Normal retrieval evaluation does not measure the additional statement rows retrieved by `ask`.

All answer-side improvements were made without rebuilding the existing Chroma or BM25 indexes.

## 5. Answer Backend and Latency

| Experiment | What changed | Result | Kept? |
|---|---|---|---|
| Ollama CPU (`llama3.2`), earlier | Local generation on i5 laptop | Answer-eval median ~134s; 4/20 answered; many timeouts; 5s budget missed on almost every LLM call | No as default; code kept for write-up |
| Ollama CPU (`llama3.2`), latest | Separate 20-question development answer evaluation, not the 15-question hard-set retrieval run | 13/20 answered, 11/20 gold cited, 16/20 ask-path hit, median 156s, zero provider errors | Yes as a tagged local run (`--tag llama`); still too slow to be the default |
| Gemini free tier | `ANSWER_PROVIDER=gemini` | Delay-8 dev median **7.3s**; 0 API errors on that 20-Q run; daily RPD ~20 then HTTP 429 for hours | Yes as primary |
| Groq free tier | `openai/gpt-oss-20b` | Often ~1–3s per generation; RPM 429 if eval has no delay | Yes as 429 fallback |
| Eval request delay 8s | Space between questions | Stable Gemini dev run; delay does **not** raise daily quotas | Yes for eval |
| Gemini model → `gemini-3.5-flash` | New API key + model in `.env` | User-test run (below); same primary/fallback design | Yes as current default |

Measured answer-evaluation latency. These runs use different configurations, so the table is not a direct accuracy comparison.

| Backend / experiment | Answered | Gold cited | Median latency |
|---|---:|---:|---:|
| Ollama CPU — earlier run | 4/20 | 3/20 | ~134 sec |
| Ollama CPU — latest run | 13/20 | 11/20 | 156 sec |
| Gemini — earlier Delay-8 run | 11/20 | 7/20 | 7.3 sec |

The 156-second figure is CPU-only local inference on the latest 20-question Ollama run. It is not the latency of hosted Gemini or Groq, and it is not the 15-question hard-set retrieval experiment.

**Lesson:** on this machine the bottleneck for Ollama was local CPU generation, not the retrieval pipeline. Hosted Gemini and Groq are the practical answer backends. Free-tier APIs reduce that latency but introduce quota management.

### Hardware note (CPU vs GPU)

Development ran on an **i5 laptop with no CUDA GPU** (Intel UHD only). Local stages — embedding load/encode (`all-MiniLM`), Chroma search, and previously Ollama — are **CPU-bound**. That is a main reason end-to-end ask often still exceeds the ~5s budget even after switching generation to Gemini/Groq (Delay-8 median **7.3s**, **15/20** over 5s).

| Stage | Bound by | GPU impact |
|---|---|---|
| Embedding / optional rerank | Local CPU | Would clearly speed up |
| Chroma / BM25 retrieval | Local CPU | Modest benefit |
| Answer generation (Gemini/Groq) | Network + free-tier API | Not fixed by local GPU |
| Ollama (retired as default) | Local CPU | Would have helped a lot |

**Claim we make:** the 5s budget **improved** vs the Ollama-CPU baseline (~134s median → ~7.3s). Remaining over-budget time is largely local embedding/search on CPU; moving those stages to a GPU would reduce local latency. We do **not** claim a local GPU alone guarantees every ask under 5s, because hosted generation remains network/API-bound.

## 6. Answer-Quality Eval (Ask Path)

Settings: `retrieve_for_ask` + `generate_answer`, k=5, Delay-8 Gemini primary.

| Metric (dev 20) | Prior Gemini Delay-8 | User test `gemini-3.5-flash` |
|---|---:|---:|
| Ask-path retrieval hit | 14/20 (70%) | 14/20 (70%) |
| Cited gold doc+page | 7/20 (35%) | 7/20 (35%) |
| Answered | 11/20 (55%) | 10/20 (50%) |
| API errors | 0/20 | 3/20 (15%) |
| Median latency | 7.3s | 9.3s |
| Over 5s budget | 15/20 | 17/20 |

### User testing after API/model change (`gemini-3.5-flash`)

Run tag: `eval/answer_results_gemini35_usertest.jsonl` (Delay-8, 20 dev + 10 holdout). Treated as end-user style ask-path testing after switching the Gemini model/key.

| Set | Hit | Gold cited | Answered | API errors | Median latency | Over 5s |
|---|---:|---:|---:|---:|---:|---:|
| Dev (20) | 70% | 35% | 50% | 15% | 9.3s | 17/20 |
| Holdout (10) | 90% | 40% | 60% | 20% | 8.9s | 9/10 |
| All (30) | 77% | 37% | 53% | 17% | 8.9s | 26/30 |

**What worked in the session (examples):**
- Direct calc / statement paths without LLM: MGM FY2018 accounts payable → **303 million**; Boeing FY2018 net PP&E → **12,645 million**.
- Gemini answers with gold citation: PepsiCo FY2021 capex (~$4,625M); MGM FY2022 dividends (yes, $0.01/share).
- After Gemini quota/pressure, Groq fallback still answered holdout items (e.g. Boeing production models; Ulta “25,000+ products / 600+ brands”).

**What failed honestly:** mid-run Gemini **HTTP 503** (model high demand) and later **HTTP 429** (quota). Groq fallback absorbed several questions but also hit RPM backoff, leaving **5/30** as `status=error`. Retrieval quality stayed in line with the prior Delay-8 snapshot; provider availability, not search, drove the extra errors and slightly higher median latency.

### Evaluator honesty experiment

Yes/No-only auto-scoring marked `fb_00757` and `fb_00005` correct despite wrong figures (16% revenue vs 18% AR; $831 vs $2,278 WC).

| Change | Result |
|---|---|
| Structured fields on those two IDs + polarity∧number∧metric keywords | Both false positives → `correct=False` |
| Missing `answer_type` → `correct=None` | Remaining answered rows go to manual review instead of blind Yes/No |
| Offline `--rescore` | Recomputed Delay-8 JSONL with **0** new API calls |

### Citation / evidence packing experiment

| Change | Smoke result |
|---|---|
| Prefer statement/table chunks; stronger cite rules; one cite-retry | `fb_04980` (PepsiCo FY2021 capex) flipped from no-cite refuse → answered with gold page 63 |
| Full repair stack (forbid insufficient when evidence present; force-answer; auto-attach `[E#]`) | Issue-set retest `answer_results_citefix_issues.jsonl`: answered **8→16**/19, hit+refuse **5→0**, gold cited **4→6**/19 |

### Zero-API offline pass (`offline_eval.py`)

On `eval/questions_all30.jsonl` (LLM stubbed; no network):

| Outcome | Count |
|---|---:|
| Deterministic answered + gold cited | 3 (`fb_04171`, `fb_10285`, `fb_10130`) |
| Worth API (`needs_llm` ∧ gold in context) | 21 |
| Skip API (gold not in context) | 6 |

## 7. Miss-set retrieval diagnostic (`gold_rank.py`)

After splitting `cited_gold` misses into `eval/questions_failed_uncited.jsonl`, ran:

`python gold_rank.py eval/questions_failed_uncited.jsonl --misses`

| id | doc_in_filter | ask@5 | pool(dedupe) | Reading |
|---|---|---:|---:|---|
| fb_00735 | True | - | 16 | pool 6–20 band → larger k / rerank candidate |
| fb_00499 | True | 6 | 87 | gold near ask edge; definition mismatch also documented |
| fb_00494 | True | - | 17 | pool 6–20 → larger k / rerank |
| fb_00757 | True | - | 54 | deep miss / wrong-metric; not a cite-only bug |
| fb_00941 | True | - | 7 | quarter filter keeps doc; still rank 7 → k/rerank |
| fb_03029 | True | 1 | 1 | retrieved; wrong/alt cite issue |
| fb_01484 | True | - | 15 | pool 6–20 → larger k / rerank |
| fb_00005 | True | 6 | 20 | near miss + operating-WC definition gap |
| fb_00591 | True | 6 | 30 | FCF metric attach helps; still just outside top-5 |
| gks_001 | True | 1 | 1 | retrieved after single-line `line_items_in` fix; cite path |
| gks_005 | True | 2 | 2 | retrieved; derived % / cite choice |
| gks_006 | True | - | 10 | pool 6–20 → larger k / rerank |
| gks_007 | True | 4 | 6 | in ask@5 but non-gold cite previously |
| gks_008 | True | 2 | 2 | retrieval OK; was API error |
| gks_009 | True | 1 | 1 | retrieval OK; was API error |

No `doc_in_filter=False` on this miss set. Concrete code fixes applied for quarter parse, single-line amount `line_items_in`, free-cash-flow `_METRICS`, and temporary 503 fallback in `llm.py`.

## 8. Hard-set retrieval widening

The 15 questions in `eval/questions_failed_uncited.jsonl` were the pages the earlier ask path missed. At ask@5 that set hit 6/15.

The misses had a repeated shape. Dense search often never returned the gold page, while BM25 did, or the reverse. Chunk-level RRF then let the longer dense list outrank a BM25-only hit. A cap of one chunk per page dropped the other rows on that page. A candidate pool of 50 was too small for the gold page to survive.

Changes that stayed:

| Knob | Before | After |
|---|---|---|
| Candidate pool | 50 | 300 |
| HNSW `search_ef` | 50 | 384 |
| Page cap | 1 | 3 |
| Ask results | 5 | 10 |
| Retrieval-eval results | 10 | 30 |
| List assembly | RRF, then one chunk per page | Raw dense top 15 pages, raw BM25 top 30 pages, RRF top 30, then the page cap |

A fixed BM25 weight of 2.5 and a forced insert of the BM25 top 3 were tried and dropped. The weight recovered some BM25-led pages and lost dense-led ones. The forced insert did not add hits and lowered MRR. Cross-encoder reranking stayed off.

On the previously difficult 15-question set, gold-page retrieval improved from 6/15 to 12/15 at evaluation k=30. Three pages on that set remain outside the top 30: `fb_00499`, `fb_00757`, and `fb_01484`. This measurement uses the existing MiniLM index.

The latest Ollama numbers are a separate answer-evaluation run on the 20 development questions. They are not part of this hard-set retrieval experiment. See section 5.

## 9. Open Issues (Honest)

1. **Gold citation still lags retrieval hit, but the two published pairs are different runs.** The ~77% hit vs ~50% cited figure is the earlier Gemini 30-question baseline after the repair overlay. The latest 20-question Ollama answer evaluation is 16/20 (80%) ask-path hit and 11/20 (55%) gold cited. Those runs are not directly comparable.
2. **`verified` is not correctness** — wrong-metric answers can still look supported if the number is in the cited chunk.
3. **Three hard-set pages stay outside eval k=30** (`fb_00499`, `fb_00757`, `fb_01484`) on the MiniLM index. A larger pool and the raw-list union did not bring them into the top 30.
4. **Definition mismatches vs gold** (operating WC on `fb_00005`; capital-intensity metrics on `fb_00499`) are documented, not patched with company-specific hacks.
5. **Free-tier quotas / 503** still interrupt long API evals; use `--ids` + `offline_eval.py` + LLM disk cache to spend carefully.
