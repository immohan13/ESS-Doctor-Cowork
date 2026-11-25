"""Parity tests for ess_maker.doctor.devmode, ported from ConversationAnalyzerTests.cs (DevModeCard_* and dev-mode detection)."""

from __future__ import annotations

import json

from ess_maker.doctor import devmode


def test_devmode_card_action403_fails_search_stage_with_backend_refusal():
    card = (
        "Agent metadata\nAgent ID: T_contoso.ess.hr\nCapabilities\n- OneDriveAndSharePoint\nActions\n- id: ServiceNowHRSD\n"
        "Matched functions\n- ServiceNowHRSD.createCase\nSelected functions for execution\n- ServiceNowHRSD.createCase\n"
        "Executed actions\n- ServiceNowHRSD.createCase\n  Status: Failed\n  Latency: 412 ms\n"
        "  Request: POST https://contoso.service-now.com/api/sn_hr_core/case\n  Response status: 403 Forbidden"
    )

    assert devmode.looks_like_devmode(card)
    r = devmode.diagnose(card, "Open a case about my payslip")

    assert r["source"] == "devmode"
    assert r["stages"]["planner"] == "ok"
    assert r["stages"]["search"] == "failed"
    assert "403" in r["likely_cause"]


def test_devmode_card_no_functions_selected_fails_planner_stage():
    card = (
        "Agent metadata\nAgent ID: T_contoso.ess.hr\nMatched functions\nNo matched functions\n"
        "Selected functions for execution\nNo functions selected for execution\nExecution details\nNo function execution details"
    )

    r = devmode.diagnose(card, "How many vacation days do I have?", "I couldn't find any information about that.")

    assert r["stages"]["planner"] == "failed"
    assert r["stages"]["search"] == "not run"


def test_devmode_card_knowledge_search_with_zero_results_fails_search_stage():
    card = (
        "Agent metadata\nAgent ID: T_contoso.ess.hr\nCapabilities\n- OneDriveAndSharePoint\nExecuted capabilities\n"
        "- OneDriveAndSharePoint\n  Status: Succeeded\n  Search text: parental leave policy Germany\n  Results: 0"
    )

    r = devmode.diagnose(card)

    assert r["stages"]["search"] == "failed"
    assert "0 results" in r["likely_cause"]


def test_unrecognised_or_empty_evidence_is_not_devmode():
    assert not devmode.looks_like_devmode("hello there")
    assert not devmode.looks_like_devmode("   ")
    try:
        devmode.diagnose("   ")
    except ValueError:
        pass
    else:
        raise AssertionError("empty evidence should raise ValueError")


def test_json_card_is_flattened_and_parsed():
    card = json.dumps(
        {
            "agentMetadata": {"agentId": "U_maker.ess"},
            "executedActions": [
                {"functionName": "Workday.getBalance", "status": "Failed", "httpStatus": 401, "url": "https://wd5.myworkday.com/x"}
            ],
        }
    )

    assert devmode.looks_like_devmode(card)
    r = devmode.diagnose(card)

    assert r["key_details"]["agentScope"] == "User-scoped (sideloaded)"
    assert r["card"]["exec_actions"][0]["name"] == "Workday.getBalance"
    assert r["card"]["exec_actions"][0]["http"] == 401
    assert r["stages"] == {"planner": "ok", "search": "failed", "synthesis": "unknown", "render": "unknown"}
    assert r["likely_cause"].startswith("The Workday.getBalance action reached wd5.myworkday.com but the call was rejected")


def _developer_card(titles: list[str], extract: str) -> str:
    hits = [{"resourceType": "File", "rank": i, "extracts": [{"text": extract}], "resourceMetadata": {"title": t}}
            for i, t in enumerate(titles)]
    return json.dumps({
        "capabilitiesDeveloperInfo": {
            "enabledCapabilities": [{"capabilityName": "OneDriveAndSharePoint", "scopes": [{"Url": "https://contoso.sharepoint.com/sites/HR"}]}],
            "capabilityExecutions": [{"name": "OneDriveAndSharePoint", "status": 1,
                                      "additionalDebugInfo": {"queryString": "", "totalResultCount": len(hits), "retrievalHits": hits}}],
        },
        "pluginDeveloperInfo": {"enabledPlugins": [], "matchedFunctionCandidates": [],
                                "functionsSelectedForInvocation": [], "functionExecutions": []},
        "agentMetaData": {"agentId": "", "agentVersion": "draft", "conversationId": "c-1", "requestId": "r-1", "agentName": "Contoso HR"},
    })


def test_developer_json_card_with_question_header_flags_unrelated_results():
    card = "Question: what is the relocation benefit\nResponse: I couldn't find that.\n" + _developer_card(
        ["Pay-Trends-2025", "Pay-Trends-2025", "Compensation-Guide"], "Salary bands and compensation reviews.")

    q, resp, _ = devmode.split_header(card)
    r = devmode.diagnose(card, q, resp)

    assert q == "what is the relocation benefit"
    assert r["card"]["agent_name"] == "Contoso HR"
    assert r["card"]["capabilities"] == ["OneDriveAndSharePoint"]
    assert r["card"]["no_actions"] is True
    assert r["card"]["exec_caps"][0]["results"] == 3
    assert r["card"]["exec_caps"][0]["hits"] == ["Pay Trends 2025 (2)", "Compensation Guide"]
    assert r["stages"]["search"] == "failed"
    assert "relocation" in r["likely_cause"]


def test_developer_json_card_with_matching_results_has_no_failure():
    card = _developer_card(["Relocation-Policy"], "Relocation benefit covers moving costs.")

    r = devmode.diagnose(card, "what is the relocation benefit")

    assert r["stages"]["planner"] == "ok"
    assert r["stages"]["search"] == "ok"
    assert r["likely_cause"].startswith("No failure was found")


def test_hedged_answer_fails_synthesis_and_report_quotes_why():
    from ess_maker.doctor import devmode_report

    card = _developer_card(["Leave-Policy-USA", "Leave-Policy-UK"], "Annual leave is 20 days.")
    resp = "I found leave policies, but I couldn't verify your country from your employee record. For example, the USA policy gives 20 days."

    r = devmode.diagnose(card, "what is my leave policy", resp)
    report = devmode_report.build(card, question="what is my leave policy", response=resp)

    assert r["stages"]["search"] == "ok"
    assert r["stages"]["synthesis"] == "failed"
    assert 'the answer hedged instead of answering. It said: "I found leave policies, but I couldn\'t verify your country from your employee record."' in report
