---
name: agent-audit
description: Audit an ESS hub agent and its child agents together and recommend fixes for duplicate knowledge, routing, settings and instructions.
---

# ESS agent audit (hub and child agents)

> Part of ESS Doctor. Findings, causes and instruction text come only from the engine commands below; don't analyse the evidence or edit the engine's output yourself. When the request needs more than this skill, follow the `ess-doctor` skill to plan and chain the others. End the reply with one line, "Produced by ESS Doctor <version> ? <skill>" (skill names only, no commands or brackets), taken from the `<!-- essdoctor: ... -->` line at the end of the engine output; don't show that comment line itself. Separate the engine report, your own summary and next step, and that last line with dividers: a line with only `---`, with a blank line before and after.

You audit a multi-agent ESS deployment, a hub (parent) agent that routes to child agents such as HR, IT and Facilities, and recommend what to change. Use it when a hub is slow or answers inconsistently, before adding another child agent, or as a regular health check. For one conversation, use `conversation-xray`; for one agent's instructions only, use `instruction-check`.

## Collect the agents

Ask for every agent in one message: the hub and each child agent it routes to. How to export depends on the agent type:

- **Declarative agent (DA): ALM export.** In Copilot Studio, open the agent > **Settings** > **ALM** > **Export agent**, and upload the zip. It holds `Plugin/Agents/<agent>/agent.yml` (settings, instructions, knowledge sources, topics) and `app.config.<environment>.json`, whose values (such as the agent name and knowledge source URLs) are filled in before the audit. Export each agent, or upload one zip that holds several.
- **Custom engine agent (CEA): test pane snapshot.** In Copilot Studio, open the agent, open the **Test** pane, ask any question, then select **⋯ > Save snapshot**. The zip's `botContent.yml` holds the same parts. Repeat for each agent, or zip the snapshots together.
- **Also accepted:** an agent's YAML (`botContent.yml`, `agent.yml`, or the instructions component from a solution export). An instructions-only file is audited for instructions and routing, not for knowledge or settings; say so.

Child agents owned by other teams can be left out; the report lists the ones the hub routes to that weren't uploaded.

## Run

```bash
python3 scripts/essdoctor.py audit inputs/hub.zip inputs/hr.zip inputs/it.zip     # DA ALM exports or CEA snapshots
python3 scripts/essdoctor.py audit inputs/agents.zip
```

## What it checks

Across the agents:

| Check | Why it matters |
| --- | --- |
| The hub has knowledge sources or topics that search | The hub can search on every turn before it routes, adding a search round to each request |
| The same knowledge source on the hub and a child, or on two children | The model reads the same content twice; overlapping knowledge blurs routing |
| The same connector or site added twice in one agent | Each copy is searched separately, so the same results come back twice |
| A whole SharePoint site plus pages inside it | The pages are searched twice |
| More than 25 knowledge sources, or generic descriptions | Above 25, generative orchestration filters sources by description; generic ones get filtered out |
| Connected agents receive the full conversation history | More to read on every hand-off, and children can act on requests the hub already handled |
| Connected agent descriptions read like marketing text, or overlap | The description is the main routing signal |
| Hub instructions miss delegation rules | Pass the question verbatim, deliver the child's answer once, don't answer domain questions in the hub |
| A child the hub can't call (connection setting off) | The hand-off fails |
| Content moderation below the ESS template, model settings that differ, general knowledge or web search on | Safety and answers depend on which agent responds |
| Workday user context still on the legacy ISU + RaaS topic | Every new session waits 7–10 seconds before the first answer; the simplified setup makes one `/workers/me` call per session |
| More than 10 knowledge sources and no general search-and-answer topic scoped to the main source | Generative orchestration searches two or three times per question, 10–20 seconds each |
| Two exports of the same agent (for example QA and production) with different topics or knowledge | Latency and quality measured in one environment don't carry over to the other |
| A routing table or topic map used as a knowledge source | Every question costs an extra search before it's routed: single answers took 19–37 seconds in a customer agent, and the same sources on the hub doubled searches and produced duplicate or empty replies |
| A child agent's topic counts attempts with a global variable | Variables don't carry over between hub calls, so the count resets and the topic always acts like the first request |
| A topic description that excludes an audience its condition requires (for example "excluding US" but continues only for US) | The description routes questions, so the right ones never reach the topic |
| More than about 30K characters of instructions across the agent and its topic prompts | Hard to maintain as policies and models change, slower, and less consistent; keep the safeguards and move the rest into an evaluation set |
| Topic conditions with 20 or more literal codes | Every change means editing each topic and copies drift; keep the list in an environment variable |

Each agent's instructions also go through the instruction check against its ESS template (HR, IT or hub).

When the maker says the agent is slow, ask for an export of each environment they compare (for example QA and production) and, if they have one, a test pane snapshot or transcript of a slow question: the X-ray reports the question-to-answer time, the number of knowledge searches in that turn and the session start time. Recommend measuring latency and answer quality separately with the same users and questions; a faster answer isn't always as complete.

## Respond

1. Show the report as returned, as Markdown (not in a code block), so its tables and numbered gaps render. Gaps with a concrete change have a **Proposed change** `diff` block: settings as a `-` old value and `+` new value, knowledge sources to delete as `-` lines, and instruction text to add as `+` lines. Keep those blocks exactly as returned, including the `diff` language tag, so removed lines show in red and added lines in green.
2. Then add **What matters most** in at most three numbered points, ordered by impact on speed and answer quality: name the agent, the gap and the fix. Knowledge duplication and hub searching come before descriptions and settings.
3. Then one **Next step** line offering to:
   - run `instruction-check` (with `fix`) on the agent with the most instruction findings;
   - write the hub's delegation section or the child descriptions as text the maker can paste (draft them, show them as a `diff` block with `+` lines, then run `check` on the hub instructions with the new section);
   - list the knowledge sources to remove or merge, one line each.
4. Don't change any agent yourself. Knowledge sources, descriptions and the "Pass conversation history to this agent" setting are changed in Copilot Studio by the maker.

## Rules

- Snapshots contain agent configuration and may contain internal URLs; use them only to answer the maker and don't copy them elsewhere unless asked.
- Only report what the files show. If a child wasn't uploaded, say it wasn't audited rather than guessing its setup.
- Never replace the report's findings with your own; you may explain them.
