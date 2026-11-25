"""Copilot Studio transcript X-ray and custom engine agent (CEA) handling in DevUI exports."""

from __future__ import annotations

import csv
import io
import json
import re

from ess_maker.doctor import devui, sorry, transcript


def _msg(role: int, text: str = "", name: str = "") -> dict:
    a = {"type": "message", "from": {"role": role}, "text": text}
    if name:
        a["name"] = name
    return a


def _doc(*activities: dict) -> dict:
    return {"activities": list(activities)}


def test_consent_card_is_the_cause_and_guid_topics_are_hidden():
    doc = _doc(
        {"type": "trace", "valueType": "DialogRedirect", "value": {"targetDialogId": "e88ce912-e196-44e5-9018-90bf5f1255e3"}},
        _msg(1, "Show my tickets"),
        {"type": "event", "valueType": "DynamicPlanReceived", "value": {"steps": ["contoso_hr.topic.GetUserTickets"]}},
        _msg(0, name="connectors/consentCard"),
    )

    d = transcript.diagnose(doc)

    assert d["consent"] is True
    assert d["stages"]["search"] == "failed"
    assert d["planned"] == ["GetUserTickets"]
    assert d["topics"] == []
    assert "sign in or allow a connection" in d["likely_cause"]
    assert sorry._transcript_item(d, "t").cause == "consent-required"


def test_empty_knowledge_search_and_failed_tool():
    empty = transcript.diagnose(_doc(
        _msg(1, "What is the parental leave policy?"),
        {"type": "event", "valueType": "UniversalSearchToolTraceData", "value": {"searchTerms": "parental leave", "fullResults": []}},
        _msg(0, "I couldn't find that."),
    ))
    failed = transcript.diagnose(_doc(
        _msg(1, "Open a case"),
        {"type": "event", "valueType": "DynamicPlanStepFinished",
         "value": {"taskDialogId": "contoso_hr.action.CreateCase", "state": "failed", "error": {"message": "HTTP 403"}}},
    ))

    assert empty["stages"]["search"] == "failed"
    assert empty["likely_cause"].startswith("Knowledge search ran but returned no results")
    assert failed["failed"] == ["CreateCase: HTTP 403"]
    assert sorry._transcript_item(failed, "t").cause == "action-failed"


def test_sessions_csv_and_question_pick():
    a = _doc(_msg(1, "How many vacation days do I have?"), _msg(0, "You have 12 days."))
    b = _doc(_msg(1, "Show my tickets"), _msg(0, name="connectors/consentCard"))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["SessionId", "Content"])
    w.writerow(["s1", json.dumps(a)])
    w.writerow(["s2", json.dumps(b)])

    docs = transcript.load(buf.getvalue())
    report = transcript.build(buf.getvalue(), question="show my tickets")

    assert len(docs) == 2
    assert transcript.pick(docs, "show my tickets") is docs[1]
    assert '"Show my tickets"' in report
    assert "2 transcripts in the file" in report


def test_sessions_download_rows_feed_sorry_triage():
    text = ("SessionID,StartDateTime,SessionOutcome,OutcomeReason,IsResolvedImplied,Turns,ChatTranscript,InitialUserMessage,TopicName\r\n"
            "s1,2026-11-24,Escalated,AgentTransferFromQuestion,,2,\"User says: what is a veo?; Bot says: I'm sorry, I couldn't find that.;\","
            "what is a veo?,Conversational boosting\r\n"
            "s2,2026-11-24,Abandoned,UserExit,,2,\"User says: xyz; Bot says: I'm not sure how to help with that.;\",xyz,Fallback\r\n")

    rows = transcript.load_sessions(text)
    items = [sorry._session_item(r, r["session"]) for r in rows]

    assert [r["question"] for r in rows] == ["what is a veo?", "xyz"]
    assert rows[0]["response"] == "I'm sorry, I couldn't find that."
    assert [i.cause for i in items] == ["cea-inside", "no-tool-match"]


def test_test_pane_snapshot_skips_commands_and_reads_instructions():
    import zipfile
    from pathlib import Path
    import tempfile
    import essdoctor_cli

    acts = [
        {"type": "message", "from": {"role": 1}, "text": "what is relocation benefit", "timestamp": 1},
        {"type": "event", "valueType": "UniversalSearchToolTraceData", "value": {"searchTerms": "relocation benefit", "fullResults": [1, 2]}, "timestamp": 2},
        {"type": "message", "from": {"role": 0}, "text": "Relocation covers moving costs.", "timestamp": 3},
        {"type": "message", "from": {"role": 1}, "text": "/debug conversationid", "timestamp": 4},
        {"type": "message", "from": {"role": 0}, "text": "Conversation ID: abc", "timestamp": 5},
    ]
    bot = "kind: BotDefinition\ncomponents:\n  - kind: GptComponent\n    displayName: Contoso HR\n    instructions: |-\n      #Core identity\n      You are an HR agent.\n"
    with tempfile.TemporaryDirectory() as d:
        z = Path(d) / "snapshot.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("dialog.json", json.dumps(acts))
            f.writestr("botContent.yml", bot)
        out = essdoctor_cli.main(["xray", str(z)])
    diag = transcript.diagnose({"activities": acts})

    assert out == 0
    assert diag["question"] == "what is relocation benefit"
    assert diag["response"] == "Relocation covers moving costs."
    assert diag["stages"]["search"] == "ok"


def test_snapshot_string_roles_pii_query_and_profile_hedge():
    ref = "[1]: https://contoso.sharepoint.com/Leave.aspx \"Leave.aspx\""
    acts = [
        {"type": "message", "from": {"role": "user"}, "text": "what is my leave policy"},
        {"type": "event", "valueType": "DynamicPlanStepTriggered", "value": {"taskDialogId": "P:UniversalSearchTool", "type": "KnowledgeSource"}},
        {"type": "event", "valueType": "DynamicPlanStepBindUpdate",
         "value": {"taskDialogId": "P:UniversalSearchTool",
                   "arguments": {"search_query": "Find Alex Doe's employee profile for alex@contoso.com, country and job level"}}},
        {"type": "event", "valueType": "UniversalSearchToolTraceData", "value": {"fullResults": []}},
        {"type": "event", "valueType": "DynamicPlanStepFinished",
         "value": {"taskDialogId": "P:UniversalSearchTool", "state": "completed",
                   "observation": {"search_result": {"search_results": [{"Name": "Leave.aspx"}, {"Name": "Benefits.aspx"}]}}}},
        {"type": "message", "from": {"role": "bot"},
         "text": f"I couldn't verify your country or job level from your employee record.\n\n{ref}\n\n{ref}"},
        {"type": "message", "from": {"role": "user"}, "text": "/debug conversationid"},
        {"type": "message", "from": {"role": "bot"}, "text": "Conversation ID: abc"},
    ]

    d = transcript.diagnose({"activities": acts})

    assert d["question"] == "what is my leave policy"
    assert d["response"].count(ref) == 1
    assert d["stages"]["search"] == "ok"
    assert d["stages"]["synthesis"] == "failed"
    assert "employee's own profile" in d["likely_cause"]
    assert any("name or email" in s for s in d["happened"])


def test_snapshot_bot_content_flags_empty_user_context_setup_topic():
    bot = ("kind: BotDefinition\ncomponents:\n"
           "  - kind: GptComponent\n    instructions: |-\n      'Country': {Global.ESS_UserContext_Country_Code}\n"
           "  - kind: DialogComponent\n    displayName: \"[Admin] - User Context - Setup\"\n"
           "    schemaName: contoso.topic.Setusercontext\n    dialog:\n      beginDialog:\n        kind: OnRedirect\n        id: main\n"
           "  - kind: DialogComponent\n    displayName: Greeting\n    dialog:\n      beginDialog:\n        kind: OnRecognizedIntent\n"
           "        actions:\n          - kind: SendActivity\n")
    acts = [
        {"type": "message", "from": {"role": "user"}, "text": "what is my leave policy"},
        {"type": "event", "valueType": "DynamicPlanStepBindUpdate",
         "value": {"taskDialogId": "P:UniversalSearchTool", "arguments": {"search_query": "employee profile country and job level"}}},
        {"type": "message", "from": {"role": "bot"}, "text": "I couldn't verify your country from your employee record."},
    ]

    report = transcript.build(json.dumps({"activities": acts}), snapshot=True, bot_yaml=bot)

    assert transcript.bot_findings(bot) == {"empty_context_topics": ["[Admin] - User Context - Setup"],
                                            "context_vars": ["ESS_UserContext_Country_Code"]}
    assert "The employee's details never loaded" in report
    assert '1. Set up the "[Admin] - User Context - Setup" topic' in report


def test_devui_custom_engine_turn_is_not_reported_as_ungrounded():
    export = {
        "conversationId": "c-1",
        "messages": [
            {"author": "user", "requestId": "r1", "text": "what is a veo?"},
            {"author": "bot", "requestId": "r1", "text": "A VEO is a voluntary exit offer.", "contentOrigin": "CustomEngineCopilot",
             "references": {"1-abc": {"targetLink": "https://contoso.sharepoint.com/sites/HR/SitePages/VEO.aspx"}}},
        ],
        "requests": [{"requestId": "r1", "telemetry": {"metrics": [
            {"serviceName": "CustomEngineCopilotMcsClient", "status": "Success", "latencyMilliseconds": 2400,
             "input": json.dumps({"SchemaName": "contoso_esscore"})},
        ]}}],
    }

    parsed = devui.parse(export)
    turn = parsed["turns"][0]
    report = devui.build(parsed, 0)

    assert turn["cea"]["schema"] == "contoso_esscore"
    assert turn["sources"] == ["VEO.aspx"]
    assert "custom engine agent" in report
    assert "FAILED HERE" not in report
    assert "in Copilot Studio" in report


def test_context_draft_lands_in_context_handling_section():
    import tempfile
    from pathlib import Path
    import essdoctor_cli

    instructions = ("#Core identity\nYou are an HR agent.\n\n#Context handling\n"
                    "Use 'Country': {Global.ESS_UserContext_Country_Code} in every search.\n\n#When unsure\nSay so.\n")
    intake = {"change_type": "context", "intent": "Ask when country is blank; no emails in searches",
              "subject": "country", "should_affect": ["what is my leave policy"], "must_not_affect": ["reset my password"]}
    with tempfile.TemporaryDirectory() as d:
        src, req, out = Path(d) / "current.txt", Path(d) / "intake.json", Path(d) / "draft.txt"
        src.write_text(instructions, encoding="utf-8")
        req.write_text(json.dumps(intake), encoding="utf-8")
        assert essdoctor_cli.main(["draft", str(src), "--intake", str(req), "--out", str(out)]) == 0
        lines = out.read_text(encoding="utf-8").splitlines()

    added = next(i for i, ln in enumerate(lines) if "user context values" in ln)
    assert lines.index("#Context handling") < added < lines.index("#When unsure")
    assert "never put names, emails or IDs in search queries" in lines[added]


def test_application_insights_spans_become_an_xray_report():
    import contextlib
    import tempfile
    from pathlib import Path
    import essdoctor_cli
    from ess_maker.doctor import appinsights

    cid = "aaaaa111-1a1a-1111-1aa1-a111111a11a1"
    common = {"gen_ai.conversation.id": cid, "microsoft.channel.name": "msteams", "gen_ai.request.model": "gpt-x"}
    rows = [
        ("2026-11-24T10:00:00Z", "dependency", "InvokeAgent", "OK", "True",
         {**common, "gen_ai.operation.name": "invoke_agent",
          "gen_ai.input.messages": json.dumps([{"role": "user", "parts": [{"type": "text", "content": "what is my leave policy"}]}])}),
        ("2026-11-24T10:00:01Z", "dependency", "ExecuteTool", "OK", "True",
         {**common, "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "workiqsharepoint:mcp_SharePointRemoteServer",
          "gen_ai.tool.call.arguments": json.dumps({"query": "leave policy alex@contoso.com"}),
          "gen_ai.tool.call.result": json.dumps({"results": [{"title": "Leave-Policy-USA.aspx", "url": "https://x/1"},
                                                              {"title": "Leave-Policy-UK.aspx", "url": "https://x/2"}]})}),
        ("2026-11-24T10:00:02Z", "dependency", "ExecuteTool", "ERROR", "False",
         {**common, "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "Workday-GetLeaveBalance",
          "Status.code": "2", "Status.message": "401 Unauthorized"}),
        ("2026-11-24T10:00:03Z", "dependency", "OutputMessages", "OK", "True",
         {**common, "gen_ai.operation.name": "output_messages",
          "gen_ai.output.messages": json.dumps([{"role": "assistant", "parts": [{"type": "text", "content": "I couldn't verify your country, so here is the base policy."}]}])}),
        ("2026-11-24T10:00:04Z", "customEvent", "ESS_UserContext", "", "",
         {"Message": "Country code is empty", "ConversationId": cid}),
        ("2026-11-24T10:00:05Z", "dependency", "InvokeAgent", "OK", "True",
         {"gen_ai.conversation.id": "other-conversation", "gen_ai.input.messages": "unrelated"}),
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp [UTC]", "itemType", "name", "resultCode", "success", "operation_Id", "session_Id", "customDimensions"])
    for ts, it, name, rc, ok, cd in rows:
        w.writerow([ts, it, name, rc, ok, "op", "", json.dumps(cd)])

    acts, info = appinsights.to_activities(buf.getvalue(), cid)
    d = transcript.diagnose({"activities": acts})
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "logs.csv"
        f.write_text(buf.getvalue(), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert essdoctor_cli.main(["xray", str(f), "--conversation", cid]) == 0
    report = out.getvalue()

    assert appinsights.is_export(buf.getvalue())
    assert d["question"] == "what is my leave policy"
    assert d["response"].startswith("I couldn't verify your country")
    assert d["failed"] == ["Workday-GetLeaveBalance: 401 Unauthorized"]
    assert any("2 result(s)" in s and "Leave-Policy-USA.aspx" in s for s in d["searches"])
    assert any("name or email" in h for h in d["happened"])
    assert info["logger"] == ["ESS_UserContext: Country code is empty"]
    assert "unrelated" not in json.dumps(acts)
    assert "Conversation Investigation (Copilot Studio Application Insights logs)" in report
    assert "Logged by a topic: ESS_UserContext: Country code is empty" in report


def test_agent_level_custom_events_use_real_field_names():
    from ess_maker.doctor import appinsights

    cid = "7b9bd8c2-0000-0000-0000-000000000000"
    rows = [
        ("BotMessageReceived", {"conversationId": cid, "DesignMode": "True", "type": "event", "text": ""}),
        ("TopicStart", {"conversationId": cid, "DesignMode": "True", "TopicName": "contoso.topic.ConversationStart"}),
        ("BotMessageReceived", {"conversationId": cid, "DesignMode": "True", "type": "message", "text": "what is my leave policy"}),
        ("TopicAction", {"conversationId": cid, "DesignMode": "True", "TopicId": "contoso.topic.System-UserContext-Validate",
                         "TopicName": "contoso.topic.System-UserContext-Validate", "Kind": "ConditionGroup", "ActionId": "c1"}),
        ("UserContextLog", {"conversationId": cid, "ConversationId": cid, "Message": "User context validation start"}),
        ("BotMessageSend", {"conversationId": cid, "DesignMode": "True", "text": "Here is the leave policy."}),
        ("BotMessageSend", {"conversationId": "someone-else", "text": "other conversation"}),
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp [UTC]", "itemType", "name", "operation_Id", "session_Id", "customDimensions"])
    for i, (name, cd) in enumerate(rows):
        w.writerow([f"2026-11-25T03:40:{i:02d}Z", "customEvent", name, "op", "", json.dumps(cd)])

    acts, info = appinsights.to_activities(buf.getvalue(), cid)
    d = transcript.diagnose({"activities": acts})

    assert d["question"] == "what is my leave policy"
    assert d["response"] == "Here is the leave policy."
    assert "System-UserContext-Validate" in d["topics"]
    assert info["test_pane"] is True and info["steps"] == 1 and info["spans"] == 0
    assert info["logger"] == ["UserContextLog: User context validation start"]
    assert "other conversation" not in json.dumps(acts)


def test_business_apps_transcript_rows_pick_the_question():
    def row(q, a, when):
        content = json.dumps({"activities": [_msg(1, q), _msg(0, a)]})
        return {"conversationtranscriptid": when, "conversationstarttime": when, "content": content}

    rows = json.dumps([row("Show my tickets", "Here are your tickets.", "2026-11-25T05:00:00Z"),
                       row("what is my leave policy", "I couldn't verify your country.", "2026-11-25T04:00:00Z")])

    report = transcript.build(rows, question="what is my leave policy")

    assert '"what is my leave policy"' in report
    assert "2 transcripts in the file" in report
    assert "I couldn't verify your country." in report


def test_eval_triage_report_flags_required_line_missing_from_passing_answers():
    import contextlib
    import tempfile
    from pathlib import Path
    import essdoctor_cli

    instructions = ("#Core identity\nYou are an HR agent.\n"
                    "Never guess or use KB search as a fallback for personal data or HR case operations.\n"
                    "#Formatting\nEnd every response with: \"The agent is in preview, so verify the results it provides.\"\n")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["question", "expectedResponse", "actualResponse", "result", "explanation"])
    w.writerow(["What is the parental leave policy?", "Policy", "Parental leave is 16 weeks.", "Pass", "ok"])
    w.writerow(["How many vacation days do I have left?", "Balance",
                "I couldn't find a definitive answer. [1](https://x/Benefits.aspx \"Benefits.aspx\")", "Fail", "no balance"])
    with tempfile.TemporaryDirectory() as d:
        src, res = Path(d) / "current.txt", Path(d) / "results.csv"
        src.write_text(instructions, encoding="utf-8")
        res.write_text(buf.getvalue(), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert essdoctor_cli.main(["eval-triage", str(res), "--instructions", str(src)]) == 0
    report = out.getvalue()

    assert "**1 of 2 passed**" in report
    assert "A required line is missing from 2 of 2 responses, including 1 that passed" in report
    assert "### At a glance" in report and "| Scenario doesn't exist | 1 | #1 |" in report
    assert "### Who fixes what" in report


def test_draft_writes_importable_evalset_with_specific_expectations():
    import contextlib
    import tempfile
    from pathlib import Path
    import essdoctor_cli

    instructions = "#Core identity\nYou are an HR agent.\n\n#Formatting rules\n- Use short paragraphs.\n"
    intake = {"change_type": "disclaimer", "intent": "Add a preview disclaimer",
              "disclaimer_text": "This agent is in preview; verify the results.",
              "should_affect": ["What is the parental leave policy?"], "must_not_affect": ["Reset my password"]}
    with tempfile.TemporaryDirectory() as d:
        src, req, out = Path(d) / "current.txt", Path(d) / "intake.json", Path(d) / "outputs" / "draft.txt"
        src.write_text(instructions, encoding="utf-8")
        req.write_text(json.dumps(intake), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert essdoctor_cli.main(["draft", str(src), "--intake", str(req), "--out", str(out)]) == 0
        rows = list(csv.reader(io.StringIO((out.parent / "evalset.csv").read_text(encoding="utf-8"))))

    assert rows[0] == ["Question", "Expected response"]
    by_q = dict(rows[1:])
    assert "This agent is in preview; verify the results." in by_q["What is the parental leave policy?"]
    assert "Who is my manager?" in by_q
    assert len(rows) - 1 <= 100 and all(len(q) <= 1000 and len(e) <= 1000 for q, e in rows[1:])
    assert "### Evaluation test set" in buf.getvalue()


def test_check_report_orders_findings_and_splits_practice_gaps():
    import contextlib
    import essdoctor_cli

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert essdoctor_cli.main(["check", "samples/inputs/contoso_instructions.txt", "--persona", "hr", "--no-evalset"]) == 0
    report = out.getvalue()

    assert report.index("### Fix first") < report.index("### ESS core blocks") < report.index("### Authoring practices")
    first = report.split("### Fix first", 1)[1].split("\n2. ", 1)[0]
    assert "🔴" in first and "**Employees see:**" in first and "**Fix:**" in first
    assert "**Proposed change:**" in report
    practices_part = report.split("### Authoring practices", 1)[1]
    assert "```diff\n" in practices_part and "\n   + " in practices_part
    assert not re.search(r"`(L\d|BP)-[A-Z]", report) and "Line 1065" not in report
    assert "Copilot Studio count" in report


def test_draft_report_shows_summary_checks_and_test_table():
    import contextlib
    import tempfile
    from pathlib import Path
    import essdoctor_cli

    instructions = "#Core identity\nYou are an HR agent.\n\n#Routing\n- Policy questions: KB search.\n"
    intake = {"change_type": "routing", "intent": "VEO questions use the CAP source", "subject": "VEO",
              "source_name": "CAP site", "mode": "only",
              "should_affect": ["What is VEO?"], "must_not_affect": ["How much PTO do I have?"]}
    with tempfile.TemporaryDirectory() as d:
        src, req = Path(d) / "current.txt", Path(d) / "intake.json"
        src.write_text(instructions, encoding="utf-8")
        req.write_text(json.dumps(intake), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert essdoctor_cli.main(["draft", str(src), "--intake", str(req), "--out", str(Path(d) / "draft.txt")]) == 0
    report = out.getvalue()

    assert "| Route a subject to a knowledge source | Routing |" in report
    assert "Also put this in the knowledge source's description" in report
    assert "### Check before publishing" in report
    assert "| What is VEO? | How much PTO do I have? |" in report


def test_draft_over_limit_is_shortened_automatically_keeping_the_new_rule():
    import contextlib
    import tempfile
    from pathlib import Path
    import essdoctor_cli

    body = "#Core identity\nYou are an HR agent.\n\n#Routing\n" + "".join(
        f"- Policy rule {i}: do not answer from memory, do not guess, and do not use general knowledge in order to "
        f"answer questions about topic {i}.   \n\n\n" for i in range(55))
    disclaimer = "This agent is in preview; verify the results it gives you."
    with tempfile.TemporaryDirectory() as d:
        src, req, out = Path(d) / "c.txt", Path(d) / "i.json", Path(d) / "o" / "d.txt"
        src.write_text(body, encoding="utf-8")
        req.write_text(json.dumps({"change_type": "disclaimer", "intent": "add disclaimer", "disclaimer_text": disclaimer}),
                       encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert essdoctor_cli.main(["draft", str(src), "--intake", str(req), "--out", str(out)]) == 0
        text = out.read_text(encoding="utf-8")

    assert "Shortened automatically:" in buf.getvalue() and "10% buffer" in buf.getvalue()
    assert disclaimer in text
    assert essdoctor_cli.studio_len(text) <= 8000


def test_sorry_triage_reads_snapshots_and_app_insights_exports():
    import contextlib
    import tempfile
    import zipfile
    from pathlib import Path
    import essdoctor_cli

    bot = ("kind: BotDefinition\ncomponents:\n"
           "  - kind: GptComponent\n    instructions: |-\n      #Core identity\n      'Country': {Global.ESS_UserContext_Country_Code}\n"
           "  - kind: DialogComponent\n    displayName: \"[Admin] - User Context - Setup\"\n"
           "    schemaName: contoso.topic.Setusercontext\n    dialog:\n      beginDialog:\n        kind: OnRedirect\n        id: main\n")
    hedge = [{"type": "message", "from": {"role": "user"}, "text": "what is my leave policy"},
             {"type": "event", "valueType": "DynamicPlanStepFinished",
              "value": {"taskDialogId": "P:UniversalSearchTool", "state": "completed",
                        "observation": {"search_result": {"search_results": [{"Name": "Leave.aspx"}]}}}},
             {"type": "message", "from": {"role": "bot"}, "text": "I couldn't verify your country from your employee record."}]
    error = [{"type": "message", "from": {"role": "user"}, "text": "something unsafe"},
             {"type": "message", "from": {"role": "bot"}, "text": "Sorry, something went wrong. Error code: SystemError."},
             {"type": "trace", "valueType": "ErrorCode", "value": {"ErrorCode": "SystemError"}}]
    rows = io.StringIO()
    w = csv.writer(rows)
    w.writerow(["timestamp [UTC]", "itemType", "name", "operation_Id", "session_Id", "customDimensions"])
    for i, (cid, name, text) in enumerate([("c-1", "BotMessageReceived", "Show my tickets"),
                                           ("c-1", "BotMessageSend", "I'm sorry, I couldn't find that."),
                                           ("c-2", "BotMessageReceived", "What is PTO?"),
                                           ("c-2", "BotMessageSend", "PTO means paid time off.")]):
        w.writerow([f"2026-11-25T03:40:{i:02d}Z", "customEvent", name, "op", "",
                    json.dumps({"conversationId": cid, "type": "message", "text": text})])
    with tempfile.TemporaryDirectory() as d:
        bundle = Path(d) / "evidence.zip"
        with zipfile.ZipFile(bundle, "w") as z:
            for n, acts in (("leave.zip", hedge), ("error.zip", error)):
                inner = io.BytesIO()
                with zipfile.ZipFile(inner, "w") as s:
                    s.writestr("dialog.json", json.dumps({"activities": acts}))
                    s.writestr("botContent.yml", bot)
                z.writestr(n, inner.getvalue())
        logs = Path(d) / "logs.csv"
        logs.write_text(rows.getvalue(), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert essdoctor_cli.main(["sorry-triage", str(bundle), str(logs)]) == 0
    report = out.getvalue()

    assert "4 turn(s) analyzed; 3 ended in a sorry" in report
    assert "Employee details weren't loaded" in report
    assert "Copilot Studio returned a system error" in report
    assert "Skipped" not in report and "MISSING" not in report


def test_agent_audit_finds_multi_agent_gaps():
    from ess_maker.doctor import audit

    def ks(name, source):
        return (f"  - kind: KnowledgeSourceComponent\n    displayName: {name}\n    schemaName: x.knowledge.{name}\n"
                f"    description: This knowledge source provides information found in {name} SharePoint.\n"
                f"    state: Active\n    configuration:\n      source:\n{source}\n")

    hub = ("kind: BotDefinition\nentity:\n  schemaName: contoso_core\n  configuration:\n    aISettings:\n"
           "      contentModeration: Low\n      optInUseLatestModels: true\ncomponents:\n"
           "  - kind: GptComponent\n    displayName: Contoso Hub\n    schemaName: contoso_core.gpt.default\n"
           "    metadata:\n      instructions: |-\n        # Core identity\n        You route employees to HR or IT. Child agents own domain answers; do not answer them yourself.\n"
           "      gptCapabilities: {}\n"
           + ks("Benefits", "        kind: SharePointSearchSource\n        site: https://contoso.sharepoint.com/sites/benefits")
           + "  - kind: DialogComponent\n    displayName: Contoso HR\n    schemaName: contoso_core.InvokeConnectedAgentTaskAction.ContosoHR\n"
             "    state: Active\n    dialog:\n      kind: TaskDialog\n      modelDescription: Get personalized HR support, simply ask.\n"
             "      action:\n        kind: InvokeConnectedAgentTaskAction\n        botSchemaName: contoso_hr\n"
             "        historyType:\n          kind: ConversationHistory\n")
    child = ("kind: BotDefinition\nentity:\n  schemaName: contoso_hr\n  configuration:\n    isAgentConnectable: false\n"
             "    aISettings:\n      contentModeration: Medium\n      optInUseLatestModels: false\ncomponents:\n"
             "  - kind: GptComponent\n    displayName: Contoso HR\n    schemaName: contoso_hr.gpt.default\n"
             "    metadata:\n      instructions: |-\n        # Core identity\n        You are an HR agent.\n      gptCapabilities: {}\n"
             + ks("Benefits", "        kind: SharePointSearchSource\n        site: https://contoso.sharepoint.com/sites/benefits")
             + ks("Benefits-401k", "        kind: SharePointSearchSource\n        site: https://contoso.sharepoint.com/sites/benefits/SitePages/401k.aspx")
             + ks("SNOW-A", "        kind: GraphConnectorSearchSource\n        connectionName: ServiceNowKB")
             + ks("SNOW-B", "        kind: GraphConnectorSearchSource\n        connectionName: ServiceNowKB"))

    r = audit.audit([audit.parse(hub, "hub.yml"), audit.parse(child, "hr.yml")])
    ids = {g.rule_id for g in r["gaps"]}

    assert r["hub"].name == "Contoso Hub" and r["children"][0][1].name == "Contoso HR"
    assert {"A01-HUB-KNOWLEDGE", "A02-DUP-KNOWLEDGE-HUB-CHILD", "A04-DUP-KNOWLEDGE-IN-AGENT", "A05-OVERLAPPING-KNOWLEDGE",
            "A12-CHILD-NOT-CONNECTABLE", "A13-HISTORY-TO-EVERY-CHILD", "A14-CHILD-DESCRIPTION-VAGUE",
            "A16-HUB-DELEGATION-RULES", "A17-MODERATION-BELOW-TEMPLATE", "A18-MODEL-SETTINGS-DIFFER"} <= ids
    assert "A06-KNOWLEDGE-OVER-25" not in ids
    change = {g.rule_id: g.change for g in r["gaps"]}
    assert change["A17-MODERATION-BELOW-TEMPLATE"][0].startswith("- ") and change["A17-MODERATION-BELOW-TEMPLATE"][1].startswith("+ ")
    assert change["A16-HUB-DELEGATION-RULES"][0] == "+ # Delegation"
    assert all(ln.startswith("- ") for ln in change["A04-DUP-KNOWLEDGE-IN-AGENT"])


def test_agent_audit_reads_alm_exports_with_config_values():
    import contextlib
    import tempfile
    import zipfile
    from pathlib import Path
    import essdoctor_cli

    agent = ("kind: BotDefinition\nentity:\n  displayName: ${config.values[\"botName\"]}\n  schemaName: contoso_hr\n"
             "  configuration:\n    aISettings:\n      contentModeration: Low\ncomponents:\n"
             "  - kind: GptComponent\n    schemaName: contoso_hr.gpt.default\n    metadata:\n      instructions: |-\n"
             "        # Core identity\n        You are an HR agent.\n      gptCapabilities: {}\n"
             "  - kind: KnowledgeSourceComponent\n    displayName: Policies\n    schemaName: contoso_hr.knowledge.Policies\n"
             "    configuration:\n      source:\n        kind: SharePointSearchSource\n"
             "        site: ${config.values[\"knowledge:contoso_hr.knowledge.Policies:site\"]}\n")
    config = {"values": {"botName": "Contoso HR", "knowledge:contoso_hr.knowledge.Policies:site": "https://contoso.sharepoint.com/sites/hr"}}
    with tempfile.TemporaryDirectory() as d:
        z = Path(d) / "export.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("Plugin/package.json", "{}")
            f.writestr("Plugin/Agents/contoso_hr/agent.yml", agent)
            f.writestr("Plugin/Agents/contoso_hr/app.config.dev.json", json.dumps(config))
        files = essdoctor_cli._agent_files([str(z)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert essdoctor_cli.main(["audit", str(z)]) == 0

    assert len(files) == 1 and "https://contoso.sharepoint.com/sites/hr" in files[0][1]
    assert "| Contoso HR |" in out.getvalue() and "Content moderation is Low" in out.getvalue()


def test_what_changed_uses_colored_diff_blocks():
    import contextlib
    import tempfile
    from pathlib import Path
    import essdoctor_cli

    with tempfile.TemporaryDirectory() as d:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert essdoctor_cli.main(["fix", "samples/inputs/contoso_instructions.txt", "--out", str(Path(d) / "f.txt"),
                                       "--no-evalset"]) == 0
    changed = out.getvalue().split("### What changed", 1)[1]

    assert "```diff\n- " in changed and "\n+ " in changed
    assert "**✏️ Changed: Routing decision tree**" in changed
    assert "\u00e2\u2020\u2019" not in out.getvalue() and "\u00e2\u20ac\u00a6" not in out.getvalue()


_SESSIONS_CSV = (
    "SessionId,StartDateTime(UTC),SessionOutcome,OutcomeReason,IsResolvedImplied,Turns,ChatTranscript,InitialUserMessage,TopicName,TopicId,Channel,CSAT,Comments\n"
    "s1,11/25/2026 5:17:33 PM,Abandoned,UserExit,,2,User says: what is perks+;Agent says: Sorry I am not able to find a related topic. Can you rephrase and try again?;,what is perks+,,,m365copilot,,\n"
    "s2,11/25/2026 7:44:38 AM,Resolved,Resolved,true,4,User says: what is my leave policy;Agent says: <div><m-official> **Your leave policy**I found leave policies but I can’t reliably determine which one applies because your profile does not provide your country/office or tenure.For example...;User says: /share;Agent says: Sorry I am not able to find a related topic. Can you rephrase and try again?;,what is my leave policy,[System] - Response Preparation,x,m365copilot,,\n"
    "s3,11/25/2026 5:23:53 PM,Resolved,Resolved,true,2,User says: how to replace my laptop;Agent says: <div> Open a ticket in the Support Portal. [1];,how to replace my laptop,,,m365copilot,,\n")


def test_sessions_download_gets_a_verdict_per_session():
    from ess_maker.doctor import sorry, transcript

    rows = transcript.load_sessions(_SESSIONS_CSV)
    kinds = {r["session"]: transcript.classify_session(r)["kind"] for r in rows}
    assert kinds == {"s1": "fallback", "s2": "generic", "s3": "ok"}
    s2 = next(r for r in rows if r["session"] == "s2")
    assert s2["start"].startswith("11/25/2026") and "<div>" not in s2["first_response"]
    assert "country/office or tenure" in transcript.classify_session(s2)["missing"]
    r = sorry.triage([("session", row["session"], row) for row in rows])
    assert {i.conversation: i.cause for i in r["items"] if i.sorry} == {"s1": "no-tool-match", "s2": "context-missing"}


def test_sessions_xray_report_orders_by_time_and_proposes_a_diff():
    import contextlib
    import tempfile
    from pathlib import Path
    import essdoctor_cli

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "Sessions.csv"
        p.write_text(_SESSIONS_CSV, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert essdoctor_cli.main(["xray", str(p)]) == 0
    md = out.getvalue()
    assert md.startswith("## Sessions X-ray")
    assert md.index("what is my leave policy") < md.index("what is perks+")
    assert '"perks+"' in md and "```diff\n" in md and "Command typed in the chat" in md


def test_sorry_triage_counts_a_session_once_when_its_transcript_is_uploaded():
    import contextlib
    import json as _json
    import tempfile
    from pathlib import Path
    import essdoctor_cli

    doc = {"activities": [
        {"type": "message", "from": {"role": 1}, "text": "what is perks+"},
        {"type": "message", "from": {"role": 0}, "text": "Sorry I am not able to find a related topic. Can you rephrase and try again?"}]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "Sessions.csv").write_text(_SESSIONS_CSV, encoding="utf-8")
        (Path(d) / "t.json").write_text(_json.dumps([{"content": _json.dumps(doc)}]), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert essdoctor_cli.main(["sorry-triage", str(Path(d) / "Sessions.csv"), str(Path(d) / "t.json")]) == 0
    assert "3 turn(s) analyzed; 2 ended" in out.getvalue()


def test_check_skips_core_blocks_for_cea_and_fix_takes_finding_numbers():
    import contextlib
    import tempfile
    from pathlib import Path
    import essdoctor_cli

    text = ("# Core identity\nYou are an HR assistant for Contoso employees.\n"
            "When searching, always append the user's job level {Global.JobLevel} to the query.\n")
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "i.txt"
        src.write_text(text, encoding="utf-8")
        reports = {}
        for kind in ("da", "cea"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert essdoctor_cli.main(["check", str(src), "--persona", "hr", "--agent-type", kind, "--no-evalset"]) == 0
            reports[kind] = out.getvalue()
        assert "ESS core block missing" in reports["da"] and "ESS core block missing" not in reports["cea"]
        assert "\n1. **" in reports["cea"] and not re.search(r"`(L\d|BP)-", reports["cea"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert essdoctor_cli.main(["fix", str(src), "--rules", "1", "--agent-type", "cea", "--out", str(Path(d) / "f.txt"),
                                       "--no-evalset"]) == 0
        assert "Applied 1 fix(es)" in out.getvalue()
