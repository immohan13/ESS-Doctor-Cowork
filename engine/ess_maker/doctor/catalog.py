"""Plain-language catalog of what ESS Doctor checks, for the `rules` command. Every instruction rule in rules.py and
every audit check in audit.py has an entry; a test fails when one is added without it."""

from __future__ import annotations

NOTE = ("ESS Doctor's rules are field guidance from the ESS templates and reviews of ESS agents, not Copilot Studio "
        "product documentation. A clean check doesn't replace the evaluation test set: run it before and after every "
        "change.")

# Instruction rules: id -> (area, what it catches). Areas follow the order the agent works: limits, references, the
# ESS core blocks, conflicts, routing, search, tool calls, safety, then style.
INSTRUCTION_RULES: dict[str, tuple[str, str]] = {
    "L0-LENGTH": ("Length and limits", "Instructions over the 8,000-character limit (Copilot Studio cuts off the end)"),
    "L0-HEADROOM": ("Length and limits", "Note: instructions above the recommended 7,200 characters (guidance, not a finding)"),
    "L0-SELF-REPORTING": ("Length and limits", "The agent is asked to report or show its own steps or reasoning"),
    "L1-REFS-UNVERIFIED": ("References", "Topic references that can't be checked from an instructions-only file"),
    "L1-TOPIC-MISSING": ("References", "A referenced topic isn't in the agent"),
    "L1-TOPIC-INACTIVE": ("References", "A referenced topic is turned off"),
    "L1-GLOBAL-UNDEFINED": ("References", "A variable is used but never set"),
    "L1-STRAY-TOKEN": ("References", "A stray character after a variable (it ends up in searches)"),
    "L1-HIDDEN-INSTRUCTION": ("References", "Instruction text pulled in through a variable, so it isn't visible"),
    "L1-TOPIC-OVERLAP": ("References", "Topics with near-identical descriptions"),
    "L1-TOPIC-DESC-VAGUE": ("References", "Topic descriptions too short to choose the right topic"),
    "L2-ROUTING-MISSING": ("ESS core blocks (declarative agents)", "The routing decision tree is missing"),
    "L2-GROUNDING-MISSING": ("ESS core blocks (declarative agents)", "Tool-first grounding is missing"),
    "L2-RETRY-MISSING": ("ESS core blocks (declarative agents)", "The knowledge retry budget is missing"),
    "L3-ABSOLUTE-OVERRIDE": ("Conflicts", "'Override everything' wording makes prohibitions beat routing and grounding"),
    "L3-JURISDICTION-REFUSAL": ("Conflicts", "A region rule refuses answers when the source is a global policy"),
    "L3-SELF-CONTRADICTION": ("Conflicts", "A rule and its own examples say different things"),
    "L3-DANGLING-HEADER": ("Conflicts", "A standalone heading-like rule that reads two ways"),
    "L3-OVERHARDENED-FIGURES": ("Conflicts", "A grounding rule also blocks figures the sources state"),
    "L3-MULTI-FALLBACK": ("Conflicts", "Several different 'not found' messages"),
    "L4-NO-ACTION-OFFER": ("Routing", "A blanket 'never offer' rule blocks offering an action after a how-to answer"),
    "L4-BLANKET-SUBJECT-BLOCK": ("Routing", "A blocked word also blocks questions beyond the intended subject"),
    "L4-FALLBACK-ESCALATE": ("Routing", "'When unsure, open a ticket' escalates before searching knowledge"),
    "L4-GENERIC-CLARIFY-OPTIONS": ("Routing", "Clarifying questions offer made-up options instead of what search found"),
    "L5-OVER-AUGMENTED-QUERY": ("Search", "Job level or title added to every search"),
    "L5-QUERY-REWRITE": ("Search", "Search queries rewritten, translated or padded with device, OS or profile details"),
    "L5-CONTEXT-IN-SEARCH": ("Search", "Employee context used to build searches instead of tailoring the answer"),
    "L5-SINGLE-SOURCE": ("Search", "All questions forced to one knowledge source"),
    "L5-BROAD-KNOWLEDGE": ("Search", "Many knowledge sources searched together"),
    "L5-WEB-SEARCH-ON": ("Search", "Web search on for an agent that must use only approved sources"),
    "L6-HARD-ROUTE-EXCLUDES-TOOLS": ("Tool calls", "A hard route blocks tools that follow-up questions need"),
    "L6-EXHAUSTIVE-SEARCH": ("Tool calls", "Several searches required for every question"),
    "L6-MANDATED-TOOL-CHAIN": ("Tool calls", "Several tools required before every answer"),
    "L6-PROSE-TOOL-ORDER": ("Tool calls", "A tool order written in prose, which the model doesn't always follow"),
    "L6-UNSAFE-DEFAULT": ("Tool calls", "A missing profile value silently defaults (for example to 'not a manager')"),
    "L7-PREEMPTIVE-ROUTE": ("Safety", "A sensitive-topic route with a very broad trigger runs before all routing"),
    "L7-SENSITIVE-SUPPRESSION": ("Safety", "A rule changes how sensitive disclosures are handled"),
    "L7-MODERATION-DRIFT": ("Safety", "Content moderation differs from the ESS template"),
    "L8-LITERAL-EXAMPLE": ("Style", "An example the model is likely to copy word for word"),
    "L8-PHRASE-BLACKLIST": ("Style", "A list of banned phrases instead of a behavior rule"),
    "L8-WORD-BAN": ("Style", "Banned words the agent has to paraphrase out of policy text"),
    "L8-CITATION-STRIP": ("Style", "Links or citations removed from answers"),
    "L8-LENGTH-CAP": ("Style", "A hard length cap on every answer"),
    "L8-RENDER-FORMAT": ("Style", "A forced format that may not render in Teams or Microsoft 365 Copilot"),
}

# Agent audit checks: id -> (area, what it catches).
AUDIT_CHECKS: dict[str, tuple[str, str]] = {
    "A01-HUB-KNOWLEDGE": ("Knowledge", "The hub searches knowledge itself before routing"),
    "A02-DUP-KNOWLEDGE-HUB-CHILD": ("Knowledge", "The hub and a child search the same knowledge"),
    "A03-DUP-KNOWLEDGE-CHILDREN": ("Knowledge", "Two child agents search the same knowledge"),
    "A04-DUP-KNOWLEDGE-IN-AGENT": ("Knowledge", "The same site or connector added twice in one agent"),
    "A05-OVERLAPPING-KNOWLEDGE": ("Knowledge", "A whole site and pages inside it both added"),
    "A06-KNOWLEDGE-OVER-25": ("Knowledge", "More than 25 knowledge sources (filtered by description)"),
    "A07-GENERIC-KNOWLEDGE-DESCRIPTION": ("Knowledge", "Knowledge sources with generic descriptions"),
    "A08-KNOWLEDGE-NOT-SEARCHED": ("Knowledge", "Knowledge sources set not to be searched automatically"),
    "A09-MODEL-KNOWLEDGE": ("Settings", "General model knowledge on"),
    "A10-WEB-SEARCH": ("Settings", "Web search on"),
    "A11-CHILD-NOT-PROVIDED": ("Hand-offs", "A connected agent wasn't uploaded, so it wasn't audited"),
    "A12-CHILD-NOT-CONNECTABLE": ("Hand-offs", "A child agent the hub can't call"),
    "A13-HISTORY-TO-EVERY-CHILD": ("Hand-offs", "Conversation history passed to every child (optional trade-off)"),
    "A14-CHILD-DESCRIPTION-VAGUE": ("Hand-offs", "Connected agent descriptions that read like marketing text"),
    "A15-CHILD-DESCRIPTIONS-OVERLAP": ("Hand-offs", "Two connected agents with overlapping descriptions"),
    "A16-HUB-DELEGATION-RULES": ("Hand-offs", "Hub instructions without delegation rules"),
    "A17-MODERATION-BELOW-TEMPLATE": ("Settings", "Content moderation Low or different across agents (optional)"),
    "A18-MODEL-SETTINGS-DIFFER": ("Settings", "Model settings that differ between the hub and children"),
    "A19-WORKDAY-LEGACY-USER-CONTEXT": ("Speed", "Workday user context on the legacy setup (slow session start)"),
    "A20-NO-SCOPED-SEARCH-TOPIC": ("Speed", "Many knowledge sources searched on every question (optional speed trade-off)"),
    "A21-ENVIRONMENT-DRIFT": ("Speed", "Two environments of the same agent set up differently"),
    "A22-ROUTING-TABLE-KNOWLEDGE": ("Configuration", "A routing table or topic map used as a knowledge source"),
    "A23-SESSION-STATE-IN-CHILD": ("Configuration", "A child agent's topic counts attempts with a global variable"),
    "A24-DESCRIPTION-CONTRADICTS-CONDITION": ("Configuration", "A topic description contradicts the topic's condition"),
    "A25-INSTRUCTION-VOLUME": ("Configuration", "More than about 30K characters of instructions across agent and topics"),
    "A26-INLINE-ALLOWLIST": ("Configuration", "Topic conditions with 20 or more hard-coded codes"),
}
