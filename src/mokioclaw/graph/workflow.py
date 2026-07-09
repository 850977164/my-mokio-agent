"""LangGraph 图编译入口 —— MultiAgent 工作流.

图结构（简单图）:
    START → Planner → context_monitor → Verifier → context_monitor → Final → END
                ↑                           │
                └───────────────────────────┘ (monitor_route → "planner")

图结构（复杂图，含压缩）:
    START → Planner → context_monitor ⇄ context_compressor → Verifier → Final → END

Usage:
    from mokioclaw.graph.workflow import build_workflow
    graph = build_workflow()
    result = graph.invoke({"task": "...", "runtime": runtime, ...})
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from mokioclaw.graph.nodes import (
    planner_node,
    verifier_node,
    context_monitor_node,
    context_monitor_route,
    context_compressor_node,
    context_compressor_route,
    intent_router_node,
    chat_responder_node,
    intent_route_fn,
)
from mokioclaw.graph.state import MokioGraphState


def final_node(state: MokioGraphState) -> dict:
    """Final 节点: 将 passed/failed 状态格式化为 final_answer 文本.

    不调用 LLM —— 纯粹基于已收集的状态字段做格式化，
    保证最终输出始终有可读的汇总。

    Returns:
        dict: 包含 final_answer 的状态更新.
    """
    passed: bool = state.get("passed", False)
    task: str = state.get("task", "")
    plan_summary: str = state.get("plan_summary", "")
    attempts: int = state.get("attempts", 0)
    max_attempts: int = state.get("max_attempts", 3)
    code_agent_summary: str = state.get("code_agent_summary", "")
    verification_checks: list[dict] = state.get("verification_checks", [])
    verification_results: list[dict] = state.get("verification_results", [])
    research_notes: str = state.get("research_notes", "")
    agent_handoffs: list[dict] = state.get("agent_handoffs", [])

    lines: list[str] = []

    # ── 标题 ──
    lines.append("=" * 60)
    if passed:
        lines.append("✅ 任务执行成功")
    else:
        lines.append("❌ 任务执行失败")
    lines.append("=" * 60)
    lines.append("")

    # ── 任务 ──
    lines.append(f"📋 原始任务: {task}")
    lines.append(f"📝 执行计划: {plan_summary}")
    lines.append(f"🔁 尝试次数: {attempts}/{max_attempts}")
    lines.append("")

    # ── Agent 委托记录 ──
    if agent_handoffs:
        lines.append("─" * 60)
        lines.append("🤖 Agent 委托记录")
        lines.append("─" * 60)
        for h in agent_handoffs:
            lines.append(f"  {h.get('from_agent', '?')} → {h.get('to_agent', '?')}: {h.get('instruction', '')[:120]}")
        lines.append("")

    # ── 研究笔记 ──
    if research_notes:
        lines.append("─" * 60)
        lines.append("🔍 研究笔记")
        lines.append("─" * 60)
        lines.append(research_notes[:1000])
        lines.append("")

    # ── codeAgent 总结 ──
    lines.append("─" * 60)
    lines.append("🔧 codeAgent 执行总结")
    lines.append("─" * 60)
    lines.append(code_agent_summary or "(codeAgent 未产出总结)")
    lines.append("")

    # ── 验证命令结果 ──
    if verification_results:
        lines.append("─" * 60)
        lines.append("🖥️  验证命令执行结果")
        lines.append("─" * 60)
        for vr in verification_results:
            cmd = vr.get("command", "?")
            ok = vr.get("ok", False)
            exit_code = vr.get("exit_code", None)
            stdout = vr.get("stdout", "")
            stderr = vr.get("stderr", "")
            status = "✅" if ok else "❌"
            lines.append(f"  {status} $ {cmd} (exit={exit_code})")
            if stdout:
                for line in stdout.strip().split("\n")[:5]:
                    lines.append(f"       {line}")
            if stderr:
                for line in stderr.strip().split("\n")[:3]:
                    lines.append(f"       [stderr] {line}")
            lines.append("")
    else:
        lines.append("(无验证命令)")
        lines.append("")

    # ── 逐项检查 ──
    if verification_checks:
        lines.append("─" * 60)
        lines.append("📊 验收检查明细")
        lines.append("─" * 60)
        for c in verification_checks:
            name = c.get("name", "?")
            check_passed = c.get("passed", False)
            detail = c.get("detail", "")
            icon = "✅" if check_passed else "❌"
            lines.append(f"  {icon} {name}")
            if detail:
                lines.append(f"       {detail}")
        lines.append("")

    # ── 最终判定 ──
    lines.append("=" * 60)
    if passed:
        lines.append("🏁 最终结果: PASSED — 所有验收标准均已满足。")
    else:
        lines.append("🏁 最终结果: FAILED — 未通过验收。")
        failed = [c for c in verification_checks if not c.get("passed", True)]
        if failed:
            lines.append("     失败项:")
            for f in failed:
                lines.append(f"       - {f.get('name', '?')}: {f.get('detail', '')}")
    lines.append("=" * 60)

    final_answer = "\n".join(lines)
    return {"final_answer": final_answer}


def build_workflow():
    """编译并返回 MultiAgent 工作流图（无上下文压缩）.

    图结构:
        START → Planner → context_monitor → Verifier → context_monitor → Final → END
                    ↑                           │
                    └───────────────────────────┘ (monitor_route → "planner")

    Returns:
        CompiledStateGraph: 已编译的 LangGraph 状态图，可直接 .invoke() 调用.
    """
    graph = StateGraph(MokioGraphState)
    graph.add_node("planner", planner_node)
    graph.add_node("context_monitor", context_monitor_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("final", final_node)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "context_monitor")
    graph.add_conditional_edges("context_monitor", context_monitor_route, {
        "context_compressor": "context_monitor",  # 无 compressor 时自循环
        "verifier": "verifier",
        "planner": "planner",
        "final": "final",
    })
    graph.add_edge("verifier", "context_monitor")
    graph.add_edge("final", END)
    return graph.compile()


def build_complex_workflow():
    """编译并返回 MultiAgent 复合工作流图.

    图结构:
        START → Planner → context_monitor → context_compressor/Verifier → Final → END
            ↑               ↑                   │            │
            └───────────────┴───────────────────┘            │
            (context_monitor_route → "planner")              │
            (context_compressor_route → "verifier"/"planner")│
            (verifier → context_monitor)                     │
            └────────────────────────────────────────────────┘

    Returns:
        CompiledStateGraph: 已编译的 LangGraph 状态图，可直接 .invoke() 调用.
    """
    graph = StateGraph(MokioGraphState)

    # ── 注册节点 ──
    graph.add_node("planner", planner_node)
    graph.add_node("context_monitor", context_monitor_node)
    graph.add_node("context_compressor", context_compressor_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("final", final_node)

    # ── 连线 ──
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "context_monitor")

    # context_monitor 路由: passed → final | should_compress → context_compressor | 否则 → context_next_node
    graph.add_conditional_edges(
        "context_monitor",
        context_monitor_route,
        {
            "context_compressor": "context_compressor",
            "verifier": "verifier",
            "planner": "planner",
            "final": "final",
        },
    )

    # context_compressor 路由: 压缩后直接由 context_next_node 决定目标
    graph.add_conditional_edges(
        "context_compressor",
        context_compressor_route,
        {
            "verifier": "verifier",
            "planner": "planner",
            "final": "final",
        },
    )

    graph.add_edge("verifier", "context_monitor")  # 验证后也过 monitor
    graph.add_edge("final", END)

    return graph.compile()


def build_entry_workflow():
    graph = StateGraph(MokioGraphState)
    graph.add_node("intent_router", intent_router_node)
    graph.add_node("chat_responder", chat_responder_node)
    graph.add_edge(START, "intent_router")
    graph.add_conditional_edges("intent_router", intent_route_fn, {"chat_responder": "chat_responder", "planner": END})
    graph.add_edge("chat_responder", END)
    return graph.compile()
