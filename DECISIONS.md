# Decisions

## 1. Keep the physical page on every chunk

Options were a page number, a sliding window with no page, or a section id. The eval and the citation contract both use the PDF page counted from 1. Each chunk stores that page. What would change this: a corpus where the printed page and the PDF page disagree and the questions used the printed number. They do not.

## 2. Extract tables separately from body text

PyMuPDF text drops or scrambles statement rows. pdfplumber reads the same page as rows. Body text and table rows are stored as different chunks. The merge is layout-first: a line-based row replaces a layout row only when it is the same row and higher quality. `(154)` and `154` are different amounts. A lone `(1)` or `(26)` is a footnote, not an amount, unless the row has several such cells. Year columns are the trailing amounts; words stay on the label. What would change this: a filing family whose statements are images. These PDFs have a text layer.

## 3. Chunk tables by row, text by words

A fixed word window splits a statement line across chunks, so the label and the year values separate. Table chunks keep rows together. Text chunks are word windows with overlap, and a short page stays whole. What would change this: page hit rising after a section-sized chunk experiment. It did not, on the pages we checked, because the wrong page was retrieved, not a cut-off sentence.

## 4. Local MiniLM and Chroma, cosine

`all-MiniLM-L6-v2` (384 dimensions) runs locally with no key. Chroma persists under `data/chroma_db`. Score for semantic hits is `1 - cosine distance`, so a higher score is closer. What would change this: a free embedding model that beat this one on page hit@5 by a margin larger than the noise on 30 questions. We did not swap models. The assignment forbids fine-tuning.

## 5. Idempotent indexing by file hash

Re-ingesting a changed filing must not duplicate chunks. Extracted JSON is hashed with the chunking version. Unchanged files are skipped. A changed file deletes that `doc_id` and inserts the replacement. PDF bytes are hashed too, so a changed PDF is extracted again and then follows the same path. What would change this: a need to edit a single page without rewriting the JSON. We do not have that.

## 6. Enforce the desk inside the query

Roles map to desks: consumer, health_industrials, tech_telecom, and lead (all three). The Chroma `where` and the BM25 filter both require the desk before ranking. Filtering after retrieval would still spend the top-k slots on other desks. Cost: a question that needs two desks only works for `lead`, and a company that sits on the wrong desk is invisible. What would change this: a question set where analysts routinely need another desk's filing. The brief says they do not.

## 7. Hybrid search, then keep it for document rank, not page hit

Baseline keyword search (BM25 only) was 25/30 document hit and 16/30 page hit. Adding semantic search and reciprocal rank fusion moved document hit to 29/30 and page MRR from 0.324 to 0.441. Page hit stayed 16/30. We kept the fusion because the right document arrives earlier, which is what the answer step reads. What would change this: a later run where fusion lowers page MRR. It has not.

## 8. Lock the reporting year, except forecasts

The same line item appears in many years. If the question names FY2021 and is not a forecast, both lists keep only that period. "Forecast", "outlook", "guidance", and "anticipat..." turn the lock off, because the evidence lives in an earlier filing. What would change this: forecast questions whose gold page is still missing after the lock is off. Boeing's FY2023 production-rate question is that case, and the lock is not the fix. See `EXPERIMENTS.md`.

## 9. A third vote for a named statement

Questions that say "balance sheet" or "statement of cash flows" were landing on the narrative that mentions the statement. Pages whose text contains that heading get their own candidate list and a fusion vote. What would change this: the heading list matching the table of contents more often than the statement. Contents pages are dropped before fusion.

## 10. The model may not answer from outside the chunks

`ask` sends only the retrieved chunks. `status` is `answered` only when the model marks the reply grounded and cites one of those chunks. Otherwise `status` is `refused` and `citations` is empty. A single-year amount that is printed on a retrieved statement row is copied from that row, including a unit conversion that names the printed figure. What would change this: a hidden set where the right behavior is to compute a ratio from pages that retrieval did not return. We would rather refuse.

## 11. Page hit@5 is the retrieval metric

Document hit is easy on this corpus once the company and year are known, and it hides the failure mode we care about: the right filing, the wrong page. Page hit@5 matches the contract, because the citation is a page. MRR is the tie-break when hit rate does not move. Answer quality is separate and requires the cited page to be a labeled evidence page.

## 12. One environment variable for the answer model

Answers use Ollama at `OLLAMA_HOST`, model `LLM_MODEL`, provider `LLM_PROVIDER`. Embeddings stay local either way. What would change this: a provider we cannot reach from a clean machine. Only Ollama is implemented. A paid model would be a new provider behind the same variable, which we did not add.
