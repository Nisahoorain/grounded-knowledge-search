Rules in `src/ingestion/table_extractor.py`. Each one is in the production functions named below.

## Layout-first merge

`process_page` reads layout rows first, then `lines/lines` and `lines/text`.

`merge_layout_first` keeps that layout order. A line-based row replaces a layout row only when `row_key` is the same and `row_quality` is higher. A line-based row whose key is not in the layout is not added.

If layout returns no rows, `process_page` uses `merge_unique` on the line-based rows.

`row_key` stores `\([\d,.]+\)` before a plain number. `(154)` and `154` are different keys.

## Amount detection

`is_amount` rejects `\(\d{1,2}\)`. `(26)` and `(1)` both come back false. There is no digit-count rule that accepts one and rejects the other.

`real_amounts` adds parenthesis cells only when the same row has at least two cells matching `\(\d{1,3}\)`. Interest income `(26) | (33) | (41)` counts. One `(1)` does not.

`is_amount` accepts `$—`. `real_amounts` does not count a bare `$`, `—`, `–`, or `-`. `$ —` is a value. A dash by itself is not.

`_column_spans` skips a `\(\d{1,2}\)` match when the character before it is a letter. `Working capital(2)` keeps `(2)` on the label.

## Label and year columns

`_split_label_columns` takes the trailing amounts whose gaps have no letters. Those are the year columns. A letter in a gap stops the run, so `of $91 and $94` stays on the label. If that run also has a comma-thousands figure, leading `$91`-style cells are pulled back into the label while more than two columns remain.

A bare em dash is not a column span, so `Total interest expense — net` and `— basic` stay on the label. `fold_label_dash` does the same when the dash and the following word are already separate cells.

`DECIMAL_RE` treats `625.6` as a column. It does not treat the `7.72` inside `$ 7.72` as a separate number. One trailing amount after a word is still split. A cell that is only `$ 7.72` stays one cell.

`split_bare_integers` splits a letter label with a tail of two or more 1–3 digit integers, as in `8 42 62`.

`split_glued_dollar` turns a cell matching `$ … $` plus a following amount into `$ 9 | $ 9`.

## Footer and prose

`keep_row` drops a row with no `real_amounts` hit. `Assets` and `Cash Flows from Operating Activities` are not emitted as rows.

`heading_fragment` may carry a label onto the next row. It allows at most 90 characters and 16 words, and it rejects a period or `the`, `our`, `this`, `that`, `following`, or `agreement`. `attach_pending` uses that text only when the next row starts with a number or a lowercase word. That is how `… benefit` joins `plans | 635 | 968 | 1,609`. A capitalized next label does not receive it.
