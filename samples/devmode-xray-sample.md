```text
Conversation Investigation (developer mode card) — conversation ID not shown
Source: Microsoft 365 Copilot developer mode debug card, agent instructions
Generated: 2026-11-25 20:19 UTC

==============================================================
USER QUESTION
==============================================================
"How many vacation days do I have left?"

==============================================================
AGENT RESPONSE
==============================================================
I couldn't find any information about that.

==============================================================
WHAT HAPPENED
==============================================================
Copilot didn't pick any action or topic for this question: none of the descriptions match what the user asked.
- No functions matched the question.
- No functions were selected for execution.
- No function execution details were returned.

==============================================================
WHERE IT WENT WRONG
==============================================================
  1 Planner / tool selection     ◀ FAILED HERE
  2 Search / tool execution      not run
  3 Synthesis                    affected (downstream)
  4 Render                       affected (downstream)

==============================================================
KEY DETAILS
==============================================================
Agent ID:               T_contoso.ess.hr
Agent scope:            Tenant-scoped (org catalog)
Matched functions:      none
Selected functions:     none

==============================================================
1 PLANNER / TOOL SELECTION
==============================================================
Capabilities: none listed
Actions: none listed
Matched functions: none
Selected for execution: none

==============================================================
2 SEARCH / TOOL EXECUTION
==============================================================
No function execution details were returned.

==============================================================
3 SYNTHESIS & SAFETY
==============================================================
The card doesn't show safety classifiers or how the answer was written. The answer you added was checked for abstain wording.
Why this stage is marked: the answer declined or said it found nothing. It said: "I couldn't find any information about that." If the search returned relevant documents, an instruction or content moderation stopped the answer.

==============================================================
INSTRUCTION LINES LIKELY RESPONSIBLE
==============================================================
None found: no instruction line targets this question. Check the descriptions of the topics, actions and knowledge sources that should handle it.

==============================================================
RECOMMENDED NEXT STEPS
==============================================================
1. Rewrite the description of the topic or action that should handle this so it uses the words users ask with (for example “How many vacation days do I have left?”).
2. Check the instructions don't steer this kind of question away from tools.
3. If this used to work, ask again later: 'No functions selected' after earlier success can mean throttling.
4. If Microsoft support is helping you, a DevUI export of the same conversation adds the instructions that ran, the search queries, every model call and the safety checks.

==============================================================
WHAT THIS CARD CAN'T SEE
==============================================================
- The answer text, and how the model used what came back. Pass the agent's response to check that step.
- Why the planner picked or skipped each function; the card shows only the outcome.
- The content of search results and which documents were cited.
- Steps inside Copilot Studio topics or flows after the action was called.
```

<!-- essdoctor: version=2.9.2 skill=conversation-xray command=xray -->
