---
name: sorry-triage
description: Find why an ESS agent says sorry or gives no answer across many conversations, with owner and fix.
---

# ESS sorry triage

You help a team cut the share of "sorry" or no-answer replies from their ESS agent by finding which causes are behind most of them and who can fix each.

## Collect evidence

Ask for one file per failing conversation, uploaded together or as one `.zip`. What to collect depends on the agent type:

- **Custom engine agent (CEA)**, a Copilot Studio agent behind Microsoft 365 Copilot: the Copilot Studio **Analytics > Sessions** download (CSV; one file covers every session of the last 28 days) ranks sorry answers by last topic and outcome. For the cause behind each one, add the full transcripts (Dataverse `conversationtranscript` content, through the Business apps connector or a Power Apps export); one transcript per conversation, or a CSV export with a content column.
- **Declarative agent (DA)**: the files below.

1. **Developer mode cards** (customers can get these themselves): in the Copilot Studio **Test** pane or Microsoft 365 Copilot with the agent selected, send `-developer on`, ask the question that got a sorry in a new chat, open **Raw info** on the card and copy the JSON (or the whole card) into a `.txt` file, then send `-developer off`. Add two lines at the top of the file so the answer stage can be judged:

   ```text
   Question: <what the employee asked>
   Response: <what the agent replied>
   ```

2. **DevUI conversation exports** (Microsoft support only): `conversationHistory-<id>.json` from DevUI; every turn in the export is checked. Only suggest DevUI when the maker says Microsoft support or the ESS team is helping.

Also ask for the agent's **current instructions** so the report can say which implicated rules are already fixed. If the **Business apps (preview)** plugin is connected, read them from Copilot Studio instead of asking the maker to copy them (follow "Read the agent from Copilot Studio" in the `instruction-check` skill) and pass the saved component file with `--instructions`. 10–30 conversations give a reliable picture; a few are enough to start.

## Run

```bash
python3 scripts/essdoctor.py sorry-triage inputs/*.json inputs/*.txt --instructions inputs/current.txt
python3 scripts/essdoctor.py sorry-triage inputs/evidence.zip --instructions inputs/current.txt
python3 scripts/essdoctor.py sorry-triage inputs/sessions.csv inputs/transcripts/*.json   # custom engine agent
```

## Respond

1. Show the report as returned: causes biggest first, the maker-owned share, the instruction rules behind them, every sorry turn, and next steps.
2. Summarize in 3–5 sentences: the share of sorries the maker can fix in instructions, the top cause, and the top rule to fix first.
3. Offer the next actions:
   - Fix the instruction rules with the `instruction-check` skill (`fix`, then `check`).
   - X-ray any single turn in depth with the `conversation-xray` skill.
   - Draft short messages to the knowledge owner (questions where search found nothing relevant, with the queries) and to the connection admin (connector failures, with the HTTP status). Send only if the maker asks.

## Rules

- Exports and cards contain employee questions and identifiers; keep them in this session and don't copy them elsewhere unless asked.
- Don't promise a specific reduction; after fixes, ask the maker to re-run the same questions and triage the new exports to measure it.
