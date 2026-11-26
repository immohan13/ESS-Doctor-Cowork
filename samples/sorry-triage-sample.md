## ESS Doctor sorry triage

4 turn(s) analyzed; 4 ended in a sorry, an error or no answer (100%).

### Causes, biggest first

| Cause | Turns | Share | Who fixes it | Fix |
| --- | --- | --- | --- | --- |
| Topic, action or connector failed | 1 | 25% | Connection or backend admin | Open the failing connection (ServiceNow, Workday, ...) and check sign-in, permissions and the instance URL; the developer mode card shows the HTTP status. |
| No topic, action or knowledge matched | 1 | 25% | Agent maker (descriptions and instructions) | Rewrite the description of the topic or action that should handle this question using employees' words, and check the instructions don't steer it away from tools. |
| Knowledge search found nothing relevant | 1 | 25% | Knowledge owner (content), then maker | Check a document that answers this exists and is indexed. If it does, compare the search query with the document title; remove query enrichment (job level, country) and add synonyms to the terminology rules. |
| Search found content but the agent still declined | 1 | 25% | Agent maker (instructions) | Either the kept documents are the wrong ones (fix the query) or the abstain rule is too strict: let the agent answer from what search returned and cite it. |

**2 of 4** sorry answers have causes the maker can fix in the agent (instructions, descriptions, moderation or connections) without waiting for content or backend changes.

### Instruction rules behind the maker-owned sorries

| What's wrong | Sorry turns | Fix |
| --- | --- | --- |
| Job level/title is appended to search queries (line 51) | 1 | Add the user's country only for policies that vary by country (holidays, leave, benefits, payroll). Add job level only for level-specific programs or eligibility. Otherwise search with the user's words. |
| ESS core block missing: Tool-first grounding | 1 | Restore the ESS template '#Tool-first grounding' block: require a completed tool/KB call before any substantive answer, and use the standard abstain message when sources don't cover the question. |
| ESS core block missing: Knowledge retry budget | 1 | Add: 'If the first knowledge search lacks a close match, make at most one more search with synonyms or broader terms. If still unsupported, say so and offer the approved support route.' |

Already fixed in the current instructions: ESS core block missing: Tool-first grounding; ESS core block missing: Knowledge retry budget.

### Every sorry turn

| # | Question | Agent said | Cause | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Open an HR case about my payslip | Sorry, something went wrong. Please try again later. | Topic, action or connector failed | The ServiceNowHRSD.createCase action reached contoso.service-now.com and signed in, but ServiceNow refused the call (HTTP 403). The connection works; the backend doesn't allow this |
| 2 | How many vacation days do I have left? | I couldn't find any information about that. | No topic, action or knowledge matched | Copilot didn't pick any action or topic for this question: none of the descriptions match what the user asked. |
| 3 | What is the parental leave policy in Germany? | I couldn't find a definitive answer in our HR resources. Please contact the People team for guidance. | Knowledge search found nothing relevant | Knowledge search ran (OneDriveAndSharePoint for “parental leave policy Germany L5”) but returned 0 results. Either the content isn't in the knowledge source, or the search wording  |
| 4 | How much parental leave do I get? | I couldn't find information about parental leave for your level. Please contact the People team. | Search found content but the agent still declined | kept: L5 Compensation Guide |

Agent versions in this evidence: Contoso ESS 1.0.7, T_contoso.ess.hr 1.0.7.

### Next steps

1. Send the failing topics or connectors to the connection admin, with the conversation details above.
2. Improve the descriptions of the topics and tools that should handle these questions.
3. Send the questions where search found nothing relevant to the knowledge owner, with the questions listed above.
4. Run the instruction check: the agent found content but its rules stopped the answer.
5. Re-run the same questions after publishing and triage the new evidence to confirm the sorry share dropped.

### Proposed instruction changes

ESS Doctor can apply 1 of these automatically. Ask it to run the fix, or copy the green lines into Copilot Studio > Overview > Instructions.

**✏️ Changed: Employee relocation restriction** (line 52)

```diff
+ - Add the user's country only for policies that vary by country (holidays, leave, benefits, payroll). Add job level only for level-specific programs or eligibility. Otherwise search with the user's own words.
```

Length changed by +209 characters.

<!-- essdoctor: version=2.12.5 skill=sorry-triage command=sorry-triage -->
