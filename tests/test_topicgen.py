"""Topic generator and topic check."""

import contextlib
import io
import json
import os
import re
import tempfile
from pathlib import Path

from ess_maker.doctor import topicgen as tg

_BASE = {"triggers": ["What is my education?", "Show my degrees", "Where did I study?"],
         "not_for": ["another person's education", "updating education records"]}

_SPECS = [
    dict(_BASE, name="Get education", backend="workday", purpose="their education history", outputs=["School"],
         options={"service": "Human_Resources", "operation": "Get_Workers", "version": "v42.0"}),
    dict(_BASE, name="Update work phone", backend="workday", operation="update", purpose="update their work phone",
         inputs=[{"name": "PhoneNumber", "prompt": "What is your new work phone number?"}]),
    dict(_BASE, name="Team cost centers", backend="workday", audience="manager", purpose="cost centers of their team"),
    dict(_BASE, name="Get user cases", backend="servicenow-hrsd", purpose="their HR cases",
         outputs=[{"name": "CaseNumber", "source": "number"}]),
    dict(_BASE, name="Create payroll case", backend="servicenow-hrsd", operation="create", purpose="open a payroll case",
         inputs=[{"name": "ShortDescription", "source": "short_description"}], options={"table": "sn_hr_core_case_payroll"}),
    dict(_BASE, name="Create ticket", backend="servicenow-itsm", operation="create", purpose="open an IT incident",
         inputs=[{"name": "Urgency", "choices": ["Low", "High"], "source": "urgency"}]),
    dict(_BASE, name="Get contact email", backend="successfactors", purpose="their contact email",
         outputs=[{"name": "Email", "source": "emailAddress"}], options={"entity": "PerEmail"}),
    dict(_BASE, name="Update preferred name", backend="successfactors", operation="update",
         purpose="update their preferred name", inputs=[{"name": "PreferredName", "source": "preferredName"}]),
    dict(_BASE, name="Benefits", backend="knowledge", purpose="benefits policies", options={"sources": ["cr1.topic.Benefits"]}),
    dict(_BASE, name="Book desk", backend="http", operation="create", purpose="book a desk", inputs=[{"name": "Day", "type": "date"}]),
]


def test_every_backend_generates_a_topic_that_passes_its_own_check():
    for spec in _SPECS:
        for agent in ("cea", "da"):
            r = tg.generate(dict(spec, agent_type=agent))
            s, topic = r["spec"], r["topic"]
            assert r["findings"] == [], (s.backend, s.operation, agent, [(f.rule, f.topic) for f in r["findings"]])
            assert topic.startswith("kind: AdaptiveDialog") and "Do NOT trigger for:" in topic and "\t" not in topic
            if s.operation != "read":
                assert 'version: "1.5"' in topic and 'associatedInputs: "none"' in topic
                assert topic.index("AdaptiveCardPrompt") < topic.index("on_result")
            if s.backend not in ("knowledge", "http"):
                assert f"{tg.prefix(s)}.topic." in topic and ("employeeselfserviceda" in topic) == (agent == "da")
                name, cfg = r["config"]
                assert tg.scenario_name(s) in cfg and tg.scenario_name(s) in topic
            else:
                assert r["config"] is None
            assert "Global.ESS_UserContext_" in topic or s.backend == "knowledge"
            assert r["skill"].startswith("---\nname: ") and "## Guardrails" in r["skill"]


def test_declarative_agent_topics_send_the_generated_answer():
    r = tg.generate(dict(_SPECS[0], agent_type="da"))
    assert "kind: AnswerQuestionWithAI" in r["topic"] and 'activity: "{Topic.Answer}"' in r["topic"]
    assert "employeeselfservicedahr.topic.WorkdaySystemGetCommonExecution" in r["topic"]


def test_spec_without_triggers_or_boundaries_is_refused():
    for bad, needle in [({"name": "X", "backend": "workday", "purpose": "x", "triggers": ["a"]}, "trigger"),
                        ({"name": "X", "backend": "workday", "purpose": "x", "triggers": ["a", "b", "c"]}, "not_for"),
                        ({"name": "X", "backend": "oracle", "purpose": "x"}, "backend")]:
        try:
            tg.generate(bad)
            raise AssertionError("expected SpecError")
        except tg.SpecError as e:
            assert needle in str(e)


def test_topic_check_flags_the_topic_rules():
    bad = r'''kind: AdaptiveDialog
modelDescription: HR stuff
beginDialog:
  kind: OnRecognizedIntent
  id: main
  intent:
    triggerQueries:
      - update my phone
      - update my phone
  actions:
    - kind: BeginDialog
      id: x
      input:
        binding:
          parameters: ="{""params"":[{""key"":""{Employee_ID}"",""value"":""" & Topic.EmployeeId & """}]}"
          scenarioName: msdyn_HRWorkdayHCMEmployeeUpdatePhone
      dialog: msdyn_copilotforemployeeselfservicedahr.topic.WorkdaySystemGetCommonExecution
      output:
        binding:
          isSuccess: Topic.isSuccess
    - kind: AnswerQuestionWithAI
      id: y
      autoSend: false
      variable: Topic.Reply
      userInput: =Topic.x
    - kind: HttpRequestAction
      id: z
      url: https://api.mycompany-hr.net/v1
      headers:
        Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456
'''
    rules = {f.rule for f in tg.check(bad, "da")}
    assert {"T01", "T04", "T08", "T09", "T10", "T11", "T13"} <= rules
    assert tg.check(tg.generate(_SPECS[1])["topic"], "cea") == []


def test_topic_gen_command_writes_topic_config_skill_and_tests_and_plan_routes_them():
    import essdoctor_cli

    with tempfile.TemporaryDirectory() as d:
        spec = Path(d) / "spec.json"
        spec.write_text(json.dumps(_SPECS[4]), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert essdoctor_cli.main(["topic-gen", str(spec), "--out", str(Path(d) / "t")]) == 0
        md = out.getvalue()
        files = sorted(p.name for p in (Path(d) / "t").iterdir())
        assert files == ["SKILL.md", "evalset.csv", "msdyn_employeeselfservicetemplateconfig.xml", "topic.yaml"]
        assert "```yaml" in md and '"Table": "sn_hr_core_case_payroll"' in md and "Passes every ESS topic rule" in md
        assert md.index("### Step 1: Add the topic") < md.index("### Step 2: Add the template configuration") \
            < md.index("### Files") < md.index("### What next")
        plan = io.StringIO()
        with contextlib.redirect_stdout(plan):
            assert essdoctor_cli.main(["plan", str(spec), str(Path(d) / "t" / "topic.yaml")]) == 0
        assert "| generate-workflows | `python3 scripts/essdoctor.py topic-gen" in plan.getvalue()
        assert "| generate-workflows | `python3 scripts/essdoctor.py topic-check" in plan.getvalue()


def test_shipped_text_keeps_its_symbols():
    # Edits piped through a console code page once turned these symbols into '?'.
    root = Path(__file__).resolve().parent.parent
    for p in (root / "plugin" / "skills").glob("*/SKILL.md"):
        assert "<version> ? <skill>" not in p.read_text(encoding="utf-8"), p
    cli = (root / "essdoctor_cli.py").read_text(encoding="utf-8")
    assert '" ? ".join' not in cli and "else '?'} |" not in cli.split("def cmd_plan")[0]
