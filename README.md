# Grounded Knowledge Search

Analysts ask questions about 119 US filings (10-K, 10-Q, 8-K, earnings). The system retrieves chunks from the companies on that analyst's desk and answers only from those chunks, with document and page citations. If the retrieved pages do not contain the answer, it refuses.

## Run

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Ollama must be running locally with the model named in `.env` (`llama3.2` by default). Embeddings are `all-MiniLM-L6-v2` and do not need a key. `LLM_PROVIDER` is the only switch for the answer model.

```powershell
.\venv\Scripts\python.exe main.py ingest pdfs
.\venv\Scripts\python.exe main.py search --role consumer --k 5 "What is the FY2021 capital expenditure amount for PepsiCo?"
.\venv\Scripts\python.exe main.py ask --role consumer "What is the FY2021 capital expenditure amount for PepsiCo?"
.\venv\Scripts\python.exe eval\run_eval.py
```

`ingest` reads `manifest.csv`, extracts text and tables from `<corpus_dir>`, embeds new or changed filings into Chroma, and rebuilds the BM25 index only when chunks change. A second run skips unchanged PDFs. Replacing a filing deletes that document's old chunks before inserting the new ones.

`search` prints a JSON list, best first: `doc_id`, `page` (from 1), `chunk_id`, `score`, `text`.

`ask` prints one JSON object: `status` is `answered` or `refused`, plus `answer` and `citations` (`doc_id`, `page`, `chunk_id`).

Roles are `consumer`, `health_industrials`, `tech_telecom`, and `lead`.

## Architecture

PyMuPDF extracts page text. pdfplumber extracts table rows. Each chunk keeps company, desk, period, page, and content type. Semantic search (Chroma, cosine) and BM25 are fused with reciprocal rank fusion. The desk filter is inside both queries. A named company and a named reporting year narrow both lists. A named financial statement adds a third list of pages that contain that statement heading. The local LLM sees only the fused chunks. A single-year figure that is printed on a statement row is copied from that row, and a unit change shows the printed input.

## Numbers

Eval set: 30 questions in `eval/questions.jsonl` (the 20 dev questions plus 10). `k = 5`. Primary retrieval metric is page hit: at least one labeled evidence page in the top 5. Document hit is reported because the same line item repeats across years.

| Run | Document hit@5 | Page hit@5 | Page MRR |
|---|---:|---:|---:|
| BM25 only | 25/30 (83.3%) | 16/30 (53.3%) | 0.324 |
| Hybrid (semantic + BM25) | 29/30 (96.7%) | 16/30 (53.3%) | 0.441 |

Hybrid did not raise page hit. It did raise document hit and page MRR, so the right filing shows up earlier even when the exact page is still outside the top 5. Answer quality is `eval/run_answers.py`: an answer counts only when `status` is `answered` and a citation page is in the labeled set. Anything else is a refusal or a wrong citation.

## Limitations

Page hit is 16/30. Narrative disclosures, forecasts, and computed ratios still lose to boilerplate and to statement rows whose label does not match the question's wording. A first answer can exceed 5 seconds because the embedding model and the BM25 index load on the first call; later calls are the search plus one local generation. Calculated answers show the printed inputs they scaled. They do not recompute a ratio from several pages when those pages were not retrieved together.
