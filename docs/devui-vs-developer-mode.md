# DevUI vs developer mode: what makers can see

ESS Doctor produces the same conversation X-ray layout (question, answer, what happened, where it went wrong, planner, search, synthesis and safety, instruction lines responsible, next steps) from whatever evidence the maker has. This page compares the evidence sources, shows which X-ray sections each one fills, and lists what customers can't get today.

DevUI is a Microsoft-internal tool: `/share` and the DevUI debug link need a Microsoft corporate account. Customer makers work from developer mode, their agent's instructions and, through their compliance admin, Purview audit exports.

## Turn on developer mode

There are no tenant or admin settings for developer mode. A maker needs:

- A licensed Microsoft 365 Copilot experience, or Copilot Chat with pay-as-you-go billing for agents grounded in organizational data.
- Access to the agent: the Copilot Studio **Test** pane for agents built in Copilot Studio, or Microsoft 365 Copilot with the agent selected.

Steps:

1. Open the agent in the Copilot Studio **Test** pane (or Microsoft 365 Copilot with the agent selected) and start a new chat.
2. Send `-developer on`. Copilot replies "Successfully enabled developer mode."
3. Ask the exact question that failed.
4. Under the answer, open **Agent debug info** to read the card, or **Raw info** to see the full JSON. Copy the Raw info JSON into a `.txt` file; it contains the most detail, including the text of each document the search returned.
5. Add two lines at the top of the file: `Question: <what the employee asked>` and `Response: <what the agent replied>`. The card doesn't include either.
6. Send `-developer off` when done.

Tips:

- Use a new chat for each question so earlier turns don't change the result.
- No card means Copilot answered without the agent's knowledge, capabilities or actions, or the request was throttled. "No card" is itself a finding: the question wasn't grounded.
- Developer mode only works in the licensed Microsoft 365 Copilot experience, including the Copilot Studio Test pane.
- Pro-code declarative agents (Agents Toolkit) also get a **Debug panel** and a downloadable diagnostic log when previewed with F5.
- Copy the agent's instructions from Copilot Studio (**Overview > Instructions**) once, and pass them with the card so ESS Doctor can name the instruction line behind the failure.

## Evidence compared

The example sizes come from one ESS HR conversation ("compare my salary with amazon when my compa ratio is 1.8").

| What it shows | Developer mode card (Raw info, ~25 KB) | DevUI export (~48 MB) | Purview audit export |
| --- | --- | --- | --- |
| Who can get it | Any maker chatting with the agent | Microsoft support and engineering only | Customer compliance admin (audit search) |
| Question and answer | No (maker adds them) | Yes, every turn | No |
| Agent name, version, conversation ID | Yes | Yes | Yes (name, ID, version) |
| Instructions that ran | No (maker adds the current instructions) | Yes, as sent to the model | No |
| Knowledge sources configured | Yes, with SharePoint scopes | Yes | No |
| Search queries Copilot wrote | No (empty in the card) | Yes, every query | No |
| Documents the search returned | Yes: titles, ranks and extract text | Yes, with the funnel (found → admitted → kept) | Resources accessed, per interaction |
| Documents cited in the answer | No | Yes | No |
| Tools offered and called, planner steps | Matched and selected functions only | Yes, every step | Plugins used |
| Model calls (model, latency, tokens) | No | Yes | Model name only |
| Safety classifiers | No | Yes, each classifier and whether it triggered | No |
| Action and connector HTTP status, latency, errors | Yes | Yes | No |
| Many conversations at once | One card per question | One export per conversation | Yes, all interactions in a time window |

## How ESS Doctor fills each X-ray section

| X-ray section | From developer mode + instructions | From DevUI |
| --- | --- | --- |
| User question, agent response | From the `Question:` and `Response:` lines | From the export |
| What happened, where it went wrong | Yes: tool selection, action failures (401/403/404/429/5xx, timeouts), empty searches, searches returning unrelated documents, declines applied after searching | Yes, plus the exact search queries and funnel |
| Instruction check (rule followed or not) | Yes, when the instructions and answer are provided | Yes, against the instructions that ran |
| Planner / tool selection | Capabilities, actions, matched and selected functions | Every tool offered and called, per model call |
| Search / tool execution | Searches with result counts and returned document titles | Queries, funnel and kept documents |
| Synthesis and safety | Abstain check on the answer | Safety classifiers, grounding and citations |
| Instruction lines likely responsible | Yes, with the instructions | Yes |
| Recommended next steps | Yes | Yes |

## Custom engine agents (CEA)

A custom engine agent is a Copilot Studio agent that Microsoft 365 Copilot hands the question to. Its planning, topics, knowledge search and tools run inside Copilot Studio, so the evidence is different.

### Which evidence to use

Environment-level Application Insights export needs a managed environment, so the recommended path works without it:

| Situation | Evidence | What it covers |
| --- | --- | --- |
| Maker reproduces it in the test pane | Test pane snapshot | Everything: question, search query and documents, topic steps, instructions and setup |
| Real employee conversation | Transcript (Bot Transcript Viewer role) | Plan, knowledge searches, tools, errors |
| Real conversation without transcript access | Agent-level Application Insights telemetry | Question and reply, topics and steps, ESS logger events; no searches |

### Evidence compared

| What it shows | DevUI export | Test pane snapshot | Copilot Studio transcript (Dataverse `conversationtranscript`) | Application Insights logs | Copilot Studio Monitor > Sessions download |
| --- | --- | --- | --- | --- | --- |
| Who can get it | Microsoft support and engineering only | The maker, in the Copilot Studio test pane (⋯ > Save snapshot) | Makers with the Bot Transcript Viewer role (Business apps connector, Power Apps export) | Anyone with the Reader role on the Application Insights resource, once telemetry is set up | Makers with the Bot Transcript Viewer role |
| Real employee conversations | Yes | No, only the maker's own test chats | Yes | Yes (and test chats) | Yes |
| Question and answer | Yes | Yes, full text | Yes (answers grounded on SharePoint are stored as REDACTED) | Yes (agent-level telemetry needs Log conversation details) | Yes (each reply cut at 512 characters) |
| Instructions and topic setup (for example an empty user context topic) | No | Yes (`botContent.yml`) | No | No | No |
| Handoff to Copilot Studio (calls, latency, status) | Yes | No | No | No | No |
| Microsoft 365 Copilot safety classifiers | Yes | No | No | No | No |
| Plan, topics and tools that ran | No | Yes | Yes | Environment-level: tools. Agent-level: topics and every topic step (Node execution events) | Last topic only |
| Knowledge searches and results | No | Yes, with document names | Yes, when recorded | Environment-level: tool arguments and results. Agent-level: not logged | No |
| Failed steps and errors | Handoff failures only | Yes | Yes | Yes, with error status and message | Outcome and outcome reason |
| Connection consent prompts | No | Yes | Yes | Not documented | No |
| When it's available | After the conversation | Immediately | About 30 minutes after the conversation goes idle | Minutes; up to 24 hours after telemetry is first set up | — |
| Many conversations at once | One export per conversation | One snapshot per test chat | One record per conversation | Yes, by query | Yes, every session of the last 28 days |

ESS Doctor reads all five. A DevUI export of a custom engine agent turn is reported as a handoff ("in Copilot Studio") instead of an ungrounded answer, and points to the transcript for the plan and searches. The `conversation-logs` skill writes the Application Insights query for a conversation ID. For the fullest picture of one employee's conversation, combine its transcript or Application Insights logs with a test pane snapshot of the agent, which adds the instructions and topic setup. Agent-level telemetry works in any environment but doesn't log knowledge searches, so pair it with the transcript when search matters; environment-level telemetry, which logs tool calls, is only available for managed environments.

## What customers can't get today

These DevUI sections have no customer-accessible source:

- The search queries Copilot wrote (the card's `queryString` is empty).
- The search funnel (how many results were found, admitted and kept) and which documents the answer cited.
- The instructions as sent to the model for a specific turn.
- Each model call with its latency and tokens.
- Safety classifier results.
- Conversation activity for agents stored in Copilot Studio's newer backend: their **Activity** tab stays empty.

ESS Doctor says in each report what its evidence can't see, so makers know when to involve Microsoft support.
