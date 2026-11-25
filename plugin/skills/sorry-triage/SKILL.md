---
name: sorry-triage
description: Find why an ESS agent says sorry or gives no answer across many conversations, with owner and fix.
---

# ESS sorry triage

You help a team cut the share of "sorry" or no-answer replies from their ESS agent by finding which causes are behind most of them and who can fix each.

## Collect evidence

Upload the files together or as one `.zip` (zips of zips are fine). First find out which kind of ESS agent it is; what to collect depends on it.

**Custom engine agent (CEA)**, a Copilot Studio agent behind Microsoft 365 Copilot. Best first:

1. **Monitor > Sessions download**: in Copilot Studio open the agent > **Monitor** > **Sessions** > **Download Sessions** (CSV; up to 28 days, refreshed every 15 minutes). One file covers every session and ranks the sorry answers by last topic and outcome. Bot replies are cut at 512 characters, so it shows _that_ the agent said sorry more than _why_.
2. **Transcripts** of those conversations, for the cause behind each one: with the **Business apps (preview)** connector, read them yourself as in the `transcript-xray` skill (keep the rows whose question matches the sorry sessions, up to 30, and save them as one JSON file), or use a Power Apps export of the ConversationTranscript table. Needs the Bot Transcript Viewer role.
3. **Test pane snapshots** (`.zip`, ⋯ > Save snapshot in the test pane) of questions the maker can reproduce. They add the agent's setup, so the report can spot problems such as an empty user context topic.
4. **Application Insights export** (agent-level telemetry, see `conversation-logs`) if the agent sends telemetry: one CSV can hold many conversations; each is checked separately.

**Declarative agent (DA)**:

1. **Developer mode cards** (customers can get these themselves): in the Copilot Studio **Test** pane or Microsoft 365 Copilot with the agent selected, send `-developer on`, ask the question that got a sorry in a new chat, open **Raw info** on the card and copy the JSON (or the whole card) into a `.txt` file, then send `-developer off`. Add two lines at the top of the file so the answer stage can be judged:

   ```text
   Question: <what the employee asked>
   Response: <what the agent replied>
   ```

2. **DevUI conversation exports** (Microsoft support only): `conversationHistory-<id>.json` from DevUI; every turn in the export is checked. Only suggest DevUI when the maker says Microsoft support or the ESS team is helping.

Also ask for the agent's **current instructions** so the report can name the rules behind the declines (a snapshot already contains them). If the **Business apps (preview)** plugin is connected, read them from Copilot Studio instead of asking the maker to copy them (follow "Read the agent from Copilot Studio" in the `instruction-check` skill) and pass the saved component file with `--instructions`. 10–30 conversations give a reliable picture; a few are enough to start.

## Run

```bash
python3 scripts/essdoctor.py sorry-triage inputs/*.json inputs/*.txt --instructions inputs/current.txt   # developer mode cards, DevUI
python3 scripts/essdoctor.py sorry-triage inputs/evidence.zip --instructions inputs/current.txt
python3 scripts/essdoctor.py sorry-triage inputs/sessions.csv inputs/transcripts.json inputs/*.zip     # custom engine agent: sessions, transcripts, snapshots
python3 scripts/essdoctor.py sorry-triage inputs/appinsights.csv                                       # Application Insights export
```

## Respond

1. Show the report as returned: causes biggest first, the maker-owned share, the instruction rules behind them, every sorry turn, next steps and, when the instructions were provided, **Proposed instruction changes**. Keep each `diff` block exactly as returned (removed lines `-` in red, added lines `+` in green, with the `diff` language tag) so the colors render.
2. Summarize in 3–5 sentences: the share of sorries the maker can fix in instructions, the top cause, and the top rule to fix first.
3. Offer the next actions:
   - Fix the instruction rules with the `instruction-check` skill (`fix`, then `check`).
   - X-ray any single turn in depth with the `conversation-xray` skill.
   - Draft short messages to the knowledge owner (questions where search found nothing relevant, with the queries) and to the connection admin (connector failures, with the HTTP status). Send only if the maker asks.

## Rules

- Exports and cards contain employee questions and identifiers; keep them in this session and don't copy them elsewhere unless asked.
- Don't promise a specific reduction; after fixes, ask the maker to re-run the same questions and triage the new exports to measure it.
