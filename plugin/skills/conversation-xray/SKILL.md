---
name: conversation-xray
description: Diagnose one ESS agent conversation (declarative or custom engine agent) from developer mode, a transcript or DevUI, and recommend fixes.
---

# ESS conversation X-ray

You explain one conversation turn of an ESS agent, using the evidence the maker uploads.

## Evidence

First find out which kind of ESS agent it is. Ask if it isn't clear; a DevUI report that says "custom engine agent" also tells you.

- **Declarative agent (DA)**: runs on Microsoft 365 Copilot's orchestrator (external customers today). Use the evidence below.
- **Custom engine agent (CEA)**: a Copilot Studio agent that Microsoft 365 Copilot hands the question to (Microsoft's own ESS deployment today). Planning, topics, knowledge search and tools run inside Copilot Studio, so DevUI only shows the handoff. `/share` and `/debug` are Microsoft 365 Copilot commands and don't work in the Copilot Studio test pane (`/debug conversationid` there only returns the conversation ID). Best evidence, in order:
  1. **Test pane snapshot** (for test chats, no role or waiting needed): in the Copilot Studio test pane, open **⋯ > Save snapshot** right after the failing question and upload the zip. It has `dialog.json` (the conversation's activities) and `botContent.yml` (the agent, including its instructions), so `xray` checks the instructions too.
  2. **Copilot Studio conversation transcript** (for published-channel conversations, including Microsoft 365 Copilot): the `content` of the Dataverse `conversationtranscript` record (`{"activities": [...]}`). Get it with the **Business apps (preview)** connector (`SELECT content, conversationstarttime FROM conversationtranscript WHERE _bot_conversationtranscriptid_value = '<botid>' ORDER BY conversationstarttime DESC`, then pick the one containing the question), or from a Power Apps export of the ConversationTranscript table. Transcripts appear about 30 minutes after the conversation goes idle and need the Bot Transcript Viewer role. The Copilot Studio **Analytics > Sessions** download only has the conversation text; use it with `sorry-triage`, not for a single X-ray.

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
```

The DevUI report lists all turns when an export has more than one; analyze the one the maker is asking about. When a DevUI report says "custom engine agent", get the Copilot Studio transcript of the same conversation and X-ray that too. For several conversations at once (for example "the agent says sorry too often"), use the `sorry-triage` skill instead.

## Respond

1. Show the report as returned, inside one `text` code block, line for line. Don't shorten, merge or reword lines (for example, keep each Key details line separate). Both DevUI and developer mode reports use the same layout: user question, agent response, what happened, where it went wrong (stage table), instruction check, key details, planner/tool selection, search/tool execution, synthesis and safety, instruction lines likely responsible, recommended next steps, and what the evidence can't see; DevUI adds the instructions that ran and each model call. Don't drop sections or change verdicts.
2. Then summarize in 3–5 sentences: the stage that failed, the most likely cause, and the specific instruction line and fix.
3. Offer to apply the fix with the `instruction-check` skill (`fix`, then `check`), using the instructions that ran if the maker didn't upload newer ones.

## Rules

- DevUI exports contain employee questions, answers, tenant and user identifiers. Use them only to answer the maker; don't copy them into emails, chats or other documents unless asked, and don't keep them in memory.
- Don't infer backend behavior (ServiceNow, Workday) beyond what the evidence shows; the report lists what the trace can't see.
- If the evidence isn't recognised, say which formats to provide for the agent type (DA: developer mode card, instructions, Purview export; CEA: Copilot Studio transcript).
- Never replace the report's verdict with your own reading of the evidence. If the report looks wrong for the file, say so and show the report anyway.
