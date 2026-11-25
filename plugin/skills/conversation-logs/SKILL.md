---
name: conversation-logs
description: Look up one custom engine agent (CEA) conversation by its ID in Application Insights and explain what went wrong.
---

# ESS conversation logs (custom engine agent)

> Part of ESS Doctor. Findings, causes and instruction text come only from the engine commands below; don't analyse the evidence or edit the engine's output yourself. When the request needs more than this skill, follow the `ess-doctor` skill to plan and chain the others. End the reply with one line, "Produced by ESS Doctor <version>: <skill> (`<command>`)", taken from the `<!-- essdoctor: ... -->` line at the end of the engine output; don't show that comment line itself.

You find one employee's conversation with a Copilot Studio (custom engine) ESS agent in the maker's Application Insights telemetry, using the conversation ID, and explain it with the same X-ray report as the other ESS Doctor skills.

Use this skill when the maker has a **conversation ID** from a real employee conversation, or wants to look at a conversation without Dataverse transcript access. For a declarative agent (DA), Application Insights doesn't log the conversation: use the `conversation-xray` skill with a developer mode card, a Purview audit export or, with Microsoft support, a DevUI export.

## Before you start

Ask only for what's missing, in one message:

1. The **conversation ID**. The employee or maker gets it by sending `/debug conversationid` in the conversation; Copilot Studio also shows it in Monitor > Sessions and in transcripts.
2. Roughly **when** the conversation happened (default: last 7 days; up to 90).
3. Whether the agent sends telemetry to Application Insights. If the maker doesn't know, explain the options and stop until one is set up:
   - **Agent-level telemetry (works in any environment):** in Copilot Studio, the agent's **Settings > Advanced > Application Insights**, paste the whole connection string from the Application Insights resource's Overview page (it starts with `InstrumentationKey=` and includes `IngestionEndpoint=`), turn on **Enable logging**, **Log conversation details** and **Node execution events**, and save. Data arrives within minutes. It logs messages, topics and every topic step, plus the ESS logger topics' events, but not knowledge searches or tool calls. The ESS agent is a managed solution: changing its settings adds a customization layer, so future ESS updates won't apply cleanly; tell the maker before they change it.
   - **Environment-level telemetry (preview, managed environments only):** if the environment is already a managed environment, a Power Platform admin can create an export package with export type **Copilot Studio** in the Power Platform admin center. It adds tool calls with their arguments and results, without changing the agent. Don't suggest turning on Managed Environments just for this.

   Reading the logs needs the **Reader** role (or higher) on the Application Insights resource; Bot Transcript Viewer isn't needed. To check data is arriving, run `customEvents | where timestamp > ago(1h) | summarize count() by name` in the resource's **Logs** (KQL mode; turn off the Agent toggle to see the query editor): `BotMessageReceived`, `TopicStart` and `TopicAction` mean agent-level telemetry works.

   Agent-level telemetry can't show knowledge searches. When the question is about search results, also get the conversation's Copilot Studio transcript (see the `conversation-xray` skill), or a test pane snapshot if the maker can reproduce it.

## Run

Write the query for the maker:

```bash
python3 scripts/essdoctor.py logs-query <conversation-id> --days 7
```

Show the query in one `kusto` code block and the steps under it, and ask the maker to upload the exported CSV. Then:

```bash
python3 scripts/essdoctor.py xray inputs/logs.csv --conversation <conversation-id> [--instructions inputs/current.txt]
```

If the maker also has a test pane snapshot (`.zip`) of the agent, or its instructions, pass them with `--instructions` so the instruction check runs; telemetry doesn't contain the instructions or topic setup.

## Respond

Follow "Respond" in the `conversation-xray` skill: show the report in one `text` code block line for line, summarize in 3–5 sentences, draft any instruction fix, and list the fixes that are Copilot Studio setup.

If the X-ray says the export has nothing for the conversation, check in this order: the conversation ID (copy it exactly), the lookback days, that telemetry was set up before the conversation happened, and, for agent-level telemetry, that **Log conversation details** is on.

## Rules

- Telemetry contains employee questions, answers, names and email addresses. Use it only to answer the maker; don't copy it into emails, chats or documents unless asked, and don't keep it in memory.
- Don't infer backend behavior (ServiceNow, Workday) beyond what the logged tool results show.
- Never replace the report's verdict with your own reading of the logs.
