# Architecture and Technical Decisions

This document records the major engineering decisions behind Grounded Knowledge Search. It explains the chosen approaches, alternatives considered, evaluation evidence, and known trade-offs.

The goal is to make the system's architecture understandable and explain why certain approaches are enabled, optional, or intentionally excluded.

## 1. Default Retrieval: Hybrid Search with RRF

**Decision:** Use dense vector search and BM25 together, combining their rankings with Reciprocal Rank Fusion (RRF).

### Configuration

| Setting | Value |
|---|---:|
| Dense retrieval | Enabled |
| BM25 retrieval | Enabled |
| RRF constant | `60` |
| BM25 weight | `1` |
| Fusion level | Chunk |
| Final result limit | One chunk per page |
| Company/year filtering | Before retrieval |

Both `search` and `ask` use this retrieval method as their starting point.

### Why hybrid retrieval?

Dense search helps retrieve semantically related passages, even when the question uses different wording from the filing.

BM25 helps match exact financial terms, company-specific terminology, statement labels, and numeric-related keywords.

Combining both methods provides a more balanced retrieval approach than relying on either one alone.

RRF combines the ranked results without requiring dense similarity scores and BM25 scores to share the same scale.

### Why one result per page?

Multiple chunks from the same page can occupy the top results and reduce the diversity of the evidence shown to the user.

Keeping one chunk per page gives the final result set better page coverage.

**Trade-off:** This improves result diversity but can hide another useful chunk from the same page. The `ask` command handles some financial questions separately by retrieving additional statement rows.

## 2. Reranking Is Optional

**Decision:** Keep cross-encoder reranking disabled by default. Users can enable it with `--rerank`.

The reranking path first keeps one result per page and then applies a cross-encoder to reorder the candidate results.

### Configuration

| Setting | Value |
|---|---|
| Model | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Maximum input length | 256 tokens |
| Text limit | 1,200 characters |
| Candidate pool | 20 results |

### Evaluation

| Dataset | Method | Hit | MRR | Average reranking time |
|---|---|---:|---:|---:|
| Dev (20) | Default | 0.60 | 0.3910 | — |
| Dev (20) | Reranking blend | 0.70 | 0.3514 | 1,426 ms |
| Holdout (10) | Default | 1.00 | 0.5067 | — |
| Holdout (10) | Reranking blend | 0.90 | 0.7700 | 1,485 ms |

Reranking improved the ranking position of some relevant results, particularly in the holdout MRR score. However, it also lost one correct holdout hit and added approximately 1.4–1.5 seconds of processing time.

An earlier cross-encoder-only approach was slower and performed worse.

**Reason for keeping it optional:**
- The default method retrieved all expected holdout pages in this evaluation.
- Reranking adds latency.
- Its improvement was not consistent across all metrics.
- The default pipeline is simpler and has fewer dependencies on additional inference.

Reranking remains available for experiments and cases where ranking quality is more important than latency.

## 3. Refusal Uses Retrieval and Evidence Checks

The system uses multiple checks to reduce unsupported answers. Retrieval similarity alone is not sufficient to establish that a filing contains the requested information.

### Check 1: Dense retrieval score

Before sending a question to the language model, the system checks the best dense retrieval score.

```python
MIN_DENSE_SCORE = 0.50
```

The weakest best-dense score among the 30 labeled questions was `0.537`.

However, questions about facts that may not appear in a filing can still retrieve similar-looking pages. For example, headcount and stock-price questions can receive scores within the range of valid questions.

**Decision:** Keep the threshold as an initial retrieval-quality check, not as proof that the requested fact exists in the filing.

### Check 2: Answer evidence

The model can return `INSUFFICIENT_EVIDENCE` when the retrieved evidence does not support an answer.

The system also checks numeric claims against their cited evidence.

If a number is not supported, the answer can be retained with the status `answered_unverified` instead of being silently accepted as verified.

A simple numeric comparison can reject a valid rounded answer. For example:

- `$86.4 billion`
- `86,392 million`

These represent approximately the same amount, but their textual numeric representations differ.

**Trade-off:** Numeric verification helps catch unsupported values, but rounding, unit conversions, and formatting differences require careful handling. A failed check should not automatically be interpreted as proof that the entire answer is incorrect.

## 4. Search and Ask Have Different Responsibilities

The normal `search` command and retrieval evaluation use:

1. Dense and BM25 retrieval.
2. Chunk-level RRF.
3. One result per page.

The `ask` command starts with the same retrieval method but can perform additional financial statement row retrieval.

This distinction matters because financial calculations may require multiple rows from the same page. A one-result-per-page search result is not always sufficient for those calculations.

### Evaluation boundary

Normal retrieval evaluation measures the standard search results. It does not measure the additional statement rows retrieved by `ask`.

**Decision:** Keep retrieval evaluation separate from answer-side retrieval and calculation behavior.

The answer-side retrieval improvements did not require rebuilding the indexes.

The existing chunking format remains `table-row-v4`, and the existing Chroma and FTS indexes were retained.

## 5. Calculated Answers Require a Calculation Check

A number copied directly from cited evidence does not require a calculation block.

A calculated number, such as an average, difference, ratio, or unit conversion, must be represented in a closed `calc` block.

The calculation checker verifies that:

- Every input comes from cited evidence or the question.
- Each calculation step is valid.
- The formula produces the stated final answer.
- The final answer follows the required rounding.

An average or a change must be calculated from its original inputs. It cannot be treated as an independent input.

### Supported scale factors

```text
1, 2, 3, 4, 10, 12, 52, 100, 1000, 1000000
```

The number `365` must come from the question or cited evidence rather than being introduced as an unsupported constant.

An incomplete calculation block is not accepted as verified. The answer can be retained but marked `answered_unverified`.

**Decision:** Separate direct evidence extraction from arithmetic verification. Calculated answers must have traceable inputs and a reproducible calculation.

## 6. Questions with Defined Formulas Are Calculated Directly

When a question contains `defined as:`, the system attempts to calculate the requested metric directly instead of relying on the language model for arithmetic.

The process:

1. Identifies the required financial statement line items.
2. Retrieves the values for the requested years.
3. Applies the formula specified in the question.
4. Passes the result through the calculation verifier.

Examples:

- Average between two years = average of the two values.
- Change between two years = later-year value minus earlier-year value.

If a required line item or year is missing, or the calculation does not pass verification, the system can fall back to the configured answer-generation path.

### Example: Corning FY2020 DPO

The DPO question was used to test direct formula calculation.

| Input | Value (USD millions) | Source page |
|---|---:|---:|
| Accounts payable FY2019 | 1,587 | 72 |
| Accounts payable FY2020 | 1,174 | 72 |
| Inventory FY2019 | 2,320 | 72 |
| Inventory FY2020 | 2,438 | 72 |
| Cost of sales FY2020 | 7,772 | 70 |

The calculation was:

```text
365 × 1,380.5 / 7,890 = 63.86 days
```

The result passed verification.

### Avoiding confusion with reported ratios

When a question defines its own formula, the filing's printed ratio is excluded from the calculation context when it matches the metric being recalculated.

For example, the printed Corning DPO values `44` and `48` are not accounts payable values. Treating them as financial statement inputs would produce an invalid calculation.

**Decision:** Use the question's explicit formula and the underlying statement values rather than substituting a separately reported ratio.

## 7. Single Financial Statement Lines Can Be Extracted Directly

When a question asks for one financial statement line for one year, the system can return the value directly without calling the language model.

If the question requests a different unit, the system converts the value and checks the calculation.

For example, when a filing reports amounts in thousands and the question asks for millions, the value is divided by 1,000 and rounded to the nearest million.

### Tested examples

**MGM Resorts FY2018 accounts payable**

- Reported amount: 302,578 thousand USD
- Source page: 57
- Converted answer: 303 million USD

**Boeing FY2018 net property, plant and equipment**

- Reported amount: 12,645 million USD
- Source page: 52
- Answer: 12,645 million USD

The system does not average values from another year when the question asks for only one year.

Questions involving margins, working capital, capital intensity, quarters, or multiple years are not treated as simple single-line lookups.

**Decision:** Handle straightforward statement-line questions deterministically where possible. Use the answer-generation and calculation paths for more complex questions.

## 8. Answer Backend: Gemini Primary, Groq Fallback

**Decision:** Use Gemini as the default answer provider and Groq as the fallback provider.

Local Ollama was tested, but CPU inference was too slow for the assignment's approximate five-second answer budget.

### Options considered

- Ollama only
- Groq only
- Gemini only
- Gemini primary with Groq fallback

### Evaluation evidence

| Backend / experiment | Observed result |
|---|---|
| Ollama on CPU | Median approximately 134 seconds |
| Earlier Gemini Delay-8 dev run | Median approximately 7.3 seconds; no provider errors |
| Gemini 3.5 Flash user test | Median approximately 8.9 seconds across 30 questions |
| Gemini 3.5 Flash user test | 5 provider errors across 30 questions |

The Gemini 3.5 Flash user test showed:

| Dataset | Retrieval hit | Gold cited | Provider errors | Median time |
|---|---:|---:|---:|---:|
| Dev (20) | 70% | 35% | — | 9.3 sec |
| Holdout (10) | 90% | 40% | — | 8.9 sec |

The run encountered HTTP 503 high-demand and HTTP 429 quota errors. Groq rescued several holdout requests.

### Current configuration

```text
ANSWER_PROVIDER=gemini
GEMINI_MODEL=gemini-3.5-flash
```

On HTTP 429, daily quota errors, or temporary provider failures, the system can fall back to Groq using the same prompt and evidence.

Daily-quota errors skip repeated local retries and move to fallback sooner.

Ollama code remains as an experiment artifact, not the active default provider.

### Latency trade-offs

The project was developed and timed on CPU-only hardware.

Moving generation from local Ollama to hosted APIs reduced answer-generation time substantially. However, the end-to-end five-second target is still not consistently met.

Local embedding and search stages may benefit from GPU acceleration. Hosted Gemini and Groq generation remain network- and API-dependent, so a local GPU is not guaranteed to bring every answer below five seconds.

**Decision:** Prefer the hosted API configuration for practical response time, while documenting free-tier quotas, network latency, and remaining performance limitations.

## 9. Access Control Is Enforced During Retrieval

**Decision:** Apply desk-level access restrictions inside the retrieval layer, not only in the language model prompt.

Desk filtering uses chunk metadata:

- Chroma applies the desk filter through `where`.
- BM25 applies the corresponding filter in SQL.
- Role-to-desk mappings come from `ROLE_DESKS` / `DESK_*` environment variables in `src/config.py`.
- The `lead` role can access every desk.

Prompt-only filtering was rejected because the model could still receive and cite chunks from an unauthorized desk.

### Trade-offs

- Non-lead roles search a smaller candidate set, improving isolation and potentially reducing search work.
- Non-lead roles intentionally have less cross-desk recall.
- The lead role searches across all desks and therefore has a larger candidate pool.
- Company and year filters further narrow the relevant `doc_id` values when the query identifies them.

**Decision:** Enforce authorization before evidence reaches the answer-generation stage.

## 10. Runtime Verification and Offline Scoring Are Separate

The system distinguishes between evidence verification during normal use and benchmark scoring during evaluation.

Structured fields such as:

- `answer_type`
- `expected_number`
- `expected_polarity`
- `expected_metric_keywords`

are benchmark annotations. The `ask` command does not read gold answers.

### Metric definitions

| Metric | Meaning |
|---|---|
| `ask_retrieval_hit` | Whether the gold page was among the retrieved chunks |
| `verified` | Whether citations and calculation checks passed against the available evidence |
| `correct` | Offline score against the gold answer, using structured or manual evaluation |

A verified answer is not automatically a semantically correct answer. It means the applicable evidence and calculation checks passed.

When `answer_type` is missing, the evaluator returns `correct=None` for manual review rather than applying a blind Yes/No comparison.

**Decision:** Runtime grounding must work for unseen questions. Gold-answer annotations are used only to measure performance on labeled evaluation sets.

## 11. Citation Repair Before Refusal

Some questions had relevant evidence in the retrieved context but were refused because the model omitted the required `[E#]` citation tags.

Examples from the evaluation issue set included:

- `fb_04980`
- `fb_00603`
- `fb_02608`

### Current repair approach

The citation-repair stack includes:

1. The prompt requires exact `[E#]` tags for factual and numeric claims.
2. Evidence is formatted using explicit `### [E#]` labels.
3. A force-answer retry can be used when the model returns insufficient evidence despite relevant evidence being available.
4. A citation-rewrite retry can be used when a non-empty answer lacks citation tags.
5. The system can attach matching or top evidence IDs to a non-empty answer as a final repair attempt.

Dense-only refusal is also softened when BM25 or financial statement row evidence is available.

### Why this approach was chosen

A missing citation tag does not always mean that the underlying evidence is missing. Repairing citation formatting can recover otherwise usable answers.

### Important limitation

Automatically attaching an evidence ID does not independently prove that every claim in the answer is supported by that evidence.

Therefore, citation presence and evidence support must remain separate concerns. A repaired citation should not be treated as proof of semantic correctness.

**Decision:** Attempt citation repair before refusing a potentially answerable question, while retaining evidence verification as a separate requirement.

## 12. API Evaluation Is Limited to Questions Worth Testing

Free-tier API quotas are limited. Running the language model on questions where the gold evidence is absent from the retrieval context is unlikely to improve the result.

**Decision:** Use zero-API evaluation tools as a gate before spending API quota.

The workflow uses:

- `offline_eval.py` to identify questions that can be answered without an API and questions that need generation.
- `gold_rank.py` to diagnose where the gold evidence appears in retrieval results.
- `eval/ids_worth_api.txt` to select questions worth sending to the answer model.
- `evaluate_answers.py --ids` to run a targeted answer evaluation.
- `data/llm_cache.json` to cache successful `chat_complete_with_meta` responses. The cache is gitignored.

The offline evaluation is useful for separating retrieval problems from answer-generation problems:

- If the gold evidence is already in context, answer generation or citation behavior may be the bottleneck.
- If the gold evidence is absent, changing the language model is unlikely to solve the retrieval problem.

**Important:** `verified=True` must not be reported as benchmark accuracy. Retrieval quality, citation support, and answer correctness are separate measurements.

## 13. Summary of Key Decisions

| Area | Decision | Main reason |
|---|---|---|
| Retrieval | Dense + BM25 with RRF | Combines semantic and exact-term matching |
| Result diversity | One chunk per page | Prevents one page dominating results |
| Reranking | Optional | Quality improvements are inconsistent and add latency |
| Refusal | Retrieval and evidence checks | Similarity score alone is insufficient |
| Financial calculations | Deterministic calculation and verification | Reduces arithmetic errors |
| Single-line financial lookup | Direct extraction where possible | Avoids unnecessary model calls |
| Answer generation | Gemini primary, Groq fallback | Faster than local CPU Ollama |
| Access control | Filter during retrieval | Prevents unauthorized evidence from entering context |
| Evaluation | Runtime verification separate from gold scoring | Avoids confusing evidence support with correctness |
| Citation repair | Repair before hard refusal | Recovers answers with missing citation formatting |
| API usage | Zero-API filtering before answer evaluation | Conserves free-tier quota |

## 14. Reproducing and Reviewing Decisions

The following project areas contain the implementation and evaluation evidence behind these decisions:

| Area | Main files / locations |
|---|---|
| Configuration and role access | `src/config.py` |
| Hybrid retrieval | `src/retrieval/hybrid_search.py` |
| Vector retrieval | `src/retrieval/vector_store.py` |
| Embeddings | `src/retrieval/embeddings.py` |
| Answer generation | Answer provider and LLM modules under `src/` |
| Evaluation | `eval/` |
| Experiment results | `EXPERIMENTS.md` |
| Main usage instructions | `README.md` |

For exact commands, environment setup, and evaluation procedures, refer to `README.md` and `EXPERIMENTS.md`.

---

**Overall engineering principle:** Prefer deterministic retrieval, financial extraction, access control, and calculation checks wherever possible. Use language models for evidence-based interpretation and explanation, not as a substitute for source retrieval, authorization, or arithmetic verification.