---
name: instruction-draft
description: Draft an ESS instruction change, such as a restriction, routing rule or format, and check side effects.
---

# ESS instruction draft

You turn a maker's intended change into instruction text that fits the ESS template, then check it didn't break anything else.

## Collect the change

Ask only for what's missing, in **one** message, never one question at a time. Ask for the instructions in the same message (or read them through the Business apps connector, see `instruction-check`), and offer the change type you inferred so the maker only confirms it:

| Change type (`change_type`) | Details to collect (`intake` fields) |
| --- | --- |
| `terminology` | Pairs of the employee's word and the word the sources use: `terms` `[["PTO", "paid time off"]]` |
| `restricted` | `subject` to restrict; what is still `allowed`; optional `scope` (country, role) |
| `format` | `format_spec` (for example "bullets, max 5, end with a link"); optional `scope` |
| `routing` | `subject`, `source_name` (knowledge source), `mode` `only` or `exclude` |
| `escalation` | `scope` (which questions) and `route` (where to send employees) |
| `persona` | `intent` describing tone and voice |
| `disclaimer` | exact `disclaimer_text`; optional `disclaimer_style` (`bold`, `italic`); optional `scope` |
| `context` | profile fields the answer depends on as `subject` (for example "country and job level"); optional `scope` (which questions). Drafts one rule: use the user context values, ask or give the base policy when one is blank, and keep names, emails and IDs out of search queries |

Also ask for 2–4 example questions that **should** change (`should_affect`) and 2–4 that **must not** (`must_not_affect`); propose your own examples in the same message so the maker can just accept them. Always put the maker's own words in `intent`.

Save the instructions to `inputs/current.txt` (or use the uploaded agent file), and the details to `inputs/intake.json`, for example:

```json
{
  "change_type": "restricted",
  "intent": "Don't compare our salaries with other companies",
  "subject": "salary comparisons with other companies",
  "allowed": "our own pay bands and policies",
  "should_affect": ["How does my pay compare to Contoso?"],
  "must_not_affect": ["What is my pay band?"]
}
```

## Run

```bash
python3 scripts/essdoctor.py draft inputs/current.txt --intake inputs/intake.json --out outputs/drafted_instructions.txt
python3 scripts/essdoctor.py check outputs/drafted_instructions.txt --previous inputs/current.txt --no-evalset
```

`draft` also writes `outputs/evalset.csv`: a Copilot Studio evaluation test set with the questions this change should and must not affect, plus ESS regression questions, each with its expected response. The follow-up `check` uses `--no-evalset` so it doesn't overwrite it.

If the draft report says more detail is needed, ask the maker those questions and run it again.

## Respond

The report's **What changed** section shows each changed section as a `diff` code block: removed lines start with `-` (red) and added lines with `+` (green). Keep those blocks exactly as returned, including the `diff` language tag, so the colors render.

1. Show the draft report as returned, as Markdown (not in a code block), so its summary table, test table and checks render. Don't rebuild its tables.
2. `draft` shortens the instructions automatically when the change takes them over the 8,000-character limit, aiming for 7,200 to leave a 10% buffer (the new rule is kept word for word, and every ESS ability is kept); the report says what it tightened, or which lines to shorten if it's still over. Don't shorten lines yourself. If it's still over, tell the maker which line to move into a topic description, or offer to run `condense` with a lower `--target` and `--no-evalset`.
3. Run `check --no-evalset` on the final file. If it introduced a high finding, say so plainly, propose a narrower wording in the same section and re-run. You may suggest clearer wording for the added clause, but re-run `check` on any edited version before offering it.
4. Then add **What to check** in at most three bullets: what the report flags, any assumption you made (for example how the agent knows who the rule applies to), and whether the new rule's subject or source name overlaps a restriction already in the instructions (for example a source whose name contains a restricted word). Don't repeat the report's tables.
5. Then **Final instructions**: print the final file (`cat`) and show its full content, unchanged, inside one fenced code block marked `text`. Put it last so the maker doesn't scroll past it, with the copy steps from "Applying" in one line under it.
6. Share `outputs/evalset.csv` (and the instructions file) as downloadable files, with the four import steps: in Copilot Studio open the agent > **Evaluation** > **Create a test set** > upload the CSV; run it on the current version; publish the change and run it again; upload both results files here to compare them with `eval-compare`.
7. Keep it scannable: no headings beyond "What to check" and "Final instructions", bold only key numbers, and no emoji beyond those in the report.

## Applying

Tell the maker to copy the instructions with the code block's **Copy** button, not from the file preview: the preview shows the file as formatted Markdown, so numbered lines lose their numbers and blank lines disappear when copied. Keep line breaks, blank lines, numbering and `#` headings exactly as the engine wrote them.

The maker pastes the new instructions in Copilot Studio (**Overview > Instructions > Edit**, select all the existing text, paste), saves, publishes and tests in a new chat. You can't change the agent yourself.

## Rules

- Stay within 8,000 characters as Copilot Studio counts them, ideally 7,200 (a 10% buffer); `draft` shortens automatically when it goes over.
- Don't remove or rewrite the ESS core blocks (routing, grounding, tool use) to make room for a change.
