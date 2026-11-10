# AI Notes

Cursor's coding agent was used to help write parts of the answer-side code.

The retrieval evaluation results, including Hit and MRR, came from running:

```powershell
python eval/evaluate_retrieval.py
```

They were measured by the evaluation script, not produced by the coding agent.

The coding agent helped with:

- The calculation-block checker.
- Retrieving financial statement rows during `ask`.
- Reading year-column values to calculate formulas without relying on the language model.

Testing also caught three mistakes in the agent's suggestions:

1. **Corning DPO:** The first suggested solution used three searches written specifically for Corning. This would not work as a general solution for other companies. It was replaced with a company-independent statement-row lookup.

2. **MGM unit conversion:** The first conversion was in the wrong direction. It changed 302,578 thousand into 302,578,000 million. A unit test exposed the error, and the conversion was corrected.

3. **AMD net loss row:** The agent treated AMD's operations statement's "Net loss" as a table row in the index. However, the amounts appear on the lines below the label in the page text. The lookup initially selected a different summary on page 34. Reading the actual page text helped identify the correct evidence.

These examples show why AI-generated code was tested before its results were accepted.