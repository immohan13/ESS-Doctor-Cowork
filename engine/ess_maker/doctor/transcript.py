"""Copilot Studio conversation transcript X-ray (custom engine agents).

Reads ``conversationtranscript.content`` (``{"activities": [...]}``). Messages come from ``type: message``
(``from.role`` 1 = user, 0 = agent). Orchestration, knowledge search, topic and error details come from event and
trace activities, matched on ``valueType`` by keyword because the generative-orchestration value types aren't a
documented contract. Accepts the raw content, a Dataverse row or row list, or the Copilot Studio Analytics sessions
CSV (a column holding the transcript JSON).
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone

from ess_maker.doctor.report import RULE, _REFUSAL, instruction_check
from ess_maker.doctor.rules import PIPELINE_ORDER

OK, FAILED, NOT_RUN, UNKNOWN = "ok", "failed", "not run", "unknown"
SOURCE_LABEL = "Copilot Studio conversation transcript"
_FAILED = re.compile(r"fail|error|fault|cancel|timed?\s*out|denied|forbidden|unauthori", re.I)
_NO_ANSWER = re.compile(r"nosearchresults|noresults|notanswered|unanswered|filtered|moderat|blocked|noanswer", re.I)
_TOOLISH = re.compile(r"Connector|Flow|Http|Plugin|Tool|Search|Prompt", re.I)
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _activities_of(doc) -> list[dict]:
    if isinstance(doc, dict) and isinstance(doc.get("activities"), list):
        return doc["activities"]
    return []


def _looks_like_activity(x) -> bool:
    return isinstance(x, dict) and "type" in x and any(k in x for k in ("from", "valueType", "timestamp", "channelId"))


def _content_docs(obj) -> list[dict]:
    """Transcript documents from a raw transcript, a Dataverse row, a list of rows or an OData response."""
    if isinstance(obj, dict) and isinstance(obj.get("activities"), list):
        return [obj]
    if isinstance(obj, list) and obj and all(_looks_like_activity(x) for x in obj[:20]):
        return [{"activities": obj}]
    if isinstance(obj, dict) and isinstance(obj.get("value"), list):
        return _content_docs(obj["value"])
    if isinstance(obj, dict):
        c = obj.get("content") or obj.get("Content")
        if isinstance(c, str):
            try:
                return _content_docs(json.loads(c))
            except ValueError:
                return []
        return _content_docs(c) if isinstance(c, dict) else []
    if isinstance(obj, list):
        return [d for x in obj for d in _content_docs(x)]
    return []


def load(text: str) -> list[dict]:
    """Transcript documents in the text (JSON or the sessions CSV); empty if it isn't a transcript."""
    t = (text or "").lstrip("\ufeff").strip()
    if t.startswith("{") or t.startswith("["):
        try:
            return _content_docs(json.loads(t))
        except ValueError:
            return []
    if '"activities"' not in t[:200000]:
        return []
    docs = []
    csv.field_size_limit(10_000_000)
    for row in csv.reader(io.StringIO(t)):
        for cell in row:
            c = cell.strip()
            if c.startswith("{") and '"activities"' in c[:200]:
                try:
                    docs += _content_docs(json.loads(c))
                except ValueError:
                    pass
    return docs


def looks_like_transcript(text: str) -> bool:
    return bool(load(text))


def load_sessions(text: str) -> list[dict]:
    """Rows of a Copilot Studio Analytics sessions download (CSV with a ChatTranscript column)."""
    t = (text or "").lstrip("\ufeff")
    head = t[:2000].lower()
    if "chattranscript" not in head or "sessionid" not in head:
        return []
    csv.field_size_limit(10_000_000)
    rows = list(csv.DictReader(io.StringIO(t)))
    out = []
    for r in rows:
        chat = r.get("ChatTranscript") or ""
        parts = [p.strip() for p in re.split(r";\s*(?=(?:User|Bot|Agent) says:)", chat) if p.strip()]
        users = [re.sub(r"^User says:\s*", "", p).rstrip(";") for p in parts if p.startswith("User says:")]
        bots = [re.sub(r"^(Bot|Agent) says:\s*", "", p).rstrip(";") for p in parts if re.match(r"(Bot|Agent) says:", p)]
        out.append({"session": r.get("SessionID") or r.get("SessionId") or "", "start": r.get("StartDateTime") or "",
                    "outcome": r.get("SessionOutcome") or "", "reason": r.get("OutcomeReason") or "",
                    "topic": r.get("TopicName") or "", "question": r.get("InitialUserMessage") or (users[0] if users else ""),
                    "response": bots[-1] if bots else "", "turns": r.get("Turns") or ""})
    return out


def _messages(doc: dict) -> tuple[list[str], list[str]]:
    users, bots = [], []
    for a in _activities_of(doc):
        if a.get("type") == "message":
            role = (a.get("from") or {}).get("role")
            text = a.get("text") or ""
            (users if role == 1 else bots).append(text)
    return users, bots


def pick(docs: list[dict], question: str = "") -> dict:
    """The transcript whose user messages contain the question text, else the one with the most activities."""
    q = (question or "").strip().lower()
    if q:
        for d in docs:
            if any(q[:60] in (u or "").lower() for u in _messages(d)[0]):
                return d
    return max(docs, key=lambda d: len(_activities_of(d)))


def _str(v) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _get(d, key: str):
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return None


def _s(d, key: str) -> str:
    return _str(_get(d, key))


def _first(*vals: str) -> str:
    return next((v for v in vals if v and v.strip()), "")


def _short(i: str) -> str:
    i = (i or "").strip()
    k = i.rfind(".")
    return i[k + 1:] if 0 <= k < len(i) - 1 else i


def _count(value, *keys: str) -> int:
    if not isinstance(value, dict):
        return -1
    for k in keys:
        v = _get(value, k)
        if isinstance(v, list):
            return len(v)
    return -1


def _head(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def diagnose(doc: dict, question: str = "") -> dict:
    acts = _activities_of(doc)
    users, bots = [], []
    seq: list[tuple[str, str]] = []
    planned, ran, failed, topics, searches, answer_states, errors = [], [], [], [], [], [], []
    empty = 0
    unknown_intent = design = consent = False
    details: dict[str, str] = {}

    for a in acts:
        typ, vt_raw = a.get("type") or "", a.get("valueType") or ""
        value = a.get("value")
        if typ == "message":
            text = a.get("text") or ""
            if re.search(r"consentCard|connectionManagerCard|oauthCard|signinCard", vt_raw + " " + str(a.get("name") or ""), re.I):
                consent = True
            if (a.get("from") or {}).get("role") == 1:
                if text and not re.match(r"\s*(/|-developer\b)", text):
                    users.append(text)
                    seq.append(("user", text))
                elif text:
                    seq.append(("command", text))
            elif text:
                bots.append(text)
                seq.append(("bot", text))
            continue
        vt = (vt_raw or a.get("name") or "").lower()
        if vt == "dialogtracing":
            for x in (_get(value, "actions") or []) if isinstance(value, dict) else []:
                topic, atype, aid, ex = _short(_s(x, "topicId")), _s(x, "actionType"), _s(x, "actionId"), _s(x, "exception")
                if topic:
                    topics.append(topic)
                if ex:
                    failed.append(f"{topic} {atype} ({aid}): {_head(ex, 160)}")
                elif _TOOLISH.search(atype):
                    ran.append(f"{topic} {atype}")
            continue
        if vt == "sessioninfo":
            details["sessionOutcome"] = _first(_s(value, "outcome"), _s(value, "outcomeReason"))
            details["turns"] = _s(value, "turnCount")
            continue
        if vt == "conversationinfo" and _get(value, "isDesignMode") is True:
            design = True
        elif "planreceived" in vt and "debug" not in vt:
            for x in _get(value, "steps") or [] if isinstance(value, dict) else []:
                n = x if isinstance(x, str) else _first(_s(x, "taskDialogId"), _s(x, "toolName"), _s(x, "displayName"))
                if n:
                    planned.append(_short(n))
        elif "planstep" in vt and "finish" in vt:
            name = _first(_s(value, "taskDialogId"), _s(value, "toolName"), _s(value, "displayName"), "a step")
            state = _first(_s(value, "state"), _s(value, "status"), _s(value, "result"))
            err = _first(_s(value, "error"), _s(_get(value, "error"), "message"), _s(value, "errorMessage"), _s(value, "exception"))
            if _FAILED.search(state) or err:
                failed.append(_short(name) + (f": {_head(err, 160)}" if err else ""))
            else:
                ran.append(_short(name))
        elif "planstep" in vt and "trigger" in vt:
            n = _first(_s(value, "taskDialogId"), _s(value, "toolName"), _s(value, "displayName"))
            if n:
                planned.append(_short(n))
        elif "planreceiveddebug" in vt or "planfinished" in vt or vt in ("variableassignment", "startconversation"):
            continue
        elif "search" in vt or "knowledge" in vt:
            results = _count(value, "fullResults", "filteredResults", "searchResults", "results", "outputKnowledgeSources")
            terms = _first(_s(value, "searchTerms"), _s(value, "rewrittenMessage"), _s(value, "query"))
            searches.append((f"“{_head(terms, 80)}” " if terms else "") + (f"{results} result(s)" if results >= 0 else "ran"))
            if results == 0:
                empty += 1
        elif "generativeanswers" in vt or "answersupport" in vt:
            st = _first(_s(value, "completionState"), _s(value, "gPTAnswerState"), _s(value, "answerState"))
            if st:
                answer_states.append(st)
        elif vt in ("intentrecognition", "dialogredirect"):
            t = _first(_s(value, "intentId"), _s(value, "targetDialogId"), _s(value, "topicName"))
            if t and not _GUID.fullmatch(t):
                topics.append(_short(t))
        elif vt == "unknownintent":
            unknown_intent = True
        if "error" in vt:
            code = _first(_s(value, "errorCode"), _s(value, "code"))
            msg = _first(_s(value, "errorMessage"), _s(value, "message"))
            e = ": ".join(x for x in (code, _head(msg, 160)) if x)
            if e:
                errors.append(e)

    uniq = lambda xs: list(dict.fromkeys(x for x in xs if x))
    happened = [f"{len(users)} user message(s) and {len(bots)} agent message(s)" + (" (Copilot Studio test pane)." if design else ".")]
    if planned:
        happened.append("Planned: " + ", ".join(uniq(planned)) + ".")
    if ran:
        happened.append("Ran: " + ", ".join(uniq(ran)) + ".")
    happened += [f"Failed: {f}." for f in failed]
    if topics:
        happened.append("Topics: " + ", ".join(uniq(topics)) + ".")
    happened += [f"Knowledge search: {s}." for s in searches]
    if answer_states:
        happened.append("Answer state: " + ", ".join(uniq(answer_states)) + ".")
    if consent:
        happened.append("The agent asked the user to sign in or allow a connection (consent card).")
    happened += [f"Error: {e}." for e in uniq(errors)]

    any_step = bool(planned or ran or failed or topics or searches)
    stages = {"planner": FAILED if unknown_intent and not any_step else OK if any_step else UNKNOWN}
    stages["search"] = (FAILED if failed or consent or (searches and empty == len(searches))
                        else OK if ran or searches else NOT_RUN if stages["planner"] == FAILED else UNKNOWN)
    stages["synthesis"] = FAILED if any(_NO_ANSWER.search(s) for s in answer_states) else OK if answer_states else UNKNOWN
    stages["render"] = OK if bots else UNKNOWN

    steps: list[str] = []
    cause = ""
    if consent:
        cause = ("The agent asked the user to sign in or allow a connection before running a tool, so the question wasn't "
                 "answered in this turn.")
        steps.append("Check the tool's connection: use the agent maker's connection (or a connection reference) where the "
                     "user shouldn't have to consent, or tell users to accept the connection prompt.")
    elif failed:
        cause = f"A tool or topic failed: {failed[0]}."
        steps.append("Open that tool or topic in Copilot Studio, test it with the same input and check the run history of any "
                     "flow or connector it calls.")
    elif errors:
        cause = f"The conversation hit an error: {errors[0]}."
        steps.append("Look up the error code in the Copilot Studio error reference and test the topic that was running.")
    elif stages["planner"] == FAILED:
        cause = "The agent didn't match the question to any topic, tool or knowledge source."
        steps.append("Check the descriptions of the topics and tools that should handle this question, and any instruction "
                     "that restricts when they run.")
    elif searches and empty == len(searches):
        cause = "Knowledge search ran but returned no results."
        steps.append("Check that the answer is in a configured knowledge source, and whether instructions add terms to the "
                     "search that narrow it too much.")
    elif stages["synthesis"] == FAILED:
        cause = f"The answer step didn't produce an answer ({next(s for s in answer_states if _NO_ANSWER.search(s))})."
        steps.append("If the state mentions moderation or filtering, review content moderation and any instruction that tells "
                     "the agent to refuse.")
    elif not acts:
        cause = "The transcript has no activities."
    else:
        cause = "No failure was found in the transcript."

    details.update({"activities": str(len(acts)), "testPane": "yes" if design else "",
                    "planned": ", ".join(uniq(planned)), "topics": ", ".join(uniq(topics)),
                    "failedSteps": "; ".join(failed), "knowledgeSearches": str(len(searches)) if searches else ""})
    q_low = (question or "").strip().lower()[:60]
    qi = next((i for i in range(len(seq) - 1, -1, -1) if seq[i][0] == "user" and q_low and q_low in seq[i][1].lower()), None)
    if qi is None:
        qi = next((i for i in range(len(seq) - 1, -1, -1) if seq[i][0] == "user"), None)
    asked = seq[qi][1] if qi is not None else ""
    reply = next((t for r, t in seq[qi + 1:] if r == "bot"), "") if qi is not None else (bots[-1] if bots else "")
    return {"stages": stages, "happened": happened, "likely_cause": cause, "next_steps": steps,
            "details": {k: v for k, v in details.items() if v},
            "question": asked, "response": reply,
            "searches": searches, "topics": uniq(topics), "planned": uniq(planned), "ran": uniq(ran), "failed": failed,
            "consent": consent, "conversation": _conversation_id(acts)}


def _conversation_id(acts: list[dict]) -> str:
    for a in acts:
        c = (a.get("conversation") or {}).get("id") if isinstance(a.get("conversation"), dict) else None
        if c:
            return str(c)
    return ""


def _section(title: str) -> list[str]:
    return ["", RULE, title.upper(), RULE]


_LABEL = {"sessionOutcome": "Session outcome", "turns": "Turns", "activities": "Activities", "testPane": "Test pane",
          "planned": "Planned", "topics": "Topics", "failedSteps": "Failed steps", "knowledgeSearches": "Knowledge searches"}


def build(text: str, *, question: str = "", response: str = "", current=None, snapshot: bool = False) -> str:
    docs = load(text)
    if not docs:
        raise ValueError("No Copilot Studio transcript found.")
    doc = pick(docs, question)
    d = diagnose(doc, question)
    q = d["question"] or question
    resp = response or d["response"]
    st = dict(d["stages"])
    check = instruction_check(q, resp, current) if current is not None and q else None
    cause = d["likely_cause"]
    if resp and _REFUSAL.search(resp[:600]) and st["synthesis"] != FAILED and cause.startswith("No failure"):
        st["synthesis"] = FAILED
        cause = ("The agent declined or found no answer although no step failed: an instruction, the knowledge sources or "
                 "the answer step's content moderation stopped it.")
    if check and check["followed"] is False:
        cause = "The instructions say to decline this question, but the agent answered it."
    keys = ["planner", "search", "synthesis", "render"]
    first = next((k for k in keys if st.get(k) == FAILED), None)

    L = [f"Conversation Investigation (Copilot Studio {'test pane snapshot' if snapshot else 'transcript'}) — {d['conversation'] or 'conversation ID not in transcript'}",
         f"Source: {'Copilot Studio test pane snapshot (dialog.json, botContent.yml)' if snapshot else SOURCE_LABEL}" + (", agent instructions" if current is not None else "")
         + (f" ({len(docs)} transcripts in the file; picked the one with this question)" if len(docs) > 1 else ""),
         f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
    L += _section("User question"); L.append(f'"{q}"' if q else "Not in the transcript.")
    L += _section("Agent response"); L.append(resp or "No agent message in the transcript.")
    L += _section("What happened"); L.append(cause); L += [f"- {h}" for h in d["happened"]]
    L += _section("Where it went wrong")
    for i, k in enumerate(keys):
        v = st.get(k, UNKNOWN)
        mark = "◀ FAILED HERE" if k == first else ("affected (downstream)" if first and i > keys.index(first) and v == OK else v)
        L.append(f"  {PIPELINE_ORDER[i]:<30} {mark}")
    if check:
        L += _section("Instruction check")
        for r in check["rules"]:
            L.append(f'Rule (line {r["line"]}): "{r["text"][:260]}"')
        L.append(f"Expected: {check['expected']}. Observed: {check['observed']}.")
        L.append("Result: " + ("INSTRUCTION FOLLOWED" if check["followed"] else "INSTRUCTION NOT FOLLOWED"
                               if check["followed"] is False else "UNKNOWN"))
    L += _section("Key details")
    L += [f"{_LABEL.get(k, k) + ':':<24}{v}" for k, v in d["details"].items()]
    L += _section("1 Planner / tool selection")
    L.append("Planned: " + (", ".join(d["planned"]) or "none recorded"))
    L.append("Topics: " + (", ".join(d["topics"]) or "none recorded"))
    L += _section("2 Search / tool execution")
    L += [f"  Knowledge search: {s}" for s in d["searches"]] or ["  No knowledge search recorded."]
    L += [f"  Ran: {r}" for r in d["ran"]]
    L += [f"  Failed: {f}" for f in d["failed"]]
    if d["consent"]:
        L.append("  The agent showed a sign-in / connection consent card.")
    L += _section("3 Synthesis & safety")
    L.append("Answer states and content moderation results appear above when the transcript records them.")
    L += _section("Recommended next steps")
    steps = list(d["next_steps"]) or ["Compare the answer with the topics and knowledge that ran; the instructions shape the final answer."]
    if current is None:
        steps.append("Add the agent's instructions to check which instruction line applies.")
    L += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    L += _section("What this transcript can't see")
    L.append("- Text of answers grounded on SharePoint knowledge, which transcripts store as REDACTED.")
    L.append("- Microsoft 365 Copilot's own steps for the same conversation (the handoff and its safety checks); a DevUI export shows those.")
    if not snapshot:
        L.append("- Transcripts are saved about 30 minutes after the conversation goes idle, and reading them needs the Bot Transcript Viewer role.")
    return "\n".join(L)
