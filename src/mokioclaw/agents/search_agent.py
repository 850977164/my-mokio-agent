"""SearchAgent —— 专注于网络调研的搜索专家 Agent.

职责：
    接收 Planner 下达的研究任务，通过 WebSearchTool 搜索互联网，
    收集、整理并返回结构化的研究笔记（summary + sources）。

用法：
    result = run_search_agent(state, "研究 React 19 的新特性", writer=writer)
    # → {ok: True, summary, queries, sources, messages, tool_events}
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from mokioclaw.core.state import RuntimeState
from mokioclaw.providers.openai_provider import create_model
from mokioclaw.tools.web_search_tool import WebSearchTool

SEARCH_AGENT_PROMPT = """You are searchAgent, a focused research specialist.

Your only external capability is WebSearchTool. Search for reliable information
needed by the planner and codeAgent.

Rules:
- Use WebSearchTool for factual research.
- Prefer official or encyclopedia-style sources when available.
- Return a concise research summary and list the useful source URLs.
- Do not write files or produce application code."""


def run_search_agent(
    state: RuntimeState,
    instruction: str,
    *,
    writer: Any = None,
    max_loops: int = 4,
) -> dict:
    """运行搜索专家 Agent，执行网络调研任务。

    Args:
        state: 运行时状态（提供 model 等配置）。
        instruction: Planner 下达的研究指令。
        writer: LangGraph StreamWriter（可选），用于发射实时事件。
        max_loops: 最大 ReAct 轮数，默认 4。

    Returns:
        dict:
            ok (bool):      是否成功。
            summary (str):  研究总结。
            queries (list): 所有搜索查询列表。
            sources (list): 收集到的来源 URL 列表。
            messages (list): 完整对话消息历史。
            tool_events (list): 工具调用事件流。
    """
    # ── 1. 创建 model 并绑定 WebSearchTool ──
    llm = create_model(model=state.model, temperature=0.0)
    agent = llm.bind_tools([WebSearchTool])

    # ── 2. 构建消息 ──
    system_msg = SystemMessage(content=SEARCH_AGENT_PROMPT)
    user_msg = HumanMessage(content=(
        f"请完成以下研究任务:\n\n{instruction}\n\n"
        "请使用 WebSearch 工具搜索相关信息，然后整理一份简洁的研究总结，"
        "并列出你找到的有用来源 URL。"
    ))
    messages: list = [system_msg, user_msg]

    # ── 3. ReAct 循环 ──
    queries: list[str] = []
    sources: list[str] = []
    answers: list[str] = []
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

            # 记录查询关键词
            query = tool_args.get("query", "")
            if query:
                queries.append(query)

            # 发射 tool_call 事件
            event_tc = {"type": "tool_call", "tool": tool_name, "args": tool_args}
            tool_events.append(event_tc)
            if writer is not None:
                writer(event_tc)

            # 执行 WebSearchTool
            if tool_name == "WebSearch":
                raw = WebSearchTool.invoke(tool_args)
                try:
                    import json
                    parsed = json.loads(raw)
                except Exception:
                    parsed = {"ok": False, "error": str(raw)}

                # 收集答案和来源
                if parsed.get("ok"):
                    ans = parsed.get("answer", "")
                    if ans:
                        answers.append(ans)
                    for r in parsed.get("results", []) or []:
                        url = r.get("url", "")
                        if url and url not in sources:
                            sources.append(url)
                else:
                    answers.append(f"[搜索失败] {parsed.get('error', 'unknown')}")

                # 发射 search_results 事件
                event_sr = {"type": "search_results", "query": query, "results": parsed}
                tool_events.append(event_sr)
                if writer is not None:
                    writer(event_sr)
            else:
                # 未知工具，记录但继续
                tool_events.append({"type": "tool_result", "tool": tool_name, "result": "unknown tool"})

    # ── 4. 提取 AI 最终总结 ──
    last_content = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            last_content = msg.content
            break

    return {
        "ok": True,
        "summary": last_content,
        "queries": queries,
        "sources": sources,
        "messages": messages,
        "tool_events": tool_events,
    }
