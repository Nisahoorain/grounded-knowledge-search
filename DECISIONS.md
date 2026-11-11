# Project Decisions

This file explains the main technical decisions made during development and why they were chosen.

## 1. Default Retrieval Method

The default retrieval method combines Dense Search and BM25 using Reciprocal Rank Fusion (RRF).

- RRF constant: `60`
- BM25 weight: `1`
- Candidate results are combined at chunk level.
- Only one chunk per page is kept in the final search results.
- Company and year filters are applied before retrieval.

This method is used by both `search` and `ask` as their starting retrieval path.

The `ask` command performs an additional step to find financial statement rows needed for the question.

## 2. Reranking Is Optional

Reranking is disabled by default. It runs only when the user passes `--rerank`.

It first keeps one result per page, then uses a cross-encoder to improve the ranking.

Model: `cross-encoder/ms-marco-MiniLM-L-6-v2`

Settings:

- Maximum input length: 256 tokens
- Text limit: 1,200 characters
- Candidate pool: 20 results

### Evaluation results

| Dataset | Method | Hit | MRR | Average time |
|---|---|---:|---:|---:|
| Dev (20) | Default | 0.60 | 0.391 | — |
| Dev (20) | Reranking blend | 0.70 | 0.3514 | 1,426 ms |
| Holdout (10) | Default | 1.00 | 0.5067 | — |
| Holdout (10) | Reranking blend | 0.90 | 0.770 | 1,485 ms |

The latest evaluation showed that reranking improved the order of some results, but it also lost one correct holdout hit. Its average prediction time was also above one second.

An earlier cross-encoder-only approach was slower and performed worse.

**Decision:** Reranking remains optional. The default retrieval method is kept because it is simpler and found all expected holdout pages in the latest evaluation.

## 3. Refusal Uses Two Checks

The system has two checks to reduce unsupported answers.

### First check: Retrieval score

Before sending a question to the model, the system checks the best dense retrieval score.

The minimum threshold is:

`MIN_DENSE_SCORE = 0.50`

The weakest best-dense score among the 30 labeled questions was 0.537.

However, questions about facts missing from a company's filing can still retrieve similar-looking pages. For example, headcount and stock-price questions can receive scores within the range of valid questions.

**Decision:** Keep the score threshold as an initial check, not as a guarantee that the filing contains the answer.

### Second check: Answer evidence

The model can return `INSUFFICIENT_EVIDENCE` when the available evidence is not enough.

The system also checks numbers against their cited evidence.

If a number is not supported, the answer is marked `answered_unverified` instead of being silently accepted.

A correct rounded answer can sometimes fail a simple number check. For example, `$86.4 billion` and `86,392 million` represent approximately the same amount.

The system therefore keeps the answer but marks the unsupported claim.

## 4. Search and Ask Have Different Responsibilities

The normal `search` command and retrieval evaluation use:

1. Dense and BM25 search.
2. Chunk-level RRF.
3. One result per page.

The `ask` command starts with the same retrieval method but can add extra financial statement rows.

This is important because a financial calculation may need multiple rows from the same page.

The retrieval evaluation measures the normal search results. It does not measure the additional rows retrieved by `ask`.

**Decision:** Keep the existing retrieval evaluation separate from answer-side retrieval.

The answer-side changes did not require rebuilding the indexes.

The chunking format remains `table-row-v4`, and the existing Chroma and FTS indexes were kept unchanged.

## 5. Calculated Numbers Must Pass a Calculation Check

A number copied directly from cited evidence does not need a calculation.

A calculated number, such as an average, difference, ratio, or unit conversion, must be included in a closed ` ```calc ` block.

The calculation checker verifies that:

- Every input comes from cited evidence or the question.
- Each calculation step is correct.
- The formula produces the stated final answer.
- The final answer follows the required rounding.

An average or a change must be calculated from its original inputs. It cannot be treated as an independent input.

The calculation block can use these scale factors:

`1, 2, 3, 4, 10, 12, 52, 100, 1000, 1000000`

The number `365` must come from the question or cited evidence.

An incomplete calculation block is not accepted as verified. The answer is kept but marked `answered_unverified`.

## 6. Questions That Define a Formula Are Calculated Directly

When a question contains `defined as:`, the system attempts to calculate the formula directly instead of asking the language model to do the arithmetic.

It finds the named financial statement rows and reads the required year values.

For example:

- Average between two years = average of the two values.
- Change between two years = later year value minus earlier year value.

The calculation is then checked by the calculation verifier.

If a required line or year is missing, or the calculation does not pass verification, the system can fall back to Ollama.

### Corning FY2020 DPO test

The DPO question was used to test this feature.

| Input | Value (USD millions) | Source page |
|---|---:|---:|
| Accounts payable FY2019 | 1,587 | 72 |
| Accounts payable FY2020 | 1,174 | 72 |
| Inventory FY2019 | 2,320 | 72 |
| Inventory FY2020 | 2,438 | 72 |
| Cost of sales FY2020 | 7,772 | 70 |

The calculation produced:

`365 × 1,380.5 / 7,890 = 63.86 days`

The result was verified successfully.

When a question defines its own formula, the filing's printed ratio is removed from the calculation context when it matches the metric being replaced. For example, the printed Corning DPO values `44` and `48` are not accounts payable values.

## 7. A Single Reported Financial Line Is Read Directly

When a question asks for one financial statement line for one year, the system can return the value directly without calling the language model.

If the question asks for a different unit, the system converts the value and checks the calculation.

For example, when a filing reports amounts in thousands and the question asks for millions, the value is divided by 1,000 and rounded to the nearest million.

### Tested examples

**MGM Resorts FY2018 accounts payable**

- Reported amount: 302,578 thousand USD
- Source: page 57
- Converted answer: 303 million USD

**Boeing FY2018 net property, plant and equipment**

- Reported amount: 12,645 million USD
- Source: page 52
- Answer: 12,645 million USD

The system does not average another year's value when the question asks for only one year.

Questions involving margins, working capital, capital intensity, quarters, or multiple years are not treated as simple single-line lookups.

## 8. Answer Backend: Gemini Primary, Groq Fallback

Local Ollama on CPU was too slow for the 5-second budget (often 100s+ per answer on this laptop). Free hosted APIs are allowed by the brief.

**Options considered:** Ollama only; Groq only; Gemini only; Gemini primary with Groq fallback.

**Evidence:** Ollama-era answer eval median ~134s. Earlier Gemini Delay-8 dev median ~7.3s with 0 provider errors. After switching to `GEMINI_MODEL=gemini-3.5-flash`, a Delay-8 user-test run (`answer_results_gemini35_usertest.jsonl`) kept retrieval/citation in the same band (dev hit 70% / cited 35%; holdout hit 90% / cited 40%) but saw mid-run **503** high-demand and **429** quota errors (dev median 9.3s; 5/30 provider errors overall). Groq still rescued several holdout asks. Free-tier RPD/RPM limits remain binding.

**Decision:** `ANSWER_PROVIDER=gemini` by default with `GEMINI_MODEL=gemini-3.5-flash`. On HTTP 429 / daily quota / temporary failure, fall back to Groq with the same prompt and evidence. Ollama code remains for the latency experiment write-up but is not an active provider. Daily-quota errors skip local retries and fall over immediately.

**What would change this:** a stable free tier with higher RPD, or a GPU for local embedding/rerank (and local LLM if used again).

**Hardware framing:** the project was built and timed on CPU-only hardware. The 5s budget improved by moving generation off local Ollama to free hosted APIs. Residual over-5s latency is largely local embedding/search on CPU — those stages would improve on GPU. Hosted Gemini/Groq remain network/API-bound, so a local GPU is not claimed as an automatic fix for every ask under 5s.

## 9. Access Control Is Enforced in Retrieval

Desk filtering uses chunk metadata `desk` in both Chroma (`where`) and BM25 SQL. Role → desk lists come from `ROLE_DESKS` / `DESK_*` env vars in `src/config.py`. The lead role lists every desk. Prompt-only filtering was rejected because citations could still leak other desks' chunks.

**Cost:** non-lead roles search a smaller candidate set (faster, less recall across desks by design). Lead searches all desks (larger candidate pool). Company/year filters further narrow `doc_id`s when the query names them.

## 10. Runtime Verification vs Offline Scoring Stay Separate

Structured fields (`answer_type`, `expected_number`, `expected_polarity`, `expected_metric_keywords`) are benchmark annotations only. `ask` never reads gold answers.

| Metric | Meaning |
|---|---|
| `ask_retrieval_hit` | Gold page was among retrieved chunks |
| `verified` | Citations/calc checks against cited evidence |
| `correct` | Offline score vs gold (structured or manual) |

**Decision:** improve runtime grounding (citations, calc blocks, evidence packing) for unseen questions; use structured scoring only on labeled eval sets. Missing `answer_type` → `correct=None` (manual review), not a blind Yes/No match.

## 11. Citation Repair Before Hard Refuse

Several hit questions were refused only because the model omitted `[E#]` even when it had copied a filing number (`fb_04980`, `fb_00603`, `fb_02608`).

**Decision:** pack statement/table evidence first, require `[E#]` in the prompt, and run one cite-retry rewrite before returning “no verifiable citations.” Soften dense-only refusal when BM25 or statement-row evidence is present.