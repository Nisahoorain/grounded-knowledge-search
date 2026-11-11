# AI Notes

AI-assisted tools were used during development:

- **Cursor:** Assisted with writing and modifying parts of the retrieval, answer-generation, and evaluation code.
- **ChatGPT:** Assisted with technical discussions, debugging approaches, reviewing test outputs, and documentation.

## Developer Contributions

The developer independently performed the main investigation and validation work, including:

- Running commands, inspecting source code, extracted filing text, and retrieval results.
- Investigating retrieval failures, financial calculations, and unit conversions.
- Comparing expected gold pages with retrieved and cited evidence.
- Testing RBAC, answer verification, and evaluation results.
- Reviewing suggested changes and rerunning tests before accepting fixes.

## Issues Identified During Testing

1. **Corning DPO:** Replaced company-specific retrieval logic with a more general statement-row lookup.
2. **MGM conversion:** Corrected a unit-conversion direction error.
3. **AMD net loss:** Fixed extraction where the amount appeared on a following line.
4. **Answer scoring:** Fixed Yes/No evaluation incorrectly accepting numeric answers.
5. **Gemini quota:** Improved fallback handling for daily quota errors.
6. **Verification:** Separated evidence support (`verified`) from semantic answer correctness.

## Evaluation Integrity

Metrics were generated using project evaluation scripts and recorded JSONL results. AI suggestions were treated as proposals and reviewed through manual or scripted testing.

The developer retained responsibility for implementation decisions, debugging, validation, and final results.