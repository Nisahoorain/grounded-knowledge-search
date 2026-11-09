PDF checks of `src/ingestion/table_extractor.py`. The old csv notes in `CORPUS_ANALYSIS.md` are not these results.

## Page 52 balance sheet, 3M 2015 10-K

### Verified

41 unique rows. Layout order, not the old lines-first order. Cash is `$ 1,798 | $ 1,897`. Work in process is `1,008 | 1,081` between finished goods and raw materials. Goodwill is `9,249 | 7,050` after net PP&E. Total assets is `$ 32,718 | $ 31,209`. Common stock is `$ 9 | $ 9`. Total liabilities and equity is the last row.

### Failed

None of those checks.

Before the layout-first merge, Total assets and Goodwill were appended after Total liabilities and equity. Before the `$ 9 $` split, Common stock was dropped.

### Unresolved

The first row is `(Dollars in millions, except per share amount) 2015 | 2014`, not a separate year-header row. `Assets`, `Liabilities`, and `Equity` are not emitted.

## Page 50 income statement, 3M 2015 10-K

### Verified

19 unique rows, PDF order, from Net sales through cash dividends.

Interest income is `(26) | (33) | (41)`. NCI is `8 | 42 | 62`. Weighted average basic is `625.6 | 649.2 | 681.9` and diluted is `637.2 | 662.0 | 693.6`. EPS basic is `$ 7.72 | $ 7.63 | $ 6.83` and diluted is `$ 7.58 | $ 7.49 | $ 6.72`, with basic and diluted on the labels. Total interest expense — net is `123 | 109 | 104`.

### Failed

None of those checks.

Before the amount and label changes, those four rows were missing and the EPS and interest labels were split.

### Unresolved

`(26)` and `(1)` are still the same result from `is_amount`. A single parenthesis is still a footnote. `(1,100)` was already an amount. No separate rule was added to tell a one-digit amount from a footnote.

`Operating expenses` and `Interest expense and income` are not emitted.

## Page 54 cash flow, 3M 2015 10-K

### Verified

36 unique rows. Operating excess tax benefits is `(154) | (167) | (92)`. Financing excess tax benefits is `154 | 167 | 92`, after dividends. Both Other — net rows are present, with different numbers. Ending cash is `$ 1,798 | $ 1,897 | $ 2,581`. The issuance row keeps the full label through `plans | 635 | 968 | 1,609`.

### Failed

None of those checks.

Before `row_key` kept parentheses, the financing excess-tax row was dropped.

### Unresolved

`Cash Flows from Operating Activities`, `Investing Activities`, and `Financing Activities` are not emitted. No amount row from the PDF was missing in this check.

## Adobe 2015 10-K page 34

### Verified

16 unique rows. Header is `Fiscal Years | 2015 | 2014 | 2013 | 2012 | 2011`, without the prior-page paragraph. Revenue through worldwide employees match the five PDF years. Cash dividends is `$ —` five times. Working capital is `(2)` on the label and then `$ 2,608,336 | $ 2,107,893 | $ 2,520,281 | $ 3,125,314 | $ 2,520,672`. The `____ 2015. | 34` footer row is absent.

### Failed

None of those checks.

### Unresolved

`Operations`, `Net income per share`, `Financial position`, and `Additional data` are not on the rows. Basic and Diluted do not repeat the parent heading.

## Not re-checked

3M 2023 Q2 pages 3, 5, and 6, and the earnings pages in `CORPUS_ANALYSIS.md`, were not run again after these extractor changes.
