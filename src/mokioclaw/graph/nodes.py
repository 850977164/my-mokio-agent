"""MultiAgent 图节点 —— Planner / Verifier 两节点实现.

架构:
    Planner ──→ Verifier ──→ Final (passed)
        ↑            │
        └────────────┘ (retry, up to max_attempts)

    Planner 通过工具调用委托任务:
        - TodoWriteTool:     发布/修订计划
        - CallSearchAgentTool: 委托搜索任务给 searchAgent
        - CallCodeAgentTool:   委托实现任务给 codeAgent

    Verifier: 检查成果是否满足验收标准，运行验证命令。
"""

from __future__ import annotations

import json
import subprocess

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from mokioclaw.agents.code_agent import run_code_agent
from mokioclaw.agents.search_agent import run_search_agent
from mokioclaw.core.state import RuntimeState
from mokioclaw.graph.state import (
    AgentHandoff,
    MokioGraphState,
    SourceItem,
    TodoItem,
    VerificationResult,
)
from mokioclaw.prompts.stage3 import PLANNER_PROMPT, VERIFIER_PROMPT
from mokioclaw.providers.openai_provider import create_model
from mokioclaw.tools.registry import build_read_only_tools
from mokioclaw.tools.structured_tools import (
    ReportVerificationTool,
    TodoWriteTool,
)



# ═══════════════════════════════════════════════════════════════════════
# Agent delegation tool factories
# ═══════════════════════════════════════════════════════════════════════

def _make_call_search_agent_tool(state: MokioGraphState, writer) -> StructuredTool:
    """创建 CallSearchAgentTool —— 委托搜索任务给 searchAgent。"""

    def _call(instruction: str) -> str:
        """委托搜索任务给 searchAgent。

        Args:
            instruction: 搜索指令，描述需要研究什么内容。

        Returns:
            搜索结果摘要 JSON 字符串。
        """
        runtime: RuntimeState = state["runtime"]

        # 发射 handoff 事件
        event = {"type": "handoff", "from": "planner", "to": "searchAgent", "instruction": instruction[:500]}
        if writer is not None:
            writer(event)

        result = run_search_agent(runtime, instruction, writer=writer)

        # 更新 state 中的研究笔记和来源
        existing_notes = state.get("research_notes", "")
        new_notes = result.get("summary", "")
        state["research_notes"] = (existing_notes + "\n\n" + new_notes).strip() if new_notes else existing_notes

        existing_sources: list[SourceItem] = state.get("sources", []) or []
        seen_urls = {s.get("url", "") for s in existing_sources}
        for url in result.get("sources", []) or []:
            if url not in seen_urls:
                existing_sources.append(SourceItem(url=url, title="", content="", score=0.0))
                seen_urls.add(url)
        state["sources"] = existing_sources

        # 记录委托
        handoffs: list[dict] = state.get("agent_handoffs", []) or []
        handoffs.append(AgentHandoff(
            from_agent="planner",
            to_agent="searchAgent",
            instruction=instruction[:500],
            result=result.get("summary", "")[:500],
        ))
        state["agent_handoffs"] = handoffs

        return json.dumps({
            "ok": result.get("ok", False),
            "summary": result.get("summary", ""),
            "sources_count": len(result.get("sources", []) or []),
            "queries": result.get("queries", []) or [],
        }, ensure_ascii=False)

    return StructuredTool.from_function(
        func=_call,
        name="CallSearchAgent",
        description=(
            "将网络研究任务委托给 searchAgent。searchAgent 会使用搜索引擎查找信息并返回研究笔记。"
            "参数: instruction (研究指令，描述需要搜索的内容和要回答的问题)。"
        ),
    )


def _make_call_code_agent_tool(state: MokioGraphState, writer) -> StructuredTool:
    """创建 CallCodeAgentTool —— 委托实现任务给 codeAgent。"""

    def _call(instruction: str) -> str:
        """委托实现任务给 codeAgent。

        Args:
            instruction: 实现指令，包含完整上下文（任务、计划、todos、研究笔记等）。

        Returns:
            代码实现结果摘要 JSON 字符串。
        """
        runtime: RuntimeState = state["runtime"]

        # 发射 handoff 事件
        event = {"type": "handoff", "from": "planner", "to": "codeAgent", "instruction": instruction[:500]}
        if writer is not None:
            writer(event)

        result = run_code_agent(runtime, instruction, writer=writer)

        # 更新 state 中的 code_agent_summary
        state["code_agent_summary"] = result.get("summary", "")

        # 收集 todos 更新
        agent_todos: list[dict] = result.get("todos", []) or []
        existing_todos: list[dict] = state.get("todos", []) or []
        for ut in agent_todos:
            tid = ut.get("id", "")
            status = ut.get("status", "")
            note = ut.get("note", "")
            for t in existing_todos:
                if t.get("id") == tid:
                    t["status"] = status
                    if note:
                        t["note"] = note
                    break
        state["todos"] = existing_todos

        # 记录委托
        handoffs: list[dict] = state.get("agent_handoffs", []) or []
        handoffs.append(AgentHandoff(
            from_agent="planner",
            to_agent="codeAgent",
            instruction=instruction[:500],
            result=result.get("summary", "")[:500],
        ))
        state["agent_handoffs"] = handoffs

        return json.dumps({
            "ok": result.get("ok", False),
            "summary": result.get("summary", ""),
            "todos_updated": len(agent_todos),
        }, ensure_ascii=False)

    return StructuredTool.from_function(
        func=_call,
        name="CallCodeAgent",
        description=(
            "将代码实现任务委托给 codeAgent。codeAgent 会在 workspace 中使用文件/Shell 工具完成实现。"
            "参数: instruction (实现指令，应包含任务描述、计划摘要、todos、研究笔记等完整上下文)。"
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _extract_tool_call(messages: list, tool_name: str) -> dict | None:
    """从消息列表中提取最后一个指定工具的调用参数。"""
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        if not msg.tool_calls:
            continue
        for tc in reversed(msg.tool_calls):
            if tc["name"] == tool_name:
                return tc["args"]
    return None


def _extract_all_tool_calls(messages: list, tool_name: str) -> list[dict]:
    """从消息列表中提取所有指定工具的调用参数（按时间顺序）。"""
    results: list[dict] = []
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        if not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if tc["name"] == tool_name:
                results.append(tc["args"])
    return results


def _run_verification_commands(
    commands: list[str],
    workspace,
) -> list[VerificationResult]:
    """在 workspace 中依次执行验证命令，收集结果。"""
    results: list[VerificationResult] = []
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                cwd=str(workspace),
            )
            ok = proc.returncode == 0
            exit_code = proc.returncode
            stdout = (proc.stdout or "")[:8000]
            stderr = (proc.stderr or "")[:8000]
        except subprocess.TimeoutExpired:
            ok = False
            exit_code = None
            stdout = ""
            stderr = f"命令超时 (120s): {cmd}"[:8000]

        results.append(VerificationResult(
            command=cmd,
            ok=ok,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
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


def _format_sources(sources: list[SourceItem]) -> str:
    """将来源列表格式化为可读文本。"""
    if not sources:
        return "(暂无来源)"
    lines = []
    for s in sources[:10]:
        url = s.get("url", "")
        title = s.get("title", "") or url
        lines.append(f"  - [{title}]({url})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Core nodes
# ═══════════════════════════════════════════════════════════════════════

def planner_node(state: MokioGraphState, *, writer=None) -> dict:
    """Planner 节点：单阶段执行 —— TodoWrite + CallSearchAgent + CallCodeAgent 同时可用.

    LLM 自由决定工具调用顺序：
        1. 先用 TodoWrite 发布/修订执行计划.
        2. 如需搜索，用 CallSearchAgent 委托 searchAgent.
        3. 用 CallCodeAgent 委托 codeAgent 实现.

    首次调用（无 todos）：生成计划 → 搜索（如需）→ 委托实现.
    修订调用（verifier 失败后）：修订计划 → 仅委托修复.

    Returns:
        dict: 包含 plan_summary / todos / acceptance_criteria /
              verification_commands / research_notes / sources /
              agent_handoffs / code_agent_summary / messages 的状态更新.
    """
    runtime: RuntimeState = state["runtime"]
    llm = create_model(model=runtime.model, temperature=0.0)

    # 创建委托工具（需要 state + writer 闭包）
    call_search = _make_call_search_agent_tool(state, writer)
    call_code = _make_call_code_agent_tool(state, writer)

    tools = [TodoWriteTool, call_search, call_code]
    tool_map = {"TodoWrite": TodoWriteTool, "CallSearchAgent": call_search, "CallCodeAgent": call_code}

    agent = llm.bind_tools(tools)

    existing_todos = state.get("todos", [])
    last_error = state.get("last_error", "")
    research_notes = state.get("research_notes", "")

    # ── 构建消息 ──
    if not existing_todos:
        plan_context = (
            f"## 用户任务\n{state['task']}\n\n"
            f"请先用 TodoWrite 发布执行计划，然后根据需要调用 CallSearchAgent 搜索信息，"
            f"最后调用 CallCodeAgent 委托实现。"
        )
    else:
        plan_context = (
            f"## 原始任务\n{state['task']}\n\n"
            f"## 当前计划\n{_format_todos(existing_todos)}\n\n"
            f"## 验收标准\n" + "\n".join(f"  - {c}" for c in state.get("acceptance_criteria", [])) + "\n\n"
            f"## 研究笔记\n{research_notes or '(无)'}\n\n"
            f"## 上次验证失败\n{last_error}\n\n"
            f"请先用 TodoWrite 修订计划，然后调用 CallCodeAgent 委托修复。"
        )

    system_msg = SystemMessage(content=PLANNER_PROMPT)
    user_msg = HumanMessage(content=plan_context)
    messages: list = [system_msg, user_msg]

    # ── 单阶段工具循环：TodoWrite / CallSearchAgent / CallCodeAgent 同时可用 ──
    max_loops = max(10, len(existing_todos) * 2 + 4)
    for _loop in range(max_loops):
        response = agent.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for tool_call in response.tool_calls:
            tool_name: str = tool_call["name"]
            tool_args: dict = tool_call["args"]

            # 发射事件
            if writer is not None:
                writer({"type": "tool_call", "node": "planner", "tool": tool_name, "args": tool_args})

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


def verifier_node(state: MokioGraphState) -> dict:
    """Verifier 节点：检查 codeAgent 的成果是否满足验收标准。

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
    code_summary = state.get("code_agent_summary", "")
    research_notes = state.get("research_notes", "")
    sources = state.get("sources", []) or []

    context = (
        f"## 原始任务\n{state['task']}\n\n"
        f"## 计划摘要\n{state.get('plan_summary', '')}\n\n"
        f"## 验收标准\n" + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(state.get("acceptance_criteria", []))) + "\n\n"
        f"## 研究笔记\n{research_notes or '(无)'}\n\n"
        f"## 参考来源\n{_format_sources(sources)}\n\n"
        f"## codeAgent 执行总结\n{code_summary or '(无)'}\n\n"
        f"## 验证命令执行结果\n{_format_verification_results(verification_results)}\n\n"
        f"请逐项检查验收标准，检查 codeAgent 产出的文件，然后调用 ReportVerification 提交报告。"
    )

    messages = [
        SystemMessage(content=VERIFIER_PROMPT),
        HumanMessage(content=context),
    ]

    # 3. LLM 验证（只读工具循环）
    for _loop in range(8):
        response = agent.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

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

    # 4. 提取 ReportVerification 工具调用的参数
    report = _extract_tool_call(messages, "ReportVerification")
    if report is None:
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

    last_error = ""
    if not passed:
        failed_checks = [c for c in checks if not c.get("passed", True)]
        failed_names = ", ".join(c.get("name", "?") for c in failed_checks)
        last_error = (
            f"验证未通过 (第{attempts}次): {reason}\n"
            f"失败项: {failed_names}\n"
            f"建议: {next_instruction}"
        )

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
        "final"   — 验证通过或已达最大尝试次数，结束执行。
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

def _mark_todos_from_verification(
    todos: list[TodoItem],
    checks: list[dict],
    passed: bool,
) -> list[TodoItem]:
    """根据验证结果更新 todos 状态。"""
    if not todos:
        return todos

    if passed:
        return [
            TodoItem(
                id=t["id"],
                content=t["content"],
                status="completed",
                note=t.get("note", "验证通过"),
            )
            for t in todos
        ]

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
