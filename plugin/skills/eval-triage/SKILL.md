---
name: eval-triage
description: Explain failed ESS evaluation questions and compare runs before and after a change.
---

# ESS evaluation triage

> Part of ESS Doctor. Findings, causes and instruction text come only from the engine commands below; don't analyse the evidence or edit the engine's output yourself. When the request needs more than this skill, follow the `ess-doctor` skill to plan and chain the others. End the reply with one line, "Produced by ESS Doctor <version>: <skill> (`<command>`)", taken from the `<!-- essdoctor: ... -->` line at the end of the engine output; don't show that comment line itself.

## Inputs

- One Copilot Studio evaluation results CSV (export it from the agent's **Evaluation** page), plus the agent instructions the run used; or
- Two results CSVs of the same questions: before and after an instruction change.

## Run

```bash
python3 scripts/essdoctor.py eval-triage inputs/results.csv --instructions inputs/current.txt
python3 scripts/essdoctor.py eval-compare inputs/before.csv inputs/after.csv
```

## Respond

1. Show the report as returned, as Markdown (not in a code block), so its tables render. Don't rebuild or reword its tables.
2. Then add **What this means** in at most three short bullets: the top causes with their counts, whether any failure is an instruction issue the maker can fix, and anything the grader didn't check (for example a required line missing from responses that passed). Don't repeat the tables.
3. Then **Next steps** as a numbered list of at most three items, most important first. For instruction causes, offer to draft the fix with `instruction-check` or `instruction-draft` (a missing disclaimer or required line goes first), and show the proposed change as the `diff` blocks those skills return; then re-run the same evaluation set and compare with `eval-compare`. If the report flags a required line, suggest adding an evaluation check for that exact sentence.
4. Keep it scannable: bold only the key numbers and owners, no extra headings beyond these two, and no more than one ⚠️.

## Rules

- Evaluation CSVs can contain employee-like questions and answers; keep them in this session.
- Don't mark a question as fixed until a new evaluation run shows it passing.
