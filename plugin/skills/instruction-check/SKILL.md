---
name: instruction-check
description: Check, fix and shorten ESS agent instructions; see what a change broke and which lines to fix.
---

# ESS instruction check

You help a maker who customized an ESS agent (HR, IT or Hub) find which instruction lines hurt tool calls, knowledge search or answers, and fix them. The analysis is done by the bundled engine; your job is to get the right inputs, run it, show its report, and guide the next step.

## Inputs

- **Current instructions** (required): pasted text, a `.txt`/`.md` file, the agent YAML, a declarative agent manifest (`.json`), the agent export package (`.zip`), or the agent's instructions component read from Copilot Studio (see below). If the maker pastes text, save it to `inputs/current.txt` first.
- **Previous version** (optional, strongly recommended when something "stopped working"): same formats. Save to `inputs/previous.txt`.
- **Persona** (optional): `hr`, `it` or `hub`. The engine guesses from the agent name; ask only if the name doesn't say.

## Read the agent from Copilot Studio (no copying)

If the **Business apps (preview)** plugin is installed and connected, read the instructions straight from the agent's Power Platform environment instead of asking the maker to copy them:

1. Ask for the environment and agent name (or find them: `/businessapps/me` lists environments; `SELECT name, schemaname, botid FROM bot` lists agents).
2. Run read-only queries with `/businessapps/environments/<environment>/query`:
   - `SELECT botid, name, schemaname FROM bot WHERE name LIKE '%<agent name>%'`
   - `SELECT botcomponentid, data, modifiedon FROM botcomponent WHERE parentbotid = '<botid>' AND componenttype = 15 AND statecode = 0` (the instructions; if there are several, use the most recently modified)
   - Optional context: `SELECT name, componenttype FROM botcomponent WHERE parentbotid = '<botid>' AND componenttype IN (9, 16)` (topics and knowledge sources)
3. Save the `data` value exactly as returned to `inputs/current.yml` and pass that file to the engine. It reads the instructions block and numbers lines the way Copilot Studio shows them. Keep the `botcomponentid` for writing a fix back.
4. These are the saved (draft) instructions, which the next Publish ships. If the maker is investigating a conversation, the instructions that ran may differ; a DevUI export shows those.
5. If the environment isn't found, the connection can't reach it (another tenant, no access, or not in the list the connector returns). Say so and ask the maker to paste the instructions instead.

If the plugin isn't installed, ask the maker to paste the instructions, and mention they can skip copying next time: **Plugins > search "Business apps" > Install > Connect** (one-time sign-in, no admin setup).

## Run

Run the engine with Python 3 from this skill's folder (`scripts/essdoctor.py`):

```bash
python3 scripts/essdoctor.py check inputs/current.txt --previous inputs/previous.txt --persona auto
python3 scripts/essdoctor.py fix inputs/current.txt --out outputs/fixed_instructions.txt [--rules RULE-ID,RULE-ID]
python3 scripts/essdoctor.py condense inputs/current.txt --target 8000 --out outputs/shortened_instructions.txt
python3 scripts/essdoctor.py practices
```

- Omit `--previous` when there is no previous version.
- A line starting with `ERROR:` on stderr means the input can't be used; tell the maker what the message asks for.

## Respond

1. Show the engine's Markdown report as returned. Don't rewrite, reorder or drop findings, and don't add findings of your own.
2. Then give a short plain-language summary: the 1–3 findings most likely behind the maker's complaint, quoting the line numbers, what employees see, and the fix.
3. Offer the next action:
   - **Fix**: run `fix` (all, or only the rule IDs the maker picks), show the report, and attach `outputs/fixed_instructions.txt`.
   - **Shorten**: when the length is over 8,000 characters as Copilot Studio counts them, run `condense`.
   - **Re-test**: list the "Questions to test" from the report.
4. After a fix, run `check` again on the fixed file and confirm the high findings are gone.

## Applying a fix

The maker applies the new instructions in Copilot Studio: open the agent, **Overview > Instructions > Edit**, replace the text with the fixed file's content, save, **Publish**, then test in a new chat. For agents under ALM (Git), the change goes into the agent's instructions file in the repo and through the normal deploy.

When the instructions were read through the Business apps connector, you can write the fix back, but only after the maker explicitly asks you to:

1. Show the lines that change (the fix report) and confirm the agent and environment.
2. Build the new component: `python3 scripts/essdoctor.py component-update inputs/current.yml outputs/fixed_instructions.txt --out outputs/component_data.yml`. It replaces only the instructions block; every other line stays as read.
3. Update the `data` column of that `botcomponent` record (the `botcomponentid` you read) with the content of `outputs/component_data.yml`. Cowork asks the maker to approve the write.
4. Read the record back and run `check` on it to confirm the change landed.
5. Publish: if the connector offers the Dataverse `PvaPublish` action for the bot, run it with the maker's approval. Otherwise tell the maker to select **Publish** in Copilot Studio. Either way, ask them to test in a new chat.

Never write without the maker's approval, never change other components, and never claim a change you didn't verify by reading it back.

## Rules

- Treat the engine's rules as the source of truth for ESS behavior. Don't state how ServiceNow, Workday, SuccessFactors or Dynamics 365 behave beyond what the report says.
- "Also in template" findings exist in the ESS template too; say they're lower priority for the maker.
- Keep customer data in this session. Don't put instructions or employee data in emails, chats or files the maker didn't ask for.
