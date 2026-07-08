"""CodeAgent —— 专注于代码实现的专家 Agent.

职责：
    接收 Planner 下达的实现指令，在 workspace 中使用文件/Shell 工具
    完成代码编写、修改和验证工作。

用法：
    result = run_code_agent(state, instruction, memory_text=memory_text, writer=writer)
    # → {ok: True, summary, todos, messages, tool_events}
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from mokioclaw.core.state import RuntimeState
from mokioclaw.providers.openai_provider import create_model
from mokioclaw.tools.registry import build_tools
from mokioclaw.tools.structured_tools import TodoUpdateTool

CODE_AGENT_PROMPT = """You are codeAgent, a focused implementation specialist.

You implement the planner's instruction inside the workspace using file and
shell tools.

Rules:
- You must update todo progress explicitly.
- Before starting a todo, call TodoUpdateTool with status "in_progress".
- After finishing that todo, call TodoUpdateTool with status "completed".
- If a todo is impossible, call TodoUpdateTool with status "blocked" and explain.
- Use FileWriteTool for new files.
- Use FileReadTool before editing existing files.
- Use FileEditTool for focused edits.
- Use BashTool for non-interactive checks.
- Use NotepadAppendTool to record durable findings, decisions, important files,
  blockers, and next-step context that should survive compression.
- Use NotepadReadTool when you need to recover prior notes.
- BashTool already runs inside the workspace. Use relative paths, never "cd /workspace".
- Incorporate research notes and source URLs when the task asks for researched content.
- End with a concise summary of files changed and checks run."""


def _code_agent_input(instruction: str, *, memory: dict | None = None) -> str:
    """构建 codeAgent 的 HumanMessage 内容."""
    lines: list[str] = [
        "请完成以下实现任务:",
        "",
        instruction,
        "",
        "请先阅读相关文件，然后按步骤实现。每完成一个步骤，请用 TodoUpdate 更新进度。",
    ]
    if memory is not None:
        # 延迟导入避免循环依赖
        from mokioclaw.graph.memory import format_layered_memory_for_prompt  # noqa: PLC0415
        memory_text = format_layered_memory_for_prompt(memory)
        lines.insert(1, f"## 分层记忆\n{memory_text}\n")
    return "\n\n".join(lines)


def run_code_agent(
    state: RuntimeState,
    instruction: str,
    *,
    writer: Any = None,
    memory: dict | None = None,
    max_loops: int = 10,
) -> dict:
    """运行代码专家 Agent，在 workspace 中完成实现任务。

    Args:
        state: 运行时状态（提供 model、workspace 等配置）。
        instruction: Planner 下达的实现指令（含任务 + 计划 + 研究笔记等）。
        writer: LangGraph StreamWriter（可选），用于发射实时事件。
        memory: build_layered_memory() 返回的分层记忆快照，
                如为 None 则不注入 memory。
        max_loops: 最大 ReAct 轮数，默认 10。

    Returns:
        dict:
            ok (bool):      是否成功。
            summary (str):  代码实现总结。
            todos (list):   更新后的 todos 列表。
            messages (list):完整对话消息历史。
            tool_events (list): 工具调用事件流。
    """
    # ── 1. 创建 model 并绑定工具 ──
    tools = build_tools(state) + [TodoUpdateTool]
    tool_map: dict[str, object] = {t.name: t for t in tools}

    llm = create_model(model=state.model, temperature=0.0)
    agent = llm.bind_tools(tools)

    # ── 2. 构建消息 ──
    messages = [
        SystemMessage(content=CODE_AGENT_PROMPT),
        HumanMessage(content=_code_agent_input(instruction, memory=memory)),
    ]

    # ── 3. ReAct 循环 ──
    todos: list[dict] = []   # 从 TodoUpdate 调用中收集的 todo 状态变更
    tool_events: list[dict] = []

    for _loop in range(max_loops):
        response = agent.invoke(messages)
        messages.append(response)

        # 无工具调用 → LLM 认为任务完成
        if not response.tool_calls:
            break

        for tool_call in response.tool_calls:
            tool_name: str = tool_call["name"]
            tool_args: dict = tool_call["args"]

            # 发射 tool_call 事件
            event_tc = {"type": "tool_call", "tool": tool_name, "args": tool_args}
            tool_events.append(event_tc)
            if writer is not None:
                writer(event_tc)

            # 执行工具
            tool = tool_map.get(tool_name)
            if tool is not None:
                try:
                    result = tool.invoke(tool_args)
                except Exception as exc:
                    result = f"工具执行异常: {exc}"
            else:
                result = f"错误: 未找到工具 '{tool_name}'"

            # 收集 TodoUpdate 调用
            if tool_name == "TodoUpdate":
                todos.append({
                    "id": tool_args.get("id", ""),
                    "status": tool_args.get("status", ""),
                    "note": tool_args.get("note", ""),
                })

            # 发射 tool_result 事件
            result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            event_tr = {"type": "tool_result", "tool": tool_name, "result_preview": result_str[:500]}
            tool_events.append(event_tr)
            if writer is not None:
                writer(event_tr)

            messages.append(ToolMessage(
                content=result_str,
                tool_call_id=tool_call["id"],
            ))

    # ── 4. 提取 AI 最终总结 ──
    last_content = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            last_content = msg.content
            break

    return {
        "ok": True,
        "summary": last_content,
        "todos": todos,
        "messages": messages,
        "tool_events": tool_events,
    }
