# AI Notes

Cursor's coding agent was used to help write parts of the answer-side and evaluation code.

The retrieval evaluation numbers (Hit, MRR, Recall) came from:

```powershell
python eval/evaluate_retrieval.py
```

Ask-path latency and answer metrics came from:

```powershell
python eval/evaluate_answers.py --tag gemini_delay8_dev --delay 8
python eval/evaluate_answers.py --rescore eval/answer_results_gemini_delay8_dev.jsonl
```

They were measured by the scripts, not invented by the agent.

The coding agent helped with:

- The calculation-block checker and statement-row lookup.
- Free-tier LLM wiring (Gemini primary / Groq fallback) and rate-limit handling.
- Structured offline scoring and citation-retry / evidence packing.

Testing also caught agent mistakes:

1. **Corning DPO:** first suggestion used Corning-specific searches; replaced with company-independent statement-row lookup.
2. **MGM unit conversion:** first conversion went the wrong direction (thousands → huge “millions”); a unit test caught it.
3. **AMD net loss row:** treated a label as a pipe-row; amounts were on following lines — fixed after reading page text.
4. **Yes/No auto-score:** early eval marked wrong numeric answers correct when gold started with “Yes”; fixed with structured `yes_no_numeric` fields and offline rescoring.
5. **Gemini daily quota:** backoff slept on a multi-hour “retry in Nh” error instead of falling over to Groq immediately; fixed by detecting daily-quota 429s.

AI suggestions were accepted only after scripted or manual checks.
