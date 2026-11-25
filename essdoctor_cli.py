"""ESS Doctor command line, packaged as scripts/essdoctor.pyz in the Copilot Cowork plugin.

Every command prints a Markdown report on stdout and exits 0; problems with the input print a one-line
"ERROR: ..." on stderr and exit 2. Commands that produce new instructions also write them to --out so the
maker can download the file from Cowork's output folder.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

from ess_maker.doctor import devui, evaltriage, evidence, practices
from ess_maker.doctor.agent_file import AgentDoc, from_instructions, parse_text
from ess_maker.doctor.blocks import diff_docs
from ess_maker.doctor.condense import condense, studio_len
from ess_maker.doctor.drafter import CHANGE_TYPES, Intake, draft
from ess_maker.doctor.fixes import LENGTH_TARGET, fix
from ess_maker.doctor.rules import CORE_BLOCKS, DA_INSTRUCTION_LIMIT, core_presence, validate

TEMPLATE_MODERATION = {"hr": "Medium", "it": "Medium", "hub": "High"}


class InputError(Exception):
    """The maker's file or arguments can't be used; the message says what to provide instead."""


# --- Loading ----------------------------------------------------------------------------------------------


def _read(path: str) -> bytes:
    p = Path(path)
    if not p.is_file():
        raise InputError(f"File not found: {path}")
    return p.read_bytes()


def _text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _manifest_instructions(obj) -> tuple[str, str] | None:
    """(name, instructions) from a declarative agent manifest or any JSON that carries an instructions string."""
    if isinstance(obj, dict):
        ins = obj.get("instructions")
        if isinstance(ins, str) and len(ins.strip()) > 40:
            return str(obj.get("name") or obj.get("displayName") or "Agent"), ins
        for v in obj.values():
            r = _manifest_instructions(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _manifest_instructions(v)
            if r:
                return r
    return None


def _component_instructions(text: str) -> str | None:
    """Instructions block of a Copilot Studio instructions component (botcomponent.data, kind: GptComponentMetadata)."""
    lines = text.splitlines()
    if not any(ln.strip() == "kind: GptComponentMetadata" for ln in lines[:5]):
        return None
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)instructions:\s*[|>][-+]?\s*$", ln)
        if not m:
            continue
        key, body, out = len(m.group(1)), None, []
        for nxt in lines[i + 1:]:
            if nxt.strip():
                ind = len(nxt) - len(nxt.lstrip())
                if ind <= key:
                    break
                body = ind if body is None else body
                out.append(nxt[body:] if ind >= body else nxt.strip())
            else:
                out.append("")
        return "\n".join(out).rstrip()
    return None


def _from_text(text: str, name: str, source: str) -> AgentDoc | None:
    component = _component_instructions(text)
    if component:
        return from_instructions(component, name, source)
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            found = _manifest_instructions(json.loads(stripped))
        except ValueError:
            found = None
        if found:
            return from_instructions(found[1], found[0], source)
    if re.search(r"(?m)^\s*instructions:\s*[|>]", text):
        doc = parse_text(text, name, source)
        if doc.instructions:
            return doc
    return None


def load_agent(path: str, name: str = "") -> AgentDoc:
    """Agent instructions from pasted text, a Copilot Studio agent YAML, a declarative agent manifest or an export .zip."""
    data = _read(path)
    label = name or Path(path).stem
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries = sorted((i for i in z.infolist() if not i.is_dir() and i.file_size < 5_000_000),
                             key=lambda i: (not re.search(r"(?i)declarativeagent|gpt|manifest|agent", i.filename), i.filename))
            for info in entries:
                if not re.search(r"(?i)\.(json|ya?ml|txt|md)$|/data$|^data$", info.filename):
                    continue
                doc = _from_text(_text(z.read(info)), label, f"zip:{info.filename}")
                if doc and doc.instructions:
                    return doc
        raise InputError("No agent instructions found in this .zip. Upload the agent export package, the agent YAML, "
                         "or paste the instructions as text.")
    text = _text(data)
    doc = _from_text(text, label, f"file:{Path(path).name}")
    if doc is None:
        doc = from_instructions(text, label, "pasted")
    if not doc.instructions or not doc.instruction_text.strip():
        raise InputError("The instructions are empty.")
    return doc


def _template_text(persona: str) -> str:
    here = Path(__file__).resolve().parent
    local = here / "templates" / f"template_{persona}.txt"
    if local.is_file():
        return local.read_text(encoding="utf-8")
    import pkgutil

    data = pkgutil.get_data("templates", f"template_{persona}.txt")
    return data.decode("utf-8") if data else ""


def guess_persona(doc: AgentDoc) -> str:
    s = (doc.bot_schema or doc.name or "").lower()
    if s.endswith("it") or "it." in s or s.endswith("dait") or re.search(r"\bit\b", s):
        return "it"
    if "core" in s or "hub" in s:
        return "hub"
    return "hr"


def baseline(persona: str) -> AgentDoc | None:
    if persona == "none":
        return None
    text = _template_text(persona)
    if not text:
        return None
    doc = from_instructions(text, f"ESS template ({persona.upper()})", f"baseline:{persona}")
    doc.content_moderation = TEMPLATE_MODERATION[persona]
    return doc


def _persona(arg: str, doc: AgentDoc) -> str:
    return guess_persona(doc) if arg == "auto" else arg


def _cell(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).replace("|", "\\|").strip()


def _lines(f: dict) -> str:
    return ", ".join(str(n) for n in (f.get("lines") or [])[:6]) or "-"


def _write(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text.rstrip("\n") + "\n", encoding="utf-8")


def _evalset(a, tests: list, beside: str = "") -> list[str]:
    """Write the Copilot Studio test set for this change and return the report lines that point to it."""
    from ess_maker.doctor import evalset

    if getattr(a, "no_evalset", False):
        return []
    path = getattr(a, "evalset", None) or str(Path(beside).parent / "evalset.csv" if beside else Path("outputs/evalset.csv"))
    rows = evalset.rows_for(tests)
    _write(path, evalset.to_csv(rows))
    own = min(len(rows), len(evalset.rows_for(tests, include_golden=False)))
    return ["### Evaluation test set", "",
            f"`{path}` has {len(rows)} questions: {own} for this change and {len(rows) - own} ESS regression questions, "
            "each with the expected response. " + evalset.IMPORT_STEPS, ""]


_DIFF_STATUS = {"added": "➕ Added", "removed": "➖ Removed", "modified": "✏️ Changed"}


def _diff_md(diff: dict, limit: int = 80, per_block: int = 12) -> str:
    """Changed sections as ```diff blocks, so removed lines show in red and added lines in green."""
    out, n = [], 0
    for r in diff.get("blocks") or []:
        if r["status"] == "unchanged":
            continue
        hunks = r.get("hunks") or []
        first = next((h.get("line") for h in hunks if h.get("line")), None)
        out.append(f"**{_DIFF_STATUS.get(r['status'], r['status'])}: {_cell(re.sub(r'\s*\(.*$', '', r['title']).strip())}**"
                   + (f" (line {first})" if first else ""))
        out += ["", "```diff"]
        for h in hunks[:per_block]:
            if n >= limit:
                break
            text = (h.get("text") or "").rstrip().replace("```", "'''")
            out.append(f"{'+' if h.get('op') == '+' else '-'} {text[:400]}")
            n += 1
        if len(hunks) > per_block:
            out.append(f"@@ {len(hunks) - per_block} more changed line(s) @@")
        out += ["```", ""]
    if diff.get("moderation_changed"):
        out += ["**✏️ Changed: content moderation**", "", "```diff",
                f"- {diff['old'].get('content_moderation') or 'not set'}",
                f"+ {diff['new'].get('content_moderation') or 'not set'}", "```", ""]
    if diff.get("char_delta"):
        out.append(f"Length changed by {diff['char_delta']:+,} characters.")
    return "\n".join(out).rstrip() if out else "No section changed."


# --- Commands ---------------------------------------------------------------------------------------------

_SEV = {"high": "🔴", "medium": "🟠", "low": "🟡", "info": "⚪"}


def _practice_fix(fix: str) -> tuple[str, str]:
    """Split a practice fix like "Add under 'grounding': - text" into (section, text)."""
    m = re.match(r"\s*Add under '([^']+)':\s*-?\s*(.*)", fix or "", re.S)
    return (m.group(1).capitalize(), m.group(2).strip()) if m else ("", (fix or "").strip())


def cmd_check(a) -> str:
    doc = load_agent(a.instructions)
    persona = _persona(a.persona, doc)
    base = baseline(persona)
    findings = [f.to_dict() for f in validate(doc, base)]
    gaps, card = practices.check(doc)
    base_gaps = set(practices.check(base)[1]["gaps"]) if base else set()
    presence = core_presence(doc)
    raw, studio = len(doc.instruction_text), studio_len(doc.instruction_text)
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("high", "medium", "low")}
    missing = [b["title"] for b in CORE_BLOCKS if not presence[b["id"]]]
    who = f"the ESS {persona.upper()} template" if base else "the ESS rules (no template baseline)"
    over = studio > DA_INSTRUCTION_LIMIT
    near = studio > DA_INSTRUCTION_LIMIT * 0.9
    md = ["## Instruction check", "",
          f"Checked against {who}.", "",
          "| Findings | Length (Copilot Studio count) | Authoring practices | ESS core blocks |",
          "| --- | --- | --- | --- |",
          f"| {_SEV['high']} {counts['high']} high · {_SEV['medium']} {counts['medium']} medium · {_SEV['low']} {counts['low']} low "
          f"| {'❌' if over else '⚠️' if near else '✅'} {studio:,} of {DA_INSTRUCTION_LIMIT:,} ({raw:,} raw) "
          f"| {card['followed']} of {card['applicable']} followed "
          f"| {'✅ ' + str(len(CORE_BLOCKS)) + ' of ' + str(len(CORE_BLOCKS)) + ' present' if not missing else '❌ missing: ' + ', '.join(missing)} |",
          ""]
    if a.previous:
        prev = load_agent(a.previous, "Previous version")
        prev_keys = {(f.rule_id, f.current) for f in validate(prev, base)}
        new = [f for f in findings if (f["rule_id"], f["current"]) not in prev_keys]
        md += ["### What changed since the previous version", "", _diff_md(diff_docs(prev, doc)), ""]
        md += ["### Findings introduced by this change", ""]
        md += [f"- {_SEV.get(f['severity'], '')} **{f['severity']}** {f['rule_id']} (line {_lines(f)}): {_cell(f['title'])}. "
               f"Employees see: {_cell(f['user_sees']).lower()}." for f in new] \
            or ["- None. The findings below were already present in the previous version."]
        md.append("")
    if findings:
        order = {"high": 0, "medium": 1, "low": 2, "info": 3}
        ordered = sorted(findings, key=lambda f: (order.get(f["severity"], 9), f["origin"] == "template"))
        md += ["### Fix first", ""]
        for i, f in enumerate(ordered, 1):
            origin = " · also in the ESS template" if f["origin"] == "template" else ""
            md += [f"{i}. {_SEV.get(f['severity'], '')} **{_cell(f['title'])}**  ",
                   f"   Line {_lines(f)} · `{f['rule_id']}` · {f['severity']}{origin}",
                   f"   - **Employees see:** {_cell(f['user_sees'])}",
                   f"   - **Fix:** {_cell(f['fix'])}", ""]
        tests = sorted({p for f in findings if f["origin"] != "template" for p in f.get("test_prompts") or []})
        if tests:
            md += ["### Test after fixing", ""] + [f"- {t}" for t in tests[:12]] + [""]
            md += _evalset(a, tests)
    else:
        md += ["✅ No rule findings.", ""]
    md += ["### ESS core blocks", ""]
    if not missing:
        md += ["✅ All present: " + ", ".join(f"{b['title']} (line {presence[b['id']][0]})" for b in CORE_BLOCKS) + ".", ""]
    else:
        md += [f"- {b['title']}: present (line {presence[b['id']][0]})" if presence[b["id"]] else f"- ❌ {b['title']}: **missing**"
               for b in CORE_BLOCKS] + [""]
    md += [f"### Authoring practices: {card['followed']} of {card['applicable']} followed", ""]
    if not gaps:
        md.append("✅ All applicable practices are followed.")
    own = [g.to_dict() for g in gaps if g.to_dict()["rule_id"] not in base_gaps]
    shared = [g.to_dict() for g in gaps if g.to_dict()["rule_id"] in base_gaps]
    for title, rows in (("Gaps in your instructions", own),
                        ("Also missing from the ESS template (lower priority)", shared)):
        if rows:
            md += [f"**{title}**", "", "| Practice | Add under | Text to add |", "| --- | --- | --- |"]
            for d in rows:
                where, text = _practice_fix(d["fix"])
                md.append(f"| {d['rule_id'][3:]} {_cell(d['title'].replace('Not following: ', ''))} | {where} | {_cell(text)} |")
            md.append("")
    if a.json:
        _write(a.json, json.dumps({"persona": persona, "findings": findings, "practices": card,
                                   "chars": {"raw": raw, "studio": studio, "limit": DA_INSTRUCTION_LIMIT}}, indent=2, default=list))
    return "\n".join(md)


def cmd_fix(a) -> str:
    doc = load_agent(a.instructions)
    base = baseline(_persona(a.persona, doc))
    rule_ids = [r.strip() for r in a.rules.split(",") if r.strip()] if a.rules else None
    r = fix(doc, base, rule_ids)
    _write(a.out, r["new_instructions"])
    c = r["chars"]
    md = ["## ESS Doctor fix", "",
          f"Applied {len(r['applied'])} fix(es); {len(r['manual'])} need a manual change. Maker findings: "
          f"{r['counts']['before']} → {r['counts']['after']}. Length {c['before']:,} → {c['after']:,} of {c['limit']:,}.",
          f"The fixed instructions are in `{a.out}`.", ""]
    if r["applied"]:
        md += ["### Applied", ""] + [f"- {x['rule_id']} (line {_lines(x)}): {_cell(x['change'])}" for x in r["applied"]] + [""]
    if r["manual"]:
        md += ["### Needs your decision", ""] + [f"- {x['rule_id']} (line {_lines(x)}) {_cell(x['title'])}: {_cell(x['change'])}" for x in r["manual"]] + [""]
    md += ["### What changed", "", _diff_md(r["diff"]), ""]
    if r["remaining"]:
        md += ["### Still open", ""] + [f"- **{x['severity']}** {x['rule_id']}: {_cell(x['title'])}" for x in r["remaining"][:10]] + [""]
    if r["tests"]:
        md += ["### Re-test with", ""] + [f"- {t}" for t in r["tests"][:10]] + [""]
    md += _evalset(a, r["tests"], a.out)
    return "\n".join(md)


def cmd_condense(a) -> str:
    doc = load_agent(a.instructions)
    r = condense(doc, baseline(_persona(a.persona, doc)), a.target)
    _write(a.out, r["new_text"])
    c = r["chars"]
    md = ["## ESS Doctor shorten", "",
          f"Length as Copilot Studio counts it: {c['before']:,} → {c['after']:,} (target {r['target']:,}). "
          + ("Target reached." if r["reached"] else f"Still {r['still_needed']:,} characters over; see the candidates below."),
          f"The shortened instructions are in `{a.out}`.", ""]
    if r["steps"]:
        md += ["### What was shortened", ""] + [f"- {_cell(s if isinstance(s, str) else s.get('what') or s)}" for s in r["steps"][:20]] + [""]
    if r["abilities_kept"]:
        md += ["### Abilities kept", "", ", ".join(f"{k}: {v}" for k, v in r["abilities_kept"].items()), ""]
    if not r["reached"] and r["candidates"]:
        md += ["### Candidates to cut or move into topics or knowledge", ""] + [f"- {_cell(x if isinstance(x, str) else json.dumps(x))[:300]}" for x in r["candidates"]] + [""]
    md += _evalset(a, [], a.out)
    return "\n".join(md)


def _run_sync(coro):
    """Run a coroutine that never suspends (the drafter without an LLM) without creating an event loop.

    Some sandboxes block the local socket pair asyncio needs for its loop, so asyncio.run would hang.
    """
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    coro.close()
    raise RuntimeError("The drafter tried to wait on I/O; the Cowork build runs without an LLM.")


def cmd_draft(a) -> str:
    doc = load_agent(a.instructions)
    base = baseline(_persona(a.persona, doc))
    fields = json.loads(_text(_read(a.intake))) if a.intake else {}
    fields.setdefault("change_type", a.type)
    fields.setdefault("intent", a.intent or "")
    if fields["change_type"] not in CHANGE_TYPES:
        raise InputError(f"--type must be one of: {', '.join(CHANGE_TYPES)}")
    known = set(Intake.__dataclass_fields__)
    intake = Intake(**{k: v for k, v in fields.items() if k in known})
    r = _run_sync(draft(intake, doc, base, use_llm=False))
    if "new_instructions" not in r:
        md = ["## ESS Doctor draft: more detail needed", ""]
        if r.get("message"):
            md += [_cell(r["message"]), ""]
        md += [f"- {_cell(q if isinstance(q, str) else q.get('question') or q.get('label') or q)}"
               for q in r.get("questions") or r.get("needs_input") or []]
        return "\n".join(md)
    shortened = None
    r["section"] = ", ".join(re.sub(r"\s*\(.*$", "", b["title"]).strip()
                             for b in (r.get("diff") or {}).get("blocks") or [] if b.get("status") != "unchanged")
    if studio_len(r["new_instructions"]) > DA_INSTRUCTION_LIMIT and not getattr(a, "no_condense", False):
        drafted = studio_len(r["new_instructions"])
        shortened = _auto_condense(doc, base, r)
        if shortened:
            shortened["drafted"] = drafted
    _write(a.out, r["new_instructions"])
    return _draft_report(a, intake, r, studio_len(doc.instruction_text), shortened)


_RULE_MARK = "- ESSDOCTORNEWRULE"


def _auto_condense(doc: AgentDoc, base, r: dict) -> dict | None:
    """Shorten an over-limit draft, keeping the added rule word for word; updates r and returns the condense result."""
    text, clause = r["new_instructions"], (r.get("clause") or "").strip()
    if not clause or clause not in text:
        return None
    masked = text.replace(clause, _RULE_MARK, 1)
    target = LENGTH_TARGET - (studio_len(clause) - studio_len(_RULE_MARK))
    c = condense(from_instructions(masked, doc.name + " (draft)"), base, target)
    if _RULE_MARK not in c["new_text"]:
        return None
    new_text = c["new_text"].replace(_RULE_MARK, clause, 1)
    new_doc = from_instructions(new_text, doc.name + " (draft)")
    new_doc.topics, new_doc.globals_defined = doc.topics, doc.globals_defined
    new_doc.is_full_export, new_doc.content_moderation = doc.is_full_export, doc.content_moderation
    before = {f.rule_id for f in validate(doc, base)}
    r.update(new_instructions=new_text, diff=diff_docs(doc, new_doc),
             introduced_findings=[f.to_dict() for f in validate(new_doc, base) if f.rule_id not in before])
    return c


_PLACE = {"knowledge_source_description": "the knowledge source's description",
          "topic_description": "the descriptions of the topics that own this subject",
          "topic_response_template": "those topics' response templates"}


def _shorten_candidates(r: dict, shortened: dict) -> list[str]:
    """Up to three lines to shorten or move: condense's candidates (not ESS core lines), else the longest lines."""
    clause = (r.get("clause") or "").strip()
    out = [f"line {c['line']} ({c['chars']:,} characters: \"{_clip(c.get('excerpt', ''), 60)}\")"
           + (f" → {c['move_to']}" if c.get("move_to") and c["move_to"] != "shorter wording" else "")
           for c in shortened.get("candidates") or [] if isinstance(c, dict) and not c.get("ess_core")
           and clause[:60] not in (c.get("excerpt") or "")][:3]
    if not out:
        lines = [(i, ln) for i, ln in enumerate(r["new_instructions"].splitlines(), 1)
                 if ln.strip() and ln.strip() != clause and not ln.lstrip().startswith("#")]
        out = [f"line {i} ({len(ln):,} characters: \"{_clip(ln.strip(), 60)}\")"
               for i, ln in sorted(lines, key=lambda x: -len(x[1]))[:2]]
    return out


def _draft_report(a, intake, r: dict, before: int, shortened: dict | None = None) -> str:
    after = studio_len(r["new_instructions"])
    blocks = [b for b in (r.get("diff") or {}).get("blocks") or [] if b.get("status") != "unchanged"]
    section = r.get("section") or ", ".join(re.sub(r"\s*\(.*$", "", b["title"]).strip() for b in blocks) or "Instructions"
    new = r.get("introduced_findings") or []
    worst = next((s for s in ("high", "medium", "low") if any(f.get("severity") == s for f in new)), "")
    issues = ("✅ none" if not new else
              " · ".join(f"{_SEV[s]} {sum(1 for f in new if f.get('severity') == s)} {s}"
                         for s in ("high", "medium", "low") if any(f.get("severity") == s for f in new)))
    size = "❌" if after > DA_INSTRUCTION_LIMIT else "⚠️" if after > DA_INSTRUCTION_LIMIT * 0.9 else "✅"
    md = ["## Instruction draft", "",
          "| Change | Goes in | Length after (Copilot Studio count) | New issues |", "| --- | --- | --- | --- |",
          f"| {CHANGE_TYPES[intake.change_type]} | {_cell(section)} | {size} {after:,} of {DA_INSTRUCTION_LIMIT:,} "
          f"({after - before:+,}) | {issues} |", "",
          "### Added text", "", "```text", (r.get("clause") or "").strip(), "```", ""]
    place = r.get("placement") or {}
    if place.get("target") and place["target"] != "instructions":
        owners = f" ({', '.join(place['owners'][:3])})" if place.get("owners") else ""
        md += [f"> 💡 **Also put this in {_PLACE.get(place['target'], place['target'])}{owners}.** {_cell(place.get('why'))}", ""]

    checks = []
    if shortened:
        what = [s if isinstance(s, str) else s.get("what") or s.get("step") or "" for s in shortened.get("steps") or []
                if isinstance(s, str) or s.get("applied", True)]
        what = [w for w in what if w][:3]
        cands = _shorten_candidates(r, shortened)
        if after <= DA_INSTRUCTION_LIMIT:
            buffer = (f"under the {LENGTH_TARGET:,} target, keeping a 10% buffer" if after <= LENGTH_TARGET else
                      f"under the {DA_INSTRUCTION_LIMIT:,} limit but above the {LENGTH_TARGET:,} target (10% buffer)")
            checks.append(f"- ✂️ **Shortened automatically:** {shortened['drafted']:,} → {after:,} characters, {buffer}"
                          + (f" ({'; '.join(_cell(w) for w in what)})" if what else "")
                          + ". The new rule is unchanged and every ESS ability is kept; review the changed lines under "
                          "\"What changed\"."
                          + (" To restore the buffer, shorten or move into a topic description: " + "; ".join(cands) + "."
                             if after > LENGTH_TARGET and cands else ""))
        else:
            checks.append(f"- ❌ **Still {after - DA_INSTRUCTION_LIMIT:,} characters over after shortening** "
                          f"({shortened['drafted']:,} → {after:,}). Shorten or move into a topic description: "
                          + "; ".join(cands) + ".")
    for f in new:
        checks.append(f"- {_SEV.get(f.get('severity'), '')} **{_cell(f.get('title'))}** (line {_lines(f)}): {_cell(f.get('fix'))}"
                      + (" Run `condense` on the draft." if f.get("rule_id") == "L0-LENGTH" else ""))
    for c in r.get("collisions") or []:
        what = f"\"{c['query']}\"" if c.get("query") else f"topic {c.get('topic')}"
        checks.append(f"- ⚠️ {what} shares {', '.join(c.get('terms') or [])} with the change: {_cell(c.get('risk'))}")
    if r.get("source_note"):
        checks.append(f"- ⚠️ {_cell(r['source_note'])}")
    if after > DA_INSTRUCTION_LIMIT and not shortened and not any(f.get("rule_id") == "L0-LENGTH" for f in new):
        checks.append(f"- ❌ The instructions are {after - DA_INSTRUCTION_LIMIT:,} characters over the limit: shorten them "
                      "(`condense`) before publishing, or Copilot Studio cuts off the end.")
    unused = [u for u in r.get("unused") or [] if _cell(u).strip(" .").lower() != _cell(intake.intent).strip(" .").lower()]
    if unused:
        checks.append("- ℹ️ These parts of the request didn't become part of the rule: "
                      + "; ".join(f"\"{_cell(u)}\"" for u in unused[:4]) + ".")
    md += ["### Check before publishing", ""] + (checks or ["✅ No new findings or conflicts."]) + [""]

    tests = r.get("tests") or []
    changed = [t["prompt"] for t in tests if isinstance(t, dict) and t.get("expect") == "changed"]
    same = [t["prompt"] for t in tests if isinstance(t, dict) and t.get("expect") != "changed"]
    if changed or same:
        md += ["### Test after publishing", "", "| Should follow the new rule | Should behave as before |", "| --- | --- |"]
        for i in range(max(len(changed), len(same))):
            md.append(f"| {_cell(changed[i]) if i < len(changed) else ''} | {_cell(same[i]) if i < len(same) else ''} |")
        md.append("")
    md += _evalset(a, tests, a.out)
    md += ["### What changed", "", _diff_md(r.get("diff") or {}), ""]
    return "\n".join(md)


def _snapshot(data: bytes) -> tuple[str, str] | None:
    """(dialog activities JSON, botContent YAML) from a Copilot Studio test pane snapshot (.zip), or None."""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        return None
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = {Path(i.filename).name.lower(): i.filename for i in z.infolist() if not i.is_dir()}
        dialog = next((names[n] for n in names if n.startswith("dialog") and n.endswith(".json")), None)
        if not dialog:
            return None
        bot = next((names[n] for n in names if n.startswith("botcontent") and n.endswith((".yml", ".yaml"))), None)
        return _text(z.read(dialog)), (_text(z.read(bot)) if bot else "")


def cmd_logs_query(a) -> str:
    from ess_maker.doctor import appinsights

    cid = a.conversation_id.strip()
    if not re.fullmatch(r"[\w\-:.]{6,200}", cid):
        raise InputError("Give the conversation ID exactly as Copilot Studio shows it (for example from /debug conversationid).")
    days = max(1, min(a.days, 90))
    return "\n".join(["```kusto", appinsights.QUERY.format(cid=cid, days=days), "```", "", appinsights.HOWTO])


def cmd_xray(a) -> str:
    data = _read(a.evidence)
    snap = _snapshot(data)
    if snap:
        from ess_maker.doctor import transcript

        dialog, bot_yaml = snap
        current = load_agent(a.instructions, "Current instructions") if a.instructions else (
            _from_text(bot_yaml, "Instructions in the snapshot", "snapshot") if bot_yaml else None)
        if not transcript.load(dialog):
            raise InputError("The snapshot's dialog.json has no conversation activities.")
        return "```text\n" + transcript.build(dialog, question=a.question, response=a.response, current=current, snapshot=True,
                                             bot_yaml=bot_yaml) + "\n```"
    text = _text(data).lstrip("\ufeff")
    current = load_agent(a.instructions, "Current instructions") if a.instructions else None
    from ess_maker.doctor import appinsights

    if appinsights.is_export(text):
        from ess_maker.doctor import transcript

        acts, info = appinsights.to_activities(text, a.conversation)
        if not acts:
            raise InputError("The Application Insights export has no prompts, replies or tool calls for this conversation. "
                             "Check the conversation ID and the lookback period, and that the agent's telemetry logs "
                             "conversation details.")
        extra = [f"Telemetry: {info['spans']} span(s) and {info['events']} event(s)"
                 + (f", {info['steps']} topic step(s)" if info.get("steps") else "")
                 + (" from the Copilot Studio test pane" if info.get("test_pane") else "")
                 + (f"; channel {info['channel']}" if info["channel"] else "")
                 + (f"; model {info['model']}" if info["model"] else "") + "."]
        if not info["spans"]:
            extra.append("Only agent-level telemetry was found: it logs messages, topics and topic steps, but not "
                         "knowledge searches or tool calls, so the search stage can't be judged. The Copilot Studio "
                         "transcript of this conversation, or a test pane snapshot, shows them.")
        extra += [f"Logged by a topic: {m}" for m in info["logger"][:5]]
        return "```text\n" + transcript.build(json.dumps({"activities": acts}), question=a.question, response=a.response,
                                             current=current, kind="appinsights", extra_happened=extra) + "\n```"
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and "requests" in obj and "messages" in obj:
            parsed = devui.parse(obj)
            if not parsed["turns"]:
                raise InputError("The DevUI export has no user turns to analyze (only commands such as /debug).")
            turns = parsed["turns"]
            idx = len(turns) - 1
            if a.question:
                idx = next((i for i in range(len(turns) - 1, -1, -1) if a.question.lower() in turns[i]["question"].lower()), idx)
            if a.turn:
                idx = max(0, min(a.turn - 1, len(turns) - 1))
            t = turns[idx]
            served = from_instructions(t["instructions"], "Instructions that ran (DevUI)", "devui") if t["instructions"] else None
            basis = served or current
            findings = validate(basis, baseline(guess_persona(current or basis))) if basis else []
            head = ""
            if len(turns) > 1:
                head = "Turns in this export: " + "; ".join(f"{i + 1}. {x['question'][:80]}" for i, x in enumerate(turns)) + \
                       f"\nAnalyzing turn {idx + 1}. Pass --turn N for another.\n\n"
            report = devui.build(parsed, idx, current=current, findings=findings).replace(
                "Apply these in the Agent check-up (or copy the fixed instructions into Copilot Studio)",
                "Ask ESS Doctor to apply these fixes (or edit the instructions in Copilot Studio > Overview > Instructions)")
            return head + "```text\n" + report + "\n```"
    if re.search(r'(^|,|")\s*AuditData\s*("|,|$)', text.splitlines()[0] if text else "", re.I):
        r = evidence.parse_purview_csv(text, a.agent or "")
        if not r.get("ok"):
            raise InputError(r.get("error", "Couldn't read the Purview export."))
        md = ["## Purview audit: Copilot interactions", "", f"{r['rows']} rows ({r['skipped']} skipped).", "",
              "### Agents", ""] + [f"- {n}: {c} interactions" for n, c in r["agents"]] + ["", "### By agent version", ""]
        for v in r["versions"]:
            md.append(f"- **{_cell(v.get('agent') or '')} {v.get('version', '')}**: {v.get('interactions')} interactions, "
                      f"{v.get('first')} → {v.get('last')}; resources: {_cell(v.get('resources'))[:300]}; plugins: {_cell(v.get('plugins'))[:200]}")
        md += ["", "Compare when each version started with when the instructions changed, then upload a developer mode card "
                   "of a failing question, with the question and the agent's answer, for a step-by-step X-ray."]
        return "\n".join(md)
    from ess_maker.doctor import devmode, devmode_report, transcript

    if transcript.looks_like_transcript(text):
        return "```text\n" + transcript.build(text, question=a.question, response=a.response, current=current) + "\n```"
    sessions = transcript.load_sessions(text)
    if sessions:
        q = (a.question or "").lower()
        row = next((r for r in sessions if q and q[:60] in (r["question"] or "").lower()), sessions[0])
        return "\n".join([
            "## Copilot Studio session (summary only)", "",
            f"- **Question:** {row['question']}", f"- **Agent's last reply:** {row['response'][:512] or '(none)'}",
            f"- **Last topic:** {row['topic'] or 'none'}", f"- **Outcome:** {row['outcome'] or '?'} {row['reason']}".rstrip(),
            f"- **Session:** {row['session']} ({row['start']})", "",
            f"This file has {len(sessions)} session(s). The sessions download only has the text of the conversation, so it "
            "can't show the plan, knowledge searches or errors. Use `sorry-triage` on this file to rank causes across all "
            "sessions, and read the full transcript of this conversation (Dataverse conversationtranscript, through the "
            "Business apps connector or a Power Apps export) for a step-by-step X-ray."])
    if not devmode.looks_like_devmode(text):
        raise InputError("Couldn't recognise this evidence. Paste the developer mode (-developer on) card or its Raw info "
                         "JSON, upload a Purview audit export (CSV with an AuditData column), or, if Microsoft support is "
                         "helping you, a DevUI conversation export (JSON).")
    hq, hr, _ = devmode.split_header(text)
    return "```text\n" + devmode_report.build(text, question=a.question or hq, response=a.response or hr, current=current) + "\n```"


def _evidence_files(paths: list[str]) -> list[tuple[str, bytes]]:
    """Evidence files; a .zip is unpacked (nested zips too) except a test pane snapshot, which stays whole."""
    def expand(name: str, data: bytes, depth: int = 0) -> list[tuple[str, bytes]]:
        if not zipfile.is_zipfile(io.BytesIO(data)) or depth > 2 or _snapshot(data):
            return [(name, data)]
        found = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for i in z.infolist():
                if i.is_dir():
                    continue
                inner = f"{name}/{i.filename}"
                if i.filename.lower().endswith(".zip"):
                    found += expand(inner, z.read(i), depth + 1)
                elif re.search(r"(?i)\.(json|txt|md|csv)$", i.filename):
                    found.append((inner, z.read(i)))
        return found

    out = []
    for path in paths:
        out += expand(Path(path).name, _read(path))
    return out


def cmd_sorry(a) -> str:
    from ess_maker.doctor import appinsights, devmode, sorry, transcript

    sources, skipped, snapshot_instructions = [], [], ""
    for name, data in _evidence_files(a.evidence):
        snap = _snapshot(data)
        if snap:
            dialog, bot_yaml = snap
            bf = transcript.bot_findings(bot_yaml)
            for doc in transcript.load(dialog):
                diag = transcript.diagnose(doc)
                if bf["empty_context_topics"] and diag["stages"].get("synthesis") == "failed":
                    diag["context_missing"] = True
                    diag["likely_cause"] = (f'The "{bf["empty_context_topics"][0]}" topic has no steps, so the employee\'s '
                                            "details were blank and the agent answered generically.")
                sources.append(("transcript", name, diag))
            if bot_yaml and not snapshot_instructions:
                snapshot_instructions = bot_yaml
            continue
        text = _text(data).lstrip("\ufeff")
        if appinsights.is_export(text):
            for cid in appinsights.conversation_ids(text):
                acts, _ = appinsights.to_activities(text, cid)
                if acts:
                    sources.append(("transcript", f"{name}#{cid[:8]}", transcript.diagnose({"activities": acts})))
            continue
        obj = None
        if text.lstrip().startswith("{"):
            try:
                obj = json.loads(text)
            except ValueError:
                obj = None
        if isinstance(obj, dict) and "requests" in obj and "messages" in obj:
            sources.append(("devui", name, devui.parse(obj)))
        elif transcript.load_sessions(text):
            for row in transcript.load_sessions(text):
                sources.append(("session", f"{name}#{row['session'] or len(sources) + 1}", row))
        elif transcript.load(text):
            docs = [d for d in transcript.load(text) if any((transcript._messages(d)[0]))]
            for k, doc in enumerate(docs, 1):
                sources.append(("transcript", f"{name}#{k}" if len(docs) > 1 else name, transcript.diagnose(doc)))
        elif devmode.looks_like_devmode(text):
            q, r, _ = devmode.split_header(text)
            if not q:
                m = re.search(r"(?im)^\s*(?:question|user question|prompt)\s*[:=]\s*(.+)$", text)
                q = m.group(1).strip() if m else ""
            if not r:
                m = re.search(r"(?im)^\s*(?:response|answer|agent response|agent said)\s*[:=]\s*(.+)$", text)
                r = m.group(1).strip() if m else ""
            sources.append(("devmode", name, (devmode.diagnose(text, q, r), q, r)))
        else:
            skipped.append(name)
    if not sources:
        raise InputError("No conversation evidence found. Custom engine agent: upload the Copilot Studio Monitor > Sessions "
                         "download, transcripts, test pane snapshots (.zip) or an Application Insights export. Declarative "
                         "agent: developer mode cards (text or Raw info JSON) or, with Microsoft support, DevUI exports. "
                         "One file per conversation or several in a .zip.")
    instructions = (load_agent(a.instructions).instruction_text if a.instructions else
                    _from_text(snapshot_instructions, "Instructions in the snapshot", "snapshot").instruction_text
                    if snapshot_instructions else "")
    cea = any(kind in ("transcript", "session") for kind, _, _ in sources)
    md = sorry.build(sorry.triage(sources, instructions, cea=cea))
    if skipped:
        md += "\n\nSkipped (not recognised as conversation evidence): " + ", ".join(skipped[:20])
    return md


def _agent_files(paths: list[str]) -> list[tuple[str, str]]:
    """(name, YAML text) for each agent: .yml/.yaml files, test pane snapshots, Copilot Studio ALM exports
    (Plugin/Agents/<agent>/agent.yml with app.config.<realm>.json values filled in), or zips holding any of these."""
    out: list[tuple[str, str]] = []

    def alm(z: zipfile.ZipFile, name: str) -> bool:
        agents = [n for n in z.namelist() if re.search(r"(?i)(^|/)Agents/[^/]+/agent\.ya?ml$", n)]
        for n in agents:
            folder = n.rsplit("/", 1)[0]
            values: dict = {}
            for c in sorted(x for x in z.namelist() if x.startswith(folder + "/") and re.search(r"(?i)app\.config\.[^/]*\.json$", x)):
                try:
                    values.update((json.loads(_text(z.read(c))) or {}).get("values") or {})
                except ValueError:
                    pass
            text = re.sub(r'\$\{config\.values\["([^"]+)"\]\}', lambda m: str(values.get(m.group(1), m.group(0))),
                          _text(z.read(n)))
            out.append((f"{name}/{folder.rsplit('/', 1)[-1]}", text))
        return bool(agents)

    def add(name: str, data: bytes, depth: int = 0) -> None:
        snap = _snapshot(data)
        if snap:
            if snap[1]:
                out.append((name, snap[1]))
            return
        if zipfile.is_zipfile(io.BytesIO(data)) and depth < 2:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if alm(z, name):
                    return
                for i in z.infolist():
                    if not i.is_dir() and re.search(r"(?i)\.(ya?ml|zip)$", i.filename):
                        add(f"{name}/{Path(i.filename).name}", z.read(i), depth + 1)
            return
        out.append((name, _text(data)))

    for p in paths:
        add(Path(p).name, _read(p))
    return out


def cmd_audit(a) -> str:
    from ess_maker.doctor import audit

    agents = [audit.parse(text, name) for name, text in _agent_files(a.agents)]
    agents = [x for x in agents if x.instructions or x.knowledge or x.topics or x.connected]
    if not agents:
        raise InputError("No agents found. Upload each agent's test pane snapshot (.zip) or its YAML (botContent.yml, "
                         "or the agent's GptComponent), for the hub and every child agent.")
    r = audit.audit(agents)
    hub, gaps = r["hub"], r["gaps"]
    kids = [k for _, k, _ in r["children"] if k]
    role = {id(hub): "Hub"} if hub else {}
    role.update({id(k): "Child" for k in kids})

    checks = {}
    for x in agents:
        if not x.instructions:
            continue
        base = baseline(x.persona) if x.persona in TEMPLATE_MODERATION else None
        doc = from_instructions(x.instructions, x.name, "audit")
        checks[id(x)] = [f for f in validate(doc, base) if f.origin != "template"]

    md = ["## Agent audit", ""]
    if hub:
        md.append(f"Hub **{_cell(hub.name)}** routes to {len(r['children'])} agent(s); "
                  f"{len(kids)} of them uploaded and audited.")
    else:
        md.append("No hub (an agent that routes to other agents) was found, so each agent is audited on its own.")
    md += ["", "| Agent | Role | Knowledge sources | Topics that search | Connected agents | Instructions | Instruction findings | Moderation |",
           "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for x in sorted(agents, key=lambda x: (role.get(id(x), "Other") != "Hub", role.get(id(x), "Other") != "Child", x.name)):
        fs = checks.get(id(x), [])
        sev = " · ".join(f"{_SEV[s]} {n}" for s in ("high", "medium") if (n := sum(1 for f in fs if f.severity == s)))
        ks = f"{len(x.knowledge)}" + (" ⚠️" if len([k for k in x.knowledge if k.active]) > audit.KNOWLEDGE_FILTER_LIMIT else "")
        md.append(f"| {_cell(x.name)} | {role.get(id(x), 'Other')} | {ks if x.full_export else '—'} | "
                  f"{sum(1 for t in x.topics if t.active and t.searches) if x.full_export else '—'} | "
                  f"{len(x.connected) or len(x.instruction_refs) or '—'} | "
                  f"{studio_len(x.instructions):,} characters | {sev or ('✅ none' if x.instructions else '—')} | "
                  f"{x.settings.get('contentModeration', '—')} |")
    md.append("")

    real = [g for g in gaps if g.severity != "info"]
    md += ["### Gaps across the agents", ""]
    if not real:
        md += ["✅ No cross-agent gaps found.", ""]
    for i, g in enumerate(real, 1):
        md += [f"{i}. {_SEV.get(g.severity, '')} **{_cell(g.title)}**  ",
               f"   {', '.join(_cell(n) for n in g.agents)} · `{g.rule_id}` · {g.severity}",
               f"   - **Why it matters:** {_cell(g.detail)}",
               f"   - **Fix:** {_cell(g.fix)}", ""]

    rows = [(x, checks[id(x)]) for x in agents if checks.get(id(x))]
    md += ["### Instruction check per agent", ""]
    if rows:
        md += ["| Agent | Top findings |", "| --- | --- |"]
        for x, fs in rows:
            top = sorted(fs, key=lambda f: {"high": 0, "medium": 1, "low": 2}.get(f.severity, 3))[:3]
            md.append(f"| {_cell(x.name)} | " + "<br>".join(f"{_SEV.get(f.severity, '')} {_cell(f.title)} (line {_lines(f.to_dict())})"
                                                          for f in top) + " |")
        md += ["", "Run the instruction check on an agent for every finding and its fix.", ""]
    else:
        md += ["✅ No instruction findings beyond the ESS template.", ""]

    notes = [g for g in gaps if g.severity == "info"]
    partial = [x.name for x in agents if not x.full_export]
    if notes or partial:
        md += ["### Not checked", ""]
        md += [f"- {_cell(g.title)}: {_cell(g.fix)}" for g in notes]
        if partial:
            md.append(f"- {', '.join(_cell(n) for n in partial)}: the file has only the instructions, so knowledge sources, "
                      "topics and settings weren't checked. Upload the agent's test pane snapshot for a full audit.")
        md.append("")
    return "\n".join(md)


def cmd_eval_triage(a) -> str:
    doc = load_agent(a.instructions)
    r = evaltriage.triage(_text(_read(a.csv)), doc)
    if not r.get("has_results", True) or not r.get("total"):
        raise InputError("No evaluation results found. Upload the results CSV exported from the agent's Evaluation page.")
    return _triage_report(r)


_SHORT_OWNER = {"instruction": "You, in the instructions", "scenario_off": "Maker / admin: topics and connections",
                "scenario_missing": "Maker / admin: add the scenario", "content_gap": "Knowledge-source content owner",
                "review": "You: review the conversation"}


def _clip(s: str, n: int) -> str:
    """Shorten at a sentence (or word) boundary so table cells don't end mid-word."""
    s = _cell(s)
    if len(s) <= n:
        return s
    cut = max(s.rfind(". ", 0, n), s.rfind("; ", 0, n))
    return s[:cut + 1] if cut > n // 2 else s[:s.rfind(" ", 0, n)] + "…"


def _triage_report(r: dict) -> str:
    items, req = r.get("items") or [], r.get("required") or []
    total, passed, failed = r["total"], r["passed"], r["failed"]
    md = ["## Evaluation triage", "", f"**{passed} of {total} passed** Â· âœ… {passed} passed Â· âŒ {failed} failed", ""]

    for q in req:
        if q["missing"]:
            md += [f"> âš ï¸ **A required line is missing from {q['missing']} of {q['of']} responses"
                   + (f", including {q['missing_in_passes']} that passed" if q["missing_in_passes"] else "") + ".**",
                   f"> Instruction line {q['line']} asks every response to include: *\"{_cell(q['text'])}\"*",
                   "> The grader only judged answer quality, so passing questions don't show this. "
                   "Add a check for this exact sentence to the evaluation.", ""]

    if not items:
        md.append("No failed questions.")
        return "\n".join(md)

    by_cause: dict[str, list[int]] = {}
    for i, x in enumerate(items, 1):
        by_cause.setdefault(x.get("cause") or "review", []).append(i)
    md += ["### At a glance", "", "| Cause | Failed | Questions | Who fixes it |", "| --- | --- | --- | --- |"]
    for cause, nums in sorted(by_cause.items(), key=lambda kv: -len(kv[1])):
        label = next((x.get("cause_label") for x in items if x.get("cause") == cause), cause)
        md.append(f"| {_cell(label)} | {len(nums)} | {', '.join(f'#{n}' for n in nums)} | {_SHORT_OWNER.get(cause, '')} |")

    md += ["", "### Failed questions", "", "| # | Question | What happened | Fix |", "| --- | --- | --- | --- |"]
    for i, x in enumerate(items, 1):
        what = _clip(x.get("headline"), 240)
        if x.get("secondary"):
            what += "<br>âš ï¸ " + _clip(x["secondary"], 240)
        md.append(f"| {i} | **{_clip(x.get('question'), 160)}** | {what} | {_clip(x.get('fix'), 320)} |")

    flagged = [x for x in items if x.get("secondary") and x.get("lines")]
    if flagged:
        md += ["", "### Instruction lines involved", ""]
        seen = set()
        for x in flagged:
            for ln in x["lines"]:
                if ln["line"] not in seen:
                    seen.add(ln["line"])
                    md.append(f"- Line {ln['line']}: *\"{_cell(ln['text'])}\"*")

    owners: dict[str, list[str]] = {}
    if any(q["missing"] for q in req):
        owners.setdefault("You, in the instructions", []).append("Required line missing from responses")
    kb = [_clip(x.get("question"), 60) for x in items if x.get("secondary")]
    if kb:
        owners.setdefault("You, in the instructions", []).append(f"Knowledge search before abstaining ({', '.join(kb)})")
    for x in items:
        owners.setdefault(_SHORT_OWNER.get(x.get("cause"), "You"), []).append(_clip(x.get("question"), 60))
    md += ["", "### Who fixes what", "", "| Owner | What to fix |", "| --- | --- |"]
    md += [f"| {o} | {'; '.join(dict.fromkeys(v))} |" for o, v in owners.items()]
    return "\n".join(md)


def cmd_eval_compare(a) -> str:
    r = evaltriage.compare(_text(_read(a.before)), _text(_read(a.after)))
    if not r["rows"]:
        raise InputError("No questions found. Upload two Copilot Studio evaluation results CSVs.")
    counts = ", ".join(f"{k} {v}" for k, v in r["counts"].items() if v)
    md = ["## Evaluation comparison", "",
          f"{r['total']} questions: {r['before_pass']} passed before, {r['after_pass']} pass after ({counts}).", "",
          "| Question | Status | Before | After |", "| --- | --- | --- | --- |"]
    order = {"Regressed": 0, "Still failing": 1, "Fixed": 2, "Not comparable": 3, "Still passing": 4}
    for x in sorted(r["rows"], key=lambda x: order.get(x.get("status"), 9)):
        b, af = x.get("before") or {}, x.get("after") or {}
        mark = lambda s: ("pass" if s.get("pass") else "fail") + (f": {s.get('why')}" if s.get("why") else "")
        md.append(f"| {_cell(x.get('question'))[:160]} | **{_cell(x.get('status'))}** | {_cell(mark(b))} | {_cell(mark(af))} |")
    if r["counts"].get("Regressed"):
        md += ["", "Regressed questions passed before the change and fail now: check the instruction lines the change added."]
    return "\n".join(md)


def cmd_setup(a) -> str:
    from ess_maker.doctor.setup_servicenow import run

    answers = _text(_read(a.answers)) if a.answers else "{}"
    return run(answers)["markdown"]


def cmd_practices(a) -> str:
    md = ["## Authoring practices ESS Doctor checks", ""]
    for p in practices.PRACTICES:
        md.append(f"- **{p.id}** {_cell(p.title)}")
    return "\n".join(md)


def cmd_component_update(a) -> str:
    """Replace only the instructions block of an instructions component YAML; every other line stays as it was."""
    text = _text(_read(a.component))
    new = _text(_read(a.instructions)).replace("\r\n", "\n").rstrip("\n")
    lines = text.replace("\r\n", "\n").split("\n")
    if not any(ln.strip() == "kind: GptComponentMetadata" for ln in lines[:5]):
        raise InputError("This isn't an instructions component (kind: GptComponentMetadata). Use the data value of the "
                         "agent's botcomponent with componenttype 15.")
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)instructions:\s*[|>][-+]?\s*$", ln)
        if not m:
            continue
        key, body, j = len(m.group(1)), None, i + 1
        while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > key):
            if lines[j].strip() and body is None:
                body = len(lines[j]) - len(lines[j].lstrip())
            j += 1
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        pad = " " * (body if body is not None else key + 2)
        block = [m.group(1) + "instructions: |-"] + [(pad + t) if t.strip() else "" for t in new.split("\n")]
        out = lines[:i] + block + lines[j:]
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text("\n".join(out), encoding="utf-8")
        return (f"Wrote {a.out}: instructions block replaced ({len(new):,} characters, {len(new.splitlines())} lines); "
                f"{len(out) - len(block)} other lines unchanged.")
    raise InputError("No 'instructions: |' block found in the component.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="essdoctor", description="ESS Doctor: check, fix and troubleshoot Employee Self-Service agents.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    persona = {"choices": ["auto", "hr", "it", "hub", "none"], "default": "auto", "help": "ESS template to compare with"}

    p = sub.add_parser("check", help="Check instructions against the ESS template, known issues and practices")
    p.add_argument("instructions")
    p.add_argument("--previous", help="Previous version, to show what the change introduced")
    p.add_argument("--persona", **persona)
    p.add_argument("--json", help="Also write the full result as JSON to this path")
    p.add_argument("--evalset", help="Where to write the Copilot Studio evaluation test set CSV")
    p.add_argument("--no-evalset", action="store_true", help="Don't write the evaluation test set")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("fix", help="Apply the safe fixes and write the fixed instructions")
    p.add_argument("instructions")
    p.add_argument("--rules", help="Comma-separated rule IDs to fix (default: all auto-fixable)")
    p.add_argument("--persona", **persona)
    p.add_argument("--out", default="outputs/fixed_instructions.txt")
    p.add_argument("--evalset", help="Where to write the Copilot Studio evaluation test set CSV")
    p.add_argument("--no-evalset", action="store_true", help="Don't write the evaluation test set")
    p.set_defaults(fn=cmd_fix)

    p = sub.add_parser("condense", help="Shorten instructions to a character target without losing abilities")
    p.add_argument("instructions")
    p.add_argument("--target", type=int, default=LENGTH_TARGET,
                   help="Length to aim for as Copilot Studio counts it (default: 90%% of the limit, a 10%% buffer)")
    p.add_argument("--persona", **persona)
    p.add_argument("--out", default="outputs/shortened_instructions.txt")
    p.add_argument("--evalset", help="Where to write the Copilot Studio evaluation test set CSV")
    p.add_argument("--no-evalset", action="store_true", help="Don't write the evaluation test set")
    p.set_defaults(fn=cmd_condense)

    p = sub.add_parser("draft", help="Draft an instruction change from the maker's intent")
    p.add_argument("instructions")
    p.add_argument("--type", choices=list(CHANGE_TYPES), default="restricted")
    p.add_argument("--intent", default="")
    p.add_argument("--intake", help="JSON file with the change details (subject, allowed, terms, format_spec, ...)")
    p.add_argument("--persona", **persona)
    p.add_argument("--out", default="outputs/drafted_instructions.txt")
    p.add_argument("--no-condense", action="store_true", help="Don't shorten automatically when the draft is over the limit")
    p.add_argument("--evalset", help="Where to write the Copilot Studio evaluation test set CSV")
    p.add_argument("--no-evalset", action="store_true", help="Don't write the evaluation test set")
    p.set_defaults(fn=cmd_draft)

    p = sub.add_parser("xray", help="Explain one conversation from a DevUI export, Purview export or developer mode card")
    p.add_argument("evidence")
    p.add_argument("--instructions", help="Current instructions, to detect whether the conversation ran on another version")
    p.add_argument("--question", default="", help="Pick the turn whose question contains this text")
    p.add_argument("--turn", type=int, default=0, help="1-based turn number")
    p.add_argument("--agent", default="", help="Purview: only this agent name or ID")
    p.add_argument("--response", default="", help="Developer mode card: the agent's answer, to check the answer stage")
    p.add_argument("--conversation", default="", help="Application Insights export: only this conversation ID")
    p.set_defaults(fn=cmd_xray)

    p = sub.add_parser("logs-query", help="Application Insights query (KQL) for one custom engine agent conversation")
    p.add_argument("conversation_id")
    p.add_argument("--days", type=int, default=7, help="How many days back to search")
    p.set_defaults(fn=cmd_logs_query)

    p = sub.add_parser("audit", help="Audit a hub agent and its child agents together (knowledge, routing, settings, instructions)")
    p.add_argument("agents", nargs="+", help="Each agent's test pane snapshot (.zip) or YAML; a zip of several also works")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("sorry-triage", help="Why the agent said sorry across many conversations (DevUI exports, developer mode cards)")
    p.add_argument("evidence", nargs="+", help="DevUI exports (.json), developer mode cards (.txt) or a .zip of them")
    p.add_argument("--instructions", help="Current instructions, to show which implicated rules are already fixed")
    p.set_defaults(fn=cmd_sorry)

    p = sub.add_parser("eval-triage", help="Explain why each failed evaluation question failed")
    p.add_argument("csv")
    p.add_argument("--instructions", required=True)
    p.set_defaults(fn=cmd_eval_triage)

    p = sub.add_parser("eval-compare", help="Compare two evaluation runs of the same questions")
    p.add_argument("before")
    p.add_argument("after")
    p.set_defaults(fn=cmd_eval_compare)

    p = sub.add_parser("setup-servicenow", help="ServiceNow Knowledge connector setup checklist")
    p.add_argument("--answers", help="JSON file with the answers collected so far")
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("practices", help="List the authoring practices that are checked")
    p.set_defaults(fn=cmd_practices)

    p = sub.add_parser("component-update", help="Put new instructions into an instructions component YAML (botcomponent data)")
    p.add_argument("component", help="The component's data value (kind: GptComponentMetadata)")
    p.add_argument("instructions", help="The new instructions text")
    p.add_argument("--out", default="outputs/component_data.yml")
    p.set_defaults(fn=cmd_component_update)

    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    try:
        print(a.fn(a))
        return 0
    except InputError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
