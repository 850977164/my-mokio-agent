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
    """运行 MultiAgent 工作流，以事件流形式产出每个节点的结果.

    内部调用 ``build_workflow().stream()`` 驱动 LangGraph 图，
    将 (mode, chunk) 二元组解析为统一格式的 dict 事件。

    Args:
        task: 用户任务描述（自然语言）。
        workspace: 工作区根目录，所有文件/命令操作均限制在此范围内。
        max_attempts: 计划重试上限，默认 3 次。超过后即使验证失败也强制结束。
        model: LLM 模型名称，None 则优先读取环境变量 MODEL，回退到 gpt-4o。
        approval_mode: 审批模式 "inline" | "auto" | "deny"（默认 inline）。
        approval_handler: inline 模式下的审批回调。
        checkpoint_mode: 检查点模式 "light" | "strict" | "off"（默认 light）。
        resume_workspace: 恢复工作区路径，非 None 时从检查点恢复运行。
        trace_mode: 追踪模式 "on" | "off"（默认 on）。

    Yields:
        dict 事件，统一格式 ``{"type": str, ...}``:

        - ``{"type": "graph_event", "event": {"planner": {...}}}``
          图节点完成事件，event 内是 {节点名: 节点产出} 的映射。

        - ``{"type": "custom_event", "event": {...}}``
          节点内部通过 StreamWriter 发射的自定义事件（透传）。
    """
    # 0. 解析模型名
    if model is None:
        model = os.getenv("MODEL", "gpt-4o")

    # ── 确定工作区和恢复 ──
    actual_workspace = _resolve_workspace(workspace, resume_workspace)

    # 1. 创建 RuntimeState
    runtime = RuntimeState(
        workspace=actual_workspace,
        model=model,
        checkpoint_mode=checkpoint_mode,
        trace_mode=trace_mode,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
    )

    # 2. 创建 CheckpointManager 和 TraceRecorder
    manager = CheckpointManager(runtime, task=task)
    trace = TraceRecorder(runtime, task=task)

    # 3. 准备输入 —— 优先从检查点恢复
    inputs, resumed, resume_event = _prepare_inputs(
        runtime=runtime,
        task=task,
        max_attempts=max_attempts,
        manager=manager,
    )

    # 4. 编译图（懒加载避免循环导入）
    from mokioclaw.graph.workflow import build_workflow

    graph = build_workflow()

    # 5. 记录 start 事件 + 初始检查点
    trace.start(inputs, resumed=resumed, resume_event=resume_event)
    manager.save(inputs, status="started", latest_node="start")

    latest_node: str = "start"
    current_state: dict = dict(inputs)

    try:
        # 6. 流式执行图，解析每个事件
        for event in graph.stream(
            inputs,
            stream_mode=["updates", "custom"],
            config={"recursion_limit": 50} if not resumed else {"recursion_limit": 50},
        ):
            if isinstance(event, tuple) and len(event) == 2:
                mode, chunk = event
            else:
                mode = "updates"
                chunk = event

            if mode == "custom":
                # chunk 就是 writer() 传入的 dict
                custom_evt = chunk if isinstance(chunk, dict) else {"data": chunk}
                trace.record_custom_event(custom_evt)

                # 从 custom 事件中提取节点名（如果有）
                if isinstance(custom_evt, dict):
                    node = custom_evt.get("node", latest_node)
                    if node:
                        latest_node = node

                # 重要事件触发检查点保存
                if _custom_event_needs_checkpoint(custom_evt):
                    _merge_state(current_state, custom_evt)
                    manager.save(
                        current_state,
                        status="running",
                        latest_node=latest_node,
                        event=custom_evt,
                    )

                yield {"type": "custom_event", "event": custom_evt}

            elif mode == "updates":
                # chunk = {"planner": {...}, "actor": {...}, ...}
                for node_name, node_output in chunk.items():
                    if node_name == "__start__":
                        continue

                    latest_node = node_name

                    # 记录图更新（排除不可序列化的 messages 字段）
                    update_evt = _safe_event_dict(node_output, prefix=node_name)
                    trace.record_graph_update(update_evt)

                    # 合并 state 并保存检查点
                    _merge_state(current_state, node_output)
                    manager.save(
                        current_state,
                        status="running",
                        latest_node=node_name,
                        event=update_evt,
                    )

                    yield {"type": "graph_event", "event": {node_name: node_output}}

        # 7. 正常结束
        status = "completed" if current_state.get("passed", False) else "failed"
        manager.save(current_state, status=status, latest_node=latest_node)
        trace.end(status=status, latest_node=latest_node, final_state=current_state)

    except KeyboardInterrupt:
        # 8. 中断恢复
        manager.save(current_state, status="interrupted", latest_node=latest_node)
        trace.end(status="interrupted", latest_node=latest_node, final_state=current_state)
        raise


# ═══════════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════════

def _resolve_workspace(workspace: Path, resume_workspace: Path | None) -> Path:
    """确定实际工作区路径.

    resume_workspace 非 None 时优先使用，否则使用 workspace。
    """
    if resume_workspace is not None:
        return resume_workspace.resolve()
    return workspace.resolve()


def _prepare_inputs(
    *,
    runtime: RuntimeState,
    task: str,
    max_attempts: int,
    manager: CheckpointManager,
) -> tuple[dict, bool, dict | None]:
    """准备图输入 —— 优先从检查点恢复.

    Returns:
        (inputs, resumed, resume_event):
            - inputs: 图执行初始状态字典
            - resumed: 是否从检查点恢复
            - resume_event: 恢复事件（仅 resumed=True 时有值）
    """
    # 尝试从检查点恢复
    restored = CheckpointManager.load_resume_inputs(runtime, task=task, max_attempts=max_attempts)
    if restored is not None:
        restored_inputs, resume_event = restored
        # 使用恢复的 inputs，但 runtime 始终用最新的
        restored_inputs["runtime"] = runtime
        return restored_inputs, True, resume_event

    # 全新运行
    inputs: dict = {
        "task": task,
        "runtime": runtime,
        "max_attempts": max_attempts,
    }
    return inputs, False, None


def _merge_state(current: dict, incoming: dict) -> None:
    """将节点产出合并到当前状态追踪中（原地修改）."""
    for key, value in incoming.items():
        if key in ("messages", "runtime"):
            # 跳过消息列表和 runtime —— 由 LangGraph 管理
            continue
        current[key] = value


def _custom_event_needs_checkpoint(event: dict) -> bool:
    """判断自定义事件是否应触发检查点保存.

    以下类型的事件代表有意义的执行进度，需要保存检查点：
    - tool_call / tool_result: 工具执行
    - handoff: Agent 切换
    - checkpoint_saved: 跳过（避免循环）

    纯信息类事件（memory_injection 等）不触发检查点。
    """
    if not isinstance(event, dict):
        return False

    etype = event.get("type", "")
    if not etype:
        return False

    # 需要检查点的事件类型
    checkpoint_types = {"tool_call", "tool_result", "handoff"}
    return etype in checkpoint_types
