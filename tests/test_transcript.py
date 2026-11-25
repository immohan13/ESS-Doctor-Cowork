"""Copilot Studio transcript X-ray and custom engine agent (CEA) handling in DevUI exports."""

from __future__ import annotations

import csv
import io
import json

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
