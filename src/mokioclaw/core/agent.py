"""Graph Agent —— 基于 Plan & Execute 工作流的事件流.

将 LangGraph 的 stream 输出转换为统一的 dict 事件流，
供 CLI / GUI / API 层消费。

事件类型:
    planner   — Planner 节点产出 (plan_summary, todos, acceptance_criteria, …)
    actor     — Actor 节点产出 (last_actor_summary, todos)
    verifier  — Verifier 节点产出 (passed, verification_results, verification_checks, …)
    final     — Final 节点产出 (final_answer)
    custom    — 节点内部通过 StreamWriter 发射的自定义事件 (透传)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from mokioclaw.core.state import RuntimeState


def stream_agent_events(
    task: str,
    *,
    workspace: Path,
    max_attempts: int = 3,
    model: str | None = None,
) -> Iterator[dict]:
    """运行 Plan & Execute 工作流，以事件流形式产出每个节点的结果.

    内部调用 ``build_workflow().stream()`` 驱动 LangGraph 图，
    将 (namespace, mode, chunk) 三元组解析为统一格式的 dict 事件。

    Args:
        task: 用户任务描述（自然语言）。
        workspace: 工作区根目录，所有文件/命令操作均限制在此范围内。
        max_attempts: 计划重试上限，默认 3 次。超过后即使验证失败也强制结束。
        model: LLM 模型名称，None 则优先读取环境变量 MODEL，回退到 gpt-4o。

    Yields:
        dict 事件，统一格式 ``{"type": str, ...}``:

        - ``{"type": "planner", "plan_summary": str, "todos": list, ...}``
          Planner 节点完成，产出/修订了执行计划。

        - ``{"type": "actor", "last_actor_summary": str, "todos": list, ...}``
          Actor 节点完成，已执行了所有待办项。

        - ``{"type": "verifier", "passed": bool, "verification_results": list, ...}``
          Verifier 节点完成，包含验证结果。

        - ``{"type": "final", "final_answer": str, ...}``
          工作流结束，final_answer 为可读的最终汇总。

        - ``{"type": "custom", "data": any}``
          节点内部通过 StreamWriter 发射的自定义事件（透传）。
    """
    # 1. 解析模型名并创建运行状态
    if model is None:
        model = os.getenv("MODEL", "gpt-4o")
    runtime = RuntimeState(workspace=workspace, model=model)

    # 2. 编译图并准备初始输入（懒加载避免循环导入）
    from mokioclaw.graph.workflow import build_workflow

    graph = build_workflow()
    inputs: dict = {
        "task": task,
        "runtime": runtime,
        "max_attempts": max_attempts,
    }

    # 3. 流式执行图，解析每个事件
    for event in graph.stream(inputs, stream_mode=["updates", "custom"]):
        # LangGraph 多 stream_mode 时产出 (mode, chunk) 二元组
        # 只有开了 subgraphs=True 才是 (namespace, mode, chunk) 三元组
        if isinstance(event, tuple) and len(event) == 2:
            mode, chunk = event
        else:
            # 降级：单 stream_mode 或未知格式
            mode = "updates"
            chunk = event

        if mode == "updates":
            # chunk = {"planner": {...}, "actor": {...}, ...}
            for node_name, node_output in chunk.items():
                if node_name == "__start__":
                    continue
                yield {"type": node_name, **node_output}

        elif mode == "custom":
            # 透传节点内部的自定义事件
            yield {"type": "custom", "data": chunk}
