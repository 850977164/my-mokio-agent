"""Graph Agent —— 基于 MultiAgent 工作流的事件流.

将 LangGraph 的 stream 输出转换为统一的 dict 事件流，
供 CLI / GUI / API 层消费。

事件类型:
    graph_event  — 图节点产出 (planner / verifier / final / context_monitor / …)
    custom_event — 节点内部通过 StreamWriter 发射的自定义事件 (透传)
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Iterator

from mokioclaw.core.approval import ApprovalDecision, ApprovalRequest, normalize_approval_mode
from mokioclaw.core.checkpoint import CheckpointManager, normalize_checkpoint_mode
from mokioclaw.core.state import RuntimeState
from mokioclaw.core.trace import TraceRecorder, normalize_trace_mode


def stream_agent_events(
    task: str,
    *,
    workspace: Path,
    max_attempts: int = 3,
    model: str | None = None,
    approval_mode: str = "inline",
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision] | None = None,
    checkpoint_mode: str = "light",
    resume_workspace: Path | None = None,
    trace_mode: str = "on",
) -> Iterator[dict]:
    """运行 MultiAgent 工作流，以事件流形式产出每个节点的结果."""
    if model is None:
        model = os.getenv("MODEL", "gpt-4o")

    actual_workspace = _resolve_workspace(workspace, resume_workspace)

    runtime = RuntimeState(
        workspace=actual_workspace,
        model=model,
        checkpoint_mode=checkpoint_mode,
        trace_mode=trace_mode,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
    )

    manager = CheckpointManager(runtime, task=task)

    inputs, resumed, resume_event = _prepare_inputs(
        runtime=runtime, task=task, max_attempts=max_attempts, manager=manager,
    )

    yield from _stream_workflow_events(
        inputs, runtime=runtime, task=task,
        resumed=resumed, resume_event=resume_event,
    )


# ═══════════════════════════════════════════════════════════════════
# 核心工作流执行（共享）
# ═══════════════════════════════════════════════════════════════════

def _stream_workflow_events(inputs, *, runtime, task, resumed=False, resume_event=None):
    """共享工作流执行循环，通过 yield from + return 传回 final_state."""
    manager = CheckpointManager(runtime, task=task)
    trace = TraceRecorder(runtime, task=task)

    trace.start(inputs, resumed=resumed, resume_event=resume_event)
    manager.save(inputs, status="started", latest_node="start")

    from mokioclaw.graph.workflow import build_complex_workflow

    graph = build_complex_workflow()

    latest_node: str = "start"
    current_state: dict = dict(inputs)

    try:
        for event in graph.stream(
            inputs, stream_mode=["updates", "custom"],
            config={"recursion_limit": 50},
        ):
            if isinstance(event, tuple) and len(event) == 2:
                mode, chunk = event
            else:
                mode = "updates"
                chunk = event

            if mode == "custom":
                custom_evt = chunk if isinstance(chunk, dict) else {"data": chunk}
                trace.record_custom_event(custom_evt)

                if isinstance(custom_evt, dict):
                    node = custom_evt.get("node", latest_node)
                    if node:
                        latest_node = node

                if _custom_event_needs_checkpoint(custom_evt):
                    _merge_state(current_state, custom_evt)
                    manager.save(current_state, status="running",
                                 latest_node=latest_node, event=custom_evt)

                yield {"type": "custom_event", "event": custom_evt}

            elif mode == "updates":
                for node_name, node_output in chunk.items():
                    if node_name == "__start__":
                        continue

                    latest_node = node_name
                    update_evt = _safe_event_dict(node_output, prefix=node_name)
                    trace.record_graph_update(update_evt)
                    _merge_state(current_state, node_output)
                    manager.save(current_state, status="running",
                                 latest_node=node_name, event=update_evt)

                    yield {"type": "graph_event", "event": {node_name: node_output}}

        status = "completed" if current_state.get("passed", False) else "failed"
        manager.save(current_state, status=status, latest_node=latest_node)
        trace.end(status=status, latest_node=latest_node, final_state=current_state)

    except KeyboardInterrupt:
        manager.save(current_state, status="interrupted", latest_node=latest_node)
        trace.end(status="interrupted", latest_node=latest_node, final_state=current_state)
        raise

    return current_state


# ═══════════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════════

def _resolve_workspace(workspace: Path, resume_workspace: Path | None) -> Path:
    if resume_workspace is not None:
        return resume_workspace.resolve()
    return workspace.resolve()


def _prepare_inputs(*, runtime: RuntimeState, task: str, max_attempts: int, manager: CheckpointManager):
    restored = CheckpointManager.load_resume_inputs(runtime, task=task, max_attempts=max_attempts)
    if restored is not None:
        restored_inputs, resume_event = restored
        restored_inputs["runtime"] = runtime
        return restored_inputs, True, resume_event

    inputs: dict = {"task": task, "runtime": runtime, "max_attempts": max_attempts}
    return inputs, False, None


def _merge_state(current: dict, incoming: dict) -> None:
    for key, value in incoming.items():
        if key in ("messages", "runtime"):
            continue
        current[key] = value


def _custom_event_needs_checkpoint(event: dict) -> bool:
    if not isinstance(event, dict):
        return False
    etype = event.get("type", "")
    return etype in {"tool_call", "tool_result", "handoff"}


def _safe_event_dict(node_output: dict, prefix: str = "") -> dict:
    result: dict = {"type": prefix}
    for key, value in node_output.items():
        if key in ("messages", "runtime"):
            continue
        if value is None or isinstance(value, (bool, int, float, str)):
            result[key] = value
        elif isinstance(value, (list, tuple)):
            result[key] = [
                item if isinstance(item, (bool, int, float, str, type(None)))
                else str(item)[:200]
                for item in list(value)[:20]
            ]
        elif isinstance(value, dict):
            result[key] = {str(k): _safe_value(v) for k, v in value.items()}
        else:
            result[key] = str(value)[:500]
    return result


def _safe_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_value(v) for v in list(value)[:10]]
    if isinstance(value, dict):
        return {str(k): _safe_value(v) for k, v in list(value.items())[:20]}
    return str(value)[:200]


def _truncate_text(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[:limit - 3] + "..."
