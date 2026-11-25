"""Multi-agent audit: a hub (parent) agent and its connected (child) agents, checked together.

Reads Copilot Studio agent exports without a YAML library: the test pane snapshot's ``botContent.yml`` (or a bot content
export, ``kind: BotDefinition``) and component-only exports such as a single ``GptComponent``. From each it takes the
agent settings, instructions, knowledge sources, topics (with their knowledge search nodes) and connected-agent task
dialogs, then checks the gaps that make multi-agent ESS deployments slow or inaccurate:

- the hub searching knowledge before it routes, or holding the same knowledge as a child;
- duplicate or overlapping knowledge within an agent (the same connector twice, a site root plus pages under it);
- more than 25 knowledge sources, where generative orchestration filters sources by their descriptions;
- connected agents with generic or overlapping descriptions, full conversation history passed to every child,
  children the hub can't call, and settings that differ from the ESS templates.

Sources: Copilot Studio knowledge source limits and descriptions (knowledge-copilot-studio), connected agents
(add-agent-copilot-studio-agent, "Pass conversation history to this agent"), and multi-agent orchestration best
practices (guidance/multi-agent-patterns).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KNOWLEDGE_FILTER_LIMIT = 25
TEMPLATE_MODERATION = {"hub": "High", "hr": "Medium", "it": "Medium"}
_LEVEL = {"low": 1, "medium": 2, "high": 3}
_GENERIC_KS = re.compile(r"^this knowledge source (provides|contains) information (found )?(in|from)\b", re.I)
_MARKETING = re.compile(r"get personalized|simply ask|trusted colleague|skip the .* queue|taps into official", re.I)


@dataclass
class Knowledge:
    name: str
    schema: str
    kind: str
    target: str  # normalized site URL, or connector identity
    description: str
    active: bool
    auto_search: bool  # triggerCondition not false


@dataclass
class Connected:
    name: str
    schema: str
    bot_schema: str
    description: str
    history: str  # ConversationHistory, or "" when not passed / unknown
    active: bool


@dataclass
class Topic:
    name: str
    schema: str
    trigger: str
    active: bool
    searches: int
    scoped_sources: list[str] = field(default_factory=list)


@dataclass
class Agent:
    file: str
    name: str = ""
    schema: str = ""
    description: str = ""
    instructions: str = ""
    connectable: bool | None = None
    settings: dict = field(default_factory=dict)
    model: str = ""
    web_browsing: bool = False
    knowledge: list[Knowledge] = field(default_factory=list)
    connected: list[Connected] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)
    instruction_refs: list[str] = field(default_factory=list)  # connected agents named in the instructions
    full_export: bool = False  # has the bot definition and components, not just the instructions component

    @property
    def is_hub(self) -> bool:
        return bool(self.connected or self.instruction_refs)

    @property
    def persona(self) -> str:
        key = (self.schema + " " + self.name).lower()
        if self.is_hub or re.search(r"selfservicecore|\bhub\b|frontier", key):
            return "hub"
        if re.search(r"servicehr|\bhr\b", key):
            return "hr"
        if re.search(r"serviceit|\bit\b", key):
            return "it"
        return ""

    def keys(self) -> set[str]:
        out = {_norm(self.name), _norm(self.schema), _norm(self.schema.split(".gpt")[0])}
        out |= {k.replace("employeeselfservice", "ess") for k in out}
        return {k for k in out if k}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _url(s: str) -> str:
    return re.sub(r"[?#].*$", "", (s or "").strip().strip("'\"")).rstrip("/").lower()


def _scalar(text: str, key: str, indent: int | None = None) -> str:
    """Value of ``key:`` (first occurrence, optionally at an exact indent), including | and > block scalars."""
    pad = r" {%d}" % indent if indent is not None else r"\s*"
    m = re.search(rf"(?m)^({pad}){re.escape(key)}:[ \t]*(.*)$", text)
    if not m:
        return ""
    value = m.group(2).strip()
    if not value.startswith(("|", ">")):
        return value.strip("'\"")
    base = len(m.group(1))
    lines = []
    for ln in text[m.end() + 1:].split("\n"):
        if ln.strip() and len(ln) - len(ln.lstrip(" ")) <= base:
            break
        lines.append(ln)
    while lines and not lines[-1].strip():
        lines.pop()
    pad_n = min((len(ln) - len(ln.lstrip(" ")) for ln in lines if ln.strip()), default=0)
    return "\n".join(ln[pad_n:] for ln in lines)


def _components(text: str) -> list[tuple[str, str]]:
    heads = [m for m in re.finditer(r"(?m)^( *)- kind: (\w+Component)\s*$", text)]
    if not heads:
        return []
    indent = min(len(m.group(1)) for m in heads)
    heads = [m for m in heads if len(m.group(1)) == indent]
    return [(m.group(2), text[m.start():heads[i + 1].start() if i + 1 < len(heads) else len(text)])
            for i, m in enumerate(heads)]


def _field_indent(comp: str) -> int:
    for ln in comp.split("\n")[1:]:
        if ln.strip():
            return len(ln) - len(ln.lstrip(" "))
    return 4


def _flag(text: str, key: str) -> bool | None:
    m = re.search(rf"\b{re.escape(key)}:\s*(true|false)\b", text, re.I)
    return None if not m else m.group(1).lower() == "true"


def parse(text: str, file: str = "") -> Agent:
    text = (text or "").lstrip("\ufeff").replace("\r\n", "\n")
    a = Agent(file=file)
    first = re.search(r"(?m)^ *- kind: \w+Component\s*$", text)
    head = text[:first.start()] if first else text
    if re.search(r"(?m)^kind: BotDefinition", head):
        a.full_export = True
        a.schema = _scalar(head, "schemaName", 2)
        ent_name = _scalar(head, "displayName", 2)
        if ent_name and "${" not in ent_name:
            a.name = ent_name
        a.connectable = _flag(head, "isAgentConnectable")
        for k in ("useModelKnowledge", "isFileAnalysisEnabled", "isSemanticSearchEnabled", "optInUseLatestModels",
                  "GenerativeActionsEnabled"):
            v = _flag(head, k)
            if v is not None:
                a.settings[k] = v
        mod = re.search(r"contentModeration:\s*(\w+)", head)
        if mod:
            a.settings["contentModeration"] = mod.group(1)
    for kind, comp in _components(text):
        fi = _field_indent(comp)
        name, schema = _scalar(comp, "displayName", fi), _scalar(comp, "schemaName", fi)
        state = (_scalar(comp, "state", fi) or "Active").lower() == "active"
        if kind == "GptComponent":
            a.instructions = _scalar(comp, "instructions") or a.instructions
            a.description = _scalar(comp, "description", fi) or a.description
            a.model = (re.findall(r"modelNameHint:\s*(\w+)", comp) or [a.model])[-1]
            a.web_browsing = bool(re.search(r"webBrowsing:\s*true", comp, re.I))
            if not a.name and name and "${" not in name:
                a.name = name
            if not a.schema and schema:
                a.schema = schema.split(".gpt")[0]
            a.instruction_refs = sorted(set(re.findall(r"InvokeConnectedAgentTaskAction\.([\w\-]+)", a.instructions)))
        elif kind == "KnowledgeSourceComponent":
            skind = (re.findall(r"kind:\s*(\w+Source)\b", comp) or [""])[0]
            site = _scalar(comp, "site")
            conn = _scalar(comp, "connectionName") or _scalar(comp, "contentSourceDisplayName")
            conn_var = re.search(r"connectionId:\s*\n\s+schemaName:\s*(\S+)", comp)
            target = _url(site) if site else "connector:" + _norm(conn or (conn_var.group(1) if conn_var else name))
            a.knowledge.append(Knowledge(name, schema, skind, target, _scalar(comp, "description", fi), state,
                                         _flag(comp, "triggerCondition") is not False))
        elif kind == "DialogComponent":
            if re.search(r"(?m)^\s+kind: TaskDialog\s*$", comp) and "InvokeConnectedAgentTaskAction" in comp:
                hist = re.search(r"historyType:\s*(?:\{\s*kind:\s*(\w+)|\n\s+kind:\s*(\w+))", comp)
                a.connected.append(Connected(name, schema, _scalar(comp, "botSchemaName"),
                                             _scalar(comp, "modelDescription") or _scalar(comp, "description", fi),
                                             (hist.group(1) or hist.group(2)) if hist else "", state))
                continue
            trig = re.search(r"beginDialog:\s*\n\s+kind:\s*(\w+)", comp)
            scoped = []
            for blk in re.finditer(r"kind: SearchSpecificKnowledgeSources\s*\n\s+knowledgeSources:\s*\n((?:\s+- \S+\n?)+)", comp):
                scoped += re.findall(r"- (\S+)", blk.group(1))
            a.topics.append(Topic(name, schema, trig.group(1) if trig else "", state,
                                  comp.count("kind: SearchAndSummarizeContent"), sorted(set(scoped))))
    if not a.name:
        a.name = a.schema or file
    return a


# --- Checks ---------------------------------------------------------------------------------------------------


@dataclass
class Gap:
    rule_id: str
    severity: str  # high, medium, low, info
    agents: list[str]
    title: str
    detail: str
    fix: str
    owner: str = "Agent maker"
    # Concrete change as diff lines ("- old", "+ new") for settings, knowledge sources or instruction text.
    change: list[str] = field(default_factory=list)


def _child_for(hub: Agent, target: Connected | str, agents: list[Agent]) -> Agent | None:
    want = {_norm(target.bot_schema), _norm(target.name)} if isinstance(target, Connected) else {_norm(target)}
    want |= {w.replace("employeeselfservice", "ess") for w in want}
    want.discard("")
    return next((x for x in agents if x is not hub and x.keys() & want), None)


def _setting(name: str, old: str, new: str) -> list[str]:
    return [f"- {name}: {old}", f"+ {name}: {new}"]


def _removals(items, limit: int = 12) -> list[str]:
    items = list(items)
    return [f"- {x}" for x in items[:limit]] + ([f"@@ {len(items) - limit} more @@"] if len(items) > limit else [])


def _jaccard(a: str, b: str) -> float:
    wa, wb = set(re.findall(r"[a-z]{4,}", a.lower())), set(re.findall(r"[a-z]{4,}", b.lower()))
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def audit(agents: list[Agent]) -> dict:
    gaps: list[Gap] = []
    hubs = [a for a in agents if a.is_hub] or [a for a in agents if a.persona == "hub"]
    hub = max(hubs, key=lambda a: len(a.connected) + len(a.instruction_refs)) if hubs else None
    children: list[tuple[str, Agent | None, Connected | None]] = []
    if hub:
        for c in hub.connected:
            children.append((c.name or c.bot_schema, _child_for(hub, c, agents), c))
        for ref in hub.instruction_refs:
            if not any(_norm(ref) in {_norm(c.name), _norm(c.schema.split(".")[-1])} for c in hub.connected):
                children.append((ref, _child_for(hub, ref, agents), None))
        if not children:
            # A template hub has no connected agents yet: treat the other uploaded agents as its children.
            children = [(a.name, a, None) for a in agents if a is not hub]
    kids = [k for _, k, _ in children if k]
    others = [a for a in agents if a is not hub and a not in kids]

    # Hub searches knowledge before routing.
    if hub and (hub.knowledge or any(t.searches for t in hub.topics if t.active)):
        n_topics = sum(1 for t in hub.topics if t.active and t.searches)
        gaps.append(Gap("A01-HUB-KNOWLEDGE", "high", [hub.name],
                        "The hub searches knowledge itself",
                        f"{len(hub.knowledge)} knowledge source(s) and {n_topics} topic(s) with knowledge search on the hub. "
                        "Generative orchestration can search them on every turn before it routes, adding a full search "
                        "round to each request, and the answer can come from the hub instead of the child.",
                        "Keep the hub a router: move the knowledge into the child agent that owns the domain, and route "
                        "by intent with the connected agents' descriptions.",
                        change=_removals(f"{hub.name} knowledge source: {k.name}" for k in hub.knowledge)))

    # Same knowledge in the hub and a child, or in two children.
    owners: dict[str, list[str]] = {}
    for a in ([hub] if hub else []) + kids:
        for k in a.knowledge:
            owners.setdefault(k.target, [])
            if a.name not in owners[k.target]:
                owners[k.target].append(a.name)
    for target, names in owners.items():
        if len(names) < 2:
            continue
        label = target.replace("connector:", "connector ")
        if hub and hub.name in names:
            gaps.append(Gap("A02-DUP-KNOWLEDGE-HUB-CHILD", "high", names, "The hub and a child search the same knowledge",
                            f"{label} is a knowledge source of {' and '.join(names)}. Both search it, so the model reads "
                            "the same content twice and the request waits for both.",
                            "Remove it from the hub and keep it only in the child that owns the domain.",
                            change=_removals(f"{hub.name} knowledge source: {k.name}" for k in hub.knowledge if k.target == target)))
        else:
            gaps.append(Gap("A03-DUP-KNOWLEDGE-CHILDREN", "medium", names, "Two child agents search the same knowledge",
                            f"{label} is a knowledge source of {' and '.join(names)}. Overlapping knowledge makes the hub's "
                            "routing ambiguous and returns duplicate content.",
                            "Give each child its own, non-overlapping knowledge, and say in each child's description which "
                            "subjects it owns."))

    for a in agents:
        # Duplicate source within one agent.
        seen: dict[str, list[str]] = {}
        for k in a.knowledge:
            seen.setdefault(k.target, []).append(k.name)
        for target, names in seen.items():
            if len(names) > 1:
                kind = "connector" if target.startswith("connector:") else "site"
                gaps.append(Gap("A04-DUP-KNOWLEDGE-IN-AGENT", "high", [a.name],
                                f"The same {kind} is added twice",
                                f"{', '.join(names)} point to the same {kind} ({target.replace('connector:', '')}). Each is "
                                "searched separately, so the same results come back twice and double the content the model "
                                "reads.", "Keep one knowledge source per connector or site and delete the copies.",
                                change=_removals(f"{a.name} knowledge source: {n}" for n in names[1:])))
        # Site root plus pages under it.
        sites = [k for k in a.knowledge if k.target.startswith("http")]
        roots = {k.target for k in sites if re.fullmatch(r"https://[^/]+/sites/[^/]+", k.target)}
        for root in sorted(roots):
            under = [k.name for k in sites if k.target != root and k.target.startswith(root + "/")]
            if under:
                gaps.append(Gap("A05-OVERLAPPING-KNOWLEDGE", "medium", [a.name], "A whole site and pages inside it are both added",
                                f"{root} is a knowledge source, and so are {len(under)} page(s) inside it "
                                f"({', '.join(under[:3])}{'…' if len(under) > 3 else ''}). The pages are searched twice.",
                                "Keep the site and delete the page-level sources, or keep the pages and remove the site.",
                                change=_removals(f"{a.name} knowledge source: {n}" for n in under)))
        # Many sources: filtered by description.
        active = [k for k in a.knowledge if k.active]
        generic = [k for k in active if not k.description.strip() or _GENERIC_KS.match(k.description.strip())]
        if len(active) > KNOWLEDGE_FILTER_LIMIT:
            per_site: dict[str, int] = {}
            for k in sites:
                m = re.match(r"https://[^/]+/sites/[^/]+", k.target)
                if m:
                    per_site[m.group(0)] = per_site.get(m.group(0), 0) + 1
            merge = sum(n - 1 for n in per_site.values() if n > 1)
            gaps.append(Gap("A06-KNOWLEDGE-OVER-25", "high" if generic else "medium", [a.name],
                            f"{len(active)} knowledge sources, more than {KNOWLEDGE_FILTER_LIMIT}",
                            f"With more than {KNOWLEDGE_FILTER_LIMIT} sources, generative orchestration first filters them with "
                            f"a model that reads each source's description; {len(generic)} of {len(active)} have no description "
                            "or a generic one, so the right source can be filtered out, and every request pays for the "
                            "extra selection step." + (f" {merge} page-level source(s) share a site with another source." if merge else ""),
                            "Consolidate to one source per SharePoint site" + (f" (about {len(active) - merge} sources)" if merge else "")
                            + ", write a description for each that names the subjects it covers, and scope sensitive or "
                            "high-volume subjects to a topic that searches only the source that owns them."))
        elif generic and len(active) > 3:
            gaps.append(Gap("A07-GENERIC-KNOWLEDGE-DESCRIPTION", "low", [a.name], "Knowledge sources have generic descriptions",
                            f"{len(generic)} of {len(active)} knowledge sources have no description or the generated one "
                            "(\"This knowledge source provides information found in …\"). Descriptions are the main signal "
                            "for choosing a source.", "Describe the subjects each source covers, in employees' words."))
        off = [k.name for k in a.knowledge if k.active and not k.auto_search]
        if off:
            gaps.append(Gap("A08-KNOWLEDGE-NOT-SEARCHED", "info", [a.name], "Knowledge sources the orchestrator won't search",
                            f"{', '.join(off[:4])} {'is' if len(off) == 1 else 'are'} set not to be searched automatically; "
                            "only topics that name them use them.", "Check this is intended; otherwise turn automatic search "
                            "back on for them."))
        if a.settings.get("useModelKnowledge"):
            gaps.append(Gap("A09-MODEL-KNOWLEDGE", "high", [a.name], "General model knowledge is on",
                            "The agent can answer from the model's general knowledge instead of the company's sources.",
                            "Turn off \"Use general knowledge\" in the agent's generative AI settings.",
                            change=_setting(f"{a.name} > Use general knowledge", "On", "Off")))
        if a.web_browsing:
            gaps.append(Gap("A10-WEB-SEARCH", "high", [a.name], "Web search is on",
                            "Public web results can be mixed into HR or IT answers.", "Turn off web search for ESS agents.",
                            change=_setting(f"{a.name} > Web search", "On", "Off")))

    if hub:
        # Connected agents: missing, not connectable, history, descriptions.
        for label, kid, conn in children:
            if not kid:
                gaps.append(Gap("A11-CHILD-NOT-PROVIDED", "info", [hub.name], f"{label} wasn't uploaded",
                                f"The hub routes to {label}, but its export isn't in this audit, so its knowledge and "
                                "instructions weren't checked.", f"Add a snapshot or export of {label} to audit it too."))
            elif kid.connectable is False:
                gaps.append(Gap("A12-CHILD-NOT-CONNECTABLE", "high", [hub.name, kid.name], f"{kid.name} can't be called by other agents",
                                "Its \"Let other agents connect to and use this one\" setting is off, so the hub's hand-off fails.",
                                "Turn on the setting in the child agent and publish it.",
                                change=_setting(f"{kid.name} > Let other agents connect to and use this one", "Off", "On")))
        with_history = [c for c in hub.connected if c.active and c.history.lower() == "conversationhistory"]
        if with_history:
            gaps.append(Gap("A13-HISTORY-TO-EVERY-CHILD", "medium", [hub.name],
                            f"Full conversation history is passed to {len(with_history)} of {len(hub.connected)} connected agents",
                            "\"Pass conversation history to this agent\" is on for " + ", ".join(c.name for c in with_history[:5])
                            + ". Each child gets the whole conversation, so it reads more and can act on requests the hub "
                            "already handled, which causes duplicate or repeated answers.",
                            "Turn the setting off for children that only need the current task, and have the hub pass the "
                            "employee's question verbatim as the task.",
                            change=[ln for c in with_history
                                    for ln in _setting(f"{hub.name} > {c.name} > Pass conversation history to this agent", "On", "Off")]))
        descs = [(c.name, c.description) for c in hub.connected if c.active and c.description]
        if not descs:
            # No connected-agent definitions in the export: the hub sees each child's own description by default.
            descs = [(k.name, k.description) for k in kids if k.description]
        vague = [n for n, d in descs if _MARKETING.search(d)]
        if vague:
            gaps.append(Gap("A14-CHILD-DESCRIPTION-VAGUE", "medium", [hub.name],
                            "Connected agent descriptions read like marketing text",
                            f"{', '.join(vague)}: the description is the main signal the hub uses to route, and promotional "
                            "wording (\"Get personalized support…\") doesn't say which requests belong to the agent.",
                            "Rewrite each description as scope: the subjects and actions the agent owns, and what it doesn't do."))
        for i in range(len(descs)):
            for j in range(i + 1, len(descs)):
                if _jaccard(descs[i][1], descs[j][1]) >= 0.45:
                    gaps.append(Gap("A15-CHILD-DESCRIPTIONS-OVERLAP", "medium", [hub.name],
                                    f"{descs[i][0]} and {descs[j][0]} have overlapping descriptions",
                                    "The hub can't tell from the descriptions which of the two should handle a request.",
                                    "Make the descriptions distinct: name the subjects each owns and point to the other for the rest."))
        ins = hub.instructions
        missing = []
        if ins and not re.search(r"verbatim|exact (words|question)|as (the user|they) (asked|wrote)", ins, re.I):
            missing.append("pass the employee's question verbatim as the child's task (don't add the name, email or extra instructions)")
        if ins and not re.search(r"only once|do not repeat|don'?t repeat|single response|only agent that", ins, re.I):
            missing.append("deliver the child's answer only once and don't add a summary of it")
        if ins and not re.search(r"do not answer|don'?t answer|child agents own|let the child|not answer (them|domain)", ins, re.I):
            missing.append("not answer domain questions itself; the child agents own the answers")
        if missing:
            gaps.append(Gap("A16-HUB-DELEGATION-RULES", "medium", [hub.name], "Hub instructions miss delegation rules",
                            "The hub instructions don't say to " + "; or to ".join(missing) + ".",
                            "Add a short delegation section: " + " ".join(f"- {m[:1].upper() + m[1:]}." for m in missing),
                            change=["+ # Delegation"] + [f"+ - {m[:1].upper() + m[1:]}." for m in missing]))

    # Settings drift from the ESS templates and between hub and children.
    for a in agents:
        lvl = a.settings.get("contentModeration")
        want = TEMPLATE_MODERATION.get(a.persona)
        if lvl and want and _LEVEL.get(lvl.lower(), 0) < _LEVEL[want.lower()]:
            gaps.append(Gap("A17-MODERATION-BELOW-TEMPLATE", "medium", [a.name], f"Content moderation is {lvl}",
                            f"The ESS {a.persona.upper()} template uses {want}. A lower level lets more sensitive content "
                            "through; a mix of levels across agents makes safety behavior depend on which agent answers.",
                            f"Set content moderation back to {want}, unless a reviewed exception applies.",
                            change=_setting(f"{a.name} > Content moderation", lvl, want)))
    if hub and kids:
        opt = {a.name: a.settings.get("optInUseLatestModels") for a in [hub] + kids if "optInUseLatestModels" in a.settings}
        if len(set(opt.values())) > 1:
            gaps.append(Gap("A18-MODEL-SETTINGS-DIFFER", "low", list(opt), "Model settings differ between the hub and children",
                            "\"Use latest models\" is " + ", ".join(f"{'on' if v else 'off'} for {k}" for k, v in opt.items())
                            + ". Answers and latency can change between agents for the same question.",
                            "Use the same model setting across the hub and its children unless one needs a different model."))

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    gaps.sort(key=lambda g: (order.get(g.severity, 9), g.rule_id))
    return {"hub": hub, "children": children, "others": others, "gaps": gaps}
