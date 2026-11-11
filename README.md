# Filing Question Answering

This project answers questions using information from company financial filings.

- `search` finds relevant pages and table rows.
- `ask` provides an answer with citations.
- The system checks whether answer numbers are supported by the cited evidence.
- It can also calculate and verify certain financial formulas directly.

## 1. How to Run

### Requirements

- Python and the packages listed in `requirements.txt`
- A free answer backend for questions that need an LLM (default: **Gemini**, Groq fallback)
- The existing dense and BM25 indexes

### Install dependencies

From the project folder (`D:\ass`), run:

```powershell
pip install -r requirements.txt
copy .env.example .env
```

Set `GROQ_API_KEY` and/or `GEMINI_API_KEY` in `.env`. Default architecture: **Gemini primary**, **Groq fallback** on HTTP 429 (same evidence/prompt). `LLM_REQUEST_DELAY_S` spaces eval calls; delay does not raise daily quotas.

```powershell
ANSWER_PROVIDER=gemini
# or
ANSWER_PROVIDER=groq
```

Ollama is not an active option. Its code path stays so the local-CPU latency experiment can be described in `EXPERIMENTS.md`.

### Available roles

The system supports four roles:

- `consumer`
- `health_industrials`
- `tech_telecom`
- `lead`

The first three roles can access their assigned companies. The `lead` role can access all companies.

### Search and ask commands

```powershell
python main.py search --role tech_telecom "question text"

python main.py ask --role tech_telecom "question text"
```

The `search` command displays retrieved results.

The `ask` command returns a JSON answer with citations and verification information.

You can optionally use `--rerank` with either command. Reranking is disabled by default.

### Rebuild the indexes

The project already contains its dense and BM25 indexes:

- Dense index: `data/chroma_db`
- BM25 index: `data/fts_index.sqlite3`

To rebuild them from the PDFs, run:

```powershell
python main.py ingest pdfs
```

Only rebuild the indexes when needed.

## 2. Project Architecture

The system uses two search methods:

- **Dense search:** Finds information based on meaning.
- **BM25 search:** Finds information based on matching keywords.

The results from both methods are combined using Reciprocal Rank Fusion (RRF). The system then keeps one result per page for normal search.

The `ask` command also looks for financial statement rows named in the question. This helps when a calculation needs multiple rows from the same page.

For supported financial formulas, the system extracts the required values, performs the calculation, and checks the result.

For other questions, it calls the configured free answer backend (`ANSWER_PROVIDER`, default Groq).

Every answer must include citations. Numbers must be supported by cited evidence or by a verified calculation using cited inputs.

### Main workflow

```mermaid
flowchart TD
    A[User Question] --> B[Company and Year Filter]
    B --> C[Dense Search]
    B --> D[BM25 Search]
    C --> E[Combine Results]
    D --> E
    E --> F[Keep One Result Per Page]
    F --> G[Search Results]
    F --> H[Ask Command]
    H --> I[Find Required Statement Rows]
    I --> J{Supported Formula or Single Line?}
    J -->|Yes| K[Calculate or Extract]
    J -->|No| L[Groq or Gemini]
    K --> M[Verify Answer]
    L --> M
```

## 3. Retrieval Evaluation

Run the evaluation script:

```powershell
python eval/evaluate_retrieval.py
```

The script evaluates:

- 20 development questions from `dev_questions.jsonl`
- 10 holdout questions from `questions.jsonl` with IDs starting with `gks_`

It uses a top-10 result limit and company/year filtering.

The evaluation reports three main metrics:

- **Recall:** How many expected pages were found.
- **MRR:** How high the first correct result appeared in the ranking.
- **Hit:** Whether at least one expected page appeared in the top results.

The evaluation measures retrieval results. It does not call Ollama or evaluate generated answer text.

### Evaluation results

The two charts below summarize the latest recorded retrieval run. The default method is chunk-level RRF with one result kept per page (`chunk+dedupe1`). Reranking is optional and is not used by default. Exact values are in the tables that follow.

#### Development vs holdout — hit rate

Hit rate is the share of questions for which at least one expected page appeared in the top 10. Higher is better.

![Bar chart of retrieval hit rate for the default method: 0.60 on development and 1.00 on holdout.](docs/figures/retrieval-hit-rate.svg)

#### Default vs reranker — MRR

MRR measures how early the first correct page appears. A higher MRR means relevant results appear earlier.

![Grouped bar chart of MRR. Default: development 0.3910, holdout 0.5067. Reranker: development 0.3514, holdout 0.7700.](docs/figures/default-vs-reranker-mrr.svg)

The evaluation used 20 development questions and 10 holdout questions. The set is small, so these charts describe that run only. They are not a guarantee of overall system accuracy.

### Latest recorded results

The production retrieval method is chunk-level RRF followed by keeping one result per page (`chunk+dedupe1`).

| Dataset | Questions | Recall | Hit | MRR |
|---|---:|---:|---:|---:|
| Development | 20 | 0.5500 | 0.6000 | 0.3910 |
| Holdout | 10 | 0.9000 | 1.0000 | 0.5067 |

These results are for normal retrieval. They do not include the additional statement rows that the `ask` command retrieves.

Reranking was tested but was not selected as the default method. Its latest recorded results were:

| Dataset | Hit | MRR | Average time |
|---|---:|---:|---:|
| Development | 0.70 | 0.3514 | 1,426 ms |
| Holdout | 0.90 | 0.7700 | 1,485 ms |

Reranking remains optional and is disabled by default.

## 4. Answer Testing

The following questions were tested manually after the answer-side changes.

| Test | Result |
|---|---|
| AMD FY2022 revenue | $23,601 million, cited to page 49 |
| Corning FY2020 DPO | 63.86 days, calculation verified using pages 70 and 72 |
| MGM FY2018 accounts payable | 303 million, calculation and unit conversion verified using page 57 |
| Boeing FY2018 net PP&E | 12,645 million, cited to page 52 |
| Unsupported question | System refused to provide an unsupported answer |
| Incorrect number | A fabricated number was flagged as unverified |

The numeric verification check confirms whether numbers appear in the cited evidence. It is not a complete semantic fact-checker for every possible statement.

## 5. Progress, Achievements, and Open Issues

### Achievements

- Hybrid retrieval (dense + BM25 + RRF) with desk-level access control in the index, not only the prompt.
- Measurable retrieval baseline and improvements logged in `EXPERIMENTS.md` / `DECISIONS.md`.
- Direct formula and single-line extraction for selected financial questions, with calculation verification.
- Free-tier answering: **Gemini `gemini-3.5-flash` primary**, Groq fallback; Ollama kept only as a latency experiment artifact.
- Ask-path evaluation separate from retrieval-only eval; `verified`, `correct`, and `ask_retrieval_hit` reported independently.
- Structured offline scoring pilot fixed Yes/No false positives (`fb_00757`, `fb_00005`) without new API calls (`--rescore`).
- Citation repair path (evidence packing + cite-retry) for answers that omit `[E#]`.
- User testing after the API/model change logged in `EXPERIMENTS.md` §6 (`answer_results_gemini35_usertest.jsonl`).

### Answer backend comparison (dev, 20 questions)

The chart compares **Ollama CPU**, the **prior Gemini Delay-8** run, and the **current `gemini-3.5-flash` user test** on the same development ask-path metrics.

![Grouped charts of ask-path accuracy and median latency for Ollama CPU, prior Gemini Delay-8, and gemini-3.5-flash. Answered rates 20%, 55%, 50%. Gold cited 15%, 35%, 35%. Median latency 132s, 7.3s, 9.3s.](docs/figures/ask-backend-accuracy-performance.svg)

| Backend | Answered | Gold cited | Median latency | Ask-path hit |
|---|---:|---:|---:|---:|
| Ollama CPU | 4/20 (20%) | 3/20 (15%) | 132s | 70% |
| Prior Gemini Delay-8 | 11/20 (55%) | 7/20 (35%) | 7.3s | 70% |
| Now `gemini-3.5-flash` | 10/20 (50%) | 7/20 (35%) | 9.3s | 70% |

Latency improved about **18×** vs Ollama. Citation rate rose from **15% → 35%**; answered rate from **20% → ~50–55%**. Retrieval hit stayed **70%** (same index/path). The latest model run matches the prior Gemini accuracy band; median latency is a bit higher because of mid-run **503** / **429** pressure (Groq still covered several later questions).

### Latest ask-path snapshot (user test: `gemini-3.5-flash`, Delay-8)

| Metric | Dev (20) | Holdout (10) |
|---|---:|---:|
| Retrieval hit | 14/20 (70%) | 9/10 (90%) |
| Gold page cited | 7/20 (35%) | 4/10 (40%) |
| Answered | 10/20 (50%) | 6/10 (60%) |
| Provider errors | 3/20 (15%) | 2/10 (20%) |
| Median latency | 9.3s | 8.9s |

Example user-test answers: MGM FY2018 AP **303M** and Boeing FY2018 net PP&E **12,645M** via direct calc; PepsiCo FY2021 capex and MGM dividends via Gemini with gold pages; holdout Ulta product-count and Boeing production models via Groq after Gemini quota.

Retrieval-only charts above remain the search baseline (Hit/MRR). Answer quality is measured with `eval/evaluate_answers.py`.

### Open issues

- Citation rate lags retrieval hit — highest remaining quality gap.
- Number-in-chunk verification is not full semantic correctness (wrong metric can still look “verified”).
- Some questions still miss gold pages in top results.
- Fresh holdout ask-path hit is strong (90%) but gold citation is still only 40%.
- Free-tier daily/RPM limits and occasional Gemini 503 can interrupt long user-test evals; delay helps RPM only.
- End-to-end latency improved vs local Ollama on CPU (~134s median → ~7–9s on Gemini Delay-8 user tests) but often still exceeds the 5s budget. The laptop has no CUDA GPU; embedding load/search stay CPU-bound. A GPU would speed those local stages; Gemini/Groq generation is network/API-bound and would not “auto-fix” from local GPU alone.

## 6. Known Limitations

- Normal `search` keeps only one result per page. A question that needs multiple rows from the same page may need the additional retrieval performed by `ask`.
- The retrieval evaluation measures normal search results. It does not measure the extra statement rows added by `ask`.
- Direct formula calculation is currently limited to supported questions containing `defined as:`.
- Other calculations, such as some margin and working capital questions, may still need the language model.
- The statement-line lookup uses a fixed list of known financial terms. A line outside this list depends on normal hybrid retrieval.
- Some capital expenditure questions do not match the direct single-line lookup path.
- A calculated answer without a valid calculation block may be returned as `answered_unverified` (CLI maps this to `answered` with `verified=false`).
- Numeric verification checks whether numbers are present in cited evidence. It does not prove that every number has the correct financial meaning.
- Refusal uses dense score plus BM25/statement presence; unsupported questions can still retrieve plausible pages.
- Structured `expected_*` fields are for offline scoring only; unseen user questions are not auto-scored against gold.

## 7. Project Data

Main project folders and files:

| Path | Purpose |
|---|---|
| `pdfs/` | Source PDF filings |
| `data/` | Extracted data and search indexes |
| `src/` | Project source code |
| `eval/` | Retrieval + answer evaluation scripts and result JSONL files |
| `tests/` | Test files |
| `manifest.csv` | Filing and company information |
| `questions.jsonl` | Evaluation questions (holdout `gks_*` plus shared items) |
| `dev_questions.jsonl` | Development questions |
| `DECISIONS.md` | Technical decisions |
| `EXPERIMENTS.md` | Experiments and their results |
| `AI_NOTES.md` | How AI tools were used |
| `.env.example` | Free-tier provider and desk configuration template |
| `requirements.txt` | Python dependencies |
