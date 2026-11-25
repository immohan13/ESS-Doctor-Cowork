---
name: topic-generator
description: Generate a Copilot Studio topic for an ESS agent the way the ESS templates build them - Workday, ServiceNow HRSD/ITSM, SAP SuccessFactors, knowledge-only or HTTP - with its ESS template configuration, a matching agent skill (SKILL.md) for when topics become skills, and evaluation questions. Also checks existing topics against the same rules.
---

# ESS topic generator

> Part of ESS Doctor. Findings, causes and topic text come only from the engine commands below; don't write or edit topic YAML, template configuration or SKILL.md yourself. When the request needs more than this skill, follow the `ess-doctor` skill to plan and chain the others. End the reply with one line, "Produced by ESS Doctor <version> · <skill>" (skill names only, no commands or brackets), taken from the `<!-- essdoctor: ... -->` line at the end of the engine output; don't show that comment line itself. Separate the engine report, your own summary and next step, and that last line with dividers: a line with only `---`, with a blank line before and after.

You turn a maker's scenario ("let employees see their education history", "open a payroll case", "managers see their team's cost centers") into a topic that works like the ESS template topics, and you check existing topics against the same rules.

The topic and the skill are generated from one spec, so the same scenario can be a topic today and an agent skill later without changing what employees see.

## Collect the scenario

Ask only for what's missing, in **one** message, and propose values so the maker can just confirm. Save the answers to `inputs/topic_spec.json`:

| Field | What it is |
| --- | --- |
| `name` | Short action, for example `Get education`, `Create payroll case`, `Team cost centers` |
| `backend` | `workday`, `servicenow-hrsd`, `servicenow-itsm`, `successfactors`, `knowledge` or `http` |
| `purpose` | What the topic answers or does, in a few words ("their education history (school, degree, field of study)") |
| `operation` | `read` (default), `create` or `update` |
| `audience` | `employee` (default) or `manager` (the manager's direct reports only) |
| `persona` | `hr` (default) or `it` |
| `agent_type` | `cea` (custom engine agent, default) or `da` (declarative agent); from the plan or the maker |
| `triggers` | 3–6 short questions employees ask for this scenario |
| `not_for` | What the topic must not handle: another person's data, the neighbouring read or write scenario, general policy questions |
| `inputs` | Details to collect, each `{"name", "type": text/date/number/boolean/choice, "prompt", "choices", "required", "source"}`; `source` is the backend field or parameter name |
| `outputs` | Fields to show, each `{"name", "source"}` |
| `options` | Backend details: Workday `service`, `operation`, `version`; ServiceNow `table`, `defaults` (fixed field values); SuccessFactors `entity`, `filter`; knowledge `sources` (knowledge source schema names); HTTP `url`, `method` |

Don't ask for or accept an employee ID input: the topic always uses the signed-in user from the user context. If the maker doesn't know the Workday operation, ServiceNow table or SuccessFactors entity, generate anyway; the report lists what to fill in.

## Run

```bash
python3 scripts/essdoctor.py topic-gen inputs/topic_spec.json --out outputs/topic
python3 scripts/essdoctor.py topic-check inputs/existing_topic.yaml        # one topic from the code editor
python3 scripts/essdoctor.py topic-check inputs/snapshot.zip               # every topic in a snapshot or bot content export
```

`topic-gen` writes `outputs/topic/topic.yaml`, the template configuration (`msdyn_….xml` for Workday, `msdyn_employeeselfservicetemplateconfig.xml` for ServiceNow and SuccessFactors), `SKILL.md` and `evalset.csv`, and checks the topic it wrote. A line starting with `ERROR:` means the spec is missing something; ask the maker for exactly that.

## Respond

For `topic-gen`:

1. Show the report's summary table as returned.
2. Show the **Topic** section with its `yaml` code block unchanged, so the maker copies it with the code block's **Copy** button into the topic's code editor. Never send them to the file preview.
3. Show the **Template configuration** section with its code block unchanged, and the one line on where to add it.
4. Show **Check before importing** as a short checklist (`- [ ]`): these are the values only the maker can confirm (Workday operation, ServiceNow table, knowledge sources, the system-topic prefix of their agent).
5. Say in one line that `SKILL.md` has the same scenario as an agent skill, and share `evalset.csv` with the four import steps.
6. End with **What next? Reply with one of these:** "generate another topic", "check my existing topics" (upload a snapshot or bot content export), or "make it a declarative agent topic" (re-run with `--agent-type da`).

For `topic-check`, show the report as returned (Markdown table), then at most three bullets on what to fix first. Offer to regenerate a flagged topic with `topic-gen` from the same scenario.

## Rules

- Every generated topic follows the topic rules (`python3 scripts/essdoctor.py rules` lists them): a routing description that says whose data it is and what it must not handle, at least 3 trigger phrases, one question per missing detail, a confirmation card (Adaptive Card 1.5, Cancel with `associatedInputs: "none"`) before any write, the signed-in user from `Global.ESS_UserContext_*`, a success and a failure branch, no hard-coded tenant URLs or secrets, and, for declarative agents, an explicit send of the generated answer.
- The scenario name in the topic and in the template configuration must match; the engine sets both. Don't rename one without the other.
- The topic calls the ESS system topics of the agent's solution (`msdyn_copilotforemployeeselfservicehr…` for a custom engine agent, `…dahr…` for a declarative agent). If the maker's agent uses another prefix, tell them to change it in the topic's `dialog:` lines.
- These rules are ESS field guidance from the ESS templates, the public ESS samples and reviews of ESS agents, not Copilot Studio product documentation. Test the topic in the test pane and with the evaluation set before publishing.
