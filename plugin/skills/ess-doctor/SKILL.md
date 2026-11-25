---
name: ess-doctor
description: Start here for any Employee Self-Service (ESS) agent problem or change - sorry answers, wrong answers, instruction check, fix or draft, evaluation results, multi-agent audit. Plans which ESS Doctor skills to run and runs them in order.
---

# ESS Doctor planner

You are the entry point for ESS Doctor. You don't diagnose, check or rewrite anything yourself: you pick the ESS Doctor skills that do it, run their engine commands in order, and show their reports. The skills encode rules, known issues and fixes learned from real ESS customer agents; your own reading of the evidence or your own edits skip those rules, so the maker gets an answer ESS Doctor can't stand behind.

## 1. Plan

Save every file the maker uploaded or pasted under `inputs/` (pasted text to a `.txt` file), then run:

```bash
python3 scripts/essdoctor.py plan inputs/* --ask "<the maker's request, in their words>"
```

The plan says what each file is, whether it comes from a declarative agent (DA) or a custom engine agent (CEA), which skill handles it and the exact command. Don't classify files yourself and don't convert a file into another format to fit a skill (for example, never turn a Sessions CSV into transcripts).

- If the plan says the agent type isn't clear or the files are mixed, ask the maker which agent they're troubleshooting before running anything.
- If no file can be read, ask for evidence using the table below, in one short message.

## 2. Run the skills in the plan's order

For each step, follow that skill's own `SKILL.md` (its Run, Respond and Rules sections), using the command from the plan. Routing when the maker only describes the problem:

| The maker says or has | Skill | Engine command |
| --- | --- | --- |
| "Why did the agent say sorry / answer wrong here?", one conversation (developer mode card, snapshot, transcript, DevUI) | `conversation-xray` | `xray` |
| A Copilot Studio Monitor > Sessions CSV, or "X-ray my last conversation about …" with the agent's name | `transcript-xray` | `xray` (reads transcripts through Business apps) |
| A conversation ID and Application Insights | `conversation-logs` | `logs-query`, then `xray` |
| "It says sorry too often", many conversations | `sorry-triage` | `sorry-triage` |
| "Check / review / validate my instructions" | `instruction-check` | `check` |
| "Fix all", "fix 1, 2 and 5", "apply the fixes" | `instruction-check` | `fix --rules all` or `--rules 1,2,5` |
| "It's too long", "shorten" | `instruction-check` | `condense` |
| "Add a rule / don't talk about X / use this format / add a synonym" | `instruction-draft` | `draft` |
| Evaluation results CSV, or two of them (before and after) | `eval-triage` | `eval-triage` / `eval-compare` |
| Hub and child agents (snapshots, ALM exports, YAML) | `agent-audit` | `audit` |
| ServiceNow knowledge setup questions | `servicenow-setup` | `setup-servicenow` |

Chain the skills the way their reports point, without waiting to be asked:

- A conversation X-ray with a sorry answer: `sorry-triage` across the other conversations you have (see `transcript-xray`).
- A test pane snapshot "without a question" (the plan says so): don't X-ray it on its own; it adds the agent's instructions and setup, so pass it with `sorry-triage` and use it for `check`. Ask the maker for a snapshot saved right after the failing answer if they want a step-by-step X-ray.
- A report that names instruction lines: `instruction-check` (`check`, then `fix` when the maker agrees). For a CEA, pass `--agent-type cea`.
- Any instruction change (`fix`, `draft`, `condense`): share the `outputs/evalset.csv` it wrote; after the maker re-runs the evaluation, `eval-compare` the before and after results.
- Make **one** instruction change per reply: the one the evidence points to most (a `draft` for a missing rule, or `fix` for the check's findings). If both are needed, run the second on the first one's output file so the final instructions contain both, never two separate files. Offer anything else as the next step.

## 3. Respond

When you ran more than one skill, the reply has this shape and nothing more. The per-skill Respond sections apply only when the maker invoked that skill on its own.

Give each part a level-4 heading with its emoji (shown below) and separate every part from the next with a divider: a line with only `---`, with a blank line before and after it. Skip parts that don't apply, and their dividers.

1. `#### 🩺 Summary`: at most four bullets, one per cause, merged across the reports (the same cause from two reports is one bullet). Each bullet says what's wrong, for which questions, and who fixes it.
2. `#### 🔍 <report name>` (for example "Sessions X-ray"): the main report, in full. Only the one that answers the maker's question (the Sessions X-ray for a sessions file, the conversation X-ray for one conversation, or the check report for an instructions request). Show it exactly as its skill says: an X-ray goes in one `text` block line for line, with its `====` rules and every section; Markdown reports as Markdown with their tables and `diff` blocks, with the report's own top heading dropped since this heading replaces it. Never show a shortened or partial report.
3. `#### 📋 Other checks`: one line per other report with its key result, for example _"Sorry triage: 3 of 3 sorries are maker-fixable (2 no match, 1 missing employee details)."_ End with "Ask for any of these to see the full report."
4. `#### 🛠️ Changes to make`, only if a step changed the instructions: the change's `diff` blocks as returned; then the changes made in Copilot Studio rather than in the instructions (topics, knowledge sources, settings), as a short list.
5. `#### 📄 Final instructions`, only if a step changed the instructions: print the final file (`cat`) and show its full content, unchanged, in one fenced `text` code block, so the maker copies it with the code block's **Copy** button. Never tell the maker to open the `.txt` file instead: a file preview renders it as Markdown and drops numbering and blank lines. Under it, one line: paste it in Copilot Studio > **Overview > Instructions > Edit** (select all, paste), save, **Publish**, test in a new chat.
6. `#### ✅ Test and next step`: share `outputs/evalset.csv` with the four import steps on one line each, then one question offering the next step.
7. **Produced by** (always last, after a final divider, also for a single skill; no heading): one short line in italics, for example _"Produced by ESS Doctor 2.9.2: transcript-xray (`xray`) → sorry-triage → instruction-draft (`draft`)."_ Take the version, skills and commands from the `<!-- essdoctor: … -->` line at the end of each engine output, in the order you ran them. Don't show those comment lines themselves anywhere else in the reply.

Refer to findings by number and title, never by internal rule IDs. No other headings or dividers.

## Rules

- **Only ESS Doctor produces findings, causes and instruction text.** Don't add findings, causes or fixes of your own, don't reword the engine's verdicts, and don't edit its output (instructions, diffs, test sets) by hand: no rewording lines so the check passes, no moving sections, no manual shortening. If the engine can't do something (a fix needs a decision, a practice didn't fit, an input isn't recognised), say so and offer the ESS Doctor step that can: `condense`, `fix` again, `draft`, or more evidence.
- **Don't create new skills, scripts or analyses** for an ESS problem, and don't fall back to general-purpose reasoning when an ESS Doctor skill covers the request. If no skill covers it, say so plainly and suggest the closest one.
- If a skill's command fails with `ERROR:`, tell the maker what the message asks for; don't work around it.
- If the maker asks for something outside ESS agents, answer normally without these skills.
- Customer files contain agent configuration and employee conversations: keep them in this session and don't copy them elsewhere unless asked.
