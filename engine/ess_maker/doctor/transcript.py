"""Copilot Studio conversation transcript X-ray (custom engine agents).

Reads ``conversationtranscript.content`` (``{"activities": [...]}``). Messages come from ``type: message``
(``from.role`` 1 or "user" = user, 0 or "bot" = agent). Orchestration, knowledge search, topic and error details come from event and
trace activities, matched on ``valueType`` by keyword because the generative-orchestration value types aren't a
documented contract. Accepts the raw content, a Dataverse row or row list, or the Copilot Studio Monitor > Sessions
CSV (a column holding the transcript JSON).
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone

from ess_maker.doctor.report import RULE, _FALLBACK, _HEDGE, _REFUSAL, instruction_check, synthesis_reason
from ess_maker.doctor.rules import PIPELINE_ORDER

OK, FAILED, NOT_RUN, UNKNOWN = "ok", "failed", "not run", "unknown"
SOURCE_LABEL = "Copilot Studio conversation transcript"
_FAILED = re.compile(r"fail|error|fault|cancel|timed?\s*out|denied|forbidden|unauthori", re.I)
_NO_ANSWER = re.compile(r"nosearchresults|noresults|notanswered|unanswered|filtered|moderat|blocked|noanswer", re.I)
_TOOLISH = re.compile(r"Connector|Flow|Http|Plugin|Tool|Search|Prompt", re.I)
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PROFILE_ASK = re.compile(r"employee profile|(country|office|location)[^,.;]{0,40}(employment type|job level|tenure|start date)", re.I)


def _pretty(name: str) -> str:
    n = _short(name)
    return "Knowledge search" if re.fullmatch(r"(P:)?UniversalSearchTool", n or "", re.I) else n.removeprefix("P:")


def _dedupe_refs(text: str) -> str:
    """Copilot Studio can append the same ``[n]: url`` reference list twice; keep the first copy of each line."""
    seen, out = set(), []
    for line in text.split("\n"):
        key = line.strip()
        if re.match(r"\[\d+\]:\s", key):
            if key in seen:
                continue
            seen.add(key)
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


_FRIENDLY = {"ESS_UserContext_Country_Code": "country (ESS_UserContext_Country_Code)",
             "ESS_UserContext_Level": "job level (ESS_UserContext_Level)",
             "ESS_UserContext_TimeZoneOffset": "time zone (ESS_UserContext_TimeZoneOffset)",
             "ESS_UserContext_Is_Manager": "manager flag (ESS_UserContext_Is_Manager)",
             "ESS_UserContext_Company_Code": "company (ESS_UserContext_Company_Code)"}


def bot_findings(bot_yaml: str) -> dict:
    """Configuration gaps in a snapshot's botContent.yml: user-context topics with no steps, and the
    ``Global.ESS_UserContext_*`` variables the instructions depend on."""
    empty = []
    comps = re.split(r"\n  - kind: ", bot_yaml or "")
    for comp in comps:
        if not comp.startswith("DialogComponent"):
            continue
        name = re.search(r"\n\s*displayName:\s*\"?(.+?)\"?\s*\n", comp)
        schema = re.search(r"\n\s*schemaName:\s*(\S+)", comp)
        label = (name.group(1) if name else "") + " " + (schema.group(1) if schema else "")
        if re.search(r"user\s*context|usercontext", label, re.I) and re.search(r"setup|set\s*user|setusercontext", label, re.I) \
                and "beginDialog:" in comp and not re.search(r"\n\s*actions:", comp):
            empty.append(name.group(1) if name else schema.group(1))
    gpt = next((c for c in comps if c.startswith("GptComponent")), "")
    instr = gpt[gpt.find("instructions:"):] if "instructions:" in gpt else ""
    used = sorted(set(re.findall(r"Global\.(ESS_UserContext_\w+)", instr)))
    return {"empty_context_topics": empty, "context_vars": used}


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
    """Rows of a Copilot Studio Monitor > Sessions download (CSV with a ChatTranscript column)."""
    t = (text or "").lstrip("\ufeff")
    head = t[:2000].lower()
    if "chattranscript" not in head or "sessionid" not in head:
        return []
    csv.field_size_limit(10_000_000)
    rows = list(csv.DictReader(io.StringIO(t)))
    out = []
    for r in rows:
        r = {re.sub(r"\(.*\)$", "", (k or "").strip()).lower(): v or "" for k, v in r.items()}
        chat = r.get("chattranscript") or ""
        parts = [p.strip() for p in re.split(r";\s*(?=(?:User|Bot|Agent) says:)", chat) if p.strip()]
        pairs: list[list[str]] = []
        for p in parts:
            if p.startswith("User says:"):
                pairs.append([re.sub(r"^User says:\s*", "", p).rstrip(";").strip(), ""])
            elif re.match(r"(Bot|Agent) says:", p):
                text = _clean_reply(re.sub(r"^(Bot|Agent) says:\s*", "", p).rstrip(";"))
                if pairs and not pairs[-1][1]:
                    pairs[-1][1] = text
                else:
                    pairs.append(["", text])
        users = [u for u, _ in pairs if u]
        bots = [b for _, b in pairs if b]
        out.append({"session": r.get("sessionid") or "", "start": r.get("startdatetime") or "",
                    "outcome": r.get("sessionoutcome") or "", "reason": r.get("outcomereason") or "",
                    "topic": r.get("topicname") or "", "channel": r.get("channel") or "",
                    "question": r.get("initialusermessage") or (users[0] if users else ""),
                    "response": bots[-1] if bots else "", "first_response": bots[0] if bots else "",
                    "turns": r.get("turns") or "", "pairs": [tuple(p) for p in pairs]})
    return out


def _clean_reply(text: str) -> str:
    """Agent text from a sessions download without its HTML wrapper."""
    t = re.sub(r"</?(div|m-official|p|span|br)[^>]*>", " ", text or "", flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip()


_MISSING = re.compile(r"(?:does not|doesn['’]t) (?:provide|include|have|show|list) (?:your |the )?(.{3,160}?)(?:\.|$)", re.I)


def classify_exchange(question: str, reply: str, topic: str = "") -> dict:
    """What a single question and reply in a sessions download show. The download holds only text, so the verdict
    comes from the reply wording: kind is ok, command, fallback, generic, declined, error or empty."""
    from ess_maker.doctor.report import _ABSTAIN_TEXT, _FALLBACK

    q, r = (question or "").strip(), (reply or "").strip()
    if q.startswith("/"):
        return {"kind": "command", "label": "Command typed in the chat",
                "detail": f'"{q}" isn\'t a question; the agent sent it to Fallback.' if _FALLBACK.search(r) else
                f'"{q}" isn\'t a question.'}
    if not r:
        return {"kind": "empty", "label": "No reply", "detail": "The agent didn't reply."}
    m = re.search(r"\bError code:\s*(\w+)", r)
    if m or re.search(r"on ?error", topic, re.I):
        return {"kind": "error", "label": "System error", "detail": f"Error {m.group(1) if m else 'in the On Error topic'}."}
    if _FALLBACK.search(r[:400]) or re.search(r"fallback|unknown intent", topic, re.I):
        return {"kind": "fallback", "label": "No topic or knowledge matched",
                "detail": "The agent gave its Fallback reply: no topic matched and nothing usable came back from knowledge."}
    if _HEDGE.search(r[:1500]):
        mm = _MISSING.search(r[:1500])
        missing = re.sub(r"\s+", " ", mm.group(1)).strip(" *") if mm else ""
        return {"kind": "generic", "label": "Generic answer: employee details missing",
                "detail": ("The agent found content but couldn't tell which part applies"
                           + (f" because the profile doesn't provide {missing}." if missing else "; it said it couldn't verify a detail.")),
                "missing": missing}
    if _REFUSAL.search(r[:600]) or _ABSTAIN_TEXT.search(r[:600]):
        return {"kind": "declined", "label": "Declined or found nothing", "detail": _head(r, 160)}
    return {"kind": "ok", "label": "Answered", "detail": ""}


_SEVERITY = ["fallback", "error", "empty", "generic", "declined", "command", "ok"]


def classify_session(row: dict) -> dict:
    """The worst exchange in a session decides its verdict; typed commands only count when nothing else went wrong."""
    pairs = row.get("pairs") or [(row.get("question", ""), row.get("response", ""))]
    found = [dict(classify_exchange(q, r, row.get("topic", "")), question=q, reply=r) for q, r in pairs if q or r]
    found = found or [dict(classify_exchange(row.get("question", ""), ""), question=row.get("question", ""), reply="")]
    worst = min(found, key=lambda x: _SEVERITY.index(x["kind"]))
    return {**worst, "exchanges": found}


def _messages(doc: dict) -> tuple[list[str], list[str]]:
    users, bots = [], []
    for a in _activities_of(doc):
        if a.get("type") == "message":
            role = (a.get("from") or {}).get("role")
            text = a.get("text") or ""
            (users if role in (1, "1", "user") else bots).append(text)
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
    srch: list[dict] = []
    pending_terms = ""
    pii_queries: list[str] = []
    profile_searches: list[str] = []

    for a in acts:
        typ, vt_raw = a.get("type") or "", a.get("valueType") or ""
        value = a.get("value")
        if typ == "message":
            text = a.get("text") or ""
            if re.search(r"consentCard|connectionManagerCard|oauthCard|signinCard", vt_raw + " " + str(a.get("name") or ""), re.I):
                consent = True
            if (a.get("from") or {}).get("role") in (1, "1", "user"):
                if text and not re.match(r"\s*(/|-developer\b)", text):
                    users.append(text)
                    seq.append(("user", text))
                elif text:
                    seq.append(("command", text))
            elif text:
                text = _dedupe_refs(re.sub(r"<[^>]{1,80}>", "", text).strip())
                bots.append(text)
                seq.append(("bot", text))
            continue
        vt = (vt_raw or a.get("name") or "").lower()
        if vt.startswith("dialogtracing"):
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
                    planned.append(_pretty(n))
        elif "planstep" in vt and "finish" in vt:
            name = _first(_s(value, "taskDialogId"), _s(value, "toolName"), _s(value, "displayName"), "a step")
            state = _first(_s(value, "state"), _s(value, "status"), _s(value, "result"))
            err = _first(_s(value, "error"), _s(_get(value, "error"), "message"), _s(value, "errorMessage"), _s(value, "exception"))
            sr = _get(_get(value, "observation"), "search_result")
            if isinstance(sr, dict):
                res = _get(sr, "search_results") if isinstance(_get(sr, "search_results"), list) else []
                errs = [_head(json.dumps(e) if not isinstance(e, str) else e, 160) for e in _get(sr, "search_errors") or []]
                rec = srch[-1] if srch and not srch[-1].get("from_step") else None
                if rec is None:
                    rec = {"terms": pending_terms, "count": 0, "docs": [], "errors": []}
                    srch.append(rec)
                rec.update(count=len(res), docs=[_s(r, "Name") or _s(r, "Url") for r in res], from_step=True)
                rec["errors"] = rec.get("errors", []) + errs
                pending_terms = ""
            if _FAILED.search(state) or err:
                failed.append(_pretty(name) + (f": {_head(err, 160)}" if err else ""))
            else:
                ran.append(_pretty(name))
        elif "planstep" in vt and "bind" in vt:
            args = _get(value, "arguments")
            if isinstance(args, dict):
                q = _first(_s(args, "search_query"), _s(args, "search_keywords"), _s(args, "query"), _s(args, "searchQuery"))
                if q:
                    pending_terms = q
                    if _EMAIL.search(q):
                        pii_queries.append(q)
                    if _PROFILE_ASK.search(q):
                        profile_searches.append(q)
        elif "planstep" in vt and "trigger" in vt:
            n = _first(_s(value, "taskDialogId"), _s(value, "toolName"), _s(value, "displayName"))
            if n:
                planned.append(_pretty(n))
        elif "planreceiveddebug" in vt or "planfinished" in vt or vt in ("variableassignment", "startconversation"):
            continue
        elif "search" in vt or "knowledge" in vt:
            full = _get(value, "fullResults")
            results = len(full) if isinstance(full, list) else _count(value, "filteredResults", "searchResults", "results")
            terms = _first(_s(value, "searchTerms"), _s(value, "rewrittenMessage"), _s(value, "query"), pending_terms)
            errs = [_head(json.dumps(e) if not isinstance(e, str) else e, 160) for e in _get(value, "searchErrors") or []]
            srch.append({"terms": terms, "count": results, "errors": errs,
                         "docs": [_first(_s(_get(r, "metadata"), "title"), _s(r, "Name"), _s(r, "url")) for r in full or [] if isinstance(r, dict)]})
            pending_terms = ""
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
    search_failed = []
    for s in srch:
        c = s.get("count", -1)
        docs = [d for d in s.get("docs") or [] if d]
        label = (f"“{_head(s['terms'], 100)}” " if s.get("terms") else "") + (f"{c} result(s)" if c is not None and c >= 0 else "ran")
        if docs:
            label += ": " + "; ".join(uniq(docs)[:5]) + ("…" if len(uniq(docs)) > 5 else "")
        searches.append(label)
        if c == 0:
            empty += 1
        if s.get("errors"):
            search_failed.append("knowledge search error: " + "; ".join(s["errors"][:2]))
    failed += search_failed
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

    q_low = (question or "").strip().lower()[:60]
    qi = next((i for i in range(len(seq) - 1, -1, -1) if seq[i][0] == "user" and q_low and q_low in seq[i][1].lower()), None)
    if qi is None:
        qi = next((i for i in range(len(seq) - 1, -1, -1) if seq[i][0] == "user"), None)
    asked = seq[qi][1] if qi is not None else ""
    reply = next((t for r, t in seq[qi + 1:] if r == "bot"), "") if qi is not None else (bots[-1] if bots else "")
    hedged = bool(reply and _HEDGE.search(reply[:1500]))
    fallback = bool(reply and _FALLBACK.search(reply[:400]))
    if pii_queries:
        happened.append("The search query included the employee's name or email address.")
    if profile_searches:
        happened.append("The agent searched the knowledge sources for the employee's own profile (country, employment type, job level).")

    any_step = bool(planned or ran or failed or topics or searches)
    stages = {"planner": FAILED if (unknown_intent or fallback) and not any_step else OK if any_step else UNKNOWN}
    stages["search"] = (FAILED if failed or consent or (searches and empty == len(searches))
                        else OK if ran or searches else NOT_RUN if stages["planner"] == FAILED else UNKNOWN)
    stages["synthesis"] = (FAILED if any(_NO_ANSWER.search(s) for s in answer_states)
                           else OK if answer_states or reply else UNKNOWN)
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
    elif hedged and profile_searches:
        stages["synthesis"] = FAILED
        cause = ("The searches worked, but the agent answered generically: it looked for the employee's own profile "
                 "(country, employment type, job level) in the knowledge documents, which don't hold it, so it couldn't tell "
                 "which policy applies.")
        steps.append("Give the agent the employee's profile from the HR system (the Workday or SuccessFactors profile topic, or "
                     "the user context variables) and tell the instructions to use those values to pick the policy instead of "
                     "searching documents for them.")
    elif hedged:
        stages["synthesis"] = FAILED
        cause = (("The searches worked, but the agent" if searches else "The agent")
                 + " answered generically because it couldn't verify details it needed (see its reply).")
        steps.append("Check which detail the agent said it couldn't verify, and make sure a topic or variable provides it.")
    elif not acts:
        cause = "The transcript has no activities."
    else:
        cause = "No failure was found in the transcript."
    if pii_queries:
        steps.append("Keep personal details out of search queries: tell the instructions to search with the employee's question "
                     "plus only context that changes the answer (for example country), never names, emails or IDs.")

    details.update({"activities": str(len(acts)), "testPane": "yes" if design else "",
                    "planned": ", ".join(uniq(planned)), "topics": ", ".join(uniq(topics)),
                    "failedSteps": "; ".join(failed), "knowledgeSearches": str(len(searches)) if searches else ""})
    return {"stages": stages, "happened": happened, "likely_cause": cause, "next_steps": steps,
            "details": {k: v for k, v in details.items() if v},
            "question": asked, "response": reply,
            "searches": searches, "topics": uniq(topics), "planned": uniq(planned), "ran": uniq(ran), "failed": failed,
            "consent": consent, "errors": uniq(errors), "conversation": _conversation_id(acts)}


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


def build(text: str, *, question: str = "", response: str = "", current=None, snapshot: bool = False,
          bot_yaml: str = "", kind: str = "", extra_happened: list[str] | None = None) -> str:
    kind = kind or ("snapshot" if snapshot else "transcript")
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
    no_question = not q and not any(_messages(doc)[0])
    if no_question:
        cause = ("The snapshot has no question in it: it was saved before anything was asked, so it only shows the "
                 "welcome message. It still shows the agent's setup." if kind == "snapshot" else
                 "The conversation has no user message, so there's no answer to diagnose.")
    happened = list(d["happened"]) + list(extra_happened or [])
    steps = list(d["next_steps"])
    bf = bot_findings(bot_yaml)
    if bf["empty_context_topics"]:
        topic = bf["empty_context_topics"][0]
        vars_ = ", ".join(_FRIENDLY.get(v, v) for v in bf["context_vars"]) or "the user context variables"
        happened.append(f'The "{topic}" topic has no steps, so the employee\'s details ({vars_}) were never filled in.')
        if bf["context_vars"]:
            happened.append("The instructions use those variables (for example to add country and job level to searches), "
                            "so they ran with blank values.")
        if "employee's own profile" in cause or "couldn't verify" in cause:
            cause = (f'The employee\'s details never loaded: the "{topic}" topic has no steps, so country and job level were '
                     "blank. The searches worked, but the agent then looked for the employee's profile in the knowledge "
                     "documents, couldn't find it and answered generically.")
            steps = [s for s in steps if not s.startswith("Give the agent the employee's profile")]
        steps.insert(0, f'Set up the "{topic}" topic so it fills in {vars_} from the HR system (Workday, SuccessFactors, '
                        "ServiceNow) or Microsoft Entra ID.")
        if bf["context_vars"]:
            steps.insert(1, "Add an instruction for when a profile value is blank: never search the knowledge sources for "
                            "the employee's own record; ask the employee for the missing detail (for example country), or "
                            "give the base policy and list the variations.")
    keys = ["planner", "search", "synthesis", "render"]
    if no_question:
        st = {k: UNKNOWN for k in keys}
        steps.append("Ask the failing question in the Copilot Studio test pane, then open ⋯ > Save snapshot right after the "
                     "answer and upload that snapshot for a step-by-step X-ray." if kind == "snapshot" else
                     "Pick the transcript that contains the employee's question.")
    first = next((k for k in keys if st.get(k) == FAILED), None)

    title = {"snapshot": "test pane snapshot", "appinsights": "Application Insights logs"}.get(kind, "transcript")
    label = {"snapshot": "Copilot Studio test pane snapshot (dialog.json, botContent.yml)",
             "appinsights": "Application Insights telemetry of the Copilot Studio agent"}.get(kind, SOURCE_LABEL)
    L = [f"Conversation Investigation (Copilot Studio {title}) — {d['conversation'] or 'conversation ID not in the evidence'}",
         f"Source: {label}" + (", agent instructions" if current is not None else "")
         + (f" ({len(docs)} transcripts in the file; picked the one with this question)" if len(docs) > 1 else ""),
         f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
    L += _section("User question"); L.append(f'"{q}"' if q else "Not in the transcript.")
    L += _section("Agent response"); L.append(resp or "No agent message in the transcript.")
    L += _section("What happened"); L.append(cause); L += [f"- {h}" for h in happened]
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
    reason = synthesis_reason(resp) if st.get("synthesis") == FAILED else ""
    if reason and bf["empty_context_topics"] and "hedged" in reason:
        reason += f' Here the missing details are the user context values the "{bf["empty_context_topics"][0]}" topic should set.'
    L.append(reason or "Answer states and content moderation results appear above when the transcript records them.")
    L += _section("Recommended next steps")
    steps = steps or ["Compare the answer with the topics and knowledge that ran; the instructions shape the final answer."]
    if current is None:
        steps.append("Add the agent's instructions to check which instruction line applies.")
    L += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    L += _section("What this transcript can't see" if kind == "transcript" else "What this evidence can't see")
    if kind == "transcript":
        L.append("- Text of answers grounded on SharePoint knowledge, which transcripts store as REDACTED.")
    if kind == "appinsights":
        L.append("- Topic and instruction setup, such as an empty user context topic; a test pane snapshot shows those.")
        L.append("- Message text and tool arguments and results unless the telemetry settings log them (agent-level: Log "
                 "conversation details; environment-level spans include them by default).")
    L.append("- Microsoft 365 Copilot's own steps for the same conversation (the handoff and its safety checks); a DevUI export shows those.")
    if kind == "transcript":
        L.append("- Transcripts are saved about 30 minutes after the conversation goes idle, and reading them needs the Bot Transcript Viewer role.")
    return "\n".join(L)
