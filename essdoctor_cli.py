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
from ess_maker.doctor.fixes import LENGTH_RULES, LENGTH_TARGET, fix
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


def _lines(f: dict, offset: int = 0) -> str:
    return ", ".join(str(n - offset) for n in (f.get("lines") or [])[:6]) or "-"


def _offset(doc: AgentDoc) -> int:
    """File lines before the instructions (a YAML export), so line numbers match the Copilot Studio instructions box."""
    return max(0, doc.instructions[0].no - 1) if doc.instructions else 0


def _where(f: dict, offset: int) -> str:
    return "Not in your instructions" if f.get("current") == "(not present)" else f"Line {_lines(f, offset)}"


def _title(f: dict) -> str:
    return re.sub(r"\s*\(template '.*$", "", _cell(f.get("title"))).replace("Not following: ", "")


def _at(x: dict, offset: int) -> str:
    """' (line N)' for a fix entry, or '' for a whole block that was missing."""
    rid = x.get("rule_id") or ""
    if (rid.startswith("L2-") and rid.endswith("-MISSING")) or rid.startswith("BP-") or not x.get("lines"):
        return ""
    return f" (line {_lines(x, offset)})"


def _is_cea(path: str, agent_type: str) -> bool:
    """Custom engine agent: told so, or the input is a test pane snapshot or its botContent.yml."""
    if agent_type in ("cea", "da"):
        return agent_type == "cea"
    if re.search(r"(?i)botcontent", Path(path).name):
        return True
    try:
        return bool(_snapshot(_read(path)))
    except Exception:  # noqa: BLE001
        return False


def _cea_skip(f) -> bool:
    """Declarative agent core-block rules: a custom engine agent's template routes through topics instead."""
    rid = f["rule_id"] if isinstance(f, dict) else f.rule_id
    return rid.startswith("L2-") and rid.endswith("-MISSING")


_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


_COVERS = {"L5-OVER-AUGMENTED-QUERY": ("BP-P17",), "L5-QUERY-REWRITE": ("BP-P17",), "L5-CONTEXT-IN-SEARCH": ("BP-P17",)}


def _check_items(doc: AgentDoc, base, cea: bool) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Findings, own practice gaps and template-shared gaps in report order (numbered 1..n across all three)."""
    findings = [f.to_dict() for f in validate(doc, base) if not (cea and _cea_skip(f))]
    findings.sort(key=lambda f: (_ORDER.get(f["severity"], 9), f["origin"] == "template"))
    gaps, card = practices.check(doc)
    base_gaps = set(practices.check(base)[1]["gaps"]) if base else set()
    # A finding whose fix already writes the practice's rule: don't recommend the practice separately.
    covered = {p for f in findings for p in _COVERS.get(f["rule_id"], ())}
    gaps = [g for g in gaps if g.rule_id not in covered]
    own = [g.to_dict() for g in gaps if g.rule_id not in base_gaps]
    shared = [g.to_dict() for g in gaps if g.rule_id in base_gaps]
    return findings, own, shared, card


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
            f"`{path}` has {len(rows)} questions: {own} for this change and {len(rows) - own} ESS regression and "
            "grounding-hardening questions, "
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
        title = re.sub(r'\s*\(.*$', '', r['title']).strip() or "Text before the first heading"
        out.append(f"**{_DIFF_STATUS.get(r['status'], r['status'])}: {_cell(title)}**"
                   + (f" (line {first})" if first else ""))
        out += ["", "```diff"]
        for h in hunks[:per_block]:
            if n >= limit:
                break
            text = (h.get("text") or "").rstrip().replace("```", "'''")
            out.append(f"{'+' if h.get('op') == '+' else '-'} {text if len(text) <= 1500 else text[:1500] + '…'}")
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


def _diff_block(lines: list[str]) -> list[str]:
    """A ```diff block from lines already starting with '-' or '+'."""
    return ["```diff"] + [ln.replace("```", "'''") for ln in lines] + ["```"]


def _indent(text: str, pad: str = "   ") -> list[str]:
    return [pad + ln if ln else "" for ln in text.splitlines()]


def _rule_diff(doc: AgentDoc, base, rule_id: str, per_block: int = 8) -> tuple[str, str]:
    """(diff the auto-fix would make for one rule or practice, why it can't when there is none)."""
    try:
        r = fix(doc, base, [rule_id])
    except Exception:  # noqa: BLE001 - a proposal is optional; the prose fix still shows
        return "", ""
    if not r["applied"] or not any(b["status"] != "unchanged" for b in r["diff"].get("blocks") or []):
        why = next((m["change"] for m in r["manual"] if m["rule_id"] == rule_id), "")
        return "", why
    return _diff_md({**r["diff"], "char_delta": 0, "moderation_changed": False}, per_block=per_block), ""


_RULE_ID = re.compile(r"\b((?:L\d+|BP)-[A-Z0-9]+(?:-[A-Z0-9]+)*)\b")


def _proposed(doc: AgentDoc | None, base, report: str) -> list[str]:
    """'Proposed instruction changes' as diff blocks for the rules a diagnosis report cites."""
    if not doc or not doc.instruction_text.strip():
        return []
    ids = [i for i in dict.fromkeys(_RULE_ID.findall(report)) if i not in LENGTH_RULES]
    # Reports that cite findings by title (developer mode, DevUI) rather than by rule ID.
    flat = re.sub(r"\s+", " ", report)
    try:
        ids += [f.rule_id for f in validate(doc, base) if f.origin == "maker" and f.rule_id not in LENGTH_RULES
                and f.rule_id not in ids and f.title and f.title[:60] in flat]
    except Exception:  # noqa: BLE001
        pass
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    try:
        r = fix(doc, base, ids)
    except Exception:  # noqa: BLE001
        return []
    if not r["applied"]:
        return []
    return ["### Proposed instruction changes", "",
            f"ESS Doctor can apply {len(r['applied'])} of these automatically. Ask it to run the fix, or copy the "
            "green lines into Copilot Studio > Overview > Instructions.", "", _diff_md(r["diff"])]


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
    cea = _is_cea(a.instructions, getattr(a, "agent_type", "auto"))
    findings, own, shared, card = _check_items(doc, base, cea)
    off = _offset(doc)
    presence = core_presence(doc)
    raw, studio = len(doc.instruction_text), studio_len(doc.instruction_text)
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("high", "medium", "low")}
    missing = [b["title"] for b in CORE_BLOCKS if not presence[b["id"]]]
    who = (f"the ESS {persona.upper()} template" if base else "the ESS rules (no template baseline)") + (
        ", as a custom engine agent" if cea else "")
    over = studio > DA_INSTRUCTION_LIMIT
    near = studio > DA_INSTRUCTION_LIMIT * 0.9
    core_cell = ("➖ not used by custom engine agents" if cea else
                 f"✅ {len(CORE_BLOCKS)} of {len(CORE_BLOCKS)} present" if not missing else "❌ missing: " + ", ".join(missing))
    md = ["## Instruction check", "",
          f"Checked against {who}.", "",
          "| Findings | Length (Copilot Studio count) | Authoring practices | ESS core blocks |",
          "| --- | --- | --- | --- |",
          f"| {_SEV['high']} {counts['high']} high · {_SEV['medium']} {counts['medium']} medium · {_SEV['low']} {counts['low']} low "
          f"| {'❌' if over else '⚠️' if near else '✅'} {studio:,} of {DA_INSTRUCTION_LIMIT:,} ({raw:,} raw) "
          f"| {card['followed']} of {card['applicable']} followed | {core_cell} |",
          ""]
    if a.previous:
        prev = load_agent(a.previous, "Previous version")
        prev_keys = {(f.rule_id, f.current) for f in validate(prev, base)}
        new = [f for f in findings if (f["rule_id"], f["current"]) not in prev_keys]
        md += ["### What changed since the previous version", "", _diff_md(diff_docs(prev, doc)), ""]
        md += ["### Findings introduced by this change", ""]
        md += [f"- {_SEV.get(f['severity'], '')} **{_title(f)}** ({_where(f, off).lower()}). "
               f"Employees see: {_cell(f['user_sees']).lower()}." for f in new] \
            or ["- None. The findings below were already present in the previous version."]
        md.append("")
    if findings:
        md += ["### Fix first", ""]
        shown: dict[str, int] = {}
        for i, f in enumerate(findings, 1):
            origin = " · also in the ESS template" if f["origin"] == "template" else ""
            md += [f"{i}. {_SEV.get(f['severity'], '')} **{_title(f)}**  ",
                   f"   {_where(f, off)} · {f['severity']}{origin}",
                   f"   - **Employees see:** {_cell(f['user_sees'])}",
                   f"   - **Fix:** {_cell(f['fix'])}", ""]
            if f["origin"] == "template" or not f["auto_fixable"]:
                continue
            if f["rule_id"] in LENGTH_RULES:
                md += ["   Ask ESS Doctor to shorten the instructions for the line-by-line changes.", ""]
                continue
            d, why = _rule_diff(doc, base, f["rule_id"])
            if d and d in shown:
                md += [f"   **Proposed change:** the change under finding {shown[d]} covers this too.", ""]
            elif d:
                shown[d] = i
                md += ["   **Proposed change:**", ""] + _indent(d) + [""]
            elif why:
                md += [f"   **Why there's no proposed change:** {_cell(why)}", ""]
        tests = sorted({p for f in findings if f["origin"] != "template" for p in f.get("test_prompts") or []})
        if tests:
            md += ["### Test after fixing", ""] + [f"- {t}" for t in tests[:12]] + [""]
            md += _evalset(a, tests)
    else:
        md += ["✅ No rule findings.", ""]
    if not cea:
        md += ["### ESS core blocks", ""]
        if not missing:
            md += ["✅ All present: " + ", ".join(f"{b['title']} (line {presence[b['id']][0] - off})" for b in CORE_BLOCKS) + ".", ""]
        else:
            md += [f"❌ Missing: {', '.join(missing)}. Findings above restore them.", ""]
    name = lambda d: _cell(d["title"].replace("Not following: ", ""))  # noqa: E731
    if own:
        md += ["### Recommended additions", "",
               f"Your instructions follow {card['followed']} of {card['applicable']} ESS authoring practices. These "
               f"{len(own)} lines cover the gaps that most affect routing, search and fallback answers; add them as one change:", ""]
        try:
            r = fix(doc, base, [d["rule_id"] for d in own])
            combined = _diff_md({**r["diff"], "char_delta": 0, "moderation_changed": False}, per_block=40) if r["applied"] else ""
        except Exception:  # noqa: BLE001 - fall back to the plain sentences
            combined = ""
        md += [combined or "\n".join(_diff_block([f"+ {_practice_fix(d['fix'])[1]}" for d in own])), ""]
        md += ["Covers: " + "; ".join(name(d) for d in own) + ".", ""]
    elif not shared:
        md += [f"✅ Authoring practices: all {card['applicable']} applicable ones are followed.", ""]
    if shared:
        md += [f"**Optional:** {len(shared)} more practices the ESS template doesn't include either: "
               + "; ".join(name(d) for d in shared) + ".", ""]
    choices = []
    if findings:
        choices.append(f"**\"fix {', '.join(str(i) for i in range(1, min(len(findings), 3) + 1))}\"** for findings by number")
    if own:
        choices.append("**\"fix recommended\"** for the findings plus the recommended additions")
    if shared:
        choices.append("**\"fix all\"** to include the optional practices too")
    if choices:
        md += ["To apply, ask ESS Doctor: " + "; ".join(choices) + ".", ""]
    if a.json:
        _write(a.json, json.dumps({"persona": persona, "cea": cea, "findings": findings, "practices": card,
                                   "numbers": {i: x["rule_id"] for i, x in enumerate(findings, 1)},
                                   "recommended": [d["rule_id"] for d in own], "optional": [d["rule_id"] for d in shared],
                                   "chars": {"raw": raw, "studio": studio, "limit": DA_INSTRUCTION_LIMIT}}, indent=2, default=list))
    return "\n".join(md)


def _fix_ids(a, doc: AgentDoc, base, cea: bool) -> list[str] | None:
    """Rule IDs to fix from --rules: finding numbers, 'recommended', 'all' or rule IDs. None means all findings."""
    wanted = [r.strip() for r in (a.rules or "").replace(" ", ",").split(",") if r.strip()]
    findings, own, shared, _ = _check_items(doc, base, cea)
    numbered = [x["rule_id"] for x in findings]
    keyword = [w.lower() for w in wanted]
    if keyword == ["all"]:
        return list(dict.fromkeys(numbered + [x["rule_id"] for x in own + shared])) or ["NONE"]
    if keyword == ["recommended"]:
        return list(dict.fromkeys(numbered + [x["rule_id"] for x in own])) or ["NONE"]
    if not wanted:
        if not cea:
            return None
        ids = [f.rule_id for f in validate(doc, base) if f.origin == "maker" and not _cea_skip(f)]
        return ids or ["NONE"]
    out = []
    for w in wanted:
        if w.isdigit():
            if not 1 <= int(w) <= len(numbered):
                raise InputError(f"There's no finding {w}; the check shows " + (
                    f"findings 1 to {len(numbered)}." if numbered else "no numbered findings.")
                    + " Use 'recommended' or 'all' for the authoring practices.")
            out.append(numbered[int(w) - 1])
        else:
            out.append(w)
    return list(dict.fromkeys(out))


def _keep_condense(original: AgentDoc, text: str, base) -> tuple[str, dict | None]:
    """Shorten fixed instructions to the 10% buffer target, keeping every line the fix added word for word."""
    before = {ln.strip() for ln in original.instruction_text.splitlines()}
    added = [ln for ln in dict.fromkeys(text.splitlines()) if ln.strip() and ln.strip() not in before]
    masked, marks = text, []
    for i, ln in enumerate(added):
        mark = f"- ESSDOCTORKEEP{i}"
        if ln in masked:
            masked = masked.replace(ln, mark, 1)
            marks.append((mark, ln))
    target = LENGTH_TARGET - sum(studio_len(ln) - studio_len(m) for m, ln in marks)
    c = condense(from_instructions(masked, original.name + " (fixed)"), base, target)
    out = c["new_text"]
    if any(m not in out for m, _ in marks):
        return text, None
    for m, ln in marks:
        out = out.replace(m, ln, 1)
    return out, c


def cmd_fix(a) -> str:
    doc = load_agent(a.instructions)
    base = baseline(_persona(a.persona, doc))
    cea = _is_cea(a.instructions, getattr(a, "agent_type", "auto"))
    off = _offset(doc)
    ids = _fix_ids(a, doc, base, cea)

    def attempt(rule_ids):
        r = fix(doc, base, rule_ids)
        fixed_len, shortened = studio_len(r["new_instructions"]), None
        if fixed_len > DA_INSTRUCTION_LIMIT and not getattr(a, "no_condense", False):
            text, shortened = _keep_condense(doc, r["new_instructions"], base)
            if shortened:
                new_doc = from_instructions(text, doc.name + " (fixed)", "fix")
                new_doc.content_moderation = doc.content_moderation
                old_doc = from_instructions(doc.instruction_text, doc.name, "current")
                old_doc.content_moderation = doc.content_moderation
                r.update(new_instructions=text, diff=diff_docs(old_doc, new_doc))
        return r, fixed_len, shortened

    r, fixed_len, shortened = attempt(ids)
    mode = [w.strip().lower() for w in (a.rules or "").split(",")]
    if ids and mode in (["all"], ["recommended"]):
        # Added lines can make more practices applicable (a "never" rule brings in "balance prohibitions"): add those too.
        for _ in range(2):
            fixed_doc = from_instructions(r["new_instructions"], doc.name + " (fixed)", "fix")
            items = _check_items(fixed_doc, base, cea)
            more = [x["rule_id"] for x in items[1] + (items[2] if mode == ["all"] else [])
                    if x["rule_id"] not in ids]
            if not more:
                break
            ids += more
            r, fixed_len, shortened = attempt(ids)
    # Over the limit even after shortening: drop the lowest-priority practices (last numbers first) until it fits.
    dropped: list[str] = []
    while ids and studio_len(r["new_instructions"]) > DA_INSTRUCTION_LIMIT and not getattr(a, "no_condense", False):
        practice = [i for i in ids if i.startswith("BP-")]
        if not practice:
            break
        dropped.insert(0, practice[-1])
        ids = [i for i in ids if i != practice[-1]]
        r, fixed_len, shortened = attempt(ids)
    _write(a.out, r["new_instructions"])
    c = r["chars"]
    after = studio_len(r["new_instructions"])
    new_doc = from_instructions(r["new_instructions"], doc.name + " (fixed)", "fix")
    new_doc.topics, new_doc.globals_defined = doc.topics, doc.globals_defined
    left, left_own, left_shared, card = _check_items(new_doc, base, cea)
    left_findings = [f for f in left if f["origin"] != "template"]
    size = "❌" if after > DA_INSTRUCTION_LIMIT else "⚠️" if after > LENGTH_TARGET else "✅"
    md = ["## ESS Doctor fix", "", "", "",
          f"After the fix: {len(left_findings)} finding(s) left, {len(left_own)} recommended addition(s) not added, "
          f"{card['followed']} of {card['applicable']} authoring practices followed.", ""]
    written = len(r["applied"])
    if shortened and after < fixed_len:
        md += [f"> ✂️ **Shortened automatically:** the fixes took the instructions to {fixed_len:,} characters, over the "
               f"{DA_INSTRUCTION_LIMIT:,} limit, so the existing text was tightened to {after:,}. Every line the fix added "
               "is kept word for word and every ESS ability is kept; review the changed lines under \"What changed\".", ""]
    if dropped:
        from ess_maker.doctor.practices import BY_ID

        def _ptitle(rid):
            p = BY_ID.get(rid[3:]) if rid.startswith("BP-") else None
            return p.title if p else rid
        md += [f"> ⚠️ **{len(dropped)} practice(s) not added: they don't fit under the {DA_INSTRUCTION_LIMIT:,}-character "
               "limit.** " + "; ".join(_ptitle(d) for d in dropped) + ". To add them, first shorten or move the "
               "longest lines into topic descriptions (ask ESS Doctor to shorten), then fix again.", ""]
    elif after > DA_INSTRUCTION_LIMIT:
        md += [f"> ❌ **{after - DA_INSTRUCTION_LIMIT:,} characters over the limit.** Copilot Studio cuts off the end. "
               "Apply fewer fixes, or shorten the instructions first.", ""]
    if r["applied"]:
        md += ["### Done: already in the new instructions", "",
               f"These {len(r['applied'])} changes are written into `{a.out}`; you don't edit them in by hand.", ""]
        md += [f"- **{_title(x)}**{_at(x, off)}: {_cell(x['change'])}" for x in r["applied"]] + [""]
    if r["manual"]:
        md += ["### Not done: needs your decision", ""] + [f"- **{_title(x)}**{_at(x, off)}: {_cell(x['change'])}" for x in r["manual"]] + [""]
    md += ["### What changed", "", _diff_md(r["diff"]), ""]
    remaining = left_findings + left_own
    if remaining:
        md += ["### Still open", ""] + [f"- {_SEV.get(x['severity'], '')} {_title(x)}" for x in remaining[:10]] + [""]
    if left_shared and not dropped:
        md += [f"**Not added on purpose:** {len(left_shared)} optional practices that the ESS template doesn't include "
               "either. They're lower priority; reply \"fix all\" if you want them too.", ""]
    if r["tests"]:
        md += ["### Re-test with", ""] + [f"- {t}" for t in r["tests"][:10]] + [""]
    tests = list(r["tests"])
    if getattr(a, "append", None):
        add = [ln.rstrip() for ln in _text(_read(a.append)).splitlines() if ln.strip()]
        cur = Path(a.out).read_text(encoding="utf-8")
        have = {ln.strip() for ln in cur.splitlines()}
        new = [ln for ln in add if ln.lstrip().startswith("#") or ln.strip() not in have]
        body = [ln for ln in new if not ln.lstrip().startswith("#")]
        if body:
            text = cur.rstrip("\n") + "\n\n" + "\n".join(new) + "\n"
            short = None
            if studio_len(text) > DA_INSTRUCTION_LIMIT and not getattr(a, "no_condense", False):
                text, short = _keep_condense(doc, text, base)
            _write(a.out, text)
            final = studio_len(text)
            mark = "❌" if final > DA_INSTRUCTION_LIMIT else "⚠️" if final > LENGTH_TARGET else "✅"
            head = next((ln.lstrip("# ").strip() for ln in new if ln.lstrip().startswith("#")), "audit")
            md += [f"### Also added: {head} section", "",
                   f"From the agent audit. Length now {mark} {final:,} of {DA_INSTRUCTION_LIMIT:,}"
                   + (" (existing text tightened to fit; the added lines are word for word)" if short else "") + ".", "",
                   "\n".join(_diff_block([f"+ {ln}" for ln in new])), ""]
        else:
            md += ["### Audit section already present", "", "The instructions already contain those lines.", ""]
    if getattr(a, "draft", None):
        fields = json.loads(_text(_read(a.draft)))
        fixed_doc = load_agent(a.out)
        intake, dr, short = _do_draft(fixed_doc, base, fields, getattr(a, "no_condense", False))
        if "new_instructions" not in dr:
            md += ["### Rule not added", "", "The rule from the diagnosis needs more detail: "
                   + "; ".join(_cell(q if isinstance(q, str) else q.get("question") or q) for q in dr.get("questions") or [])
                   + ".", ""]
        else:
            _write(a.out, dr["new_instructions"])
            final = studio_len(dr["new_instructions"])
            mark = "❌" if final > DA_INSTRUCTION_LIMIT else "⚠️" if final > LENGTH_TARGET else "✅"
            md += [f"### Also added: {CHANGE_TYPES[intake.change_type].split(' (')[0].lower()} rule", "",
                   f"Goes in **{_cell(dr.get('section') or 'Instructions')}**. Length now {mark} {final:,} of "
                   f"{DA_INSTRUCTION_LIMIT:,}" + (" (existing text tightened to fit; the new rule is word for word)"
                                                   if short else "") + ".", "",
                   _diff_md({**dr["diff"], "char_delta": 0, "moderation_changed": False}), ""]
            tests += [t for t in dr.get("tests") or [] if t not in tests]
            written += 1
    if getattr(a, "append", None) and any(ln.startswith("### Also added:") for ln in md):
        written += 1
    final = studio_len(Path(a.out).read_text(encoding="utf-8"))
    mark = "❌" if final > DA_INSTRUCTION_LIMIT else "⚠️" if final > LENGTH_TARGET else "✅"
    decide = f"**{len(r['manual'])} need your decision** (listed below)" if r["manual"] else "none need your decision"
    md[2] = (f"**{written} change(s) written into the new instructions**; {decide}. "
             f"Length {studio_len(doc.instruction_text):,} → {mark} {final:,} of {c['limit']:,} (Copilot Studio count).")
    md[3] = (f"**Your step:** copy the final instructions from `{a.out}` into Copilot Studio (Overview > Instructions > "
             "Edit) and publish. ESS Doctor doesn't change the agent itself.")
    final_doc = from_instructions(Path(a.out).read_text(encoding="utf-8"), doc.name + " (final)", "fix")
    final_doc.topics, final_doc.globals_defined = doc.topics, doc.globals_defined
    fl, fown, _, fcard = _check_items(final_doc, base, cea)
    md[4] = (f"After the fix: {len([f for f in fl if f['origin'] != 'template'])} finding(s) left, "
             f"{fcard['followed']} of {fcard['applicable']} authoring practices followed."
             + (f" The added text made {len(fown)} more recommended practice(s) relevant ("
                + "; ".join(_title(x) for x in fown) + "); reply \"fix recommended\" again to add "
                + ("it." if len(fown) == 1 else "them.") if fown else ""))
    md += _evalset(a, tests, a.out)
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
        done = [s for s in r["steps"] if isinstance(s, str) or s.get("applied", True)]
        md += ["### What was shortened", ""] + ([
            f"- {_cell(s if isinstance(s, str) else s.get('what') or s.get('step'))}"
            + (f" ({s['saved']:,} characters)" if isinstance(s, dict) and s.get("saved") else "") for s in done[:20]]
            or ["- Nothing could be shortened automatically without changing what a rule says."]) + [""]
    if r["abilities_kept"]:
        md += ["### Abilities kept", "", ", ".join(f"{k}: {v}" for k, v in r["abilities_kept"].items()), ""]
    if not r["reached"] and r["candidates"]:
        md += ["### Lines to shorten or move into topic descriptions", ""] + [
            f"- Line {x['line']} ({x['chars']:,} characters): \"{_clip(x.get('excerpt', ''), 120)}\" · {_cell(x.get('why'))}"
            if isinstance(x, dict) else f"- {_cell(x)[:300]}" for x in r["candidates"]] + [""]
    if r["new_text"] != doc.instruction_text:
        md += ["### What changed", "", _diff_md(diff_docs(from_instructions(doc.instruction_text, doc.name, "current"),
                                                          from_instructions(r["new_text"], doc.name + " (shortened)"))), ""]
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


def _do_draft(doc: AgentDoc, base, fields: dict, no_condense: bool = False):
    """Run the drafter on doc: (intake, result, shortened) — result has no 'new_instructions' when it needs detail."""
    if fields.get("change_type") not in CHANGE_TYPES:
        raise InputError(f"--type must be one of: {', '.join(CHANGE_TYPES)}")
    known = set(Intake.__dataclass_fields__)
    intake = Intake(**{k: v for k, v in fields.items() if k in known})
    r = _run_sync(draft(intake, doc, base, use_llm=False))
    shortened = None
    if "new_instructions" not in r:
        return intake, r, None
    r["section"] = ", ".join(re.sub(r"\s*\(.*$", "", b["title"]).strip()
                             for b in (r.get("diff") or {}).get("blocks") or [] if b.get("status") != "unchanged")
    if studio_len(r["new_instructions"]) > DA_INSTRUCTION_LIMIT and not no_condense:
        drafted = studio_len(r["new_instructions"])
        shortened = _auto_condense(doc, base, r)
        if shortened:
            shortened["drafted"] = drafted
    return intake, r, shortened


def cmd_draft(a) -> str:
    doc = load_agent(a.instructions)
    base = baseline(_persona(a.persona, doc))
    fields = json.loads(_text(_read(a.intake))) if a.intake else {}
    fields.setdefault("change_type", a.type)
    fields.setdefault("intent", a.intent or "")
    intake, r, shortened = _do_draft(doc, base, fields, getattr(a, "no_condense", False))
    if "new_instructions" not in r:
        md = ["## ESS Doctor draft: more detail needed", ""]
        if r.get("message"):
            md += [_cell(r["message"]), ""]
        md += [f"- {_cell(q if isinstance(q, str) else q.get('question') or q.get('label') or q)}"
               for q in r.get("questions") or r.get("needs_input") or []]
        return "\n".join(md)
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
    ctx: dict = {}
    report = _xray(a, ctx)
    doc = ctx.get("doc")
    if not doc:
        return report
    base = ctx.get("base") or baseline(_persona(getattr(a, "persona", None) or "auto", doc))
    proposed = _proposed(doc, base, report)
    return report + ("\n\n" + "\n".join(proposed) if proposed else "")


def _xray(a, ctx: dict) -> str:
    data = _read(a.evidence)
    snap = _snapshot(data)
    if snap:
        from ess_maker.doctor import transcript

        dialog, bot_yaml = snap
        current = load_agent(a.instructions, "Current instructions") if a.instructions else (
            _from_text(bot_yaml, "Instructions in the snapshot", "snapshot") if bot_yaml else None)
        ctx["doc"] = current
        if not transcript.load(dialog):
            raise InputError("The snapshot's dialog.json has no conversation activities.")
        return "```text\n" + transcript.build(dialog, question=a.question, response=a.response, current=current, snapshot=True,
                                             bot_yaml=bot_yaml) + "\n```"
    text = _text(data).lstrip("\ufeff")
    current = load_agent(a.instructions, "Current instructions") if a.instructions else None
    ctx["doc"] = current
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
                raise InputError("The conversation export has no user turns to analyze (only commands such as /debug).")
            turns = parsed["turns"]
            idx = len(turns) - 1
            if a.question:
                idx = next((i for i in range(len(turns) - 1, -1, -1) if a.question.lower() in turns[i]["question"].lower()), idx)
            if a.turn:
                idx = max(0, min(a.turn - 1, len(turns) - 1))
            t = turns[idx]
            served = from_instructions(t["instructions"], "Instructions that ran", "devui") if t["instructions"] else None
            basis = served or current
            findings = validate(basis, baseline(guess_persona(current or basis))) if basis else []
            ctx.update(doc=basis, base=baseline(guess_persona(current or basis)) if basis else None)
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
        return _sessions_report(sessions, a.question or "", current)
    if not devmode.looks_like_devmode(text):
        raise InputError("Couldn't recognise this evidence. Paste the developer mode (-developer on) card or its Raw info "
                         "JSON, upload a Purview audit export (CSV with an AuditData column), or, if Microsoft support is "
                         "helping you, a conversation export (JSON) they shared.")
    hq, hr, _ = devmode.split_header(text)
    return "```text\n" + devmode_report.build(text, question=a.question or hq, response=a.response or hr, current=current) + "\n```"


_SESSION_ICON = {"ok": "✅", "command": "ℹ️", "generic": "⚠️", "declined": "❌", "fallback": "❌", "error": "❌", "empty": "❌"}
_SESSION_FIX = {
    "fallback": ("Agent maker, then knowledge owner",
                 "Check whether a knowledge source covers {subjects}. If one does, put the words employees use in its title "
                 "and description (the name and how people write it, for example with and without symbols or "
                 "abbreviations), or add them to the terminology rules. If none does, add the content. If the question "
                 "asks for an action, check the description of the topic that should handle it."),
    "generic": ("Agent maker (user context topic and instructions)",
                "Make the user context topic fill in {missing} for every employee, from the HR system or Microsoft Entra "
                "ID; here they were blank. Then add the instruction below so a blank value leads to a question, not a "
                "generic answer."),
    "declined": ("Agent maker (instructions)",
                 "Run the sorry triage with the agent's instructions or a test pane snapshot to find the rule that declined."),
    "error": ("Platform (Microsoft support), then maker (On Error topic)",
              "Re-run the question; if the error repeats, open a support case with the session ID and time, and make sure "
              "the On Error topic handles this error code."),
    "empty": ("Agent maker", "Ask the question in the test pane and upload the snapshot to see where it stopped."),
    "command": ("Agent maker (optional)",
                "Employees typed {commands} as a message. If they expect it to do something, add a topic with that trigger "
                "phrase or tell them what to use instead; otherwise ignore it."),
}
_CONTEXT_RULE = ("- When the answer depends on the employee's country, office, employment type, level or tenure, use the "
                 "user context values. If one is blank, ask the employee for it in one short question, or give the base "
                 "policy and list how it varies; don't present one country's policy as theirs.")


def _context_rule_md(question: str, missing: str, doc: AgentDoc | None, intake_path: str) -> list[str]:
    """The blank-profile-value rule as the drafter writes it: its exact section and line when the instructions are
    known, and saved as a draft intake so `fix --draft` can add it together with the fixes."""
    fields = {"change_type": "context", "subject": missing, "should_affect": [question], "must_not_affect": [],
              "intent": "When a profile value the answer depends on is blank, ask for it or give the base policy "
                        "and how it varies, instead of a generic answer."}
    placed = None
    if doc is not None and doc.instruction_text.strip():
        try:
            _, placed, _ = _do_draft(doc, baseline(guess_persona(doc)), fields, True)
        except Exception:  # noqa: BLE001 - fall back to the general wording
            placed = None
        placed = placed if placed and "new_instructions" in placed else None
    _write(intake_path, json.dumps(fields, indent=2))
    out = []
    if placed:
        block = next((b for b in placed["diff"].get("blocks") or [] if b["status"] != "unchanged"), None)
        line = next((h.get("line") for h in (block or {}).get("hunks") or [] if h.get("line")), None)
        section = re.sub(r"\s*\(.*$", "", block["title"]).strip() if block else "Context handling"
        lines = doc.instruction_text.splitlines()
        prev = next((lines[i].strip() for i in range((line or 1) - 2, -1, -1) if 0 <= i < len(lines) and lines[i].strip()), "")
        out += [f"   **Proposed instruction:** a new line at the end of your **{_cell(section)}** section"
                + (f" (line {line})" if line else "")
                + (f", right after *\"{_clip(_cell(prev), 90)}\"*" if prev else "") + ":", ""]
        out += _indent(_diff_md({**placed["diff"], "char_delta": 0, "moderation_changed": False}), "   ") + [""]
    else:
        clause = _CONTEXT_RULE
        try:
            _, d0, _ = _do_draft(from_instructions("#Context handling\n- Use the user profile.\n", "template"), None, fields, True)
            clause = (d0.get("clause") or clause).strip()
        except Exception:  # noqa: BLE001
            pass
        out += ["   **Proposed instruction:** add it as the last line of the section that lists the employee's profile "
                "(`user_profile`); in the ESS HR template that's **#Context handling**, right after the search examples. "
                "Upload the instructions or a test pane snapshot to see the exact line.", ""]
        out += _indent("\n".join(_diff_block(["+ " + clause])), "   ") + [""]
    return out + [f"   <!-- essdoctor-intake: {intake_path} -->", ""]


def _sessions_report(sessions: list[dict], question: str = "", doc: AgentDoc | None = None,
                     intake_path: str = "outputs/intake_context.json") -> str:
    """Monitor > Sessions download: a verdict per session from the reply text, grouped causes and fixes.
    With the instructions (doc), the blank-value rule is drafted into its exact section and line."""
    from ess_maker.doctor import transcript

    from datetime import datetime as _dt

    def when(s: dict):
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return _dt.strptime(s["start"].strip()[:22].rstrip("Z"), fmt)
            except ValueError:
                pass
        return _dt.max

    sessions = sorted(sessions, key=when)
    rows = [(s, transcript.classify_session(s)) for s in sessions]
    n = len(rows)
    count = {k: sum(1 for _, v in rows if v["kind"] == k) for k in _SESSION_ICON}
    bad = n - count["ok"] - count["command"]
    md = ["## Sessions X-ray", "",
          f"**{n - bad} of {n}** sessions answered · ❌ {count['fallback'] + count['declined'] + count['error'] + count['empty']} "
          f"not answered · ⚠️ {count['generic']} generic answer(s)", "",
          "> ℹ️ **This is a Monitor > Sessions download.** It holds only the message text, with commas and line breaks "
          "removed and replies cut at about 500 characters, so it can't show which topic ran, what knowledge search found "
          "or which error occurred. Verdicts come from the reply wording.", "",
          "| # | Started (UTC) | Question | Outcome | Verdict |", "| --- | --- | --- | --- | --- |"]
    q_low = question.strip().lower()[:60]
    for i, (s, v) in enumerate(rows, 1):
        extra = [x for x in v["exchanges"] if x["kind"] != v["kind"] and x["kind"] != "ok"]
        verdict = f"{_SESSION_ICON[v['kind']]} {v['label']}" + "".join(
            f"<br>{_SESSION_ICON[x['kind']]} then: {_cell(x['question'])[:40]}: {x['label'].lower()}" for x in extra)
        mark = " ◀" if q_low and q_low in (s["question"] or "").lower() else ""
        md.append(f"| {i}{mark} | {_cell(s['start'])} | **{_cell(s['question'])[:120]}** | "
                  f"{_cell(s['outcome'])}{', ' + _cell(s['turns']) + ' turns' if s.get('turns') else ''} | {verdict} |")

    groups: dict[str, list[tuple[int, dict]]] = {}
    for i, (s, v) in enumerate(rows, 1):
        for x in v["exchanges"]:
            if x["kind"] != "ok":
                groups.setdefault(x["kind"], []).append((i, x))
    if groups:
        md += ["", "### What went wrong, biggest first", ""]
        order = sorted(groups, key=lambda k: (k == "command", -len(groups[k]), _SESSION_ICON[k]))
        for k_i, kind in enumerate(order, 1):
            items = groups[kind]
            owner, fix = _SESSION_FIX[kind]
            subjects = ", ".join(dict.fromkeys(
                f'"{re.sub(r"^(what|who|where)(\s+is|\s+are|[’\x27]s)\s+|^tell me about\s+", "", _cell(x["question"]), flags=re.I).rstrip("?. ")[:60]}"'
                for _, x in items))
            missing = next((x.get("missing") for _, x in items if x.get("missing")), "") or "country, employment type and level"
            fix = fix.format(subjects=subjects, missing=missing, commands=subjects)
            md += [f"{k_i}. {_SESSION_ICON[kind]} **{items[0][1]['label']}** · {len(items)} · sessions "
                   + ", ".join(f"#{i}" for i, _ in items) + "  ",
                   f"   - **What happened:** {_cell(items[0][1]['detail'])}"
                   + (f" Agent said: *\"{_cell(items[0][1]['reply'])[:200]}\"*" if items[0][1]["reply"] and kind != "generic" else ""),
                   f"   - **Who fixes it:** {owner}",
                   f"   - **Fix:** {fix}", ""]
            if kind == "generic":
                md += _context_rule_md(items[0][1]["question"], missing, doc, intake_path)
    failed_q = [f'"{_cell(x["question"])[:60]}"' for kind in ("fallback", "declined", "error", "empty", "generic")
                for _, x in groups.get(kind, [])]
    md += ["### Next steps", ""]
    steps = []
    if failed_q:
        steps.append("For " + ", ".join(failed_q[:4]) + ": ask the same question in the Copilot Studio test pane and upload "
                     "the snapshot (.zip), or read the transcript with `transcript-xray` by agent name. Both show the topic, "
                     "the knowledge search and its results, so the X-ray can name the failing step.")
    if "generic" in groups:
        steps.append("Upload the agent's instructions (or a snapshot) and ESS Doctor drafts the blank-value rule into the "
                     "right section as a diff, with an evaluation test set.")
    if "fallback" in groups:
        steps.append("Send the unmatched subjects to the knowledge owner if no source covers them.")
    steps.append("After the fixes, download the sessions again and re-run this X-ray to compare.")
    md += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    return "\n".join(md)


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
                         "agent: developer mode cards (text or Raw info JSON) or conversation exports from Microsoft support. "
                         "One file per conversation or several in a .zip.")
    # A session row and the transcript of the same conversation are one conversation: keep the transcript, which has the cause.
    asked = {re.sub(r"\s+", " ", (p.get("question") or "")).strip().lower() for k, _, p in sources if k == "transcript"}
    sources = [s for s in sources if not (s[0] == "session" and
                                          re.sub(r"\s+", " ", s[2].get("question") or "").strip().lower() in asked - {""})]
    instructions = (load_agent(a.instructions).instruction_text if a.instructions else
                    _from_text(snapshot_instructions, "Instructions in the snapshot", "snapshot").instruction_text
                    if snapshot_instructions else "")
    cea = any(kind in ("transcript", "session") for kind, _, _ in sources)
    md = sorry.build(sorry.triage(sources, instructions, cea=cea))
    if instructions:
        doc = from_instructions(instructions, "Current instructions", "sorry")
        proposed = _proposed(doc, baseline(_persona(getattr(a, "persona", None) or "auto", doc)), md)
        md += "\n\n" + "\n".join(proposed) if proposed else ""
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
    from ess_maker.doctor import audit, topicgen

    raw: dict[int, str] = {}
    agents = []
    for name, text in _agent_files(a.agents):
        x = audit.parse(text, name)
        raw[id(x)] = text
        agents.append(x)
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
        # A full export (bot content) is a Copilot Studio agent: its template routes and grounds through topics.
        checks[id(x)] = [f for f in validate(doc, base) if f.origin != "template" and not (x.full_export and _cea_skip(f))]

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
        md.append(f"| **{_cell(x.name)}** | {role.get(id(x), 'Other')} | {ks if x.full_export else '—'} | "
                  f"{sum(1 for t in x.topics if t.active and t.searches) if x.full_export else '—'} | "
                  f"{len(x.connected) or len(x.instruction_refs) or '—'} | "
                  f"{studio_len(x.instructions):,} characters | {sev or ('✅ none' if x.instructions else '—')} | "
                  f"{x.settings.get('contentModeration', '—')} |")
    md.append("")

    real = [g for g in gaps if g.severity != "info"]
    fixes = [g for g in real if not g.optional]
    options = [g for g in real if g.optional]
    md += ["### Gaps across the agents", ""]
    if not fixes:
        md += ["✅ No cross-agent gaps found.", ""]
    for i, g in enumerate(fixes + options, 1):
        if i == len(fixes) + 1:
            md += ["### Optional", "", "Choices to consider, not fixes; each has a trade-off.", ""]
        md += [f"{i}. {_SEV.get(g.severity, '') if not g.optional else '⚪'} **{_cell(g.title)}**  ",
               f"   {', '.join('**' + _cell(n) + '**' for n in g.agents)}" + ("" if g.optional else f" · {g.severity}"),
               f"   - **Why it matters:** {_cell(g.detail)}",
               f"   - **{'Option' if g.optional else 'Fix'}:** {_cell(g.fix)}", ""]
        if g.proposals:
            what = "Source" if "Knowledge" in g.where else "Agent"
            md += [f"   | {what} | Now | Proposed description |", "   | --- | --- | --- |"]
            md += [f"   | {_cell(n)} | {_clip(_cell(now), 90)} | {_cell(prop)} |" for n, now, prop in g.proposals[:12]]
            if len(g.proposals) > 12:
                md.append(f"   | …and {len(g.proposals) - 12} more | | |")
            md.append("")
        if g.change:
            md += [f"   **Proposed change**" + (f" (Copilot Studio > {_cell(g.where)})" if g.where else "") + ":", ""]
            md += _indent("\n".join(_diff_block(g.change))) + [""]

    rows = [(x, checks[id(x)]) for x in agents if checks.get(id(x))]
    md += ["### Instruction check per agent", ""]
    if rows:
        for x, fs in rows:
            top = sorted(fs, key=lambda f: {"high": 0, "medium": 1, "low": 2}.get(f.severity, 3))
            sev = " · ".join(f"{_SEV[s]} {n} {s}" for s in ("high", "medium", "low")
                             if (n := sum(1 for f in fs if f.severity == s)))
            names = "; ".join(_title(f.to_dict()) for f in top[:4]) + (f"; and {len(top) - 4} more" if len(top) > 4 else "")
            md.append(f"- **{_cell(x.name)}** · {sev}: {names}.")
        md.append("")
    else:
        md += ["✅ No instruction findings beyond the ESS template.", ""]

    # Topics of full exports go through the same topic rules as generated workflows.
    topic_checks = {id(x): topicgen.check(raw[id(x)]) for x in agents if x.full_export and id(x) in raw}
    if topic_checks:
        md += ["### Topic check per agent", ""]
        for x in agents:
            if id(x) not in topic_checks:
                continue
            fs, n = topic_checks[id(x)], len(topicgen._topics(raw[id(x)]))
            if not fs:
                md.append(f"- **{_cell(x.name)}** · ✅ all {n} topics pass the ESS topic rules.")
                continue
            sev = " · ".join(f"{_SEV[s]} {c} {s}" for s in ("high", "medium", "low")
                             if (c := sum(1 for f in fs if f.severity == s)))
            by_rule: dict[str, list[str]] = {}
            for f in fs:
                by_rule.setdefault(f.title, []).append(f.topic)
            top = sorted(by_rule.items(), key=lambda kv: (min({"high": 0, "medium": 1, "low": 2}[f.severity]
                                                                 for f in fs if f.title == kv[0]), -len(kv[1])))[:3]
            md.append(f"- **{_cell(x.name)}** · {n} topics · {sev}: "
                      + "; ".join(f"{_cell(t)} ({len(ts)})" for t, ts in top) + ".")
        md.append("")

    notes = [g for g in gaps if g.severity == "info"]
    missing = [re.sub(r" wasn't uploaded$", "", g.title) for g in notes if g.rule_id == "A11-CHILD-NOT-PROVIDED"]
    partial = [x.name for x in agents if not x.full_export]
    if notes or partial:
        md += ["### Not checked", ""]
        if missing:
            md.append(f"- **{len(missing)} connected agent(s) weren't uploaded**, so their knowledge, topics and "
                      "instructions weren't checked: " + ", ".join(f"**{_cell(n)}**" for n in missing)
                      + ". Upload a test pane snapshot or export of each to include them.")
        md += [f"- {_cell(g.title)}: {_cell(g.fix)}" for g in notes if g.rule_id != "A11-CHILD-NOT-PROVIDED"]
        if partial:
            md.append(f"- {', '.join('**' + _cell(n) + '**' for n in partial)}: the file has only the instructions, so "
                      "knowledge sources, topics and settings weren't checked. Upload the agent's test pane snapshot for "
                      "a full audit.")
        md.append("")

    # Next step: one instruction fix per agent (its findings, recommended additions and any audit section), plus the
    # Copilot Studio changes. The commands go in hidden comments for the planner.
    src = {Path(p).name: p for p in a.agents}
    deleg = next((g for g in gaps if g.rule_id == "A16-HUB-DELEGATION-RULES"), None)
    choices, hidden = [], []
    for x in agents:
        fs = checks.get(id(x), [])
        extra = deleg is not None and hub is x
        if not (fs or extra) or x.file not in src:
            continue
        cmd = f'fix "{src[x.file]}" --rules recommended' + (" --agent-type cea" if x.full_export else "")
        what = f"its {len(fs)} instruction finding(s) and the recommended additions" if fs else "the recommended additions"
        if extra:
            path = f"outputs/audit_{_norm_name(x.name)}_delegation.txt"
            _write(path, "\n".join(ln[2:] for ln in deleg.change if ln.startswith("+ ")))
            cmd += f' --append "{path}"'
            what += ", plus the delegation section"
        choices.append(f"- **\"fix {x.name} instructions\"**: {what}, fitted under 8,000 characters, with a test set.")
        hidden.append(f"<!-- essdoctor-next: \"fix {x.name} instructions\" = {cmd} -->")
    for x in agents:
        tf = topic_checks.get(id(x), [])
        if any(f.severity in ("high", "medium") for f in tf) and x.file in src:
            choices.append(f"- **\"check {x.name} topics\"**: every topic finding with its fix (generate workflows); "
                           "any topic can then be regenerated the ESS way.")
            hidden.append(f"<!-- essdoctor-next: \"check {x.name} topics\" = topic-check \"{src[x.file]}\" -->")
    studio = [i for i, g in enumerate(fixes, 1) if g.rule_id != "A16-HUB-DELEGATION-RULES"]
    if choices or studio:
        md += ["### What to do next", ""]
        if choices:
            md += ["Reply with one of these:", ""] + choices + [""]
        if studio:
            md += [f"The other fixes ({', '.join(str(i) for i in studio[:12])}) are made in Copilot Studio, not in the "
                   "instructions. Paste the proposed descriptions as a starting point and add the subjects each covers.", ""]
        md += hidden
    return "\n".join(md)


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "agent"

def cmd_topic_gen(a) -> str:
    """Generate a topic, its ESS template configuration, a matching skill and test questions from a scenario spec."""
    from ess_maker.doctor import evalset, topicgen

    data = json.loads(_text(_read(a.spec)))
    if getattr(a, "agent_type", "auto") in ("da", "cea"):
        data["agent_type"] = a.agent_type
    try:
        r = topicgen.generate(data)
    except topicgen.SpecError as e:
        raise InputError(str(e)) from None
    s = r["spec"]
    out = Path(a.out)
    _write(str(out / "topic.yaml"), r["topic"])
    _write(str(out / "SKILL.md"), r["skill"])
    cfg = r["config"]
    if cfg:
        _write(str(out / cfg[0]), cfg[1])
    rows = evalset.rows_for([{"prompt": x["prompt"], "expected": x["expected"]} for x in r["tests"]], include_golden=False)
    _write(str(out / "evalset.csv"), evalset.to_csv(rows))
    kind = {"read": "Answers a question", "create": "Creates a record", "update": "Updates a record"}[s.operation]
    md = ["## Topic generator", "",
          "| Topic | Backend | Agent type | Does | ESS scenario |", "| --- | --- | --- | --- | --- |",
          f"| **{_cell(topicgen.topic_name(s))}** | {topicgen.BACKENDS[s.backend]} | "
          f"{'declarative agent' if s.agent_type == 'da' else 'custom engine agent'} | {kind} for the "
          f"{'manager (direct reports)' if s.audience == 'manager' else 'signed-in employee'} | "
          f"{('`' + topicgen.scenario_name(s) + '`') if topicgen.scenario_name(s) else '—'} |", ""]
    md += ["### Topic", "",
           "In Copilot Studio: **Topics > Add a topic > From blank**, name it "
           f"**{_cell(s.title)}**, open **⋯ > Open code editor**, select all, paste this, and save.", "",
           "```yaml", r["topic"].rstrip(), "```", ""]
    if cfg:
        value = cfg[1]
        m = re.search(r"<msdyn_value>(.*)</msdyn_value>", value, re.S)
        if m:
            import html as _html
            value = _html.unescape(m.group(1))
        lang = "xml" if value.lstrip().startswith("<") else "json"
        md += ["### Template configuration", "",
               "Add a row to the **Employee Self Service Template Configuration** table with unique name "
               f"`{topicgen.scenario_name(s)}` and this value. The topic finds it by that name.", "",
               f"```{lang}", value.rstrip(), "```", ""]
    md += ["### Check before importing", ""] + [f"- {_cell(n)}" for n in r["notes"]] + [""]
    if r["findings"]:
        md += ["### Topic check", ""] + [f"- {_SEV.get(x.severity, '')} {_cell(x.title)}: {_cell(x.fix)}"
                                          for x in r["findings"]] + [""]
    else:
        md += ["### Topic check", "", "✅ The generated topic passes every topic rule.", ""]
    md += ["### Files", "",
           f"- `{out / 'topic.yaml'}`: the topic above.",
           *([f"- `{out / cfg[0]}`: the template configuration record."] if cfg else []),
           f"- `{out / 'SKILL.md'}`: the same scenario as an agent skill, for when it moves from a topic to a skill.",
           f"- `{out / 'evalset.csv'}`: {len(rows)} test questions (the trigger phrases, and what the topic must not "
           "handle). " + evalset.IMPORT_STEPS, ""]
    return "\n".join(md)


def cmd_topic_check(a) -> str:
    """Check one topic (code editor YAML) or every topic in a bot content export against the ESS topic rules."""
    from ess_maker.doctor import topicgen

    raw = _read(a.topic)
    text = _text(raw)
    if zipfile.is_zipfile(io.BytesIO(raw)):
        snap = _snapshot(raw)
        text = snap[1] if snap and snap[1] else ""
    if not re.search(r"kind: (AdaptiveDialog|DialogComponent)|beginDialog:", text):
        raise InputError("This isn't a topic. Paste the topic from Copilot Studio (Topics > the topic > ⋯ > Open code "
                         "editor) or upload the agent's test pane snapshot or bot content export.")
    n = len(topicgen._topics(text))
    fs = topicgen.check(text, getattr(a, "agent_type", "auto"))
    counts = {s: sum(1 for f in fs if f.severity == s) for s in ("high", "medium", "low")}
    md = ["## Topic check", "",
          f"{n} topic(s) checked · {_SEV['high']} {counts['high']} high · {_SEV['medium']} {counts['medium']} medium · "
          f"{_SEV['low']} {counts['low']} low", ""]
    if not fs:
        return "\n".join(md + ["✅ Every topic passes the ESS topic rules."])
    md += ["| # | Topic | Finding | Fix |", "| --- | --- | --- | --- |"]
    md += [f"| {i} | **{_cell(f.topic)}** | {_SEV.get(f.severity, '')} {_cell(f.title)} | {_cell(f.fix)} |"
           for i, f in enumerate(fs[:60], 1)]
    if len(fs) > 60:
        md.append(f"| | …and {len(fs) - 60} more | | |")
    md += ["", "To rebuild a topic the ESS way, describe the scenario and ESS Doctor generates it (generate workflows)."]
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
    md = ["## Evaluation triage", "", f"**{passed} of {total} passed** · ✅ {passed} passed · ❌ {failed} failed", ""]

    for q in req:
        if q["missing"]:
            md += [f"> ⚠️ **A required line is missing from {q['missing']} of {q['of']} responses"
                   + (f", including {q['missing_in_passes']} that passed" if q["missing_in_passes"] else "") + ".**",
                   f"> Instruction line {q['line']} asks every response to include: *\"{_cell(q['text'])}\"*",
                   "> The grader only judged answer quality, so passing questions don't show this. "
                   "Add a check for this exact sentence to the evaluation.", ""]

    ch = r.get("contact_hr") or {}
    if ch.get("count"):
        md += [f"> ℹ️ **{ch['count']} of {ch['of']} responses tell the employee to contact HR or give a phone number.** "
               "Articles written before the agent often say this. If employees using the agent shouldn't be sent to HR "
               "this way, add a rule with the instruction draft: offer the agent's help or a case first, and give HR "
               "contact details only when asked.", ""]

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
            what += "<br>⚠️ " + _clip(x["secondary"], 240)
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


_STAGE_NAME = {"Identity": "Role and tone", "1 Planner": "Routing", "2 Search": "Search and grounding",
               "3 Synthesis": "Answers and hand-offs", "4 Render": "Format and style"}


def _grouped(items) -> list[str]:
    """Bullets under bold group names, in first-seen order: items are (group, text)."""
    groups: dict[str, list[str]] = {}
    for g, text in items:
        groups.setdefault(g, []).append(text)
    out = []
    for g, texts in groups.items():
        out += [f"**{g}**", ""] + [f"- {_cell(x)}" for x in texts] + [""]
    return out


def cmd_practices(a) -> str:
    from ess_maker.doctor.catalog import NOTE

    md = [f"## Authoring practices ESS Doctor checks ({len(practices.PRACTICES)})", "", f"> {NOTE}", ""]
    md += _grouped((_STAGE_NAME.get(p.stage, p.stage), p.title) for p in practices.PRACTICES)
    return "\n".join(md).rstrip()


def cmd_rules(a) -> str:
    """Everything ESS Doctor checks: instruction rules, agent audit checks and the authoring practices."""
    from ess_maker.doctor.catalog import AUDIT_CHECKS, INSTRUCTION_RULES, NOTE

    md = ["## What ESS Doctor checks", "", f"> {NOTE}", "",
          f"### Instruction rules ({len(INSTRUCTION_RULES)})", "",
          "The instruction check compares the instructions with the ESS template (HR, IT or hub) and flags:", ""]
    md += _grouped(INSTRUCTION_RULES.values())
    md += [f"### Agent audit checks ({len(AUDIT_CHECKS)})", "",
           "The agent audit checks a hub and its child agents together:", ""]
    md += _grouped(AUDIT_CHECKS.values())
    from ess_maker.doctor.topicgen import TOPIC_RULES

    md += [f"### Topic rules ({len(TOPIC_RULES)})", "",
           "The topic check (and every topic that generate workflows writes) follows these:", ""]
    md += _grouped(TOPIC_RULES.values())
    md += [f"### Authoring practices ({len(practices.PRACTICES)})", ""]
    md += _grouped((_STAGE_NAME.get(p.stage, p.stage), p.title) for p in practices.PRACTICES)
    return "\n".join(md).rstrip()


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


_SKILL_OF = {"check": "instruction-check", "fix": "instruction-check", "condense": "instruction-check",
             "draft": "instruction-draft", "xray": "conversation-xray", "sorry-triage": "sorry-triage",
             "eval-triage": "eval-triage", "eval-compare": "eval-triage", "audit": "agent-audit", "plan": "ess-doctor",
             "rules": "ess-doctor", "practices": "ess-doctor", "topic-gen": "generate-workflows",
             "topic-check": "generate-workflows"}


def _identify(name: str, data: bytes) -> tuple[str, str, str]:
    """(what the file is, agent type: da / cea / "", kind key) for one uploaded file."""
    from ess_maker.doctor import appinsights, devmode, transcript

    if zipfile.is_zipfile(io.BytesIO(data)):
        snap = _snapshot(data)
        if snap:
            docs = transcript.load(snap[0])
            if docs and not any(u for d in docs for u in transcript._messages(d)[0]):
                return "Test pane snapshot without a question (agent setup and welcome message only)", "cea", "snapshot"
            return "Copilot Studio test pane snapshot (conversation + agent setup)", "cea", "snapshot"
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
        if any(re.search(r"(?i)(^|/)Agents/[^/]+/agent\.ya?ml$", n) for n in names):
            return "Copilot Studio ALM agent export", "da", "agent"
        if any(re.search(r"(?i)declarativeagent|manifest\.json$", n) for n in names):
            return "Declarative agent package", "da", "instructions"
        if any(n.lower().endswith(".zip") or re.search(r"(?i)botcontent|\.ya?ml$", n) for n in names):
            return "Zip of agent exports or snapshots", "", "agent"
        return "Zip of conversation evidence", "", "evidence"
    text = _text(data).lstrip("\ufeff")
    head = text[:3000]
    low = head.lower()
    first = head.splitlines()[0] if head else ""
    if re.match(r"\s*kind: AdaptiveDialog\b", text) or (re.search(r"(?m)^beginDialog:", head) and "kind: BotDefinition" not in head):
        return "Copilot Studio topic (code editor YAML)", "", "topic"
    if text.lstrip().startswith(("{", "[")):
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and obj.get("backend") and obj.get("purpose"):
            return "Topic generator spec", "", "topicspec"
        if isinstance(obj, dict) and "requests" in obj and "messages" in obj:
            return "Conversation export from Microsoft support", "da", "devui"
        if transcript.load(text):
            return "Copilot Studio conversation transcript", "cea", "transcript"
    if transcript.load_sessions(text):
        return "Copilot Studio Monitor > Sessions download", "cea", "sessions"
    if appinsights.is_export(text):
        return "Application Insights export", "cea", "appinsights"
    if re.search(r'(^|,|")\s*AuditData\s*("|,|$)', first, re.I):
        return "Purview audit export", "da", "purview"
    is_csv = first.count(",") >= 1 and not first.lstrip().startswith(("#", "{"))
    if is_csv and re.search(r"(?i)\bquestion\b", first):
        rows = evaltriage.parse_results(text)
        if any(r.get("result") for r in rows):
            return "Copilot Studio evaluation results", "", "evalresults"
        return "Evaluation test set (questions only, no results)", "", "evalset"
    if transcript.load(text):
        return "Copilot Studio conversation transcript", "cea", "transcript"
    if re.search(r"(?m)^kind:\s*BotDefinition|^-?\s*kind:\s*GptComponent", text) or "knowledgesourcecomponent" in low:
        return "Agent YAML (botContent.yml / agent.yml)", "cea" if "botcontent" in name.lower() else "", "agent"
    if is_csv:
        return "A CSV ESS Doctor doesn't recognise", "", "empty"
    if devmode.looks_like_devmode(text):
        return "Developer mode card", "da", "devmode"
    if text.strip():
        return "Agent instructions", "", "instructions"
    return "Empty file", "", "empty"


_PLAN = {
    "sessions": ("transcript-xray", 'xray "{f}"', "Verdict per session; then sorry triage with the failed sessions' transcripts"),
    "transcript": ("conversation-xray", 'xray "{f}" --question "<words from the question>"', "Step-by-step X-ray"),
    "snapshot": ("conversation-xray", 'xray "{f}"', "Step-by-step X-ray with the agent's instructions and topics"),
    "appinsights": ("conversation-logs", 'xray "{f}" --conversation <id>', "X-ray of one conversation from telemetry"),
    "devmode": ("conversation-xray", 'xray "{f}" --question "<question>" --response "<answer>"', "Step-by-step X-ray"),
    "devui": ("conversation-xray", 'xray "{f}"', "Full X-ray with the instructions that ran"),
    "purview": ("conversation-xray", 'xray "{f}" --agent "<agent name>"', "Which agent version ran when"),
    "evalresults": ("eval-triage", 'eval-triage "{f}" --instructions <instructions>', "Why each question failed, who fixes it"),
    "evalset": ("eval-triage", "", "Import it in Copilot Studio > Evaluation, run it, and upload the results CSV"),
    "agent": ("agent-audit", 'audit "{f}" [other agents]', "Gaps across hub and child agents"),
    "instructions": ("instruction-check", 'check "{f}"', "Findings, practices and proposed changes"),
    "topic": ("generate-workflows", 'topic-check "{f}"', "Checks the topic against the ESS topic rules"),
    "topicspec": ("generate-workflows", 'topic-gen "{f}"', "Generates the topic, template configuration, skill and tests"),
}


def cmd_plan(a) -> str:
    """Which ESS Doctor skill and command handles each uploaded file, and in what order to run them."""
    rows = []
    for path in a.files:
        name = Path(path).name
        try:
            what, kind_agent, key = _identify(name, _read(path))
        except InputError as e:
            what, kind_agent, key = f"Can't read: {e}", "", "empty"
        rows.append((path, name, what, kind_agent, key))
    ask = (a.ask or "").lower()
    types = {t for *_, t, _ in rows if t}
    agent_type = types.pop() if len(types) == 1 else ""
    mixed = len({t for *_, t, _ in rows if t}) > 1
    md = ["## ESS Doctor plan", "",
          "| File | What it is | Agent type | Skill | Command |", "| --- | --- | --- | --- | --- |"]
    steps: list[str] = []
    ins_src = next((p for p, _, _, _, k in rows if k in ("instructions", "snapshot")), "")
    for path, name, what, t, key in rows:
        skill, cmd, why = _PLAN.get(key, ("", "", ""))
        if ins_src and ins_src != path and key in ("sessions", "transcript", "devmode") and cmd.startswith("xray"):
            # The X-ray places proposed rules on exact lines when it has the instructions.
            cmd += f' --instructions "{ins_src}"'
        if key == "snapshot" and "without a question" in what:
            skill, cmd, why = "instruction-check", f'check "{path}"', \
                "No question in it, so it's used for the agent's instructions and setup, not an X-ray"
        if key == "instructions" and re.search(r"\b(add|change|draft|write|update|new rule|tune|don'?t|never)\b", ask) \
                and not re.search(r"\b(check|review|fix|validate)\b", ask):
            skill, cmd, why = "instruction-draft", f'draft "{path}" --type <change type> --intent "<the change>"', \
                "Draft the change into the right section, with a diff and a test set"
        elif key == "instructions" and re.search(r"\bfix\b", ask):
            mode = ("all" if re.search(r"\b(all|every|everything)\b", ask) else
                    ",".join(re.findall(r"\b\d+\b", ask)) or "recommended")
            skill, cmd, why = "instruction-check", f'fix "{path}" --rules {mode}', "Apply the fixes, fitted under the limit"
        elif key == "instructions" and re.search(r"\b(shorten|condense|too long|limit)\b", ask):
            skill, cmd, why = "instruction-check", f'condense "{path}"', "Shorten to the 10% buffer target"
        if cmd and agent_type == "cea" and key in ("instructions", "snapshot") and cmd.startswith(("check", "fix")):
            cmd += " --agent-type cea"
        rows_cmd = f"`python3 scripts/essdoctor.py {cmd.format(f=path)}`" if cmd else "—"
        md.append(f"| {_cell(name)} | {_cell(what)} | {t.upper() or '?'} | {skill or '—'} | {rows_cmd} |")
        if skill:
            steps.append(f"**{skill}**: {why} ({_cell(name)}).")
    keys = {k for *_, k in rows}
    if keys & {"sessions", "transcript", "appinsights", "devmode", "devui", "snapshot"} and len(rows) > 1:
        steps.append("**sorry-triage**: run across all the conversation files together when more than one answer was a sorry.")
    if "evalresults" in keys and sum(1 for *_, k in rows if k == "evalresults") == 2:
        steps.append("**eval-triage**: two results files are a before/after pair: run `eval-compare` on them.")
    chosen = {s.split("**")[1] for s in steps}
    if keys & {"snapshot", "instructions", "agent"} and not re.search(r"\bfix\b", ask) and "instruction-draft" not in chosen:
        steps.append("**instruction-check**: after the diagnosis, run `check` on the instructions; on \"fix\", run `fix` "
                     "(it writes the evaluation test set). Show the diffs as returned.")
    if not any(k in _PLAN for k in keys):
        steps.append("Nothing ESS Doctor can read yet. Ask the maker which agent type it is (declarative or custom engine) "
                     "and collect the evidence listed in the `ess-doctor` skill.")
    md += ["", "### Run in this order", ""] + [f"{i}. {s}" for i, s in enumerate(dict.fromkeys(steps), 1)]
    if agent_type:
        md += ["", f"Agent type: **{'custom engine agent' if agent_type == 'cea' else 'declarative agent'}** "
                   f"(from the files). Pass `--agent-type {agent_type}` to `check` and `fix`."]
    elif mixed:
        md += ["", "⚠️ These files come from both a declarative agent and a custom engine agent. Ask the maker which agent "
                   "they're troubleshooting before running anything, and pass `--agent-type` to `check` and `fix`."]
    else:
        md += ["", "Agent type: not clear from the files. Ask the maker whether it's a declarative agent or a custom "
                   "engine agent, and pass `--agent-type da` or `--agent-type cea` to `check` and `fix`."]
    return "\n".join(md)


def _version() -> str:
    """Plugin version stamped into the built engine (VERSION in the .pyz), else the source manifest."""
    import pkgutil

    try:
        data = pkgutil.get_data("__main__", "VERSION")
        if data:
            return data.decode("utf-8").strip()
    except (OSError, ValueError, AttributeError):
        pass
    try:
        m = Path(__file__).resolve().parent / "plugin" / "manifest.json"
        return json.loads(m.read_text(encoding="utf-8-sig"))["version"]
    except (OSError, ValueError, KeyError):
        return "dev"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="essdoctor", description="ESS Doctor: check, fix and troubleshoot Employee Self-Service agents.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    persona = {"choices": ["auto", "hr", "it", "hub", "none"], "default": "auto", "help": "ESS template to compare with"}
    agent_type = {"choices": ["auto", "da", "cea"], "default": "auto",
                  "help": "Declarative agent or custom engine agent (auto: a test pane snapshot or botContent.yml is a CEA)"}

    p = sub.add_parser("check", help="Check instructions against the ESS template, known issues and practices")
    p.add_argument("instructions")
    p.add_argument("--previous", help="Previous version, to show what the change introduced")
    p.add_argument("--persona", **persona)
    p.add_argument("--agent-type", **agent_type)
    p.add_argument("--json", help="Also write the full result as JSON to this path")
    p.add_argument("--evalset", help="Where to write the Copilot Studio evaluation test set CSV")
    p.add_argument("--no-evalset", action="store_true", help="Don't write the evaluation test set")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("fix", help="Apply the safe fixes and write the fixed instructions")
    p.add_argument("instructions")
    p.add_argument("--rules", help="Finding numbers from the check report (for example 1,2), 'recommended' (findings plus "
                                   "the recommended additions), 'all' (also the optional practices), or rule IDs "
                                   "(default: all findings, no practices)")
    p.add_argument("--no-condense", action="store_true", help="Don't shorten automatically when the fixes go over the limit")
    p.add_argument("--draft", help="Also add a drafted rule (intake JSON, for example the one an X-ray saved) to the fixed text")
    p.add_argument("--append", help="Also add a section the agent audit saved (for example the hub's delegation rules)")
    p.add_argument("--persona", **persona)
    p.add_argument("--agent-type", **agent_type)
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

    p = sub.add_parser("plan", help="Identify each uploaded file and say which ESS Doctor skill and command handles it")
    p.add_argument("files", nargs="+")
    p.add_argument("--ask", default="", help="What the maker asked, to pick between check, fix, shorten and draft")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("topic-gen", help="Generate a topic, its template configuration, a matching skill and tests from a spec")
    p.add_argument("spec", help="Scenario spec (JSON): name, backend, purpose, triggers, not_for, inputs, outputs, options")
    p.add_argument("--agent-type", **agent_type)
    p.add_argument("--out", default="outputs/topic")
    p.set_defaults(fn=cmd_topic_gen)

    p = sub.add_parser("topic-check", help="Check a topic or every topic in an agent export against the ESS topic rules")
    p.add_argument("topic", help="Topic YAML (code editor), test pane snapshot or bot content export")
    p.add_argument("--agent-type", **agent_type)
    p.set_defaults(fn=cmd_topic_check)

    p = sub.add_parser("rules", help="List everything ESS Doctor checks: instruction rules, audit checks, practices")
    p.set_defaults(fn=cmd_rules)

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
        out = a.fn(a)
        print(out)
        if a.cmd in _SKILL_OF and out.lstrip().startswith(("#", "```")):
            # Hidden when rendered; the skills collect these into one "Produced by" section at the end of the reply.
            print(f"\n<!-- essdoctor: version={_version()} skill={_SKILL_OF[a.cmd]} command={a.cmd} -->")
        return 0
    except InputError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
