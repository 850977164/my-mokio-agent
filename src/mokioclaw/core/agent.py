"""Graph Agent —— 基于 MultiAgent 工作流的事件流."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Iterator

from mokioclaw.core.approval import ApprovalDecision, ApprovalRequest
from mokioclaw.core.checkpoint import CheckpointManager
from mokioclaw.core.state import RuntimeState
from mokioclaw.core.trace import TraceRecorder


def stream_agent_events(
    task: str, *, workspace: Path, max_attempts: int = 3, model: str | None = None,
    approval_mode: str = "inline",
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision] | None = None,
    checkpoint_mode: str = "light", resume_workspace: Path | None = None,
    trace_mode: str = "on",
) -> Iterator[dict]:
    """运行 MultiAgent 工作流."""
    if model is None:
        model = os.getenv("MODEL", "gpt-4o")
    actual_workspace = _resolve_workspace(workspace, resume_workspace)
    runtime = RuntimeState(workspace=actual_workspace, model=model,
                           checkpoint_mode=checkpoint_mode, trace_mode=trace_mode,
                           approval_mode=approval_mode, approval_handler=approval_handler)
    manager = CheckpointManager(runtime, task=task)
    inputs, resumed, resume_event = _prepare_inputs(
        runtime=runtime, task=task, max_attempts=max_attempts, manager=manager)
    yield from _stream_workflow_events(
        inputs, runtime=runtime, task=task, resumed=resumed, resume_event=resume_event)


def stream_session_events(
    task: str, *, workspace: Path, max_attempts: int = 3, model: str | None = None,
    approval_mode: str = "inline",
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision] | None = None,
    checkpoint_mode: str = "light", trace_mode: str = "on",
) -> Iterator[dict]:
    """支持多轮对话的事件流."""
    from mokioclaw.core.session import (
        load_or_create_session, append_user_turn,
        append_assistant_turn, save_session, build_session_context)
    from mokioclaw.graph.workflow import build_entry_workflow
    if model is None:
        model = os.getenv("MODEL", "gpt-4o")
    actual_workspace = workspace.resolve()
    session = load_or_create_session(actual_workspace)
    turn = append_user_turn(session, task)
    yield {"type": "session_event", "event": "user_turn_recorded", "turn": turn}
    session_context = build_session_context(actual_workspace, session)
    runtime = RuntimeState(workspace=actual_workspace, model=model,
                           checkpoint_mode=checkpoint_mode, trace_mode=trace_mode,
                           approval_mode=approval_mode, approval_handler=approval_handler)
    entry_graph = build_entry_workflow()
    entry_result = entry_graph.invoke({
        "task": task, "last_user_input": task,
        "session_context": session_context, "runtime": runtime,
        "max_attempts": max_attempts})
    intent_route = entry_result.get("intent_route", "workflow")
    yield {"type": "session_event", "event": "intent_routed",
           "route": intent_route,
           "reason": entry_result.get("intent_reason", ""),
           "confidence": entry_result.get("intent_confidence", 0.0)}
    if intent_route == "chat":
        assistant_content = entry_result.get("chat_response", "")
        yield {"type": "session_event", "event": "chat_response", "content": assistant_content}
        assistant_summary = _truncate_text(assistant_content, 200)
    else:
        final_state = yield from _stream_workflow_events({
            "task": task, "last_user_input": task,
            "session_context": session_context, "runtime": runtime,
            "max_attempts": max_attempts}, runtime=runtime, task=task)
        assistant_content = final_state.get("final_answer", "") or ""
        assistant_summary = final_state.get("plan_summary", "") or _truncate_text(assistant_content, 200)
    append_assistant_turn(session, turn=turn, route=intent_route,
                          content=assistant_content, summary=assistant_summary)
    save_session(actual_workspace, session)
    yield {"type": "session_event", "event": "session_saved", "turn": turn, "route": intent_route}


def _stream_workflow_events(inputs, *, runtime, task, resumed=False, resume_event=None):
    """共享工作流执行循环。通过 yield from + return 传回 final_state."""
    manager = CheckpointManager(runtime, task=task)
    trace = TraceRecorder(runtime, task=task)
    trace.start(inputs, resumed=resumed, resume_event=resume_event)
    manager.save(inputs, status="started", latest_node="start")
    from mokioclaw.graph.workflow import build_complex_workflow
    graph = build_complex_workflow()
    latest_node, current_state = "start", dict(inputs)
    try:
        for event in graph.stream(inputs, stream_mode=["updates", "custom"], config={"recursion_limit": 50}):
            if isinstance(event, tuple) and len(event) == 2: mode, chunk = event
            else: mode, chunk = "updates", event
            if mode == "custom":
                ce = chunk if isinstance(chunk, dict) else {"data": chunk}
                trace.record_custom_event(ce)
                if isinstance(ce, dict) and ce.get("node"): latest_node = ce["node"]
                if _custom_event_needs_checkpoint(ce):
                    _merge_state(current_state, ce)
                    manager.save(current_state, status="running", latest_node=latest_node, event=ce)
                yield {"type": "custom_event", "event": ce}
            elif mode == "updates":
                for nn, no in chunk.items():
                    if nn == "__start__": continue
                    latest_node = nn
                    evt = _safe_event_dict(no, prefix=nn)
                    trace.record_graph_update(evt)
                    _merge_state(current_state, no)
                    manager.save(current_state, status="running", latest_node=nn, event=evt)
                    yield {"type": "graph_event", "event": {nn: no}}
        status = "completed" if current_state.get("passed", False) else "failed"
        manager.save(current_state, status=status, latest_node=latest_node)
        trace.end(status=status, latest_node=latest_node, final_state=current_state)
    except KeyboardInterrupt:
        manager.save(current_state, status="interrupted", latest_node=latest_node)
        trace.end(status="interrupted", latest_node=latest_node, final_state=current_state)
        raise
    return current_state


def _resolve_workspace(workspace, resume_workspace):
    return resume_workspace.resolve() if resume_workspace is not None else workspace.resolve()

def _prepare_inputs(*, runtime, task, max_attempts, manager):
    restored = CheckpointManager.load_resume_inputs(runtime, task=task, max_attempts=max_attempts)
    if restored is not None:
        ri, re = restored; ri["runtime"] = runtime; return ri, True, re
    return {"task": task, "runtime": runtime, "max_attempts": max_attempts}, False, None

def _merge_state(current, incoming):
    for k, v in incoming.items():
        if k not in ("messages", "runtime"): current[k] = v

def _custom_event_needs_checkpoint(event):
    return isinstance(event, dict) and event.get("type", "") in {"tool_call", "tool_result", "handoff"}

def _safe_event_dict(node_output, prefix=""):
    r = {"type": prefix}
    for k, v in node_output.items():
        if k in ("messages", "runtime"): continue
        if v is None or isinstance(v, (bool, int, float, str)): r[k] = v
        elif isinstance(v, (list, tuple)): r[k] = [i if isinstance(i, (bool, int, float, str, type(None))) else str(i)[:200] for i in list(v)[:20]]
        elif isinstance(v, dict): r[k] = {str(k2): _safe_value(v2) for k2, v2 in v.items()}
        else: r[k] = str(v)[:500]
    return r

def _safe_value(v):
    if v is None or isinstance(v, (bool, int, float, str)): return v
    if isinstance(v, (list, tuple)): return [_safe_value(x) for x in list(v)[:10]]
    if isinstance(v, dict): return {str(k): _safe_value(x) for k, x in list(v.items())[:20]}
    return str(v)[:200]

def _truncate_text(text, limit):
    if not text: return ""
    if len(text) <= limit: return text
    if limit <= 3: return text[:limit]
    return text[:limit - 3] + "..."
