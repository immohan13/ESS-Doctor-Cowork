---
name: transcript-xray
description: X-ray a custom engine agent's (CEA) recent conversation straight from its Copilot Studio transcripts, by agent name and question, through the Business apps connector.
---

# ESS transcript X-ray (custom engine agent)

You find a Copilot Studio agent's conversation transcript yourself, through the **Business apps (preview)** plugin, and explain it with the ESS Doctor X-ray. The maker only names the agent and, optionally, what they asked, for example "X-ray my last ESS CEA conversation about leave policy".

Use this skill for custom engine agents (Copilot Studio agents stored in Dataverse). For declarative agents, or when the maker uploads evidence, use the `conversation-xray` skill.

If the maker uploads a **Monitor > Sessions** download (CSV with `SessionId` and `ChatTranscript` columns) instead of naming the agent, don't convert it into transcripts: run `python3 scripts/essdoctor.py xray "<file>.csv"` and show the returned **Sessions X-ray** as Markdown. Then offer to read the full transcripts of the failed sessions here, by agent name, for the step-by-step X-ray.

## Before you start

- The **Business apps (preview)** plugin must be installed and connected. If it isn't, tell the maker: **Plugins > search "Business apps" > Install > Connect** (one-time sign-in, no admin setup), and stop.
- Reading transcripts needs the **Bot Transcript Viewer** (or System Administrator) role in the agent's environment.
- Transcripts are saved for published channels (Teams, Microsoft 365 Copilot, the demo website), about 30 minutes after the conversation goes idle. Test pane chats may not have one; for those, ask for a test pane snapshot and use `conversation-xray`.

Take from the maker's message, and ask only for what's missing, in one message:

1. The **agent name** (or bot ID).
2. The **environment**, if the maker has more than one (`/businessapps/me` lists them).
3. Optional: words from the **question** they asked, and roughly **when**. Without a question, use their most recent conversation.

## Read the transcripts

Run these through the Business apps connector, in the chosen environment:

1. Find the agent, unless the maker gave the bot ID:

   ```sql
   SELECT botid, name, schemaname FROM bot WHERE name LIKE '%<agent name>%'
   ```

   If several match, ask which one.

2. Get the most recent transcripts (newest first):

   ```sql
   SELECT TOP 20 conversationtranscriptid, conversationstarttime, content FROM conversationtranscript WHERE _bot_conversationtranscriptid_value = '<botid>' ORDER BY conversationstarttime DESC
   ```

   With a question, keep the rows whose `content` contains its words. Without one, keep only the newest row. If nothing matches, widen to `TOP 100` once, then tell the maker no transcript was found and why that may be (not saved yet, a test pane chat, or a missing role).

3. Save the kept rows exactly as returned (a JSON array of objects with `content`) to `inputs/transcripts.json`. Don't edit `content`; it's a JSON string with the conversation's activities.

4. Optional but better: read the agent's instructions too, so the X-ray can name the instruction line:

   ```sql
   SELECT botcomponentid, data, modifiedon FROM botcomponent WHERE parentbotid = '<botid>' AND componenttype = 15 AND statecode = 0
   ```

   Save the most recently modified `data` to `inputs/current.yml`.

## Run

```bash
python3 scripts/essdoctor.py xray inputs/transcripts.json --question "<words from the question>" [--instructions inputs/current.yml]
```

The engine picks the transcript whose user messages contain the question. Without `--question` it uses the one with the most activity, so save only the newest row in that case.

## When the answer was a sorry: run the sorry triage too

Don't wait to be asked. One sorry can be a one-off or a pattern, and the sorry triage tells them apart across conversations and links the causes to instruction lines.

- **After a single-transcript X-ray** whose agent response declines, says it found nothing, gives the Fallback reply ("not able to find a related topic") or answers generically: save all the rows you already read in step 2 (up to `TOP 20`, not only the matching one) to `inputs/recent.json` and run `python3 scripts/essdoctor.py sorry-triage inputs/recent.json [--instructions inputs/current.yml]`. Skip it if you read only one transcript.
- **After a Sessions X-ray** with any ❌ or ⚠️ session:
  1. If the Business apps connector is connected and you know the agent, read the transcripts of those sessions only (step 2, keeping the rows whose `content` contains each failed question and whose start time is within a few minutes of the session's start), and save them to `inputs/sorry_transcripts.json`. Also read the instructions (step 4).
  2. Run `python3 scripts/essdoctor.py sorry-triage "<Sessions>.csv" [inputs/sorry_transcripts.json] [--instructions inputs/current.yml]`. A session that also has its transcript is counted once, from the transcript.
  3. Without transcripts or instructions the triage would only repeat the Sessions X-ray: skip it and offer the transcript read instead.

Show the sorry triage after the X-ray under the heading **Across these conversations**, keeping its tables and `diff` blocks. Don't repeat sessions or turns the X-ray already showed; say only what the triage adds (the real cause from a transcript, the instruction rules behind the declines, the proposed changes, and how many conversations share the cause).

## Respond

For a Sessions X-ray (Markdown, starts with `## Sessions X-ray`): show it as returned, as Markdown, keeping the `diff` block. Then summarize in at most three bullets (the top cause, who fixes it, and the single most useful next step) and add local times only if the maker's time zone is known. Don't re-run the transcript X-ray per session on converted text.

For a single transcript: Follow "Respond" in the `conversation-xray` skill: show the report in one `text` code block line for line, summarize in 3–5 sentences, draft any instruction fix, and list the fixes that are Copilot Studio setup. Mention the transcript's start time so the maker knows which conversation it was.

Transcripts store answers grounded on SharePoint as REDACTED and don't include the agent's topic setup. If the report points at the answer text or at a topic (for example an empty user context topic), suggest a test pane snapshot of the same question.

## Rules

- Transcripts contain employees' questions, answers, names and email addresses. Read only the agent and time window the maker asked about, use them only to answer the maker, and don't copy them into emails, chats or documents unless asked.
- Only read. Never write to the `conversationtranscript` table.
- Never replace the report's verdict with your own reading of the transcript.
