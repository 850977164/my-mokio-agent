"""Plan & Execute 图节点 —— Planner / Actor / Verifier 三节点实现.

架构:
    Planner ──→ Actor ──→ Verifier ──→ Final (passed)
                    ↑          │
                    └──────────┘ (retry, up to max_attempts)

Planner:     根据任务生成/修订计划（todos、验收标准、验证命令）。
Actor:       按计划执行，使用工具完成每个 todo。
Verifier:    检查成果是否满足验收标准，运行验证命令。
"""

from __future__ import annotations

import json
import subprocess

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from mokioclaw.core.state import RuntimeState
from mokioclaw.graph.state import MokioGraphState, TodoItem, VerificationResult
from mokioclaw.prompts.stage2 import (
    ACTOR_PROMPT,
    PLANNER_PROMPT,
    VERIFIER_PROMPT,
)
from mokioclaw.providers.openai_provider import create_model
from mokioclaw.tools.registry import build_tools, build_read_only_tools


# ═══════════════════════════════════════════════════════════════════════
# Structured-output tools (LLM calls these to return structured data)
# ═══════════════════════════════════════════════════════════════════════

def _write_todos(
    plan_summary: str,
    todos: list[dict],
    acceptance_criteria: list[str],
    verification_commands: list[str],
) -> str:
    """No-op stub: the Planner node extracts the structured data from the tool-call args."""
    return f"计划已记录: {len(todos)} 个步骤, {len(acceptance_criteria)} 条验收标准"


TodoWriteTool = StructuredTool.from_function(
    func=_write_todos,
    name="TodoWrite",
    description=(
        "向系统提交完整的执行计划。"
        "参数: plan_summary (计划摘要), todos (待办列表, 每项含 id/content), "
        "acceptance_criteria (验收标准列表), verification_commands (验证命令列表)。"
    ),
)


def _update_todo(id: str, status: str, note: str = "") -> str:
    """No-op stub: the Actor node tracks status changes via tool-call args."""
    return f"Todo {id} → {status}" + (f": {note}" if note else "")


TodoUpdateTool = StructuredTool.from_function(
    func=_update_todo,
    name="TodoUpdate",
    description=(
        "更新某个 todo 的执行状态。"
        "参数: id (todo ID), status (pending|in_progress|completed|blocked), "
        "note (可选, 执行笔记)。"
    ),
)


def _report_verification(
    passed: bool,
    reason: str,
    checks: list[dict],
    recommended_next_instruction: str,
) -> str:
    """No-op stub: the Verifier node extracts structured results from the tool-call args."""
    status = "通过" if passed else "未通过"
    return f"验证{status}: {reason}"


ReportVerificationTool = StructuredTool.from_function(
    func=_report_verification,
    name="ReportVerification",
    description=(
        "提交验证报告。"
        "参数: passed (bool, 是否全部通过), reason (总结原因), "
        "checks (列表, 每项含 name/passed/detail), "
        "recommended_next_instruction (如未通过, 给 Planner 的具体修订建议)。"
    ),
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _extract_tool_call(messages: list, tool_name: str) -> dict | None:
    """从消息列表中提取最后一个指定工具的调用参数。

    倒序遍历消息列表，找到第一个 AIMessage 中匹配 tool_name 的 tool_call。

    Args:
        messages: LLM 对话消息列表。
        tool_name: 要查找的工具名称。

    Returns:
        工具调用参数字典，未找到则返回 None。
    """
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        if not msg.tool_calls:
            continue
        for tc in reversed(msg.tool_calls):
            if tc["name"] == tool_name:
                return tc["args"]
    return None


def _run_verification_commands(
    commands: list[str],
    workspace,
) -> list[VerificationResult]:
    """在 workspace 中依次执行验证命令，收集结果。

    Args:
        commands: 命令字符串列表（如 ["pytest", "mypy src/"]）。
        workspace: 工作区路径（Path 对象）。

    Returns:
        VerificationResult 列表，每个命令一条记录。
    """
    results: list[VerificationResult] = []
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(workspace),
            )
            ok = proc.returncode == 0
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired:
            ok = False
            exit_code = None
            stdout = ""
            stderr = f"命令超时 (120s): {cmd}"

        results.append(VerificationResult(
            command=cmd,
            ok=ok,
            exit_code=exit_code,
            stdout=stdout[:8000],   # 截断防止 token 爆炸
            stderr=stderr[:8000],
        ))
    return results


def _format_todos(todos: list[dict]) -> str:
    """将 todos 列表格式化为可读文本。"""
    if not todos:
        return "(暂无)"
    lines = []
    for t in todos:
        status = t.get("status", "pending")
        icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "blocked": "❌"}.get(status, "⬜")
        note = f" — {t['note']}" if t.get("note") else ""
        lines.append(f"  {icon} [{t.get('id', '?')}] {t.get('content', t.get('description', ''))}{note}")
    return "\n".join(lines)


def _format_verification_results(results: list[VerificationResult]) -> str:
    """将验证命令执行结果格式化为可读文本。"""
    if not results:
        return "(无验证命令)"
    lines = []
    for r in results:
        status = "✅ PASS" if r["ok"] else f"❌ FAIL (exit={r['exit_code']})"
        lines.append(f"  $ {r['command']}")
        lines.append(f"    {status}")
        if r["stdout"]:
            lines.append(f"    stdout: {r['stdout'][:500]}")
        if r["stderr"]:
            lines.append(f"    stderr: {r['stderr'][:500]}")
    return "\n".join(lines)


def _run_react_loop(
    llm,
    messages: list,
    tool_map: dict[str, object],
    max_loops: int = 10,
) -> tuple[list, str]:
    """执行 ReAct 循环：LLM 推理 ↔ 工具调用交替，直到完成或达到上限。

    Args:
        llm: 已绑定工具的模型实例。
        messages: 初始消息列表（会在循环中追加）。
        tool_map: 工具名 → 工具实例的映射。
        max_loops: 最大推理轮数。

    Returns:
        (messages, last_ai_content): 更新后的完整消息列表和最终 AI 文本内容。
    """
    last_ai_content = ""

    for _ in range(max_loops):
        response = llm.invoke(messages)
        messages.append(response)

        content = response.content or ""
        if content:
            last_ai_content = content

        # 没有工具调用 → LLM 认为任务完成
        if not response.tool_calls:
            break

        # 逐个执行工具调用
        for tool_call in response.tool_calls:
            tool_name: str = tool_call["name"]
            tool_args: dict = tool_call["args"]

            tool = tool_map.get(tool_name)
            if tool is not None:
                try:
                    result = tool.invoke(tool_args)
                except Exception as exc:
                    result = f"工具执行异常: {exc}"
            else:
                result = f"错误: 未找到工具 '{tool_name}'"

            result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

            messages.append(ToolMessage(
                content=result_str,
                tool_call_id=tool_call["id"],
            ))

    return messages, last_ai_content


# ═══════════════════════════════════════════════════════════════════════
# Core nodes
# ═══════════════════════════════════════════════════════════════════════

def planner_node(state: MokioGraphState) -> dict:
    """Planner 节点：生成或修订执行计划。

    首次调用（无 todos）：
        根据 state["task"] 生成新计划，LLM 通过 TodoWriteTool 返回结构化数据。
    修订调用（verifier 失败后）：
        根据 state["last_error"] 修订现有计划，保留已完成项，重新规划剩余工作。

    Returns:
        dict: 包含 plan_summary / todos / acceptance_criteria / verification_commands 的状态更新。
    """
    runtime: RuntimeState = state["runtime"]
    llm = create_model(model=runtime.model, temperature=0.0)
    agent = llm.bind_tools([TodoWriteTool])

    existing_todos = state.get("todos", [])
    last_error = state.get("last_error", "")

    # 区分首次规划 vs 修订
    if not existing_todos:
        # ── 首次规划 ──
        system_msg = SystemMessage(content=PLANNER_PROMPT)
        user_msg = HumanMessage(content=f"请为以下任务创建执行计划:\n\n{state['task']}")
        messages = [system_msg, user_msg]
    else:
        # ── 修订计划 ──
        plan_context = (
            f"## 原始任务\n{state['task']}\n\n"
            f"## 当前计划\n{_format_todos(existing_todos)}\n\n"
            f"## 验收标准\n" + "\n".join(f"  - {c}" for c in state.get("acceptance_criteria", [])) + "\n\n"
            f"## 上次验证失败\n{last_error}\n\n"
            f"请修订计划，解决验证失败的问题。已完成（✅）的 todo 保留不变，"
            f"为剩余工作添加新的 todo。调用 TodoWrite 工具提交修订后的完整计划。"
        )
        system_msg = SystemMessage(content=PLANNER_PROMPT)
        user_msg = HumanMessage(content=plan_context)
        messages = [system_msg, user_msg]

    # 调用 LLM
    response = agent.invoke(messages)

    # 提取 TodoWrite 工具调用的参数
    plan_args = _extract_tool_call([response], "TodoWrite")
    if plan_args is None:
        # LLM 可能直接文本输出（降级处理），尝试从 JSON 解析
        content = response.content or ""
        try:
            plan_args = json.loads(content)
        except json.JSONDecodeError:
            return {
                "plan_summary": content[:500],
                "todos": existing_todos,
            }

    # 构造 TodoItem 列表
    raw_todos: list[dict] = plan_args.get("todos", [])
    todos: list[TodoItem] = []
    for i, t in enumerate(raw_todos):
        todos.append(TodoItem(
            id=t.get("id", str(i + 1)),
            content=t.get("content", str(t)),
            status=t.get("status", "pending"),
            note=t.get("note", ""),
        ))

    return {
        "plan_summary": plan_args.get("plan_summary", ""),
        "todos": todos,
        "acceptance_criteria": plan_args.get("acceptance_criteria", []),
        "verification_commands": plan_args.get("verification_commands", []),
        "messages": [system_msg, user_msg, response],
    }


def actor_node(state: MokioGraphState) -> dict:
    """Actor 节点：按计划执行 todos。

    使用 ReAct 循环（最多 10 轮）让 LLM 交替推理和调用工具。
    LLM 可以调用所有文件/bash/grep 工具，以及 TodoUpdate 来标记进度。

    Returns:
        dict: 包含 messages / last_actor_summary / todos 的状态更新。
    """
    runtime: RuntimeState = state["runtime"]
    tools = build_tools(runtime) + [TodoUpdateTool]
    tool_map: dict[str, object] = {t.name: t for t in tools}
    llm = create_model(model=runtime.model, temperature=0.0)
    agent = llm.bind_tools(tools)

    # 构建输入消息
    todos = state.get("todos", [])
    last_error = state.get("last_error", "")

    # 区分首次执行 vs 重试执行
    if last_error or state.get("attempts", 0) > 0:
        # ── 重试轮：强调上次漏了什么，只关注未完成的 todo ──
        pending_todos = [t for t in todos if t.get("status") != "completed"]
        plan_text = (
            f"## 任务\n{state['task']}\n\n"
            f"## 计划摘要\n{state.get('plan_summary', '')}\n\n"
            f"## ⚠️ 这是重试轮！以下 todos 需要你真正完成\n"
            f"{_format_todos(pending_todos) if pending_todos else _format_todos(todos)}\n\n"
            f"## 上次验证失败的反馈\n{last_error}\n\n"
            f"## 本次已完成的 todos（不要重复执行）\n"
            + "\n".join(f"  ✅ [{t['id']}] {t['content']}" for t in todos if t.get("status") == "completed") + "\n\n"
            f"**请集中精力完成上面 ⚠️ 标记的待办项。不要只读文件，要真正创建/修改它们。**"
        )
    else:
        # ── 首次执行 ──
        plan_text = (
            f"## 任务\n{state['task']}\n\n"
            f"## 计划摘要\n{state.get('plan_summary', '')}\n\n"
            f"## 待办列表\n{_format_todos(todos)}\n\n"
            f"请开始执行。每完成一个 todo 请用 TodoUpdate 更新状态。"
        )

    messages: list = [
        SystemMessage(content=ACTOR_PROMPT),
        HumanMessage(content=plan_text),
    ]

    # ReAct 循环
    messages, last_summary = _run_react_loop(agent, messages, tool_map, max_loops=10)

    # 从消息中提取 TodoUpdate 调用，更新 todos 状态
    updated_todos = _apply_todo_updates(todos, messages)

    return {
        "messages": messages,
        "last_actor_summary": last_summary,
        "todos": updated_todos,
    }


def verifier_node(state: MokioGraphState) -> dict:
    """Verifier 节点：检查 Actor 的成果是否满足验收标准。

    步骤:
        1. 在 workspace 中运行 verification_commands，收集 VerificationResult。
        2. 让 LLM 以只读工具检查文件内容，对照验收标准逐项评估。
        3. LLM 通过 ReportVerificationTool 返回结构化验证结果。

    Returns:
        dict: 包含 passed / attempts / verification_results / verification_checks /
              last_error / todos 的状态更新。
    """
    runtime: RuntimeState = state["runtime"]
    tools = build_read_only_tools(runtime) + [ReportVerificationTool]
    tool_map: dict[str, object] = {t.name: t for t in tools}
    llm = create_model(model=runtime.model, temperature=0.0)
    agent = llm.bind_tools(tools)

    # 1. 运行验证命令，收集原始结果
    commands = state.get("verification_commands", [])
    verification_results = _run_verification_commands(commands, runtime.workspace)

    # 2. 构建验证上下文
    context = (
        f"## 原始任务\n{state['task']}\n\n"
        f"## 计划摘要\n{state.get('plan_summary', '')}\n\n"
        f"## 验收标准\n" + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(state.get("acceptance_criteria", []))) + "\n\n"
        f"## Actor 执行总结\n{state.get('last_actor_summary', '(无)')}\n\n"
        f"## 验证命令执行结果\n{_format_verification_results(verification_results)}\n\n"
        f"请逐项检查验收标准，检查 Actor 产出的文件，然后调用 ReportVerification 提交报告。"
    )

    messages = [
        SystemMessage(content=VERIFIER_PROMPT),
        HumanMessage(content=context),
    ]

    # 3. LLM 验证（只读工具循环）
    messages, _ = _run_react_loop(agent, messages, tool_map, max_loops=8)

    # 4. 提取 ReportVerification 工具调用的参数
    report = _extract_tool_call(messages, "ReportVerification")
    if report is None:
        # 降级：尝试从最后一个 AI 消息内容解析 JSON
        last_content = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                last_content = msg.content
                break
        try:
            report = json.loads(last_content)
        except json.JSONDecodeError:
            report = {
                "passed": False,
                "reason": f"无法解析验证报告: {last_content[:300]}",
                "checks": [],
                "recommended_next_instruction": "请重新验证并调用 ReportVerification 工具。",
            }

    passed: bool = report.get("passed", False)
    checks: list[dict] = report.get("checks", [])
    reason: str = report.get("reason", "")
    next_instruction: str = report.get("recommended_next_instruction", "")

    # 5. 更新 state
    attempts = state.get("attempts", 0) + 1

    # 构建 last_error（失败时）
    last_error = ""
    if not passed:
        failed_checks = [c for c in checks if not c.get("passed", True)]
        failed_names = ", ".join(c.get("name", "?") for c in failed_checks)
        last_error = (
            f"验证未通过 (第{attempts}次): {reason}\n"
            f"失败项: {failed_names}\n"
            f"建议: {next_instruction}"
        )

    # 更新 todos 状态（根据验证结果标记）
    updated_todos = _mark_todos_from_verification(state.get("todos", []), checks, passed)

    return {
        "passed": passed,
        "attempts": attempts,
        "verification_results": verification_results,
        "verification_checks": checks,
        "last_error": last_error,
        "todos": updated_todos,
        "messages": messages,
    }


# ═══════════════════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════════════════

def verifier_route(state: MokioGraphState) -> str:
    """Verifier 之后的路由决策。

    Returns:
        "final"  — 验证通过或已达最大尝试次数，结束执行。
        "planner" — 验证未通过且还有重试空间，返回 Planner 修订计划。
    """
    if state.get("passed", False):
        return "final"
    attempts = state.get("attempts", 0)
    max_attempts = state.get("max_attempts", 3)
    if attempts >= max_attempts:
        return "final"
    return "planner"


# ═══════════════════════════════════════════════════════════════════════
# 内部辅助（节点实现细节）
# ═══════════════════════════════════════════════════════════════════════

def _apply_todo_updates(
    todos: list[TodoItem],
    messages: list,
) -> list[TodoItem]:
    """从消息历史中提取 TodoUpdate 调用并应用到 todos。

    Args:
        todos: 当前 todos 列表。
        messages: ReAct 循环产生的完整消息列表。

    Returns:
        更新后的 todos 列表。
    """
    if not todos:
        return todos

    # 收集所有 TodoUpdate 调用（按时间顺序）
    updates: dict[str, dict] = {}  # id → {status, note}
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        if not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if tc["name"] != "TodoUpdate":
                continue
            args = tc["args"]
            tid = args.get("id", "")
            updates[tid] = {
                "status": args.get("status", "pending"),
                "note": args.get("note", ""),
            }

    # 应用更新
    updated: list[TodoItem] = []
    for t in todos:
        tid = t["id"]
        if tid in updates:
            updated.append(TodoItem(
                id=t["id"],
                content=t["content"],
                status=updates[tid]["status"],
                note=updates[tid]["note"] or t.get("note", ""),
            ))
        else:
            updated.append(t)
    return updated


def _mark_todos_from_verification(
    todos: list[TodoItem],
    checks: list[dict],
    passed: bool,
) -> list[TodoItem]:
    """根据验证结果更新 todos 状态。

    全部通过 → 所有 todo 标记为 completed。
    部分失败 → 相关 todo 标记为 blocked。
    """
    if not todos:
        return todos

    if passed:
        # 全部标记为完成
        return [
            TodoItem(
                id=t["id"],
                content=t["content"],
                status="completed",
                note=t.get("note", "验证通过"),
            )
            for t in todos
        ]

    # 检查哪些 todo 对应的验证项失败了
    failed_names: set[str] = set()
    for c in checks:
        if not c.get("passed", True):
            failed_names.add(c.get("name", ""))

    updated: list[TodoItem] = []
    for t in todos:
        is_failed = any(
            name.lower() in t["content"].lower() or name.lower() in t["id"].lower()
            for name in failed_names
        ) if failed_names else False

        if t.get("status") == "completed" and is_failed:
            updated.append(TodoItem(
                id=t["id"],
                content=t["content"],
                status="blocked",
                note=t.get("note", "") + " [验证失败，需重新执行]",
            ))
        else:
            updated.append(t)
    return updated
