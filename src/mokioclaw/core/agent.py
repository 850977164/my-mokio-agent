"""ReAct Agent —— stream_agent_events 事件流驱动的工具调用循环.

ReAct (Reasoning + Acting) 交替模式:
    LLM 推理 → 产出工具调用 → 执行工具 → 结果喂回 LLM → 继续推理
    直到 LLM 不再调用工具（任务完成）或达到最大循环次数。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from mokioclaw.core.state import RuntimeState
from mokioclaw.providers.openai_provider import create_model
from mokioclaw.tools.registry import build_tools

ACTOR_PROMPT = """You are the actor node in MokioClaw's ReAct workflow.

You implement the user's task using tools. Work inside the workspace only.

Rules:
- Use FileWriteTool for new files.
- Use FileReadTool before editing existing files.
- Use FileEditTool for focused edits.
- Use BashTool to run commands and test results.
- BashTool already runs inside the workspace. Use relative paths, never "cd /workspace".
- End with a concise summary of files changed and commands run.
"""


def stream_agent_events(
    task: str,
    *,
    workspace: Path,
    max_loops: int = 10,
    model: str | None = None,
) -> Iterator[dict]:
    """ReAct 循环：交替进行 LLM 推理和工具调用，以事件流形式产出。

    调用方（CLI / GUI / API）遍历此生成器，实时获取每一步的进展，
    无需等待整个任务完成。

    Args:
        task: 用户任务描述（自然语言）。
        workspace: 工作区根目录，所有文件/命令操作均限制在此范围内。
        max_loops: 最大推理-工具调用轮数，防止无限循环。默认 10。
        model: LLM 模型名称，None 则优先读取环境变量 MODEL，回退到 gpt-4o。

    Yields:
        dict 事件，统一格式 ``{"type": str, ...}``：

        - ``{"type": "ai_message", "content": str}``
          LLM 本轮推理产出的文本（可能为空，当 LLM 直接调用工具时）。

        - ``{"type": "tool_call", "name": str, "args": dict}``
          LLM 请求调用某个工具。``name`` 是工具名，``args`` 是传给工具的参数。

        - ``{"type": "tool_result", "name": str, "result": str}``
          工具执行完毕返回的结果字符串。

        - ``{"type": "final_answer", "content": str}``
          循环结束后的最终回答（最后一轮 AI 消息的 content）。
    """
    # 1. 创建运行时状态，固化 workspace 和 model
    if model is None:
        model = os.getenv("MODEL", "gpt-4o")
    state = RuntimeState(workspace=workspace, model=model)

    # 2. 构建消息列表 —— SystemMessage 设定角色规则，HumanMessage 承载用户任务
    messages: list = [
        SystemMessage(content=ACTOR_PROMPT),
        HumanMessage(content=task),
    ]

    # 3. 创建模型并绑定工具
    tools = build_tools(state)
    tool_map: dict[str, object] = {t.name: t for t in tools}
    llm = create_model(model=state.model)
    agent = llm.bind_tools(tools)

    last_ai_content = ""

    # 4. ReAct 循环
    for _ in range(max_loops):
        # 4a. LLM 推理：传入完整对话历史，获取 AI 回复
        response = agent.invoke(messages)
        messages.append(response)

        content = response.content or ""
        if content:
            last_ai_content = content
        yield {"type": "ai_message", "content": content}

        # 4b. 没有工具调用 → LLM 认为任务完成，退出循环
        if not response.tool_calls:
            break

        # 4c. 逐个执行工具调用
        for tool_call in response.tool_calls:
            tool_name: str = tool_call["name"]
            tool_args: dict = tool_call["args"]

            yield {"type": "tool_call", "name": tool_name, "args": tool_args}

            # 查找工具并执行
            tool = tool_map.get(tool_name)
            if tool is not None:
                try:
                    result = tool.invoke(tool_args)
                except Exception as exc:
                    result = f"工具执行异常: {exc}"
            else:
                result = f"错误: 未找到工具 '{tool_name}'"

            # 确保结果为字符串（所有内置工具都返回 str，此处防御性处理）
            result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

            # 将工具结果作为 ToolMessage 追加到对话历史
            tool_message = ToolMessage(
                content=result_str,
                tool_call_id=tool_call["id"],
            )
            messages.append(tool_message)

            yield {"type": "tool_result", "name": tool_name, "result": result_str}

    # 5. 循环结束，产出最终回答
    yield {"type": "final_answer", "content": last_ai_content}
