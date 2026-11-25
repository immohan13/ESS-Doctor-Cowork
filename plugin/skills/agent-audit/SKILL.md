---
name: agent-audit
description: Audit an ESS hub agent and its child agents together and recommend fixes for duplicate knowledge, routing, settings and instructions.
---

# ESS agent audit (hub and child agents)

You audit a multi-agent ESS deployment, a hub (parent) agent that routes to child agents such as HR, IT and Facilities, and recommend what to change. Use it when a hub is slow or answers inconsistently, before adding another child agent, or as a regular health check. For one conversation, use `conversation-xray`; for one agent's instructions only, use `instruction-check`.

## Collect the agents

Ask for every agent in one message: the hub and each child agent it routes to.

- **Best: a test pane snapshot of each agent.** In Copilot Studio, open the agent, open the **Test** pane, ask any question, then select **⋯ > Save snapshot**. The zip's `botContent.yml` holds the agent's settings, instructions, knowledge sources, topics and connected agents. Repeat for each agent, or zip the snapshots together.
- **Also accepted:** an agent's YAML (`botContent.yml`, or the instructions component from a solution or ALM export). An instructions-only file is audited for instructions and routing, not for knowledge or settings; say so.

Child agents owned by other teams can be left out; the report lists the ones the hub routes to that weren't uploaded.

## Run

```bash
python3 scripts/essdoctor.py audit inputs/hub.zip inputs/hr.zip inputs/it.zip
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

Each agent's instructions also go through the instruction check against its ESS template (HR, IT or hub).

## Respond

1. Show the report as returned, as Markdown (not in a code block), so its tables and numbered gaps render.
2. Then add **What matters most** in at most three numbered points, ordered by impact on speed and answer quality: name the agent, the gap and the fix. Knowledge duplication and hub searching come before descriptions and settings.
3. Then one **Next step** line offering to:
   - run `instruction-check` (with `fix`) on the agent with the most instruction findings;
   - write the hub's delegation section or the child descriptions as text the maker can paste (draft them, then run `check` on the hub instructions with the new section);
   - list the knowledge sources to remove or merge, one line each.
4. Don't change any agent yourself. Knowledge sources, descriptions and the "Pass conversation history to this agent" setting are changed in Copilot Studio by the maker.

## Rules

- Snapshots contain agent configuration and may contain internal URLs; use them only to answer the maker and don't copy them elsewhere unless asked.
- Only report what the files show. If a child wasn't uploaded, say it wasn't audited rather than guessing its setup.
- Never replace the report's findings with your own; you may explain them.
