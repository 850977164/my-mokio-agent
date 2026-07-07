"""LangGraph 图编译入口 —— Plan & Execute 工作流.

将 Planner / Actor / Verifier / Final 四个节点组装为状态图，
由 LangGraph 引擎驱动完整生命周期:

    START → Planner → Actor → Verifier → Final → END
                        ↑          │
                        └──────────┘ (verifier_route → "planner")

Usage:
    from mokioclaw.graph.workflow import build_workflow

    graph = build_workflow()
    result = graph.invoke({"task": "...", "runtime": runtime, ...})
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from mokioclaw.graph.nodes import (
    actor_node,
    planner_node,
    verifier_node,
    verifier_route,
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
    last_actor_summary: str = state.get("last_actor_summary", "")
    verification_checks: list[dict] = state.get("verification_checks", [])
    verification_results: list[dict] = state.get("verification_results", [])

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

    # ── Actor 总结 ──
    lines.append("─" * 60)
    lines.append("🔧 Actor 执行总结")
    lines.append("─" * 60)
    lines.append(last_actor_summary or "(Actor 未产出总结)")
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
        # 列出失败项
        failed = [c for c in verification_checks if not c.get("passed", True)]
        if failed:
            lines.append("     失败项:")
            for f in failed:
                lines.append(f"       - {f.get('name', '?')}: {f.get('detail', '')}")
    lines.append("=" * 60)

    final_answer = "\n".join(lines)
    return {"final_answer": final_answer}


def build_workflow():
    """编译并返回 Plan & Execute 工作流图.

    Returns:
        CompiledStateGraph: 已编译的 LangGraph 状态图，可直接 .invoke() 调用.
    """
    graph = StateGraph(MokioGraphState)

    # ── 注册节点 ──
    graph.add_node("planner", planner_node)
    graph.add_node("actor", actor_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("final", final_node)

    # ── 连线 ──
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "actor")
    graph.add_edge("actor", "verifier")
    graph.add_conditional_edges(
        "verifier",
        verifier_route,
        {
            "final": "final",
            "planner": "planner",
        },
    )
    graph.add_edge("final", END)

    return graph.compile()
