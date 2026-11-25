"""Sorry triage: why an ESS agent said "sorry" (or gave no answer) across many conversations.

Feeds on developer mode (-developer on) debug cards, which makers can collect themselves, and Microsoft 365 Copilot
DevUI conversation exports (Microsoft support only; every turn is classified). Each "no answer" turn gets one cause, an owner and a
fix; causes are grouped so the team can work on the biggest one first, and the instruction lines behind the
maker-owned causes are counted across conversations.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ess_maker.doctor.agent_file import from_instructions
from ess_maker.doctor.report import _REFUSAL, instruction_check
from ess_maker.doctor.rules import validate

# Wider than the report's refusal pattern: ESS apologies and abstain messages seen in production.
SORRY = re.compile(
    _REFUSAL.pattern + r"|\bsorry\b|i (couldn['’]t|could not|can['’]t|cannot) find|couldn['’]t find a definitive answer|"
    r"no (relevant )?information (was )?found|i (don['’]t|do not) have (that|this|any) information|something went wrong|"
    r"unable to (find|provide|answer)", re.I)
SEARCH_TOOL = re.compile(r"search|find|retriev|knowledge|grounding", re.I)


@dataclass(frozen=True)
class Cause:
    id: str
    title: str
    owner: str
    fix: str
    stages: tuple[str, ...]  # instruction finding stages that can produce this cause


CAUSES = {c.id: c for c in [
    Cause("rule-decline", "Declined by an instruction rule", "Agent maker (instructions)",
          "The agent followed a restriction in the instructions. If employees should get an answer, narrow the rule to "
          "the exact subject and say what is still allowed.", ("routing", "rai", "synthesis")),
    Cause("no-search-decline", "Declined without searching", "Agent maker (instructions)",
          "No rule asks for this refusal and no tool ran. Check the routing and grounding rules: info questions must "
          "search knowledge or call a topic before answering.", ("routing", "tool", "synthesis")),
    Cause("no-tool-match", "No topic, action or knowledge matched", "Agent maker (descriptions and instructions)",
          "Rewrite the description of the topic or action that should handle this question using employees' words, and "
          "check the instructions don't steer it away from tools.", ("routing", "tool")),
    Cause("search-empty", "Knowledge search found nothing relevant", "Knowledge owner (content), then maker",
          "Check a document that answers this exists and is indexed. If it does, compare the search query with the "
          "document title; remove query enrichment (job level, country) and add synonyms to the terminology rules.",
          ("search",)),
    Cause("found-but-declined", "Search found content but the agent still declined", "Agent maker (instructions)",
          "Either the kept documents are the wrong ones (fix the query) or the abstain rule is too strict: let the agent "
          "answer from what search returned and cite it.", ("synthesis", "search")),
    Cause("action-failed", "Topic, action or connector failed", "Connection or backend admin",
          "Open the failing connection (ServiceNow, Workday, ...) and check sign-in, permissions and the instance URL; "
          "the developer mode card shows the HTTP status.", ()),
    Cause("safety-block", "Safety filter blocked the answer", "Agent maker (moderation) and content owner",
          "Review content moderation and instruction wording that resembles a jailbreak ('ignore', 'override', 'never "
          "refuse'), then retest the question.", ("rai",)),
    Cause("throttled", "Copilot throttled the request", "Platform (no maker fix)",
          "Ask again later. If it keeps happening, report it with thumbs down.", ()),
    Cause("platform-error", "Copilot Studio returned a system error", "Platform (Microsoft support), then maker (On Error topic)",
          "Re-run the question; if the same error code comes back, open a Microsoft support case with the conversation ID "
          "and time. Make sure the On Error topic has a branch for this error code (and a fallback) so employees get a "
          "friendly message.", ()),
    Cause("context-missing", "Employee details weren't loaded, so the answer was generic",
          "Agent maker (user context topic)",
          "Set up the user context topic (for example \"[Admin] - User Context - Setup\") so country, job level and similar "
          "values are filled in from the HR system or Microsoft Entra ID, and tell the instructions what to do when one is "
          "blank.", ()),
    Cause("consent-required", "Asked the user to sign in or allow a connection", "Agent maker (connections)",
          "Use a maker-provided connection (or connection reference) for tools users shouldn't have to consent to, or tell "
          "users to accept the connection prompt.", ()),
    Cause("cea-inside", "Custom engine agent: reason is inside Copilot Studio", "Collect the full Copilot Studio transcript",
          "Read the full transcripts of these conversations (Dataverse conversationtranscript, through the Business apps "
          "connector or a Power Apps export) and run the X-ray on them; they show the topic, knowledge search or error "
          "behind each sorry.", ()),
    Cause("unknown", "Not enough evidence", "Collect more evidence",
          "Collect a developer mode card for the question (Raw info JSON) with the question, the agent's answer and the "
          "instructions; with Microsoft support, a DevUI export also shows the instructions that ran and the search funnel.",
          ()),
]}


@dataclass
class Item:
    source: str
    conversation: str
    question: str
    response: str
    sorry: bool
    cause: str = ""
    detail: str = ""
    agent: str = ""
    version: str = ""
    rules: list[str] = field(default_factory=list)


def _devui_items(parsed: dict, name: str, seen: dict) -> list[Item]:
    out = []
    for t in parsed["turns"]:
        resp = t["response"] or ""
        sorry = not resp.strip() or bool(SORRY.search(resp[:800])) or bool(t["rai"]["triggered"])
        it = Item(name, parsed["conversation_id"], t["question"], resp, sorry,
                  agent=t["agent_name"] or (t["agent"] or {}).get("compliantAgentName", ""),
                  version=(t["agent"] or {}).get("version", ""))
        out.append(it)
        if not sorry:
            continue
        if t.get("cea"):
            cea = t["cea"]
            if t["rai"]["triggered"]:
                it.cause, it.detail = "safety-block", ", ".join(t["rai"]["triggered"])
            elif cea["calls"] and not cea["ok"]:
                it.cause, it.detail = "action-failed", f"handoff to {cea.get('schema') or 'Copilot Studio'} failed ({cea['calls']} call(s))"
            else:
                it.cause = "cea-inside"
                it.detail = f"{cea.get('schema') or 'Copilot Studio agent'} answered; planning and search aren't in the DevUI export"
            continue
        served = from_instructions(t["instructions"], "served", "devui") if t["instructions"] else None
        check = instruction_check(t["question"], resp, served)
        searched = [c for c in t["tool_calls"] if SEARCH_TOOL.search(c["name"])]
        runs = t.get("searches") or []
        kept = sum(r.get("kept") or 0 for r in runs) if runs else (t["citations"].get("search_results") or 0)
        failed_steps = [e for e in t["errors"] if re.search(r"Mcs|Plugin|Connector|Action|Topic", e.get("service") or "", re.I)]
        if t["rai"]["triggered"]:
            it.cause, it.detail = "safety-block", ", ".join(t["rai"]["triggered"])
        elif check and check["observed"] == "declined":
            it.cause = "rule-decline"
            it.detail = "; ".join(f"line {r['line']}: {r['text'][:140]}" for r in check["rules"][:2])
        elif failed_steps:
            it.cause, it.detail = "action-failed", "; ".join(f"{e['service']} ({e['status']})" for e in failed_steps[:3])
        elif not t["tool_calls"]:
            it.cause = "no-search-decline"
            it.detail = f"{len(t['tools_offered'])} tools offered, none called"
        elif searched and kept == 0 and not t["sources"]:
            it.cause = "search-empty"
            qs = [q for c in searched for q in c.get("queries") or []] or [q for r in runs for q in r.get("queries") or []]
            it.detail = ("query: " + " | ".join(qs[:2])) if qs else "no results kept"
        elif searched:
            it.cause = "found-but-declined"
            titles = [d.get("title") or d.get("ref") for r in runs for d in r.get("kept_docs") or []]
            it.detail = ("kept: " + ", ".join(titles[:3])) if titles else f"{kept} result(s) kept"
        else:
            it.cause, it.detail = "unknown", "tools ran: " + ", ".join(c["name"] for c in t["tool_calls"][:3])
        if served and CAUSES[it.cause].stages:
            stages = set(CAUSES[it.cause].stages)
            hits = [f for f in validate(served) if f.origin == "maker" and f.stage in stages and f.severity in ("high", "medium")]
            for f in hits:
                seen.setdefault(f.rule_id, f)
            it.rules = sorted({f.rule_id for f in hits})
    return out


def _devmode_item(diag: dict, name: str, question: str, response: str) -> Item:
    card = diag.get("card") or {}
    resp = response or ""
    st = diag.get("stages") or {}
    it = Item(name, card.get("conversation_id") or "", question, resp,
              sorry=not resp or bool(SORRY.search(resp[:800])) or "failed" in st.values(),
              agent=card.get("agent_id") or "", version=card.get("agent_version") or "")
    if not it.sorry:
        return it
    cause_text = diag.get("likely_cause") or ""
    failed_actions = [a for a in card.get("exec_actions") or [] if a.get("ok") is False]
    if card.get("throttled") and not (card.get("exec_caps") or card.get("exec_actions")):
        it.cause = "throttled"
    elif failed_actions or re.search(r"HTTP (401|403|404|429|5\d\d)|timed out|didn't run", cause_text):
        it.cause = "action-failed"
    elif st.get("planner") == "failed" and (card.get("no_selected") or card.get("no_matched")):
        it.cause = "no-tool-match"
    elif st.get("planner") == "failed":
        it.cause = "no-search-decline"
    elif st.get("search") == "failed":
        it.cause = "search-empty"
    elif st.get("synthesis") == "failed":
        it.cause = "found-but-declined"
    else:
        it.cause = "unknown"
    it.detail = cause_text[:220]
    return it


def _transcript_item(diag: dict, name: str) -> Item:
    st, resp = diag["stages"], diag["response"] or ""
    it = Item(name, diag.get("conversation") or "", diag["question"], resp,
              sorry=not resp or bool(SORRY.search(resp[:800])) or "failed" in st.values() or diag["consent"])
    if not it.sorry:
        return it
    if diag["consent"]:
        it.cause = "consent-required"
    elif diag.get("context_missing"):
        it.cause = "context-missing"
    elif diag["failed"]:
        it.cause = "action-failed"
    elif diag.get("errors") or re.search(r"\bError code:\s*\w+", resp[:400]):
        it.cause = "platform-error"
    elif st.get("planner") == "failed":
        it.cause = "no-tool-match"
    elif st.get("search") == "failed":
        it.cause = "search-empty"
    elif diag["searches"]:
        it.cause = "found-but-declined"
    elif diag["planned"] or diag["topics"]:
        it.cause = "rule-decline" if SORRY.search(resp[:800]) and not diag["ran"] else "unknown"
    else:
        it.cause = "no-search-decline"
    topics = ", ".join(diag["topics"][:3])
    it.detail = (diag["likely_cause"][:160] + (f" Topics: {topics}." if topics else ""))[:220]
    return it


def _session_item(row: dict, name: str) -> Item:
    from ess_maker.doctor.transcript import classify_session

    resp = row["response"] or ""
    topic = row["topic"] or ""
    v = classify_session(row)
    it = Item(name, row["session"], row["question"], v.get("reply") or resp,
              sorry=v["kind"] not in ("ok", "command"))
    if not it.sorry:
        return it
    it.cause = {"error": "platform-error", "fallback": "no-tool-match", "generic": "context-missing"}.get(v["kind"], "cea-inside")
    if re.search(r"on ?error", topic, re.I) and v["kind"] != "error":
        it.cause = "action-failed"
    it.detail = (f"{v['detail']} Last topic: {topic or 'none'}; outcome: {row['outcome'] or '?'}"
                 f"{(' (' + row['reason'] + ')') if row['reason'] else ''}")[:220]
    return it


def triage(sources: list[tuple[str, str, object]], instructions: str = "", cea: bool = False) -> dict:
    """sources: (kind, name, payload) with kind "devui" (parsed export), "transcript" (diagnosis dict), "session"
    (sessions CSV row) or "devmode" (diagnosis dict, question, response). cea: the instructions belong to a custom
    engine agent, whose template routes through topics, so the declarative agent core-block rules don't apply."""
    items: list[Item] = []
    seen: dict = {}
    for kind, name, payload in sources:
        if kind == "devui":
            items += _devui_items(payload, name, seen)
        elif kind == "transcript":
            items.append(_transcript_item(payload, name))
        elif kind == "session":
            items.append(_session_item(payload, name))
        else:
            diag, q, r = payload
            items.append(_devmode_item(diag, name, q, r))
    sorry = [i for i in items if i.sorry]
    counts = Counter(i.cause for i in sorry)
    current = from_instructions(instructions, "current") if instructions.strip() else None
    current_list = validate(current) if current else []
    if cea:
        current_list = [f for f in current_list if not (f.rule_id.startswith("L2-") and f.rule_id.endswith("-MISSING"))]
    current_findings = {f.rule_id: f for f in current_list}
    # Evidence without the instructions that ran (developer mode cards): link the current instructions' rules.
    for i in sorry:
        stages = set(CAUSES[i.cause].stages)
        if i.rules or not stages or not current_list:
            continue
        words = {w for w in re.findall(r"[a-z]{5,}", i.question.lower())}
        for f in current_list:
            if f.origin != "maker" or f.stage not in stages or f.severity not in ("high", "medium"):
                continue
            restriction = re.search(r"BLOCK|RESTRICT|REFUSAL|OVERRIDE|JURISDICTION", f.rule_id)
            if restriction and not any(w in (f.current or "").lower() for w in words):
                continue
            i.rules.append(f.rule_id)
    rule_hits: dict[str, set] = defaultdict(set)
    for i in sorry:
        for r in i.rules:
            rule_hits[r].add((i.conversation, i.question))
    return {"turns": len(items), "sorry": len(sorry), "counts": counts, "items": items,
            "rules": sorted(((r, len(v)) for r, v in rule_hits.items()), key=lambda x: -x[1]),
            "current_findings": current_findings, "served_findings": seen}


def _cell(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).replace("|", "\\|").strip()


def build(result: dict) -> str:
    items, sorry, counts = result["items"], [i for i in result["items"] if i.sorry], result["counts"]
    n = len(sorry)
    L = ["## ESS Doctor sorry triage", ""]
    L.append(f"{result['turns']} turn(s) analyzed; {n} ended in a sorry, an error or no answer"
             + (f" ({n * 100 // max(1, result['turns'])}%)." if result["turns"] else "."))
    if not n:
        L += ["", "No sorry answers in this evidence. Upload developer mode cards (or DevUI exports, with Microsoft support) "
                  "of conversations where the agent apologized or gave no answer."]
        return "\n".join(L)
    L += ["", "### Causes, biggest first", "", "| Cause | Turns | Share | Who fixes it | Fix |", "| --- | --- | --- | --- | --- |"]
    for cid, c in counts.most_common():
        cause = CAUSES[cid]
        L.append(f"| {cause.title} | {c} | {c * 100 // n}% | {cause.owner} | {_cell(cause.fix)} |")
    maker = sum(c for cid, c in counts.items() if CAUSES[cid].owner.startswith("Agent maker"))
    L += ["", f"**{maker} of {n}** sorry answers have causes the maker can fix in the agent (instructions, descriptions, "
              "moderation or connections) without waiting for content or backend changes."]
    if result["rules"]:
        L += ["", "### Instruction rules behind the maker-owned sorries", "",
              "| What's wrong | Sorry turns | Fix |", "| --- | --- | --- |"]
        cf = result["current_findings"]
        ordered = sorted(result["rules"], key=lambda x: (bool(cf) and x[0] not in cf, -x[1]))
        gone_titles = []
        for rid, k in ordered[:10]:
            f = cf.get(rid) or result["served_findings"].get(rid)
            title = re.sub(r"\s*\(template '.*$", "", f.title) if f else "A rule in the instructions that ran"
            if f is not None and f.lines and f.current != "(not present)":
                title += " (line " + ", ".join(map(str, f.lines[:3])) + ")"
            fix = f.fix if f else "Run the instruction check on the current instructions to see the line and fix."
            L.append(f"| {_cell(title)[:160]} | {k} | {_cell(fix)[:220]} |")
            if cf and rid not in cf:
                gone_titles.append(re.sub(r"\s*\(template '.*$", "", f.title) if f else "a rule")
        if gone_titles:
            L.append(f"\nAlready fixed in the current instructions: {'; '.join(gone_titles)}.")
    L += ["", "### Every sorry turn", "", "| # | Question | Agent said | Cause | Evidence |", "| --- | --- | --- | --- | --- |"]
    for k, i in enumerate(sorry, 1):
        L.append(f"| {k} | {_cell(i.question)[:120]} | {_cell(i.response)[:120] or '(no answer)'} | {CAUSES[i.cause].title} | {_cell(i.detail)[:180]} |")
    versions = sorted({(i.agent, i.version) for i in items if i.version})
    if versions:
        L += ["", "Agent versions in this evidence: " + ", ".join(f"{a or 'agent'} {v}" for a, v in versions) + "."]
    present = [c for c, _ in Counter(i.cause for i in items if i.sorry).most_common()]
    step_for = {
        "rule-decline": "Run the instruction check and narrow the restriction rules behind these declines.",
        "no-search-decline": "Run the instruction check: the grounding and abstain rules make the agent decline before searching.",
        "found-but-declined": "Run the instruction check: the agent found content but its rules stopped the answer.",
        "no-tool-match": "Improve the descriptions of the topics and tools that should handle these questions.",
        "context-missing": "Set up the user context topic so country, job level and similar details are filled in, then re-test.",
        "search-empty": "Send the questions where search found nothing relevant to the knowledge owner, with the questions listed above.",
        "action-failed": "Send the failing topics or connectors to the connection admin, with the conversation details above.",
        "consent-required": "Switch those tools to a maker-provided connection, or tell employees to accept the connection prompt.",
        "safety-block": "Review content moderation and any instruction that adds sensitive wording to answers.",
        "platform-error": "Re-run the questions that hit a system error; if it repeats, open a Microsoft support case with the "
                          "conversation IDs, and add a branch for the error code to the On Error topic.",
        "throttled": "Throttling is on the platform side; re-test later.",
        "cea-inside": "Get the Copilot Studio transcripts or test pane snapshots of these conversations and triage them again.",
        "unknown": "Collect a transcript, test pane snapshot or developer mode card for these conversations and triage them again.",
    }
    steps = list(dict.fromkeys(step_for[c] for c in present if c in step_for))[:4]
    steps.append("Re-run the same questions after publishing and triage the new evidence to confirm the sorry share dropped.")
    L += ["", "### Next steps", ""] + [f"{n}. {s}" for n, s in enumerate(steps, 1)]
    return "\n".join(L)
