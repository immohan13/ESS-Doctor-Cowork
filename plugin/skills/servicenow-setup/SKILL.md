---
name: servicenow-setup
description: Check the ServiceNow Knowledge connector setup for an ESS agent and give each admin their fixes.
---

# ServiceNow Knowledge setup check

> Part of ESS Doctor. Findings, causes and instruction text come only from the engine commands below; don't analyse the evidence or edit the engine's output yourself. When the request needs more than this skill, follow the `ess-doctor` skill to plan and chain the others. End the reply with one line, "Produced by ESS Doctor <version> · <skill>" (skill names only, no commands or brackets), taken from the `<!-- essdoctor: ... -->` line at the end of the engine output; don't show that comment line itself. Separate the engine report, your own summary and next step, and that last line with dividers: a line with only `---`, with a blank line before and after.

You run the setup checklist conversationally: collect answers, run the engine, show the result, and ask the next questions it returns, until every step is done or assigned to someone.

## Answers

Keep the answers in `inputs/servicenow_answers.json` (a flat JSON object) and update it after each reply. Values can be `true`/`false`, `"yes"`/`"no"`, numbers or short text. Keys:

| Owner | Keys |
| --- | --- |
| ServiceNow admin | `serviceAccountTables` (yes, or the missing tables), `sysPropertiesReadable`, `userCriteriaReadable`, `hrScope`, `hrScopeRole` (role name, `none` or `no HR scope`), `sncReadOnly`, `oauthRegistry`, `oauthRedirectUrl`, `refreshTokenLifespan`, `usesUserCriteria`, `advancedCriteria`, `scriptedRestResources`, `identitySync`, `sso`, `connectorChecker` |
| Microsoft 365 Search admin | `connectorState`, `connectorAuth`, `flow` (`simple` or `advanced`), `indexedCount`, `expectedCount`, `limitedAudience`, `testersInAudience` |
| Microsoft Entra admin | `identityMapping`, `oauthConsent` |
| ESS agent maker | `knowledgeAdded`, `testQuery` |

Start with `{}` if nothing is known yet.

## Run

```bash
python3 scripts/essdoctor.py setup-servicenow --answers inputs/servicenow_answers.json
```

## Respond

1. Show the report as returned: summary, "Can fail silently", the steps table, a checklist card per role, and "Next questions".
2. Ask the next questions (at most five, highest severity first). When the maker answers, update the JSON and run again.
3. When a step belongs to another admin, offer to draft a short message to that person with their card's items and the linked Microsoft Learn steps. Send it only if the maker asks.

## Rules

- Treat **SN-04** (HR Core scope role) as urgent when it isn't confirmed: without it, HR-scoped articles can be visible to everyone.
- Don't guess ServiceNow configuration; the checklist text and its Microsoft Learn links are the source of truth.
- Never ask for passwords, client secrets or tokens. Answers are yes/no facts about the setup.
