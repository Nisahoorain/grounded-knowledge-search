# AI Notes

Cursor's coding agent was used to help write parts of the answer-side, retrieval, and evaluation code.

## What was measured by scripts (not invented)

Retrieval evaluation (Hit, MRR, Recall):

```powershell
python eval/evaluate_retrieval.py
```

Ask-path latency and answer metrics:

```powershell
python eval/evaluate_answers.py --tag gemini_delay8_dev --delay 8
python eval/evaluate_answers.py --rescore eval/answer_results_gemini_delay8_dev.jsonl
python eval/evaluate_answers.py --tag gemini35_usertest --delay 8
python eval/evaluate_answers.py --tag citefix_issues --delay 8 --ids (Get-Content eval/ids_failed_uncited.txt)
```

Zero-API diagnostics:

```powershell
python gold_rank.py eval/questions_all30.jsonl --rerank
python offline_eval.py eval/questions_all30.jsonl
```

## Where the agent helped

- Calculation-block checker and company-independent statement-row lookup.
- Free-tier LLM wiring (Gemini primary / Groq fallback), 429/503 handling, and disk cache.
- Citation repair (strict `[E#]` prompt, force-answer retry, cite-retry, auto-attach).
- Query helpers: quarter parsing, single-line amount `line_items_in`, free-cash-flow metric pack.
- Eval tooling: `--ids` / `--rescore`, `gold_rank.py`, `offline_eval.py`, passed/failed question splits.
- README / EXPERIMENTS / DECISIONS write-ups aligned to measured JSONL logs.

## Mistakes caught in testing

1. **Corning DPO:** first suggestion used Corning-specific searches; replaced with company-independent statement-row lookup.
2. **MGM unit conversion:** first conversion went the wrong direction; a unit test caught it.
3. **AMD net loss row:** treated a label as a pipe-row; amounts were on following lines — fixed after reading page text.
4. **Yes/No auto-score:** early eval marked wrong numeric answers correct when gold started with “Yes”; fixed with structured `yes_no_numeric` fields and offline rescoring.
5. **Gemini daily quota:** backoff slept on a multi-hour “retry in Nh” error instead of falling over to Groq; fixed by detecting daily-quota 429s.
6. **`verified` vs correctness:** agent and early docs treated `verified=True` as “correct”; kept as evidence-support only after miss-set analysis.

AI suggestions were accepted only after scripted or manual checks. Numbers in the README and EXPERIMENTS.md come from the eval JSONL / log files listed there.
