---
name: conversation-xray
description: Diagnose one ESS agent conversation (declarative or custom engine agent) from developer mode, a transcript or DevUI, and recommend fixes.
---

# ESS conversation X-ray

> Part of ESS Doctor. Findings, causes and instruction text come only from the engine commands below; don't analyse the evidence or edit the engine's output yourself. When the request needs more than this skill, follow the `ess-doctor` skill to plan and chain the others. End the reply with one line, "Produced by ESS Doctor <version> ? <skill>" (skill names only, no commands or brackets), taken from the `<!-- essdoctor: ... -->` line at the end of the engine output; don't show that comment line itself. Separate the engine report, your own summary and next step, and that last line with dividers: a line with only `---`, with a blank line before and after.

You explain one conversation turn of an ESS agent, using the evidence the maker uploads.

## Evidence

First find out which kind of ESS agent it is. Ask if it isn't clear; a DevUI report that says "custom engine agent" also tells you.

- **Declarative agent (DA)**: runs on Microsoft 365 Copilot's orchestrator (external customers today). Use the evidence below.
- **Custom engine agent (CEA)**: a Copilot Studio agent that Microsoft 365 Copilot hands the question to (Microsoft's own ESS deployment today). Planning, topics, knowledge search and tools run inside Copilot Studio, so DevUI only shows the handoff. `/share` and `/debug` are Microsoft 365 Copilot commands and don't work in the Copilot Studio test pane (`/debug conversationid` there only returns the conversation ID). Best evidence, in order:
  1. **Test pane snapshot** (for test chats, no role or waiting needed): in the Copilot Studio test pane, open **⋯ > Save snapshot** right after the failing question and upload the zip. It has `dialog.json` (the conversation's activities) and `botContent.yml` (the agent, including its instructions), so `xray` checks the instructions too.
  2. **Copilot Studio conversation transcript** (for published-channel conversations, including Microsoft 365 Copilot): the `content` of the Dataverse `conversationtranscript` record (`{"activities": [...]}`). Get it with the **Business apps (preview)** connector (`SELECT content, conversationstarttime FROM conversationtranscript WHERE _bot_conversationtranscriptid_value = '<botid>' ORDER BY conversationstarttime DESC`, then pick the one containing the question), or from a Power Apps export of the ConversationTranscript table. When the maker just names the agent and question, use the `transcript-xray` skill, which does this lookup for them. Transcripts appear about 30 minutes after the conversation goes idle and need the Bot Transcript Viewer role. The Copilot Studio **Monitor > Sessions** download (CSV with a `ChatTranscript` column) only has the conversation text: run `xray` on it as is (never convert it into transcripts or build activities yourself). It returns a Markdown **Sessions X-ray** with a verdict per session (Fallback / no topic matched, generic answer because employee details were missing, declined, error) and fixes; show it as Markdown, not in a code block.
  3. **Application Insights logs** (when the maker has a conversation ID and the agent sends telemetry to Application Insights): use the `conversation-logs` skill, which writes the query and X-rays the exported CSV. It needs the Reader role on the Application Insights resource instead of transcript access, and includes tool arguments and results.

For declarative agents, customer makers can get the first three themselves. DevUI is a Microsoft-internal tool: only suggest it when the maker says Microsoft support or the ESS team is helping.

1. **Developer mode card** (start here): open the agent in the Copilot Studio **Test** pane (or in Microsoft 365 Copilot with the agent selected), send `-developer on`, ask the failing question in a new chat, then open **Raw info** on the card and copy the JSON (or copy the whole card). Send `-developer off` when done. Both the JSON card (`capabilitiesDeveloperInfo`, `pluginDeveloperInfo`, `agentMetaData`) and the text card work. The card doesn't contain the question or the answer, so always pass them with `--question` and `--response` (from the chat or from `Question:` / `Response:` lines at the top of the file). No card means Copilot answered without the agent's knowledge or actions (itself a finding) or the request was throttled. The report diagnoses tool selection, each action's HTTP status and latency (401/403/404/429/5xx, timeouts), knowledge searches with zero results, searches that returned documents unrelated to the question, and "no function selected".
2. **The agent's instructions** (needed to name the instruction line): copy them from Copilot Studio (**Overview > Instructions**) or read them through the Business apps connector (below), and pass them with `--instructions`.
3. **Purview audit export**: a compliance admin searches the Purview audit log for `CopilotInteraction` records for the agent and time window and exports CSV. Useful to see which agent version ran when and which resources were accessed.
4. **DevUI conversation export** (Microsoft support only): `conversationHistory-<id>.json` from DevUI. It contains the instructions that ran, tools offered and called, every model call, the search queries and funnel, and safety classifiers. Upload it as a file; it's often several MB.

If the **Business apps (preview)** plugin is connected, read the instructions from Copilot Studio instead of asking the maker to copy them; follow "Read the agent from Copilot Studio" in the `instruction-check` skill and pass the saved component file with `--instructions`. Agents stored in Copilot Studio's newer backend (the agent URL contains `agentBackend=cosmos`) aren't in Dataverse, so the connector can't read them; ask the maker to copy the instructions.

## Run

Save pasted evidence to a file first (for example `inputs/devmode.txt`).

```bash
python3 scripts/essdoctor.py xray inputs/conversationHistory.json --instructions inputs/current.txt
python3 scripts/essdoctor.py xray inputs/conversationHistory.json --question "relocation"   # pick a turn by question text
python3 scripts/essdoctor.py xray inputs/conversationHistory.json --turn 2                   # pick a turn by number
python3 scripts/essdoctor.py xray inputs/audit.csv --agent "Employee Self-Service"
python3 scripts/essdoctor.py xray inputs/devmode.txt --question "<employee question>" --response "<agent answer>" --instructions inputs/current.txt
python3 scripts/essdoctor.py xray inputs/snapshot.zip --question "<employee question>"         # custom engine agent: test pane snapshot
python3 scripts/essdoctor.py xray inputs/transcript.json --question "<employee question>"   # custom engine agent: Copilot Studio transcript
python3 scripts/essdoctor.py xray "inputs/Sessions.csv"                                    # custom engine agent: Monitor > Sessions download (all sessions)
```

The DevUI report lists all turns when an export has more than one; analyze the one the maker is asking about. When a DevUI report says "custom engine agent", get the Copilot Studio transcript of the same conversation and X-ray that too. For several conversations at once (for example "the agent says sorry too often"), use the `sorry-triage` skill instead.

## Respond

A **Sessions X-ray** (output starting with `## Sessions X-ray`) is Markdown: show it as returned, not in a code block, keeping its `diff` block, then summarize in at most three bullets. If it has any ❌ or ⚠️ session, also run the sorry triage as described in "When the answer was a sorry" in the `transcript-xray` skill. For a single conversation whose answer was a sorry, do the same when you have other conversations of the same agent (several uploaded files, or transcripts read through the connector). The steps below are for single-conversation reports.

1. Show the report as returned, inside one `text` code block, line for line. Don't shorten, merge or reword lines (for example, keep each Key details line separate). Both DevUI and developer mode reports use the same layout: user question, agent response, what happened, where it went wrong (stage table), instruction check, key details, planner/tool selection, search/tool execution, synthesis and safety, instruction lines likely responsible, recommended next steps, and what the evidence can't see; DevUI adds the instructions that ran and each model call. Don't drop sections or change verdicts. If the output continues after the code block with **Proposed instruction changes**, show that part as Markdown right after the code block, keeping each `diff` block exactly as returned (removed lines `-` in red, added lines `+` in green, with the `diff` language tag) so the colors render.
2. Then summarize in 3–5 sentences: the stage that failed, the most likely cause, and the specific instruction line and fix.
3. Draft the instruction fix without waiting to be asked, whenever the report's recommended next steps add or change an instruction and you have the instructions (uploaded by the maker, in the snapshot's `botContent.yml`, or the instructions that ran in a DevUI export):
   - **New rule** (for example "Add an instruction for when a profile value is blank" or "Keep personal details out of search queries"): use the `instruction-draft` skill. Fill `inputs/intake.json` from the report instead of asking the maker again: the question becomes `should_affect`, pick one unrelated question for `must_not_affect`, and use the maker's or report's words for `intent`. Blank profile values and personal details in search queries map to `change_type` `context` (`subject`: the profile fields, `scope`: the kind of questions); a restriction maps to `restricted`, a source rule to `routing`.
   - **Existing line flagged** under "Instruction lines likely responsible": use the `instruction-check` skill (`fix`, then `check`).
   - Show the draft's or fix's **What changed** `diff` blocks as returned, so the maker sees the removed (red) and added (green) lines. For copying, also show the added text in one `text` code block (or hand over the fixed instructions file) and tell the maker to copy it with the code block's Copy button and paste it into the named section in Copilot Studio, then publish; the `+` and `-` markers in a diff are not part of the instructions. Share the `outputs/evalset.csv` the draft or fix wrote, so the maker can import it in Copilot Studio **Evaluation** and test the change.
4. Say clearly which fixes are not instruction changes and the maker has to do in Copilot Studio, such as setting up the "[Admin] - User Context - Setup" topic, connections or knowledge sources. Don't draft instructions for those.
5. The report also flags patterns found in customer agents; follow its next steps for each: the employee's name at the start of a delegated task (a hub instruction to pass the question unchanged, not a long privacy section), the same answer sent twice (run `agent-audit` on hub and children), answers telling employees to call HR (offer an `instruction-draft` escalation change if the maker wants the agent to help first), and a figure withheld although the source states it (`instruction-check`, then `fix`).

## Rules

- DevUI exports contain employee questions, answers, tenant and user identifiers. Use them only to answer the maker; don't copy them into emails, chats or other documents unless asked, and don't keep them in memory.
- Don't infer backend behavior (ServiceNow, Workday) beyond what the evidence shows; the report lists what the trace can't see.
- If the evidence isn't recognised, say which formats to provide for the agent type (DA: developer mode card, instructions, Purview export; CEA: Copilot Studio transcript).
- Never replace the report's verdict with your own reading of the evidence. If the report looks wrong for the file, say so and show the report anyway.
