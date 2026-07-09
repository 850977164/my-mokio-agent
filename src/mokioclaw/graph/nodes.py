"""MultiAgent 图节点 — 完整上下文工程流水线.

架构（简单图）:
    START → Planner → context_monitor → Verifier → context_monitor → Final → END
        ↑                                    │
        └────────────────────────────────────┘ (monitor_route → "planner"，retry)

架构（复杂图，含压缩）:
    START → Planner → context_monitor ⇄ context_compressor → Verifier → Final → END

Planner 通过工具调用委托任务:
    - TodoWriteTool:         发布/修订计划 → 产出 plan_summary/todos/acceptance_criteria
    - CallSearchAgentTool:   委托搜索任务给 searchAgent
    - CallCodeAgentTool:     委托实现任务给 codeAgent

Verifier: 运行验证命令 + LLM 只读检查 → ReportVerification 结构化判定

context_monitor:   估算 token → 条件触发压缩
context_compressor: LLM 压缩消息历史 → 换入精简摘要
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

from mokioclaw.agents.code_agent import run_code_agent
from mokioclaw.agents.search_agent import run_search_agent
from mokioclaw.core.state import RuntimeState
from mokioclaw.graph.state import (
    AgentHandoff,
    CompressionEvent,
    MokioGraphState,
    SourceItem,
    TodoItem,
    VerificationResult,
)
from mokioclaw.graph.memory import (
    build_layered_memory,
    format_layered_memory_for_prompt,
    memory_event,
)
from mokioclaw.prompts.stage3 import (
    PLANNER_PROMPT, VERIFIER_PROMPT,
    INTENT_ROUTER_PROMPT, CHAT_RESPONDER_PROMPT,
)
from mokioclaw.prompts.stage4 import CONTEXT_COMPRESSION_PROMPT
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

        # 构建分层 Memory 并发射事件
        memory = build_layered_memory(state, node="codeAgent")
        if writer is not None:
            writer(memory_event(memory, node="codeAgent"))

        result = run_code_agent(
            runtime, instruction,
            memory=memory,
            writer=writer,
        )

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
# Node input builders — 将 state + memory 拼接为 HumanMessage
# ═══════════════════════════════════════════════════════════════════════

def _planner_input(state: MokioGraphState, memory: dict) -> str:
    """构建 Planner 节点的 HumanMessage 内容."""
    memory_text = format_layered_memory_for_prompt(memory)
    existing_todos = state.get("todos", [])
    if not existing_todos:
        return (
            f"## 用户任务\n{state['task']}\n\n"
            f"## 分层记忆\n{memory_text}\n\n"
            "请先用 TodoWrite 发布执行计划，然后根据需要调用 CallSearchAgent 搜索信息，"
            "最后调用 CallCodeAgent 委托实现。"
        )
    research_notes = state.get("research_notes", "")
    last_error = state.get("last_error", "")
    return (
        f"## 原始任务\n{state['task']}\n\n"
        f"## 分层记忆\n{memory_text}\n\n"
        f"## 当前计划\n{_format_todos(existing_todos)}\n\n"
        f"## 验收标准\n"
        + "\n".join(f"  - {c}" for c in state.get("acceptance_criteria", []))
        + f"\n\n## 研究笔记\n{research_notes or '(无)'}\n\n"
        f"## 上次验证失败\n{last_error}\n\n"
        "请先用 TodoWrite 修订计划，然后调用 CallCodeAgent 委托修复。"
    )


def _verifier_input(
    state: MokioGraphState,
    memory: dict,
    verification_results: list[VerificationResult],
) -> str:
    """构建 Verifier 节点的 HumanMessage 内容."""
    memory_text = format_layered_memory_for_prompt(memory)
    code_summary = state.get("code_agent_summary", "")
    research_notes = state.get("research_notes", "")
    sources = state.get("sources", []) or []
    return (
        f"## 分层记忆\n{memory_text}\n\n"
        f"## 原始任务\n{state['task']}\n\n"
        f"## 计划摘要\n{state.get('plan_summary', '')}\n\n"
        f"## 验收标准\n"
        + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(state.get("acceptance_criteria", [])))
        + f"\n\n## 研究笔记\n{research_notes or '(无)'}\n\n"
        f"## 参考来源\n{_format_sources(sources)}\n\n"
        f"## codeAgent 执行总结\n{code_summary or '(无)'}\n\n"
        f"## 验证命令执行结果\n{_format_verification_results(verification_results)}\n\n"
        f"请使用只读工具检查 codeAgent 产出的文件内容，"
        f"然后调用 ReportVerification 提交你的验证判定。"
    )


# ═══════════════════════════════════════════════════════════════════════
# Helpers (提取/格式化)
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

    # ── 构建分层 Memory ──
    memory = build_layered_memory(state, node="planner")
    if writer is not None:
        writer(memory_event(memory, node="planner"))

    # ── 构建消息 ──
    messages: list = [
        SystemMessage(content=PLANNER_PROMPT),
        HumanMessage(content=_planner_input(state, memory)),
    ]

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

    # ── 提取 TodoWrite 调用，设置上下文路由 ──
    todo_call = _extract_tool_call(messages, "TodoWrite")
    update: dict = {}
    if todo_call is not None:
        plan_summary = todo_call.get("plan_summary", "")
        todos = todo_call.get("todos", [])
        acceptance_criteria = todo_call.get("acceptance_criteria", [])
        verification_commands = todo_call.get("verification_commands", [])
        update = {
            "plan_summary": plan_summary,
            "todos": todos,
            "acceptance_criteria": acceptance_criteria,
            "verification_commands": verification_commands,
            "context_next_node": "verifier",
        }
    else:
        update["context_next_node"] = "verifier"

    update["messages"] = messages
    return update


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

    # 2. 构建分层 Memory 并注入
    memory = build_layered_memory(state, node="verifier")

    # 3. 构建消息
    messages = [
        SystemMessage(content=VERIFIER_PROMPT),
        HumanMessage(content=_verifier_input(state, memory, verification_results)),
    ]

    # 4. LLM 验证（只读工具循环）
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
        "context_next_node": "planner" if not passed else "verifier",
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


def context_monitor_node(state: MokioGraphState) -> dict:
    """上下文监控节点 —— 估算 token 数，决定是否需要压缩。

    1. 估算当前 token 数：
       token_count = model.get_num_tokens_from_messages(messages + [memory_payload])
       如果异常，fallback 为 len(text) // 4
    2. 判断是否需要压缩：
       should_compress = token_count > context_token_limit (默认 400000)
    3. context_next_node 由上游节点设置（planner 后设为 "verifier"，
       verifier 失败后设为 "planner"）
    4. 返回：
       {
           "context_token_count": token_count,
           "context_should_compress": should_compress,
           "context_next_node": state.get("context_next_node", "verifier"),
       }
    """
    # ── 1. 获取当前上下文 token 上限 ──
    context_token_limit: int = state.get("context_token_limit", 400000)

    # ── 2. 构建 memory_payload ──
    memory = build_layered_memory(state, node="context_monitor")
    memory_text = format_layered_memory_for_prompt(memory)
    memory_payload = SystemMessage(content=memory_text)

    # ── 3. 估算消息 token 数（messages + memory_payload） ──
    messages: list = state.get("messages", []) or []
    model = create_model(model=state["runtime"].model, temperature=0.0)

    try:
        token_count = model.get_num_tokens_from_messages(messages + [memory_payload])
    except Exception:
        # fallback: 简单字符估算（每 4 个字符 ≈ 1 token）
        total_text = ""
        for msg in messages:
            if hasattr(msg, "content") and isinstance(msg.content, str):
                total_text += msg.content
        total_text += memory_text
        token_count = len(total_text) // 4

    # ── 4. 判断是否需要压缩 ──
    should_compress = token_count > context_token_limit

    # ── 5. context_next_node 由上游节点设定 ──
    next_node: str = state.get("context_next_node", "verifier")

    return {
        "context_token_count": token_count,
        "context_should_compress": should_compress,
        "context_next_node": next_node,
    }


def context_monitor_route(state: MokioGraphState) -> str:
    """上下文监控后的路由决策.

    Returns:
        "final" — 已通过验证.
        "context_compressor" — 需要压缩上下文.
        其他 — 由 context_next_node 决定.
    """
    if state.get("passed"):
        return "final"

    attempts = state.get("attempts", 0)
    max_attempts = state.get("max_attempts", 3)
    if attempts >= max_attempts:
        return "final"

    if state.get("context_should_compress"):
        return "context_compressor"
    return state.get("context_next_node", "verifier")


def context_compressor_route(state: MokioGraphState) -> str:
    """上下文压缩后的路由决策.

    压缩完成后不经过 monitor（刚压缩完不需要再压），
    直接由 context_next_node 决定下一步。

    Returns:
        "verifier" | "planner" | "final"
    """
    if state.get("passed"):
        return "final"
    return state.get("context_next_node", "verifier")


def context_compressor_node(state: MokioGraphState) -> dict:
    """上下文压缩节点 —— 用 LLM 压缩消息历史，保留关键信息。

    1. 用 LLM 压缩消息历史，保留关键信息：
       - 调用 create_model().invoke([
           SystemMessage(CONTEXT_COMPRESSION_PROMPT),
           HumanMessage(当前所有消息 + 分层 memory 快照)
         ])
       - LLM 返回 JSON:
         {summary, active_goal, completed_work, open_todos,
          important_files, tool_findings, sources, next_steps, risks}
    2. 替换消息历史为压缩后的摘要：
       - 用 RemoveMessage(id=REMOVE_ALL_MESSAGES) 清除所有旧消息
       - 添加一条 AIMessage(content=summary) 作为新的上下文起点
    3. 持久化到 HISTORY_SUMMARY.md
    4. 截断各字段的文本长度（_short_text）
    5. 返回压缩事件：
       {
           "messages": [RemoveMessage, AIMessage(summary)],
           "context_summary": summary,
           "context_token_count": 新 token 数,
           "context_should_compress": False,
           "research_notes": 截断后,
           "agent_handoffs": 截断后,
           ... 其他截断字段,
           "history_summary": summary,
           "compression_events": [...prev, 新事件],
       }
    """
    runtime: RuntimeState = state["runtime"]

    # ── 1. 构建分层 memory 快照 ──
    memory = build_layered_memory(state, node="context_compressor")
    memory_text = format_layered_memory_for_prompt(memory)

    # ── 2. 组装压缩 prompt ──
    messages: list = state.get("messages", []) or []
    payload_text = (
        f"## Layered Memory Snapshot\n{memory_text}\n\n"
        f"## Full Message History\n"
    )
    # 将消息序列化为紧凑文本表示
    msg_lines: list[str] = []
    for msg in messages:
        role = msg.type if hasattr(msg, "type") else type(msg).__name__
        content = msg.content if hasattr(msg, "content") else ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = json.dumps(content, ensure_ascii=False)
        else:
            text = str(content)
        msg_lines.append(f"[{role}] {text}")
    payload_text += "\n\n".join(msg_lines)

    # ── 3. 调用 LLM 压缩 ──
    model = create_model(model=runtime.model, temperature=0.0)
    response = model.invoke([
        SystemMessage(content=CONTEXT_COMPRESSION_PROMPT),
        HumanMessage(content=payload_text),
    ])

    # ── 4. 解析 LLM 返回的 JSON ──
    try:
        compressed = json.loads(response.content)
    except json.JSONDecodeError:
        # fallback: 直接使用原始输出作为 summary
        compressed = {"summary": response.content}

    summary: str = compressed.get("summary", response.content)
    active_goal: str = compressed.get("active_goal", "")
    completed_work: str = compressed.get("completed_work", "")
    open_todos: str = compressed.get("open_todos", "")
    important_files: str = compressed.get("important_files", "")
    tool_findings: str = compressed.get("tool_findings", "")
    sources: str = compressed.get("sources", "")
    next_steps: str = compressed.get("next_steps", "")
    risks: str = compressed.get("risks", "")

    # ── 5. 替换消息历史 ──
    remove = RemoveMessage(id=REMOVE_ALL_MESSAGES)
    new_msg = AIMessage(content=f"[Context Compressed]\n\n{summary}")

    # ── 6. 持久化到 HISTORY_SUMMARY.md ──
    history_path = runtime.workspace / "HISTORY_SUMMARY.md"
    try:
        history_path.write_text(summary, encoding="utf-8")
    except OSError:
        pass  # 写入失败不阻塞执行

    # ── 7. 重新估算 token 数 ──
    new_messages = [new_msg]
    try:
        new_token_count = model.get_num_tokens_from_messages(new_messages)
    except Exception:
        new_token_count = len(summary) // 4

    # ── 8. 计算压缩前 token 数 ──
    token_before = state.get("context_token_count", 0)

    # ── 9. 构建压缩事件 ──
    timestamp = datetime.now(timezone.utc).isoformat()
    event: CompressionEvent = CompressionEvent(
        timestamp=timestamp,
        trigger="token_limit",
        node="context_compressor",
        token_count_before=token_before,
        token_count_after=new_token_count,
        summary=summary,
    )
    prev_events: list[CompressionEvent] = state.get("compression_events", []) or []
    prev_events.append(event)

    # ── 10. 截断各字段 ──
    return {
        "messages": [remove, new_msg],
        "context_summary": _short_text(summary, 4000),
        "context_token_count": new_token_count,
        "context_should_compress": False,
        "research_notes": _short_text(state.get("research_notes", "") or "", 2000),
        "agent_handoffs": _trim_handoffs_local(state.get("agent_handoffs", []) or []),
        "compression_events": prev_events,
        "history_summary": summary,
        # 复现压缩产出供后续节点使用
        "active_goal": active_goal,
        "completed_work": completed_work,
        "open_todos": open_todos,
        "important_files": important_files,
        "tool_findings": tool_findings,
        "next_steps": next_steps,
        "risks": risks,
    }


# ═══════════════════════════════════════════════════════════════════════
# 内部辅助（节点实现细节）
# ═══════════════════════════════════════════════════════════════════════

def _short_text(text: str, limit: int) -> str:
    """超长文本截断，末尾加 "..." 标记."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _trim_handoffs_local(handoffs: list[dict]) -> list[dict]:
    """只保留最近 6 条交接记录，并精简字段."""
    if not handoffs:
        return []
    trimmed = handoffs[-6:]
    result: list[dict] = []
    for h in trimmed:
        result.append({
            "from_agent": h.get("from_agent", ""),
            "to_agent": h.get("to_agent", ""),
            "instruction": _short_text(h.get("instruction", ""), 200),
            "result": _short_text(h.get("result", ""), 200),
        })
    return result

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

def intent_router_node(state):
    runtime = state["runtime"]
    llm = create_model(model=runtime.model, temperature=0.0)
    user_input = state.get("last_user_input", "") or state.get("task", "")
    session_ctx = state.get("session_context", "")
    sep = chr(10)  # newline
    prompt_parts = [INTENT_ROUTER_PROMPT]
    if session_ctx:
        prompt_parts.append(sep + "--- Session Context ---" + sep + session_ctx[:3000])
    prompt_parts.append(sep + "--- Latest User Input ---" + sep + user_input)
    messages = [HumanMessage(content="".join(prompt_parts))]
    try:
        response = llm.invoke(messages)
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            lines = raw.split(sep)
            lines = [l for l in lines if not l.startswith("```")]
            raw = sep.join(lines).strip()
        result = json.loads(raw)
        route = result.get("route", "workflow")
        reason = result.get("reason", "")
        confidence = float(result.get("confidence", 0.0))
        if route not in ("chat", "workflow"):
            route, reason, confidence = "workflow", "invalid route", 0.0
        if confidence < 0.55:
            route, reason, confidence = "workflow", "low confidence", 0.0
    except (json.JSONDecodeError, ValueError, KeyError):
        route, reason, confidence = "workflow", "parse failed", 0.0
    return {"intent_route": route, "intent_reason": reason, "intent_confidence": confidence}

def chat_responder_node(state):
    runtime = state["runtime"]
    llm = create_model(model=runtime.model, temperature=0.3)
    user_input = state.get("last_user_input", "") or state.get("task", "")
    session_ctx = state.get("session_context", "")
    sep = chr(10)
    prompt_parts = [CHAT_RESPONDER_PROMPT]
    if session_ctx:
        prompt_parts.append(sep + "--- Session Context ---" + sep + session_ctx[:3000])
    prompt_parts.append(sep + "--- User Message ---" + sep + user_input)
    messages = [HumanMessage(content="".join(prompt_parts))]
    response = llm.invoke(messages)
    chat_reply = (response.content or "").strip()
    return {"chat_response": chat_reply, "final_answer": chat_reply}

def intent_route_fn(state):
    return "chat_responder" if state.get("intent_route") == "chat" else "planner"
