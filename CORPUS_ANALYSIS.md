Checked with inspect_corpus.py, analyze_manifest.py, inspect_pdfs.py.

Looked at 4 PDFs:
- 10-K: 3M_2015_10K (158 pages)
- 10-Q: 3M_2023Q2_10Q (92 pages)
- 8-K: BESTBUY_2023_8K_dated-2023-04-12 (148 pages)
- Earnings: BESTBUY_2023Q4_EARNINGS (13 pages)

Compared original PDF pages vs extracted text and csv tables for income statement, balance sheet, and cash flow.

## 1. Dataset

- 119 PDFs, 119 manifest rows, 20 questions
- every doc_id has a matching pdf, no extras, no duplicate ids
- manifest is comma-separated (not tab)
- columns: doc_id, company, sector, desk, doc_type, period
- keep doc_id as-is (capitalization is mixed, filenames match)

## 2. Distribution

Companies: Best Buy 14, J&J 13, PepsiCo 13, MGM 12, Pfizer 10, Corning/3M/AMD 9 each, Adobe/Boeing/Verizon 8, Ulta 6

Desks: consumer 45, health_industrials 40, tech_telecom 34

Types (manifest values, not renamed): 10k 90, Earnings 13, 8k 10, 10q 5, 10k_annualreport 1 (AMD_2022_annualreport)

Years: 2015-2021 = 11 each, 2022 = 15, 2023 = 23, 2024 = 4

Mostly 10-Ks. Almost no 10-Qs.

Dev questions: consumer 8, health_industrials 6, tech_telecom 6. A few answers are balance sheet numbers, so tables matter.

## 3. Text extraction (PyMuPDF)

Works:
- all 4 are real text PDFs, not scans
- normal paragraphs are fine
- use physical page number (1-based), not the footer page number

Doesn't work well:
- tables come out as a vertical list (Net sales / $ / 8,325 / $ / 8,702) so the number is not tied to the label
- 3M pages start with "Table of Contents" every time
- some broken words (Busines s, Balance Shee t, Cash Flow s)
- 8-K is mostly a credit agreement, not financials
- earnings headlines are ok, the table in text mode is messy

## 4. Tables (pdfplumber)

Detected table regions (not all real financial tables):
- 10-K: 559
- 10-Q: 71
- 8-K: 3
- Earnings: 16

pdfplumber detected 559 table regions across the inspected 10-K, including fragmented and irrelevant tables (TOC, exhibits, 1-row scraps). Same issue on 10-K financial statement pages themselves: income statement page 50 became 13 csv files, balance sheet page 52 became 25, cash flow page 54 became 22.

## 5. Financial statements (PDF vs extract)

Checked the three statements on 10-Q, 10-K, and earnings. 8-K has no primary financial statements.

### Income statement

10-Q page 3 (best):
- PDF: Net sales 8,325 / 8,702 / 16,356 / 17,531
- csv: same numbers, labels next to values
- two different NI lines (not the same number):
  - attributable to 3M: (6,841) / 78 / (5,865) / 1,377
  - including NCI: (6,836) / 82 / (5,855) / 1,385
- missing in csv: title + year headers. `$` is its own column

10-K page 50:
- PDF: Net sales 30,274 / 31,821 / 30,871
- csv: same numbers exist, but as one-row files (page_50_table_1 is only Net sales). Whole statement is split. Title extracted as "Consolidated Statement of Incom e"

Earnings page 6:
- PDF: Net earnings 495 / 626 / 1,419 / 2,454
- csv: numbers match, but one statement split into 6 files

### Balance sheet

10-Q page 5:
- PDF: Cash 4,258 / 3,655. PP&E-net 9,211 / 9,178. Total assets 48,880 / 46,455. Total liab+equity 48,880 / 46,455 (balances)
- csv: same labels and numbers. One table, usable
- missing: "June 30, 2023" / "December 31, 2022" column headers

10-K page 52:
- PDF: Cash 1,798 / 1,897. PP&E-net 8,515 / 8,489. Total assets 32,718 / 31,209
- csv: Cash row is correct (page_52_table_2) but the statement is smashed into ~25 fragments. First file is just the word "Assets". Title: "Consolidated Balance Shee t"
- PyMuPDF text has the numbers, just not as a table

Earnings page 7:
- PDF/csv: Cash 1,874 / 2,936. Total assets 15,803 / 17,504. Matches. table_1 is only "Assets", table_2 is the actual sheet

### Cash flow

10-Q page 6:
- cash flow is six months only, and it starts with NI including NCI, not "attributable to 3M"
- PDF/csv: NI including NCI (5,855) / 1,385. This matches the six-month column on the income statement, not (6,841)
- (6,841) is Q2 only, attributable to 3M — that line is not the cash flow starting number
- operating cash 2,784 / 2,138. Ending cash 4,258 / 2,722 (ties to BS cash)
- csv usable. `$` columns. Years missing from header

10-K page 54:
- PDF: NI 4,841 / 4,998 / 4,721. Ending cash 1,798 / 1,897 / 2,581 (ties to BS cash)
- csv: numbers show up (table_1 = NI row, table_2 = depreciation) but 22 fragments, not one statement. Title: "Consolidated Statement of Cash Flow s"

Earnings page 8:
- PDF/csv: NI 1,419 / 2,454. Operating cash 1,824 / 3,252. One table, pretty clean

Takeaway: numbers are usually right when pdfplumber gets a real row. The problem is structure (split tables, missing titles/years, `$` columns), worse on 10-K than 10-Q/earnings. Plain PyMuPDF text is not enough for these three statements.

## 6. Other issues

- keep original manifest `doc_type` on every record (`10k`, `10q`, `8k`, `Earnings`, `10k_annualreport`)
- for processing only, map `10k` and `10k_annualreport` -> 10-K. do not rewrite the csv
- same metric shows up in multiple years/docs, so company + period + page have to stay on the chunk
- gold answers for stuff like accounts payable / PP&E live in tables
- 8-Ks here are not a replacement for 10-K financials

## 7. What I'll do for ingestion

Keep PyMuPDF + pdfplumber. Not switching libraries.

- extract page by page
- clean tables (merge `$` columns, skip 1-row junk, keep title if present)
- turn tables into searchable lines like: Net sales | 2023 Q2: 8,325 | 2022 Q2: 8,702
- drop repeating headers
- if a table fails, log it and still keep the page text

Store this metadata on every text chunk and every table:
- doc_id
- company
- period
- doc_type (original manifest value)
- physical_page_number
- table_title (where available)

## 8. Current extractor

Sections 4 and 5 describe the old per-region csv dump. `src/ingestion/table_extractor.py` is now one row list per page. Decisions and the page checks are in `DECISIONS.md` and `EXPERIMENTS.md`.

Re-checked after the extractor changes:

- 3M 2015 10-K page 50 income statement: 19 rows
- 3M 2015 10-K page 52 balance sheet: 41 rows
- 3M 2015 10-K page 54 cash flow: 36 rows
- Adobe 2015 10-K page 34 selected data: 16 rows

JSON in `data/extracted` does not update until `src/ingestion/ingest.py --all --force`.
