# ESS Doctor for Copilot Cowork

ESS Doctor diagnoses Employee Self-Service (ESS) agent problems and recommends fixes. It finds why the agent says sorry, takes the wrong path, misses knowledge or hits connector failures, and points to the instruction lines, knowledge sources or connections to fix.

It runs as a Microsoft Copilot Cowork plugin. Makers with Cowork upload their agent instructions, a DevUI export, developer mode cards, an evaluation CSV or setup answers, and get a report.

## Skills

| Skill | What it does |
| --- | --- |
| `conversation-xray` | Explain one conversation of a declarative agent (developer mode card, Purview audit export, DevUI export) or a custom engine agent (test pane snapshot, Copilot Studio transcript, Application Insights logs, DevUI handoff): tool identification, tool execution, search, summary and recommended fixes, then draft the instruction fix |
| `conversation-logs` | Look up a custom engine agent conversation by its ID in Application Insights: writes the query, then X-rays the exported CSV |
| `transcript-xray` | "X-ray my last ESS CEA conversation about leave policy": finds the agent's transcript through the Business apps connector and X-rays it, no export needed |
| `sorry-triage` | Rank why the agent says sorry across many conversations (developer mode cards, Copilot Studio sessions download and transcripts, DevUI exports), with an owner and fix per cause |
| `instruction-check` | Check instructions against the ESS template, known issues and authoring practices; show what a change broke; apply safe fixes; shorten to 8,000 characters |
| `instruction-draft` | Draft a change (terminology, restriction, format, routing, escalation, persona, disclaimer, employee context) in the right section, then check it |
| `eval-triage` | Explain failed evaluation questions and compare two runs |
| `servicenow-setup` | ServiceNow Knowledge connector setup checklist with silent-failure risks and per-role to-dos |

## Repository layout

| Path | Contents |
| --- | --- |
| `plugin/` | Microsoft 365 app manifest, icons and the six `skills/*/SKILL.md` |
| `engine/ess_maker/doctor/` | Diagnostics engine (standard library only) |
| `essdoctor_cli.py` | Command-line entry point the skills run |
| `templates/` | ESS HR, IT and Hub instruction templates used as the baseline |
| `samples/` | Sample reports and synthetic Contoso inputs to share with makers |
| `docs/reduce-sorry-playbook.md` | Playbook for bringing down sorry and no-answer replies |
| `docs/devui-vs-developer-mode.md` | Developer mode setup for makers, what developer mode, DevUI and Purview each show for declarative agents, and which evidence to use for custom engine agents (test pane snapshot, transcript, Application Insights) |
| `tests/` | Engine tests |

The engine is a standalone copy. The ESS Doctor web app and the Copilot Studio ESS Doctor solution are maintained separately.

## Build

Python 3.11 or later, no packages needed:

```powershell
python build.py
```

Output in `dist/` (git-ignored):

- `dist/ess-doctor-cowork-plugin.zip`: the file to upload to Cowork
- `dist/ess-doctor/`: the unpacked plugin
- `dist/essdoctor.pyz`: the engine, runnable locally

Try the engine locally:

```powershell
python dist\essdoctor.pyz check my_instructions.txt --previous previous_instructions.txt
python dist\essdoctor.pyz xray conversationHistory-<id>.json --instructions my_instructions.txt
python dist\essdoctor.pyz sorry-triage exports.zip --instructions my_instructions.txt
python dist\essdoctor.pyz --help
```

Run the tests:

```powershell
$env:PYTHONPATH = "engine"; python -m pytest tests
```

## Install in Cowork

- **Just you or a few makers:** in Copilot, open **Plugins** > **Cowork** > **Add** > **Upload**, choose `ess-doctor-cowork-plugin.zip`, then publish to **Only you** or **Specific users**.
- **Your organization:** an admin uploads the package in the Microsoft 365 admin center or publishes it through the Microsoft 365 App Store.
- **Updating:** delete the previous version before uploading a new one, or Cowork keeps both.

Custom plugins don't run in Cowork on mobile.

## Read agents directly from Copilot Studio

Makers who add Microsoft's **Business apps (preview)** plugin in Cowork (Plugins > search "Business apps" > Install > Connect; one-time sign-in, no admin setup) don't need to copy instructions. The `instruction-check`, `conversation-xray` and `sorry-triage` skills read the agent's instructions component (`botcomponent`, `componenttype` 15) from its Power Platform environment with read-only queries, save the `data` value, and pass it to the engine, which numbers lines the way Copilot Studio shows them.

With the maker's approval, `instruction-check` can write a fix back: `essdoctor.py component-update` replaces only the instructions block of the component, Cowork asks before the write, and the skill reads the record back to confirm. Publishing uses the Dataverse `PvaPublish` action when the connector offers it; otherwise the maker selects Publish in Copilot Studio.

Limits: the connector only reaches environments it lists for the signed-in user; if the agent's environment isn't found, makers paste the instructions instead. Agents built outside Copilot Studio (Agents Toolkit, Agent Builder) aren't in Dataverse; makers upload the app package or paste the instructions.

## Limits

- Skill names must be kebab-case and match their folder; `build.py` checks this.
- Each skill allows 20 companion files, 5 MB each and 10 MB in total; `build.py` fails if a skill goes over.
- `SKILL.md` descriptions are at most 1,024 characters.
- The engine makes no network calls and never changes an agent. Makers apply the recommended fixes in Copilot Studio.
- Conversation exports contain employee questions and identifiers. Keep them out of this repo; `.gitignore` blocks the common file names.
