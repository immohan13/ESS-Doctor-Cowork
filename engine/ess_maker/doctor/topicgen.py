"""Topic generator and topic check for ESS agents.

`generate(spec)` turns a short scenario spec into the files an ESS maker adds to an agent:

- `topic.yaml`: a Copilot Studio topic (paste into the topic's code editor), built the way the ESS templates and the
  public ESS samples build them: a routing description that states what the topic owns and what it doesn't, trigger
  phrases, one question per missing input, a confirmation card before any write, the call to the ESS system topic for
  the backend, and a success/failure branch;
- the ESS template configuration for the scenario (Workday request template XML, or the ServiceNow / SuccessFactors
  template configuration record), so the scenario name in the topic and the configuration match;
- `SKILL.md`: the same behavior as an agent skill, so the scenario can move from a topic to a skill later;
- evaluation questions for the test set.

`check(text)` lints a topic (generated or existing; a single topic or a whole bot content export) against the same
rules. Backends: Workday, ServiceNow HRSD, ServiceNow ITSM, SAP SuccessFactors, knowledge-only, and generic HTTP.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field

BACKENDS = {
    "workday": "Workday",
    "servicenow-hrsd": "ServiceNow HRSD",
    "servicenow-itsm": "ServiceNow ITSM",
    "successfactors": "SAP SuccessFactors",
    "knowledge": "knowledge sources",
    "http": "your API",
}
WRITE_OPS = ("create", "update")
_ENTITY = {"date": "DateTimePrebuiltEntity", "number": "NumberPrebuiltEntity", "text": "StringPrebuiltEntity",
           "boolean": "BooleanPrebuiltEntity", "email": "EmailPrebuiltEntity"}
_PFX_TYPE = {"date": "DateTime", "number": "Number", "boolean": "Boolean"}


class SpecError(ValueError):
    """The spec can't be turned into a topic; the message says what to add."""


@dataclass
class Field:
    name: str
    type: str = "text"
    prompt: str = ""
    description: str = ""
    required: bool = True
    choices: list[str] = field(default_factory=list)
    source: str = ""  # backend field name (ServiceNow FieldName, Workday parameter, SuccessFactors property)


@dataclass
class Spec:
    name: str
    backend: str
    purpose: str
    operation: str = "read"
    audience: str = "employee"
    persona: str = "hr"
    agent_type: str = "cea"
    title: str = ""
    triggers: list[str] = field(default_factory=list)
    not_for: list[str] = field(default_factory=list)
    inputs: list[Field] = field(default_factory=list)
    outputs: list[Field] = field(default_factory=list)
    answer_format: str = ""
    options: dict = field(default_factory=dict)  # backend-specific: service, version, operation, table, entity, ...


def _label(name: str) -> str:
    """PhoneNumber -> Phone number."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).split()
    return " ".join([words[0]] + [w if w.isupper() else w.lower() for w in words[1:]]) if words else name


def _pascal(s: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", s or "")
    return "".join(w[:1].upper() + w[1:] for w in words)


def _field(x) -> Field:
    if isinstance(x, str):
        return Field(_pascal(x) or "Value")
    known = set(Field.__dataclass_fields__)
    f = Field(**{k: v for k, v in x.items() if k in known})
    f.name = _pascal(f.name) or "Value"
    f.type = (f.type or "text").lower()
    if f.choices and f.type == "text":
        f.type = "choice"
    return f


def load_spec(data: dict) -> Spec:
    """Validate and normalise a spec; raises SpecError with what's missing."""
    missing = [k for k in ("name", "backend", "purpose") if not str(data.get(k) or "").strip()]
    if missing:
        raise SpecError("The spec needs " + ", ".join(missing) + " (name: a short action such as 'Get education'; "
                        "backend: " + ", ".join(BACKENDS) + "; purpose: what the topic answers or does).")
    backend = data["backend"].strip().lower().replace("_", "-").replace(" ", "-")
    backend = {"servicenow": "servicenow-hrsd", "hrsd": "servicenow-hrsd", "itsm": "servicenow-itsm",
               "sf": "successfactors", "sap": "successfactors", "sap-successfactors": "successfactors",
               "kb": "knowledge"}.get(backend, backend)
    if backend not in BACKENDS:
        raise SpecError(f"backend must be one of: {', '.join(BACKENDS)}.")
    known = set(Spec.__dataclass_fields__)
    s = Spec(**{k: v for k, v in data.items() if k in known and k not in ("inputs", "outputs", "backend")},
             backend=backend)
    s.inputs = [_field(x) for x in data.get("inputs") or []]
    s.outputs = [_field(x) for x in data.get("outputs") or []]
    s.name = _pascal(s.name)
    s.operation = (s.operation or "read").lower()
    s.audience = (s.audience or "employee").lower()
    s.persona = "it" if (s.persona or "").lower() == "it" or backend == "servicenow-itsm" else "hr"
    s.agent_type = "da" if (s.agent_type or "").lower() == "da" else "cea"
    s.title = s.title or re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s.name)
    if s.operation not in ("read", "create", "update"):
        raise SpecError("operation must be read, create or update.")
    if backend == "knowledge" and s.operation != "read":
        raise SpecError("A knowledge topic only answers questions (operation: read).")
    if len(s.triggers) < 3:
        raise SpecError("Add at least 3 trigger phrases: short, natural questions employees ask for this scenario.")
    if not s.not_for:
        raise SpecError("Add not_for: what this topic must not handle (for example another person's data, or the "
                        "neighbouring read/write scenario), so routing has a boundary.")
    return s


# --- Names -------------------------------------------------------------------------------------------------

def prefix(s: Spec) -> str:
    """Schema prefix of the ESS system topics in the agent the topic is added to."""
    return f"msdyn_copilotforemployeeselfservice{'da' if s.agent_type == 'da' else ''}{s.persona}"


def scenario_name(s: Spec) -> str:
    if s.options.get("scenario"):
        return s.options["scenario"]
    who = "Manager" if s.audience == "manager" else "Employee"
    return {
        "workday": f"msdyn_{s.persona.upper()}WorkdayHCM{who}{s.name}",
        "servicenow-hrsd": f"msdyn_ServiceNowHRSD{s.name}",
        "servicenow-itsm": f"msdyn_ITHelpdeskServiceNowITSM{s.name}",
        "successfactors": f"msdyn_HRSAPSuccessFactorsHCM{who}{s.name}",
    }.get(s.backend, "")


def topic_name(s: Spec) -> str:
    return {"workday": "Workday", "servicenow-hrsd": "ServiceNowHRSD", "servicenow-itsm": "ServiceNowITSM",
            "successfactors": "SuccessFactors", "knowledge": "Knowledge", "http": ""}[s.backend] + s.name


# --- YAML helpers ------------------------------------------------------------------------------------------

def _q(text: str) -> str:
    """A double-quoted YAML scalar."""
    return json.dumps(text, ensure_ascii=False)


def _block(text: str, indent: int) -> list[str]:
    pad = " " * indent
    return [pad + ln if ln else "" for ln in text.splitlines()]


def _pfx_str(text: str) -> str:
    """A Power Fx string literal."""
    return '"' + text.replace('"', '""') + '"'


def _owner(s: Spec) -> str:
    if s.audience == "manager":
        return ("Data is limited to the requesting manager's direct reports; never return data for anyone who doesn't "
                "report to them.")
    if s.backend in ("knowledge", "http") and s.operation == "read" and not s.options.get("personal", True):
        return ""
    return "Data belongs exclusively to the requesting user; never return or act on another person's data."


def model_description(s: Spec) -> str:
    verb = "asks about" if s.operation == "read" else "wants to"
    what = s.purpose.rstrip(".")
    lines = [f"Use this topic when the {'manager' if s.audience == 'manager' else 'employee'} {verb} {what}"
             + (f" in {BACKENDS[s.backend]}" if s.backend not in ("knowledge", "http") else "") + "."]
    lines += ["", "Triggers: " + ", ".join(f'"{t}"' for t in s.triggers[:6]) + "."]
    own = _owner(s)
    if own:
        lines += ["", own]
    lines += ["", "Do NOT trigger for:"] + [f"- {x.rstrip('.')}" for x in s.not_for]
    return "\n".join(lines)


def _inputs_yaml(s: Spec) -> list[str]:
    if not s.inputs:
        return []
    out = ["inputs:"]
    for f in s.inputs:
        desc = f.description or f"The {_label(f.name).lower()} the {'manager' if s.audience == 'manager' else 'employee'} gave, if any."
        out += [f"  - kind: AutomaticTaskInput", f"    propertyName: {f.name}", f"    description: {_q(desc)}"]
        if f.type != "choice":
            out.append(f"    entity: {_ENTITY.get(f.type, 'StringPrebuiltEntity')}")
        out.append("    shouldPromptUser: false")
    return out + [""]


def _question(s: Spec, f: Field, ind: int) -> list[str]:
    p = " " * ind
    prompt = f.prompt or f"What {f.name.lower()} should I use?"
    out = [f"{p}- kind: Question", f"{p}  id: ask_{f.name.lower()}", f"{p}  interruptionPolicy:",
           f"{p}    allowInterruption: true", f"{p}  alwaysPrompt: false", f"{p}  variable: Topic.{f.name}",
           f"{p}  prompt: {_q(prompt)}"]
    if f.type == "choice":
        out += [f"{p}  entity:", f"{p}    kind: EmbeddedEntity", f"{p}    definition:", f"{p}      kind: ClosedListEntity",
                f"{p}      name: {f.name}", f"{p}      items:"]
        for c in f.choices:
            out += [f"{p}        - id: {re.sub(r'[^a-z0-9]+', '_', c.lower()).strip('_') or 'value'}",
                    f"{p}          displayName: {_q(c)}"]
    else:
        out.append(f"{p}  entity: {_ENTITY.get(f.type, 'StringPrebuiltEntity')}")
    return out + [""]


def _value(f: Field) -> str:
    """Power Fx text value of an input, for request parameters."""
    if f.type == "date":
        return f'Text(Topic.{f.name}, "yyyy-MM-dd")'
    return f"Text(Topic.{f.name})"


def _confirm_card(s: Spec, ind: int) -> list[str]:
    """Adaptive Card 1.5 summary with Submit and a non-submitting Cancel."""
    p = " " * ind
    facts = ",\n".join(f'{{ title: {_pfx_str(_label(f.name))}, value: {_value(f)} }}'
                       for f in s.inputs) or '{ title: "Request", value: "' + s.title + '" }'
    card = [
        "={",
        '  type: "AdaptiveCard",',
        "  '$schema': \"https://adaptivecards.io/schemas/adaptive-card.json\",",
        '  version: "1.5",',
        "  body: [",
        f'    {{ type: "TextBlock", text: {_pfx_str("Please confirm: " + s.title.lower())}, weight: "Bolder", wrap: true }},',
        "    { type: \"FactSet\", facts: [",
        *["      " + ln for ln in facts.split("\n")],
        "    ] }",
        "  ],",
        "  actions: [",
        '    { type: "Action.Submit", id: "Submit", title: "Submit" },',
        '    { type: "Action.Submit", id: "Cancel", title: "Cancel", associatedInputs: "none" }',
        "  ]",
        "}",
    ]
    return ([f"{p}- kind: AdaptiveCardPrompt", f"{p}  id: confirm_request", f"{p}  displayName: Confirm before submitting",
             f"{p}  card: |-"] + [f"{p}    {ln}" for ln in card]
            + [f"{p}  output:", f"{p}    binding:", f"{p}      actionSubmitId: Topic.actionSubmitId", "",
               f"{p}  outputType:", f"{p}    properties:", f"{p}      actionSubmitId: String", ""])


def _answer(s: Spec, source_var: str, ind: int) -> list[str]:
    """AnswerQuestionWithAI over the backend response, plus an explicit send for declarative agents."""
    p = " " * ind
    fields = ", ".join(f.name for f in s.outputs) or "the fields the user asked about"
    instr = (s.answer_format or f"Answer only what the user asked about, from these fields: {fields}. "
             "Use a short list with each field name in bold.") + \
        " Don't show internal IDs or citations, and don't add data that isn't in the response. " \
        "Tie the answer to the user's question ({System.Activity.Text})."
    out = [f"{p}- kind: AnswerQuestionWithAI", f"{p}  id: answer_from_response", f"{p}  autoSend: false",
           f"{p}  variable: Topic.Answer", f"{p}  userInput: ={source_var}", f"{p}  additionalInstructions: {_q(instr)}", ""]
    if s.agent_type == "da":
        out += [f"{p}- kind: SendActivity", f"{p}  id: send_answer", f"{p}  activity: \"{{Topic.Answer}}\"", ""]
    return out


def _fail(s: Spec, ind: int, what: str) -> list[str]:
    p = " " * ind
    msg = (f"I couldn't {what} in {BACKENDS[s.backend]} right now. Please try again later, or contact "
           f"{'IT' if s.persona == 'it' else 'HR'} support if it keeps happening.")
    return [f"{p}- kind: SendActivity", f"{p}  id: send_failure", f"{p}  activity: {_q(msg)}", ""]


# --- Backend calls -----------------------------------------------------------------------------------------

def _workday_call(s: Spec, ind: int) -> list[str]:
    p = " " * ind
    params = []
    if s.audience == "manager":
        params.append(("Manager_Org_ID", "Global.ESS_UserContext_ManagerOrganizationId"))
    params.append(("Employee_ID", "Global.ESS_UserContext_Employee_Id"))
    params += [(f.source or f.name, _value(f)) for f in s.inputs]
    expr = ['="{""params"":[" &']
    for i, (k, v) in enumerate(params):
        sep = "," if i < len(params) - 1 else ""
        expr.append(f'"{{""key"":""{{{k}}}"",""value"":""" & {v} & """}}{sep}" &')
    expr.append('"]}"')
    return ([f"{p}- kind: BeginDialog", f"{p}  id: call_workday", f"{p}  displayName: Workday common execution",
             f"{p}  input:", f"{p}    binding:", f"{p}      parameters: |-"]
            + [f"{p}        {ln}" for ln in expr]
            + [f"{p}      scenarioName: {scenario_name(s)}", "",
               f"{p}  dialog: {prefix(s)}.topic.WorkdaySystemGetCommonExecution",
               f"{p}  output:", f"{p}    binding:", f"{p}      errorResponse: Topic.errorResponse",
               f"{p}      isSuccess: Topic.isSuccess", f"{p}      workdayResponse: Topic.workdayResponse", ""])


def _servicenow_call(s: Spec, ind: int) -> list[str]:
    p = " " * ind
    hrsd = s.backend == "servicenow-hrsd"
    system = "ServiceNowHRSDSystemCommonExecution" if hrsd else "ServiceNowITSMSystemCommonExecution"
    rows = [f'{{ Name: "{f.name}", Value: {_value(f)} }}' for f in s.inputs if f.name != "SysId"]
    params = ["=JSON(", "  Table("] + [f"    {r}{',' if i < len(rows) - 1 else ''}" for i, r in enumerate(rows)] + ["  )", ")"] \
        if rows else ['="[]"']
    sysid = next((f for f in s.inputs if f.name == "SysId"), None)
    out = [f"{p}- kind: SetVariable", f"{p}  id: set_input_parameters", f"{p}  variable: Topic.InputParameters",
           f"{p}  value: |-"] + [f"{p}    {ln}" for ln in params] + [""]
    out += [f"{p}- kind: BeginDialog", f"{p}  id: call_servicenow", f"{p}  displayName: ServiceNow common execution",
            f"{p}  input:", f"{p}    binding:", f"{p}      InputParameters: =Topic.InputParameters"]
    if hrsd:
        out.append(f"{p}      OperationType: {_q('=' + _pfx_str(s.operation.upper()))}")
    out += [f"{p}      ScenarioName: {_q('=' + _pfx_str(scenario_name(s)))}",
            f"{p}      SysId: " + ("=Topic.SysId" if sysid else _q('=""')),
            f"{p}      UserIdentifier: =Global.ESS_UserContext_UPN", "",
            f"{p}  dialog: {prefix(s)}.topic.{system}", f"{p}  output:", f"{p}    binding:",
            f"{p}      ServiceNowData: Topic.ServiceNowData"]
    if hrsd:
        out.append(f"{p}      IsSuccessful: Topic.IsSuccessful")
    return out + [""]


def _sf_call(s: Spec, ind: int) -> list[str]:
    p = " " * ind
    if s.operation == "read":
        keys = [("personIdExternalVal", "Global.ESS_UserContext_Employee_Id")] + [
            ((f.source or f.name[:1].lower() + f.name[1:] + "Val"), _value(f)) for f in s.inputs]
        body = ", ".join(f'""{k}"": """ & {v} & """' for k, v in keys)
        return [f"{p}- kind: BeginDialog", f"{p}  id: call_successfactors", f"{p}  displayName: SuccessFactors read",
                f"{p}  input:", f"{p}    binding:", f"{p}      filterParams: {_q('=' + chr(34) + '{' + body + '}' + chr(34))}",
                f"{p}      scenarioName: {scenario_name(s)}", f"{p}      userIdentifier: =Global.ESS_UserContext_User_Id", "",
                f"{p}  dialog: {prefix(s)}.topic.SuccessFactorsSystemGetCommonExecution", f"{p}  output:", f"{p}    binding:",
                f"{p}      isSucceeded: Topic.isSucceeded",
                f"{p}      successfactorsLabelResponse: Topic.successfactorsLabelResponse",
                f"{p}      successfactorsModelResponse: Topic.successfactorsModelResponse", ""]
    rows = [("personIdExternalVal", "Global.ESS_UserContext_Employee_Id")] + [
        ((f.source or f.name[:1].lower() + f.name[1:] + "Val"),
         f'Substitute(Substitute({_value(f)}, "\\", "\\\\"), """", "\\""")') for f in s.inputs]
    items = ",".join(f'{{""key"": ""{k}"", ""value"": """ & {v} & """}}' for k, v in rows)
    return [f"{p}- kind: BeginDialog", f"{p}  id: call_successfactors_update", f"{p}  displayName: SuccessFactors update",
            f"{p}  input:", f"{p}    binding:", f"{p}      targetUserId: =Global.ESS_UserContext_User_Id",
            f"{p}      var_requestParam: {_q('=' + chr(34) + '[' + items + ']' + chr(34))}",
            f"{p}      var_scenarioName: {_q('=' + _pfx_str(scenario_name(s)))}", "",
            f"{p}  dialog: {prefix(s)}.topic.SuccessFactorsSystemUpdateCommonExecution", f"{p}  output:", f"{p}    binding:",
            f"{p}      modelResponse: Topic.modelResponse", ""]


def _http_call(s: Spec, ind: int) -> list[str]:
    p = " " * ind
    method = (s.options.get("method") or ("Get" if s.operation == "read" else "Post")).capitalize()
    url = s.options.get("url") or "https://<your-api>/" + re.sub(r"(?<=[a-z])(?=[A-Z])", "-", s.name).lower()
    body = ["={", "  employeeId: Global.ESS_UserContext_Employee_Id" + ("," if s.inputs else "")]
    body += [f"  {f.name[:1].lower() + f.name[1:]}: {_value(f)}{',' if i < len(s.inputs) - 1 else ''}"
             for i, f in enumerate(s.inputs)] + ["}"]
    out = [f"{p}# Endpoint for this scenario. It must authenticate to the backend itself (managed identity or a",
           f"{p}# connection); never put secrets or tokens in a topic.",
           f"{p}- kind: SetVariable", f"{p}  id: set_api_url", f"{p}  variable: Topic.ApiUrl", f"{p}  value: {_q(url)}", "",
           f"{p}- kind: HttpRequestAction", f"{p}  id: call_api", f"{p}  method: {method}", f"{p}  url: =Topic.ApiUrl",
           f"{p}  headers:", f"{p}    Content-Type: application/json"]
    if method != "Get":
        out += [f"{p}  body:", f"{p}    kind: JsonRequestContent", f"{p}    content: |-"] + [f"{p}      {ln}" for ln in body]
    out += [f"{p}  response: Topic.ApiResponse", f"{p}  responseSchema: Any", f"{p}  errorHandling:",
            f"{p}    kind: ContinueOnErrorBehavior", f"{p}    statusCode: Topic.ApiStatusCode", ""]
    return out


def _knowledge(s: Spec, ind: int) -> list[str]:
    p = " " * ind
    sources = s.options.get("sources") or []
    instr = s.answer_format or ("Answer only from the retrieved content and cite each article you used. If the content "
                                "doesn't answer the question, say so and give the approved support route.")
    out = [f"{p}- kind: SearchAndSummarizeContent", f"{p}  id: search_and_answer", f"{p}  variable: Topic.Answer",
           f"{p}  userInput: =System.Activity.Text", f"{p}  additionalInstructions: {_q(instr)}"]
    if sources:
        out += [f"{p}  fileSearchDataSource:", f"{p}    searchFilesMode:", f"{p}      kind: DoNotSearchFiles", "",
                f"{p}  knowledgeSources:", f"{p}    kind: SearchSpecificKnowledgeSources", f"{p}    knowledgeSources:"]
        out += [f"{p}      - {x}" for x in sources]
    return out + [""]


# --- Topic ---------------------------------------------------------------------------------------------------

def build_topic(s: Spec) -> str:
    y = ["kind: AdaptiveDialog"] + _inputs_yaml(s)
    y += ["modelDescription: |-"] + _block(model_description(s), 2)
    y += ["beginDialog:", "  kind: OnRecognizedIntent", "  id: main", "  intent:", "    triggerQueries:"]
    y += [f"      - {_q(t)}" for t in s.triggers] + ["", "  actions:"]
    a = 4
    for f in s.inputs:
        if f.required:
            y += _question(s, f, a)
    write = s.operation in WRITE_OPS
    if write:
        y += _confirm_card(s, a)
        y += [f"{' ' * a}- kind: ConditionGroup", f"{' ' * a}  id: on_confirm", f"{' ' * a}  conditions:",
              f"{' ' * a}    - id: submitted", f"{' ' * a}      condition: =Topic.actionSubmitId = \"Submit\"",
              f"{' ' * a}      actions:"]
        body_ind = a + 8
    else:
        body_ind = a
    p = " " * body_ind
    what = s.title.lower() if write else f"get your {s.title.lower().removeprefix('get ').removeprefix('my ')}"
    if s.backend == "knowledge":
        body = _knowledge(s, body_ind)
        if s.agent_type == "da":
            body += [f"{p}- kind: SendActivity", f"{p}  id: send_answer", f"{p}  activity: \"{{Topic.Answer}}\"", ""]
    else:
        call = {"workday": _workday_call, "servicenow-hrsd": _servicenow_call, "servicenow-itsm": _servicenow_call,
                "successfactors": _sf_call, "http": _http_call}[s.backend](s, body_ind)
        ok, data = {
            "workday": ("=Topic.isSuccess = true", "Topic.workdayResponse"),
            "servicenow-hrsd": ("=Topic.IsSuccessful = true && !IsBlank(Topic.ServiceNowData)", "Topic.ServiceNowData"),
            "servicenow-itsm": ("=!IsBlank(Topic.ServiceNowData)", "Topic.ServiceNowData"),
            "successfactors": ("=Topic.isSucceeded = true", "Topic.successfactorsModelResponse"),
            "http": ("=IsBlank(Topic.ApiStatusCode) || (Topic.ApiStatusCode >= 200 && Topic.ApiStatusCode < 300)",
                     "JSON(Topic.ApiResponse)"),
        }[s.backend]
        if s.backend == "successfactors" and write:
            ok = "=Topic.modelResponse <> \"PermissionFailed\""
        if write:
            msg = ("Done. Your " + ("case" if s.persona == "hr" else "ticket") + " was submitted."
                   if s.backend.startswith("servicenow") else "Done. Your request to " + s.title.lower() + " was submitted.")
            done = [f"{p}        - kind: SendActivity", f"{p}          id: send_done", f"{p}          activity: {_q(msg)}", ""]
        else:
            done = _answer(s, data, body_ind + 8)
        body = call + [f"{p}- kind: ConditionGroup", f"{p}  id: on_result", f"{p}  conditions:", f"{p}    - id: succeeded",
                       f"{p}      condition: {ok}", f"{p}      actions:"] + done
        body += [f"{p}  elseActions:"] + _fail(s, body_ind + 4, what)
    y += body
    if write:
        y += [f"{' ' * a}  elseActions:", f"{' ' * a}    - kind: SendActivity", f"{' ' * a}      id: send_cancelled",
              f"{' ' * a}      activity: \"No changes were made.\"", ""]
    y += ["inputType:"]
    if s.inputs:
        y += ["  properties:"]
        for f in s.inputs:
            y += [f"    {f.name}:", f"      displayName: {f.name}",
                  f"      description: {_q(f.description or f.prompt or f.name)}",
                  f"      type: {_PFX_TYPE.get(f.type, 'String')}"]
    else:
        y[-1] = "inputType: {}"
    y += ["", "outputType:"]
    if not write:
        y += ["  properties:", "    Answer:", "      displayName: Answer", "      type: String"]
    else:
        y[-1] = "outputType: {}"
    return "\n".join(y).rstrip() + "\n"


# --- Template configuration ---------------------------------------------------------------------------------

def _xml_record(name: str, value: dict) -> str:
    body = html.escape(json.dumps(value, indent=2), quote=False)
    return (f'<msdyn_employeeselfservicetemplateconfig msdyn_uniquename="{name}">\n'
            f"  <iscustomizable>1</iscustomizable>\n  <msdyn_name>{name}</msdyn_name>\n"
            f"  <msdyn_value>{body}</msdyn_value>\n</msdyn_employeeselfservicetemplateconfig>\n")


def build_config(s: Spec) -> tuple[str, str] | None:
    """(file name, content) of the ESS template configuration, or None for knowledge and HTTP topics."""
    name = scenario_name(s)
    o = s.options
    if s.backend == "workday":
        service, version = o.get("service") or "REPLACE_Workday_Service", o.get("version") or "v42.0"
        operation = o.get("operation") or "REPLACE_Operation"
        params = (["Manager_Org_ID"] if s.audience == "manager" else []) + ["Employee_ID"] + [f.source or f.name for f in s.inputs]
        props = "\n".join(
            f"          <property>\n            <key>{f.name}</key>\n            <extractPath>"
            f"//*[local-name()='{f.source or f.name}']/text()</extractPath>\n          </property>" for f in s.outputs) \
            or "          <!-- add one <property> per response field: <key> and an XPath <extractPath> -->"
        ref = ("<bsvc:Organization_Reference>\n            <bsvc:ID bsvc:type=\"Organization_Reference_ID\">{Manager_Org_ID}</bsvc:ID>\n"
               "          </bsvc:Organization_Reference>" if s.audience == "manager" else
               "<bsvc:Employee_Reference>\n            <bsvc:ID bsvc:type=\"Employee_ID\">{Employee_ID}</bsvc:ID>\n"
               "          </bsvc:Employee_Reference>")
        extra = "\n".join(f"        <bsvc:{f.source or f.name}>{{{f.source or f.name}}}</bsvc:{f.source or f.name}>" for f in s.inputs)
        xml = f"""<workdayEntityConfigurationTemplate>
  <scenario name="{s.name}">
    <apiRequests>
      <apiRequest>
        <authType>User</authType>
        <endpoint>
          <request>{name}</request>
          <serviceName>{service}</serviceName>
          <version>{version}</version>
        </endpoint>
        <requestParameters>
""" + "\n".join(f"          <parameter>\n            <name>{x}</name>\n            <value>{{{x}}}</value>\n          </parameter>"
                for x in params) + f"""
        </requestParameters>
        <responseProperties>
{props}
        </responseProperties>
      </apiRequest>
    </apiRequests>
  </scenario>
  <requestTemplates>
    <requestTemplate name="{name}">
      <bsvc:{operation}_Request xmlns:bsvc="urn:com.workday/bsvc" bsvc:version="{version}">
        <bsvc:Request_References>
          {ref}
        </bsvc:Request_References>
{extra}
      </bsvc:{operation}_Request>
    </requestTemplate>
  </requestTemplates>
</workdayEntityConfigurationTemplate>
"""
        return f"{name}.xml", xml
    if s.backend.startswith("servicenow"):
        hrsd = s.backend == "servicenow-hrsd"
        table = o.get("table") or ("sn_hr_core_case" if hrsd else "incident")
        who = "opened_for.email" if hrsd else "caller_id.email"
        value: dict = {"Scenario": name.removeprefix("msdyn_"), "Table": table}
        outputs = [{"FieldName": f.source or f.name.lower(), "OutputName": f.name} for f in s.outputs]
        if s.operation == "read":
            value["FilterCriteria"] = [{"VariableName": "UserIdentifier", "FieldName": who, "Operator": "="}] + [
                {"VariableName": f.name, "FieldName": f.source or f.name.lower(), "Operator": "="} for f in s.inputs]
            value["SortCriteria"] = [{"FieldName": "sys_updated_on", "Operator": "ORDERBYDESC"}]
            value["Limit"] = int(o.get("limit") or 10)
        else:
            user = [{"FieldName": "opened_for" if hrsd else "caller_id", "VariableName": "UserIdentifier"}] \
                if s.operation == "create" else []
            value["InputFieldMapping"] = {
                "UserParameters": user + [{"FieldName": f.source or f.name.lower(), "VariableName": f.name}
                                          for f in s.inputs if f.name != "SysId"],
                "GlobalParameters": [{"FieldName": k, "DefaultValue": str(v)} for k, v in (o.get("defaults") or {}).items()],
            }
            outputs = outputs or [{"FieldName": "number", "OutputName": "CaseNumber" if hrsd else "TicketNumber"}]
        value["OutputFieldMapping"] = outputs + [{"FieldName": "sys_id", "OutputName": "SysId"}]
        return "msdyn_employeeselfservicetemplateconfig.xml", _xml_record(name, value)
    if s.backend == "successfactors":
        entity = o.get("entity") or "REPLACE_RootEntity"
        filt = o.get("filter") or ("personIdExternal eq '{personIdExternalVal}'" + "".join(
            f" and {f.source or f.name[:1].lower() + f.name[1:]} eq '{{{(f.source or f.name[:1].lower() + f.name[1:])}Val}}'"
            for f in s.inputs))
        value = {"scenario": name.removeprefix("msdyn_"), "rootEntity": entity, "filter": filt,
                 "requestEntities": [{"key": f.source or f.name[:1].lower() + f.name[1:],
                                      "valuePath": f.source or f.name[:1].lower() + f.name[1:],
                                      "labelPath": f"{entity}/{f.source or f.name[:1].lower() + f.name[1:]}"} for f in s.outputs],
                 "permissionsMetadata": [], "rolePermissions": []}
        return "msdyn_employeeselfservicetemplateconfig.xml", _xml_record(name, value)
    return None


# --- Skill, tests, notes ---------------------------------------------------------------------------------------

def skill_name(s: Spec) -> str:
    base = {"workday": "workday", "servicenow-hrsd": "servicenow-hrsd", "servicenow-itsm": "servicenow-itsm",
            "successfactors": "successfactors", "knowledge": "knowledge", "http": "ess"}[s.backend]
    return base + "-" + re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", s.name).lower()


def build_skill(s: Spec) -> str:
    who = "manager" if s.audience == "manager" else "employee"
    desc = (f"{s.purpose[:1].upper() + s.purpose[1:].rstrip('.')} for the signed-in {who}"
            + (f", from {BACKENDS[s.backend]}" if s.backend not in ("http",) else "") + ". USE FOR: "
            + ", ".join(f"'{t}'" for t in s.triggers[:5]) + ". DO NOT USE FOR: " + "; ".join(s.not_for) + ".")
    lines = ["---", f"name: {skill_name(s)}", f"description: {_q(desc)}", "license: MIT", "---", "",
             f"# {s.title}", "",
             f"An Employee Self-Service skill with the same behavior as the `{topic_name(s)}` topic, so the scenario can "
             "move from a topic to a skill without changing what employees see.", "",
             "## When to use", ""] + [f"- \"{t}\"" for t in s.triggers] + ["", "## When not to use", ""]
    lines += [f"- {x[:1].upper() + x[1:].rstrip('.')}." for x in s.not_for] + ["", "## Guardrails", ""]
    if _owner(s):
        lines.append(f"- {_owner(s)}")
    lines += ["- Use the signed-in user's identity from the user context; never ask for or accept an employee ID.",
              "- Ask one follow-up question at a time, only for details the user didn't already give."]
    if s.operation in WRITE_OPS:
        lines += ["- Show a summary and get an explicit confirmation before submitting; cancelling changes nothing.",
                  "- Never retry a submit automatically. On failure, say it didn't go through."]
    lines += ["- Answer only from the system's response; don't add or guess data. Hide internal IDs.", ""]
    if s.inputs:
        lines += ["## Follow-up questions", ""] + [
            f"{i}. **{_label(f.name)}**: {f.prompt or f.description or 'ask for it'}"
            + (f" ({', '.join(f.choices)})" if f.choices else "") + (" Optional." if not f.required else "")
            for i, f in enumerate(s.inputs, 1)] + [""]
    lines += ["## Tool", ""]
    if s.backend == "knowledge":
        lines += ["Search " + (", ".join(f"`{x}`" for x in s.options.get("sources") or []) or "the agent's knowledge sources")
                  + " with the user's words, answer from the retrieved content and cite it.", ""]
    elif s.backend == "http":
        lines += [f"Call `{s.options.get('url') or '<your API>'}` with the user's employee ID"
                  + (" and " + ", ".join(f.name for f in s.inputs) if s.inputs else "") + ". The endpoint authenticates "
                  "to the backend itself; don't put secrets in the skill.", ""]
    else:
        lines += [f"Run ESS scenario `{scenario_name(s)}` through the {BACKENDS[s.backend]} connection the ESS agent "
                  "already uses (the same template configuration as the topic).", ""]
    if s.outputs:
        lines += ["Returns: " + ", ".join(f"**{_label(f.name)}**" for f in s.outputs) + ".", ""]
    lines += ["## Bundled files", "", "| File | Description |", "| --- | --- |",
              f"| [topic.yaml](topic.yaml) | The Copilot Studio topic for the same scenario. |"]
    cfg = build_config(s)
    if cfg:
        lines.append(f"| [{cfg[0]}]({cfg[0]}) | The ESS template configuration for `{scenario_name(s)}`. |")
    return "\n".join(lines).rstrip() + "\n"


def tests(s: Spec) -> list[dict]:
    expect = ("Asks for confirmation, then submits for the signed-in user only after Submit." if s.operation in WRITE_OPS
              else f"Answers from {BACKENDS[s.backend]} for the signed-in user only.")
    out = [{"prompt": t, "expected": expect} for t in s.triggers]
    out += [{"prompt": _negative(x), "expected": "Doesn't use this topic; " + (
        "declines to share another person's data." if re.search(r"another|someone|other (person|employee)|colleague|manager's",
                                                                 x, re.I) else "handles it elsewhere or says it's out of scope.")}
            for x in s.not_for]
    return out


def _negative(x: str) -> str:
    x = x.strip().rstrip(".")
    return x[:1].upper() + x[1:] + ("?" if not x.endswith("?") else "")


def notes(s: Spec) -> list[str]:
    """What the maker must check or fill in before importing."""
    out = []
    if s.backend == "workday":
        if not s.options.get("service") or not s.options.get("operation"):
            out.append("Set the Workday web service, operation and version in the template configuration "
                       "(`<serviceName>`, the `bsvc:…_Request` element and `<version>`).")
        out.append("Check the request body and the XPath of each response field against the Workday operation.")
    if s.backend.startswith("servicenow"):
        out.append("Check the table and field names in the template configuration against your ServiceNow instance.")
    if s.backend == "successfactors" and not s.options.get("entity"):
        out.append("Set `rootEntity` (the SuccessFactors OData entity) and the field paths in the template configuration.")
    if s.backend == "http":
        out.append("Point `Topic.ApiUrl` at your endpoint; it must authenticate to the backend itself.")
    if s.backend == "knowledge" and not s.options.get("sources"):
        out.append("Add the knowledge sources to search (their schema names) so the topic doesn't search everything.")
    out.append(f"The topic calls `{prefix(s)}.topic.…` system topics; in another agent, change that prefix to "
               "match the agent's ESS solution.")
    return out


def generate(data: dict) -> dict:
    s = load_spec(data)
    topic = build_topic(s)
    return {"spec": s, "topic": topic, "config": build_config(s), "skill": build_skill(s), "tests": tests(s),
            "notes": notes(s), "findings": check(topic, s.agent_type)}


# --- Topic check -----------------------------------------------------------------------------------------------

@dataclass
class TopicFinding:
    rule: str
    severity: str
    topic: str
    title: str
    fix: str


TOPIC_RULES: dict[str, tuple[str, str]] = {
    "T01": ("Routing", "Intent topic without a useful routing description (missing or under 50 characters)"),
    "T02": ("Routing", "Routing description with a placeholder left in it"),
    "T03": ("Routing", "Backend topic whose description doesn't say whose data it is or what it must not handle"),
    "T04": ("Routing", "Repeated trigger phrases, or no trigger phrases and no routing description"),
    "T05": ("Cards", "Adaptive Card version other than 1.5"),
    "T06": ("Cards", "Required card input without a label or error message"),
    "T07": ("Cards", "Cancel button that submits the form's inputs"),
    "T08": ("Writes", "Write without a confirmation before it"),
    "T09": ("Results", "Backend call whose success flag is never checked"),
    "T10": ("Identity", "Employee ID taken from the conversation instead of the user context"),
    "T11": ("Configuration", "Hard-coded URL, tenant or secret in the topic"),
    "T12": ("Configuration", "Topic mixes declarative agent and custom engine agent system topics"),
    "T13": ("Answers", "Declarative agent topic that never sends its generated answer"),
    "T14": ("Knowledge", "Knowledge search not scoped to specific sources"),
    "T15": ("Configuration", "Counter kept in a global variable"),
    "T16": ("Configuration", "Condition with a long hard-coded list of values"),
}


def _topics(text: str) -> list[tuple[str, str]]:
    """(name, body) of each topic: a whole bot content export or a single topic YAML."""
    if "kind: DialogComponent" not in text:
        return [("This topic", text)]
    out = []
    for m in re.finditer(r"(?m)^\s*- kind: DialogComponent\s*$", text):
        end = re.search(r"(?m)^\s*- kind: \w+Component\s*$", text[m.end():])
        body = text[m.end(): m.end() + end.start()] if end else text[m.end():]
        name = re.search(r"(?m)^\s+displayName: (.+)$", body)
        out.append((name.group(1).strip().strip('"') if name else "Unnamed topic", body))
    return out


def _scalar_block(body: str, key: str) -> str:
    """Text of a YAML scalar (plain, quoted or block) at `key:`, read by indentation."""
    m = re.search(rf"(?m)^(\s*){key}:[ \t]*(.*)$", body)
    if not m:
        return ""
    indent, rest = len(m.group(1)), m.group(2).strip()
    if rest and not re.fullmatch(r"[|>][-+]?", rest):
        return rest.strip('"\'')
    lines = []
    for ln in body[m.end():].splitlines()[1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) <= indent:
            break
        lines.append(ln.strip())
    return "\n".join(lines).strip()


def check(text: str, agent_type: str = "auto") -> list[TopicFinding]:
    out: list[TopicFinding] = []
    for name, body in _topics(text):
        f = lambda r, sev, fix: out.append(TopicFinding(r, sev, name, TOPIC_RULES[r][1], fix))  # noqa: E731
        trig = re.search(r"beginDialog:\s*\n\s*kind: (\w+)", body)
        intent = bool(trig and trig.group(1) == "OnRecognizedIntent") or (not trig and "OnRecognizedIntent" in body)
        dtext = _scalar_block(body, "modelDescription")
        system = re.search(r"\.topic\.(Workday|ServiceNow|SuccessFactors)\w*(CommonExecution|System\w+)", body)
        if intent and len(re.sub(r"\s+", " ", dtext).strip()) < 50:
            f("T01", "high", "Describe what the topic owns, with example requests, and what it must not handle.")
        if re.search(r"\[(add|insert|todo|tbd)[^\]]*\]|<(add|insert|your)[^>]*>|\bTODO\b|\bTBD\b", dtext, re.I):
            f("T02", "medium", "Replace the placeholder with real wording.")
        if intent and system and dtext:
            owns = re.search(r"exclusively|belongs to|only (the|their)|own data|\btheir\b|\bthe user'?s\b|requesting (user|"
                             r"manager)|user making the request|direct reports|signed-in", dtext, re.I)
            limits = re.search(r"do not|don'?t|not (for|trigger)|never|invalid|\bonly (when|if)\b", dtext, re.I)
            if not owns:
                f("T03", "medium", "Say whose data it is (the requesting user, or the manager's direct reports) and add "
                                   "a 'Do NOT trigger for' list.")
            elif not limits:
                f("T03", "low", "Add a 'Do NOT trigger for' list (other people's data, the neighbouring read or write "
                                "topic) so routing has a boundary.")
        tq = re.search(r"triggerQueries:\s*\n((?:\s+- .*\n?)+)", body)
        if intent and tq:
            qs = [re.sub(r"\W+", " ", x).strip().lower() for x in re.findall(r"- (.+)", tq.group(1))]
            if len(set(qs)) < len(qs):
                f("T04", "low", "Remove the repeated trigger phrase; use distinct, natural phrasings.")
        elif intent and len(dtext) < 50:
            f("T04", "low", "Add a few natural trigger phrases, or a routing description.")
        for v in re.findall(r"version:\s*\"?(\d\.\d)\"?", body):
            if v != "1.5" and "AdaptiveCard" in body:
                f("T05", "low", "Use Adaptive Card version 1.5 (Teams and Microsoft 365 Copilot).")
                break
        for inp in re.finditer(r"type:\s*\"Input\.\w+\"[^}]*", body):
            block = inp.group(0)
            if "isRequired: true" in block and ("label" not in block or "errorMessage" not in block):
                f("T06", "medium", "Give each required input a label and an errorMessage.")
                break
        for act in re.finditer(r"\{[^{}]*type:\s*\"Action\.Submit\"[^{}]*\}", body):
            if re.search(r"title:\s*\"(Cancel|No|Back)", act.group(0), re.I) and "associatedInputs" not in act.group(0):
                f("T07", "medium", "Add associatedInputs: \"none\" to the Cancel button so it doesn't validate or submit.")
                break
        write = re.search(r"OperationType:[^\n]*(CREATE|UPDATE)|UpdateCommonExecution|scenarioName:[^\n]*"
                          r"(?:Employee|Manager|HCM|HRSD|ITSM|Absence)(?:Update|Create|Add|Request|Submit|Enter|Delete|Change|Give)(?=[A-Z_])", body)
        if intent and write and not re.search(r"AdaptiveCardPrompt|kind: Question[\s\S]{0,400}(confirm|submit|sure)", body, re.I):
            f("T08", "high", "Show a summary and ask for an explicit Submit before the write; Cancel must change nothing.")
        write = re.search(r"OperationType:[^\n]*(CREATE|UPDATE)|UpdateCommonExecution|scenarioName:[^\n]*"
                          r"(?:Employee|Manager|HCM|HRSD|ITSM|Absence)(?:Update|Create|Add|Request|Submit|Enter|Delete|Change|Give)(?=[A-Z_])", body)
        for flag in ("isSuccess", "IsSuccessful", "isSucceeded"):
            if re.search(rf"{flag}: Topic\.(\w+)", body):
                var = re.search(rf"{flag}: Topic\.(\w+)", body).group(1)
                if intent and write and len(re.findall(rf"Topic\.{var}\b", body)) < 2 and not re.search(
                        r"condition:[^\n]*Topic\.(errorResponse|ErrorMessage|ServiceNowData|workdayResponse)", body):
                    f("T09", "medium", f"Branch on Topic.{var}: answer on success, give a clear failure message otherwise.")
        ident = re.search(r"Employee_ID\}?\W{0,12}value\W{0,12}(?:&\s*)?Topic\.(\w+)|[uU]serIdentifier:\s*=Topic\."
                          r"(?!UserIdentifier\b)(\w+)", body)
        if ident and not re.search(rf"variable: Topic\.{ident.group(1) or ident.group(2)}\s+value:[^\n]*Global\.ESS_UserContext",
                                   body):
            f("T10", "high", "Pass the employee from Global.ESS_UserContext_* (the signed-in user), not a value from the "
                             "conversation.")
        urls = [u for u in re.findall(r"https?://[^\s\"')<>]+", body)
                if not re.search(r"(?i)<|adaptivecards\.io|schemas|example\.|contoso|microsoft\.com|powerapps\.com|"
                                 r"office\.com|aka\.ms|placeholder", u)]
        if re.search(r"(?i)bearer\s+[a-z0-9._-]{20,}|api[_-]?key\s*[:=]\s*\S{12,}|password\s*[:=]", body):
            f("T11", "high", "Remove the secret; use a connection or environment variable.")
        elif urls:
            f("T11", "low", f"Move {urls[0][:60]} into an environment variable or the user context.")
        prefixes = set(re.findall(r"msdyn_copilotforemployeeselfservice(da)?(?:hr|it)\.topic", body))
        if len(prefixes) > 1:
            f("T12", "high", "Use one solution prefix: …dahr/…dait for a declarative agent, …hr/…it for a custom engine agent.")
        da = agent_type == "da" or (agent_type == "auto" and "employeeselfservicedahr" in body)
        ans = re.search(r"kind: AnswerQuestionWithAI[\s\S]*?autoSend: false[\s\S]*?variable: (Topic\.\w+)", body)
        if da and ans and not re.search(rf"{re.escape(ans.group(1))}\b", body[ans.end():]):
            f("T13", "high", f"Add a SendActivity with {{{ans.group(1)}}} after the answer node.")
        if "SearchAndSummarizeContent" in body and "SearchSpecificKnowledgeSources" not in body:
            f("T14", "low", "Scope the search to the sources that own this subject (SearchSpecificKnowledgeSources).")
        if re.search(r"=\s*(?:Coalesce\(\s*)?Global\.\w+\)?\s*\+\s*1\b", body):
            f("T15", "medium", "Don't keep counts in global variables; they reset when the agent is called through a hub.")
        for cond in re.findall(r"condition:\s*(.+)", body):
            if len(re.findall(r"\"[^\"]{1,30}\"", cond)) >= 20:
                f("T16", "low", "Keep the list in an environment variable and reference it.")
                break
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda x: (order.get(x.severity, 3), x.topic))
    return out
