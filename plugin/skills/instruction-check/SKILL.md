---
name: instruction-check
description: Check, fix and shorten ESS agent instructions; see what a change broke and which lines to fix.
---

# ESS instruction check

> Part of ESS Doctor. Findings, causes and instruction text come only from the engine commands below; don't analyse the evidence or edit the engine's output yourself. When the request needs more than this skill, follow the `ess-doctor` skill to plan and chain the others. End the reply with one line, "Produced by ESS Doctor <version> · <skill>" (skill names only, no commands or brackets), taken from the `<!-- essdoctor: ... -->` line at the end of the engine output; don't show that comment line itself. Separate the engine report, your own summary and next step, and that last line with dividers: a line with only `---`, with a blank line before and after.

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
4. These are the saved (draft) instructions, which the next Publish ships. If the maker is investigating a conversation, the instructions that ran may differ; a conversation export from Microsoft support shows those.
5. If the environment isn't found, the connection can't reach it (another tenant, no access, or not in the list the connector returns). If the agent isn't in the `bot` table, it's most likely stored in Copilot Studio's newer backend (its URL contains `agentBackend=cosmos`), which the connector can't read. In both cases say so and ask the maker to paste the instructions instead.

If the plugin isn't installed, ask the maker to paste the instructions, and mention they can skip copying next time: **Plugins > search "Business apps" > Install > Connect** (one-time sign-in, no admin setup).

## Run

Run the engine with Python 3 from this skill's folder (`scripts/essdoctor.py`):

```bash
python3 scripts/essdoctor.py check inputs/current.txt --previous inputs/previous.txt --persona auto [--agent-type cea]
python3 scripts/essdoctor.py fix inputs/current.txt --out outputs/fixed_instructions.txt [--rules 1,2 | recommended | all] [--agent-type cea]
python3 scripts/essdoctor.py condense inputs/current.txt --out outputs/shortened_instructions.txt
python3 scripts/essdoctor.py practices
```

- Omit `--previous` when there is no previous version.
- Pass `--agent-type cea` for a custom engine agent (for example when the maker is working from sessions, transcripts or Application Insights logs) and `--agent-type da` for a declarative agent. A test pane snapshot or `botContent.yml` is treated as a custom engine agent automatically. Custom engine agents aren't checked for the declarative agent core blocks, because their template routes, grounds and retries through topics.
- `--rules` takes finding numbers from the check report (for example `1,2`), `recommended` (the findings plus the report's **Recommended additions**) or `all` (also the optional practices). Only findings are numbered; practices are never addressed by number.
- A line starting with `ERROR:` on stderr means the input can't be used; tell the maker what the message asks for.
- `check` (when it has questions to test), `fix` and `condense` also write `outputs/evalset.csv`: a Copilot Studio evaluation test set for the change, with ESS regression questions and an expected response for each. When you re-run `check` on a fixed or shortened file, add `--no-evalset` so it keeps the set the fix wrote.

## Respond

The check also catches patterns found while tuning customer agents: employee context used to build search queries (rather than to tailor the answer), search queries rewritten with device, OS or profile details, a tool order required in prose, grounding rules that also block figures the sources state, lists of banned phrases or words instead of a behavior rule, clarifying questions with made-up options, and asking the agent to report its own steps. Treat them like any other finding: show them as numbered, and fix them with `fix`.

The rules and practices are ESS field guidance, not Copilot Studio product documentation: a clean check doesn't replace the evaluation test set.

When the maker keeps adding rules to fix individual answers, suggest the evaluation loop instead: keep the essential safeguards in the instructions, put the behaviors they want in the evaluation test set, run it, fix only the rules behind the failures and re-run. A high pass rate on a small set isn't sign-off; check the failed questions and a sample of passes.

Every proposed change in the report is a `diff` code block: removed lines start with `-` (red) and added lines with `+` (green). That covers **What changed**, the **Proposed change** under each finding (the change `fix` would make for that rule) and the single **Recommended additions** change for the authoring practices. Keep those blocks exactly as returned, including the `diff` language tag and their indentation, so the colors render; don't turn them into tables or prose.

1. Show the engine's Markdown report as returned, as Markdown (not in a code block), so its tables and numbered findings render. Don't rewrite, reorder or drop findings, and don't add findings of your own.
2. Then add **What matters most** in at most three short numbered points: the findings most likely behind the maker's complaint (quote the line number and what employees would ask that goes wrong), and whether the length leaves room for the recommended additions. Refer to findings by number ("finding 1") and to the practices only as "the recommended additions" or "the optional practices"; never number or list practices individually. Mention the optional practices in one line at most. Don't repeat the report's tables.
3. Then one short **Next step** line offering the action:
   - **Fix**: offer exactly the choices the report's "To apply" line gives: findings by number, "fix recommended" or "fix all"; run `fix` with the matching `--rules`, show the report, and hand over `outputs/fixed_instructions.txt` as described in "Applying a fix".
   - **Shorten**: when the length is near or over 8,000 characters as Copilot Studio counts them, run `condense`. It aims for 7,200 characters, leaving a 10% buffer under the 8,000 limit (`--target` changes it); run it before adding practices.
   - **Re-test**: the questions under "Test after fixing", in the evaluation test set.
4. After a fix, run `check --no-evalset` again on the fixed file and confirm the high findings are gone.
5. Whenever `outputs/evalset.csv` was written, share it as a downloadable file and tell the maker how to use it in four short steps: in Copilot Studio open the agent > **Evaluation** > **Create a test set** > upload the CSV; run it on the current version; publish the change and run it again; upload both results files here to compare them with `eval-compare`.
6. Keep it scannable: no headings beyond the two above, bold only key numbers and line references, and no emoji beyond those in the report. Refer to findings by their number and title, never by internal rule IDs (such as `L5-…` or `BP-…`) or practice codes (such as P10); makers don't need them. Line numbers are lines in the Copilot Studio instructions box.

## Applying a fix

When the maker asks to fix ("fix 1", "fix recommended", "fix all", "apply the fixes"):

1. Run `fix` once with the same input file and agent type the check used: `--rules 1,2` with the finding numbers the maker named, `--rules recommended` for the findings plus the recommended additions (also "apply the fixes"), or `--rules all` for everything. When an X-ray or sorry triage saved a rule (`<!-- essdoctor-intake: <path> -->`) and the maker asked for it too, add `--draft <path>`: the rule is drafted into its section on the fixed text, in the same output file. Don't run it per item.
2. Show the fix report as returned, as Markdown: **Done: already in the new instructions**, any practices that didn't fit, **What changed** (`diff` blocks), Still open and the evaluation test set. The report already re-checks the result and fits it under the 8,000-character limit: when the fixes go over, it tightens existing text and keeps every added line word for word; if that's not enough, it leaves out the lowest-priority practices and names them.
3. Never edit the engine's output yourself: don't reword added lines so the check recognises them, move or merge sections, shorten lines by hand, or fix a finding the engine didn't apply. If a finding is under "Not done: needs your decision" or a practice didn't fit, say so and offer the next step: `condense` (shorten), then `fix` again, or the `instruction-draft` skill for a rule the maker wants worded differently.
4. If you ran anything besides `fix` (for example `condense` or `draft`), say which in one line, so the maker knows which ESS Doctor step produced the result.

Then hand the new instructions to the maker so their formatting survives the copy:

1. Print the file (`cat outputs/fixed_instructions.txt`) and show its full content, unchanged, inside one fenced code block marked `text`.
2. Tell the maker to copy it with the code block's **Copy** button. Don't send them to the file preview: it shows the file as formatted Markdown, so numbered lines lose their numbers and blank lines disappear when copied.
3. Keep line breaks, blank lines, numbering and `#` headings exactly as the engine wrote them.

The maker applies the new instructions in Copilot Studio: open the agent, **Overview > Instructions > Edit**, select all the existing text, paste, save, **Publish**, then test in a new chat. For agents under ALM (Git), the change goes into the agent's instructions file in the repo and through the normal deploy.

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
