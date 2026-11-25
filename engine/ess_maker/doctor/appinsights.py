"""Copilot Studio Application Insights telemetry for one conversation (custom engine agents).

Two telemetry tiers are read:

- Environment-level telemetry (preview): OpenTelemetry spans in the ``dependencies`` table. ``InvokeAgent`` carries
  the user prompt (``gen_ai.input.messages``), ``ExecuteTool`` each tool call with ``gen_ai.tool.call.arguments`` and
  ``gen_ai.tool.call.result``, and ``OutputMessages`` the reply (``gen_ai.output.messages``). Turns of one conversation
  share ``gen_ai.conversation.id``.
- Agent-level telemetry: ``customEvents`` BotMessageReceived and BotMessageSend (``text``), TopicStart and TopicAction
  (``TopicName``/``TopicId``, and ``Kind`` and ``ActionId`` per node), and custom events from topics (the ESS logger
  topics log ``Message``). Rows carry ``conversationId`` and ``DesignMode`` in customDimensions. Knowledge searches and
  tool calls aren't logged at this level.

The rows are turned into Copilot Studio-style activities so ``transcript.diagnose`` can explain them.
"""

from __future__ import annotations

import csv
import io
import json
import re

QUERY = """let conversationId = "{cid}";
let lookback = ago({days}d);
union isfuzzy=true
    (dependencies
        | where timestamp > lookback
        | where tostring(customDimensions["gen_ai.conversation.id"]) startswith conversationId),
    (customEvents
        | where timestamp > lookback
        | where tostring(customDimensions["conversationId"]) == conversationId or session_Id == conversationId)
| project timestamp, itemType, name, resultCode, success, operation_Id, session_Id, customDimensions
| order by timestamp asc"""

HOWTO = ("In the Azure portal, open the Application Insights resource the agent sends telemetry to, select Logs, switch "
         "the editor to KQL mode, paste the query and run it. Then select Export > CSV (all columns) and upload the file.")

_SEARCH_TOOL = re.compile(r"search|sharepoint|knowledge|universalsearch|graphconnector|dataverse", re.I)
_TITLE_KEYS = ("Name", "name", "title", "Title", "fileName", "FileName", "documentTitle")


def _cd(row: dict) -> dict:
    raw = row.get("customDimensions") or row.get("customDimensions [JSON]") or ""
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw) if raw else {}
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _norm(row: dict) -> dict:
    """Azure portal CSV headers carry suffixes such as 'timestamp [UTC]'; strip them."""
    return {re.sub(r"\s*\[.*?\]\s*$", "", k or "").strip(): v for k, v in row.items()}


def _rows(text: str) -> list[dict]:
    s = text.lstrip("\ufeff").lstrip()
    if s.startswith("[") or s.startswith("{"):
        try:
            obj = json.loads(s)
        except ValueError:
            return []
        if isinstance(obj, dict):
            # Log Analytics API shape: {"tables":[{"columns":[{"name":..}], "rows":[[..]]}]}
            tables = obj.get("tables") or []
            if tables:
                cols = [c.get("name") for c in tables[0].get("columns") or []]
                return [_norm(dict(zip(cols, r))) for r in tables[0].get("rows") or []]
            return []
        return [_norm(r) for r in obj if isinstance(r, dict)]
    try:
        return [_norm(r) for r in csv.DictReader(io.StringIO(s))]
    except csv.Error:
        return []


def is_export(text: str) -> bool:
    head = text.lstrip("\ufeff")[:4000]
    if not re.search(r"customDimensions", head):
        return False
    return bool(re.search(r"gen_ai\.|InvokeAgent|ExecuteTool|OutputMessages|BotMessage|TopicStart|TopicAction|[Dd]esignMode",
                          text[:200000]))


def _json(v):
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v) if isinstance(v, str) and v.strip()[:1] in "[{" else v
    except ValueError:
        return v


def _message_text(v) -> str:
    """gen_ai.input/output.messages: JSON array of {role, parts:[{content, type}]}."""
    msgs = _json(v)
    if isinstance(msgs, str):
        return msgs
    out = []
    for m in msgs if isinstance(msgs, list) else [msgs]:
        if not isinstance(m, dict):
            continue
        for p in m.get("parts") or []:
            c = p.get("content") if isinstance(p, dict) else p
            if isinstance(c, str) and c.strip():
                out.append(c.strip())
        if isinstance(m.get("content"), str):
            out.append(m["content"].strip())
    return "\n".join(out)


def _titles(result) -> list[str]:
    found: list[str] = []

    def walk(x, depth=0):
        if depth > 6 or len(found) >= 50:
            return
        if isinstance(x, dict):
            t = next((x[k] for k in _TITLE_KEYS if isinstance(x.get(k), str) and x[k].strip()), None)
            if t and ("url" in {k.lower() for k in x} or "text" in {k.lower() for k in x} or "content" in {k.lower() for k in x}):
                found.append(t.strip())
                return
            for v in x.values():
                walk(_json(v), depth + 1)
        elif isinstance(x, list):
            for v in x:
                walk(_json(v), depth + 1)

    walk(_json(result))
    return found


def _query_of(args) -> str:
    a = _json(args)
    if isinstance(a, dict):
        for k in ("search_query", "query", "searchQuery", "q", "search_keywords", "keywords"):
            if isinstance(a.get(k), str) and a[k].strip():
                return a[k].strip()
    return a.strip() if isinstance(a, str) else ""


def to_activities(text: str, conversation_id: str = "") -> tuple[list[dict], dict]:
    rows = _rows(text)
    acts: list[dict] = []
    info = {"rows": len(rows), "spans": 0, "events": 0, "conversation": conversation_id, "channel": "", "model": "",
            "logger": []}
    for r in sorted(rows, key=lambda r: str(r.get("timestamp") or "")):
        cd = _cd(r)
        name = str(r.get("name") or "")
        op = str(cd.get("gen_ai.operation.name") or "").lower()
        conv = str(cd.get("gen_ai.conversation.id") or cd.get("conversationId") or cd.get("ConversationId")
                   or r.get("session_Id") or "")
        if conversation_id and conv and not conv.startswith(conversation_id) and conversation_id not in json.dumps(cd):
            continue
        base = {"conversation": {"id": conv or conversation_id}, "timestamp": r.get("timestamp")}
        info["channel"] = info["channel"] or str(cd.get("microsoft.channel.name") or cd.get("channelId") or "")
        info["model"] = info["model"] or str(cd.get("gen_ai.request.model") or "")
        failed = str(r.get("success")).lower() == "false" or str(r.get("resultCode")).upper() == "ERROR" \
            or str(cd.get("Status.code")) == "2"
        error = str(cd.get("Status.message") or cd.get("error.type") or r.get("resultCode") or "error")

        if name == "InvokeAgent" or op == "invoke_agent":
            info["spans"] += 1
            q = _message_text(cd.get("gen_ai.input.messages"))
            if q:
                acts.append({**base, "type": "message", "from": {"role": "user"}, "text": q})
            if failed:
                acts.append({**base, "type": "event", "valueType": "DynamicPlanStepFinished",
                             "value": {"taskDialogId": "Agent turn", "state": "failed", "error": {"message": error}}})
        elif name == "ExecuteTool" or op == "execute_tool":
            info["spans"] += 1
            tool = str(cd.get("gen_ai.tool.name") or "tool")
            args, result = cd.get("gen_ai.tool.call.arguments"), cd.get("gen_ai.tool.call.result")
            if _SEARCH_TOOL.search(tool):
                tool_id = "P:UniversalSearchTool"
                q = _query_of(args)
                if q:
                    acts.append({**base, "type": "event", "valueType": "DynamicPlanStepBindUpdate",
                                 "value": {"taskDialogId": tool_id, "arguments": {"search_query": q}}})
                docs = [{"Name": t} for t in _titles(result)]
                value = {"taskDialogId": tool_id, "state": "failed" if failed else "completed",
                         "observation": {"search_result": {"search_results": docs}}}
                if failed:
                    value["error"] = {"message": error}
                acts.append({**base, "type": "event", "valueType": "DynamicPlanStepFinished", "value": value})
            else:
                value = {"taskDialogId": tool, "state": "failed" if failed else "completed"}
                if failed:
                    value["error"] = {"message": error}
                acts.append({**base, "type": "event", "valueType": "DynamicPlanStepTriggered",
                             "value": {"taskDialogId": tool, "type": str(cd.get("gen_ai.tool.type") or "Tool")}})
                acts.append({**base, "type": "event", "valueType": "DynamicPlanStepFinished", "value": value})
        elif name == "OutputMessages" or op == "output_messages":
            info["spans"] += 1
            reply = _message_text(cd.get("gen_ai.output.messages"))
            if reply:
                acts.append({**base, "type": "message", "from": {"role": "bot"}, "text": reply})
        else:
            info["events"] += 1
            info["test_pane"] = info.get("test_pane") or str(cd.get("DesignMode") or cd.get("designMode")).lower() == "true"
            text_ = str(cd.get("text") or cd.get("Text") or "")
            kind = str(cd.get("type") or "message").lower()
            topic = str(cd.get("TopicName") or cd.get("TopicId") or cd.get("topicName") or "")
            if name == "BotMessageReceived" and text_ and kind == "message":
                acts.append({**base, "type": "message", "from": {"role": "user"}, "text": text_})
            elif name == "BotMessageSend" and text_:
                acts.append({**base, "type": "message", "from": {"role": "bot"}, "text": text_})
            elif name == "TopicStart" and topic:
                acts.append({**base, "type": "event", "valueType": "DialogTracingInfo",
                             "value": {"actions": [{"topicId": topic}]}})
            elif name == "TopicAction" and topic:
                info["steps"] = info.get("steps", 0) + 1
                acts.append({**base, "type": "event", "valueType": "DialogTracingInfo",
                             "value": {"actions": [{"topicId": topic, "actionId": str(cd.get("ActionId") or ""),
                                                    "actionType": str(cd.get("Kind") or ""),
                                                    "exception": str(cd.get("Exception") or cd.get("exception") or "")}]}})
            elif cd.get("Message"):
                info["logger"].append(f"{name}: {str(cd['Message'])[:200]}")
        if conv and not info["conversation"]:
            info["conversation"] = conv
    return acts, info
