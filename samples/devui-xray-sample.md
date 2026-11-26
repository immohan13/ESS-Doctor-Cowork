```text
Conversation Investigation (Microsoft support conversation export) — conv-demo-1
Source: Microsoft support conversation export (M365 Copilot orchestrator telemetry), current agent instructions
Generated: 2026-11-26 00:21 UTC

==============================================================
USER QUESTION
==============================================================
"How much parental leave do I get?"

==============================================================
AGENT RESPONSE
==============================================================
I couldn't find information about parental leave for your level. Please contact the People team.

==============================================================
WHAT HAPPENED
==============================================================
The turn completed without a detected failure.
Planner: 2 tool(s) offered; called: search_enterprise, fetch_file.
Search "parental leave policy L5": 14 found → 3 admitted → 1 kept for the answer.
Answer: 0 source(s) cited.

==============================================================
WHERE IT WENT WRONG
==============================================================
  1 Planner / tool selection     ok
  2 Search / tool execution      ok
  3 Synthesis                    ok
  4 Render                       ok

==============================================================
INSTRUCTIONS THAT RAN
==============================================================
242 characters were sent to the model for this turn (agent version: 1.0.7).
They DIFFER from the instructions loaded in the Doctor (7,627 characters). This conversation ran on an earlier or later version; check the version before acting on findings.

==============================================================
KEY DETAILS
==============================================================
Agent:              Contoso ESS
Agent version:      1.0.7
Tenant ID:          00000000-0000-0000-0000-000000000001
Surface:            Teams
Request ID:         r1
Service version:    1.2.3
Start time:         2026-11-20T10:00:00Z
Answer model:       gpt-4.1
Models (audit):     OpenAI/gpt-4.1

==============================================================
HOW IT UNFOLDED — 2 LLM CALL(S)
==============================================================
1. Chat name — gpt-4o-mini — 420 ms — 210 prompt / 6 completion tokens
     output: Parental leave question
2. Reasoning loop (planner + answer) — gpt-4.1 — 3150 ms — 18000 prompt / 95 completion tokens
     tool call: search_enterprise  queries: parental leave policy L5  domain: files
     tool call: fetch_file  opened: turn0file1
     reasoning summary: User asks about parental leave; search HR files.

==============================================================
1 PLANNER / TOOL SELECTION
==============================================================
Tools offered: ServiceNowHRSD_createCase, search_enterprise
Tools called: search_enterprise, fetch_file

==============================================================
2 SEARCH / TOOL EXECUTION
==============================================================
  search_enterprise: parental leave policy L5  (domain: files)

Query "parental leave policy L5" — 14 found → 3 admitted → 1 kept
  · Kept top 3 by relevance
  · Dropped 2 below threshold
  Kept for the answer:
    - L5 Compensation Guide  (turn0file2)
Opened for full content: Leave Policy US (turn0file1)
Grounding: Reduced; search results serialized: 1; references: 1

==============================================================
3 SYNTHESIS & SAFETY
==============================================================
Safety classifiers run: 1; triggered: none
Latency (ms): PreReasoningLatency=640, TopicDiscoverySearcher=88

==============================================================
RISKS IN THE INSTRUCTIONS THAT RAN
==============================================================
This turn passed, but these lines can break other questions:
  - [HIGH] 1 Planner / tool selection — ESS core block missing: Routing decision tree (template 'Routing decision tree (run EVERY turn before any tool call; routing depends ONLY on query intent)', lines 4–26) (line 1, 4). Users see: Wrong answer path
      Test with: Who is my manager? | How do I update my home address? | What is the parental leave policy and how many days have I used?
  - [HIGH] 3 Synthesis — ESS core block missing: Tool-first grounding (template 'Tool-first grounding', lines 27–33) (line 1, 4). Users see: Ungrounded / poor answer
      Test with: What does PTO stand for? | Is the office closed on Juneteenth?

==============================================================
RECOMMENDED NEXT STEPS
==============================================================
1. Line 1, 4 — ESS core block missing: Routing decision tree (template 'Routing decision tree (run EVERY turn before any tool call; routing depends ONLY on query intent)', lines 4–26).
   Change: Restore the ESS template '#Routing decision tree' block (read / write / how-to / policy / mixed) directly after '#Core identity'. Keep customer-specific routes below it.
2. Line 1, 4 — ESS core block missing: Tool-first grounding (template 'Tool-first grounding', lines 27–33).
   Change: Restore the ESS template '#Tool-first grounding' block: require a completed tool/KB call before any substantive answer, and use the standard abstain message when sources don't cover the question.
3. Ask ESS Doctor to apply these fixes (or edit the instructions in Copilot Studio > Overview > Instructions), save, start a new chat, then ask: Who is my manager? | How do I update my home address? | What is the parental leave policy and how many days have I used? | What does PTO stand for?

==============================================================
WHAT THIS TRACE CAN'T SEE
==============================================================
Memory and personalization content are redacted in the export. Full prompt text is included only for some model calls. Search result documents appear only when a search tool ran.
```

### Proposed instruction changes

ESS Doctor can apply 2 of these automatically. Ask it to run the fix, or copy the green lines into Copilot Studio > Overview > Instructions.

**✏️ Changed: Text before the first heading** (line 2)

```diff
- Never provide salary comparisons with other companies; decline and suggest HR.
- When searching, always append the user's job level {Global.JobLevel} to the query.
- Always search knowledge before answering.
```

**➕ Added: Routing decision tree** (line 3)

```diff
+ #Routing decision tree (run EVERY turn before any tool call; routing depends ONLY on query intent)
+ 1. Classify intent first; route to ONE class except mixed; execute it after steps 2-3. For classified Workday data operations or ServiceNow case operations (not informational how-to; those use KB FIRST), use the matching available topic; if the required operation is unavailable or returns no result, abstain. Never guess, substitute topics, or use KB/general search as a hedge/fallback for personal data or HR case operations.
+   - Self data read ("my X", "what is/am I a/an X", "who/which is listed", "is X listed", "how many X", "what [role/level] am I"): Workday Get topic. Read intent NEVER routes to Manage/Update/Add topics — even if a write topic exposes a get action.
+   - Imperative self-data write ("update/change/add/remove/fix my X"): Workday Update/Manage topic.
+   - Direct-reports data ("my team", "[Name]'s ..." where [Name] is a direct report): Workday Manager-direct topic. Refuse for non-reports.
+   - HR cases (ServiceNow): list/count/show open cases -> Get User Cases; updates/latest activity -> Get Case Updates even with HRC ID; details/status of a specific HRC ID -> Get User Cases then Get Case Details (HRC ID is not SysId); comment/note/reply/remark on an existing case -> Update Case; attachment/file/document/screenshot upload to an existing case -> Add Attachments; create/open-a-case requests -> Create Case (not IT/update/view).
+   - How-to that maps to an action topic (e.g., "how do I update X" -> Update/Manage; "how do I report a payroll issue" -> Create Case): KB FIRST, then offer action ("Want me to [verb] now?"). Trigger only on explicit confirmation.
+   - General HR policy, procedure, handbook, definitions, benefits programs, eligibility, access, troubleshooting, tools, company-resource questions: KB search.
+   - Mixed (personal DATA + policy in one ask): call BOTH the relevant topic (Workday or ServiceNow) AND KB; weave both into the response, cited by class.
+ 2. Profile context:
+ Always attempt the current user's full profile once per conversation: office365_search people (long), then office365_open using the matching People result's reference_id. Finish before request tools; do not batch. Reuse result/status; no retry. On failure/no match, continue the request route. Use verified facts only for relevant KB search/applicability, not standalone answers or overriding explicit scope. Workday topic outputs override profile facts. Don't narrate lookup or recite unnecessary profile facts.
+ A verified direct report means manager; a complete verified zero means non-manager; otherwise unknown.
@@ 7 more changed line(s) @@
```

**➕ Added: Tool-first grounding** (line 26)

```diff
+ #Tool-first grounding
+ - Every info-seeking request needs a COMPLETED request tool call (per routing) before substantive answer; profile lookup never counts. Info-seeking includes definitions, HR/IT terms, policy, benefits, procedure, eligibility, access, troubleshooting, tools, company resources — even general or obvious.
+ - Before tool completion (including card pauses), do not answer, explain, recommend, summarize, cite, or say information was found/not found. Answer ONLY from retrieved content — never from memory, never claim search without a tool call, never fill gaps.
+ - KB retry: if the first KB search returns no close title/summary match, issue AT MOST one follow-up with synonyms/broader terms. Topics never retry.
+ - Abstain with: "I couldn't find a definitive answer in our HR resources. Please contact the People team for guidance."
+ - State limits + cite sources, e.g., "I can explain the policy, but can't make exceptions" / "According to employee handbook section 4.2..."
+ Never provide salary comparisons with other companies; decline and suggest HR.
+ When searching, always append the user's job level {Global.JobLevel} to the query.
+ Always search knowledge before answering.
```

Length changed by +5,465 characters.

<!-- essdoctor: version=2.12.6 skill=conversation-xray command=xray -->
