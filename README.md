# ESS Doctor for Copilot Cowork

ESS Doctor diagnoses Employee Self-Service (ESS) agent problems and recommends fixes. It finds why the agent says sorry, takes the wrong path, misses knowledge or hits connector failures, and points to the instruction lines, knowledge sources or connections to fix.

It runs as a Microsoft Copilot Cowork plugin. Makers with Cowork upload their agent instructions, a DevUI export, developer mode cards, an evaluation CSV or setup answers, and get a report.

## Skills

| Skill | What it does |
| --- | --- |
| `conversation-xray` | Explain one conversation from a DevUI export, a developer mode card or a Purview audit export: tool identification, tool execution, search, summary and recommended fixes |
| `sorry-triage` | Rank why the agent says sorry across many conversations, with an owner and fix per cause |
| `instruction-check` | Check instructions against the ESS template, known issues and authoring practices; show what a change broke; apply safe fixes; shorten to 8,000 characters |
| `instruction-draft` | Draft a change (terminology, restriction, format, routing, escalation, persona, disclaimer) in the right section, then check it |
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

## Limits

- Skill names must be kebab-case and match their folder; `build.py` checks this.
- Each skill allows 20 companion files, 5 MB each and 10 MB in total; `build.py` fails if a skill goes over.
- `SKILL.md` descriptions are at most 1,024 characters.
- The engine makes no network calls and never changes an agent. Makers apply the recommended fixes in Copilot Studio.
- Conversation exports contain employee questions and identifiers. Keep them out of this repo; `.gitignore` blocks the common file names.
