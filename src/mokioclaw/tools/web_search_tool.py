"""WebSearchTool —— 通过 Tavily API 执行网络搜索."""

from __future__ import annotations

import json
import os

from langchain_core.tools import StructuredTool


def _web_search(query: str, *, max_results: int = 5) -> str:
    """执行一次 Web 搜索并返回结构化结果。

    Args:
        query: 搜索查询字符串。
        max_results: 最大返回结果数，默认 5。

    Returns:
        JSON 字符串，格式:
            {ok, query, answer, results: [{title, url, content, score}]}
        或
            {ok: False, error: "..."}
    """
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return json.dumps({
            "ok": False,
            "error": "missing TAVILY_API_KEY — 未设置 TAVILY_API_KEY 环境变量，无法执行网络搜索。",
        }, ensure_ascii=False)

    try:
        from tavily import TavilyClient
    except ImportError:
        return json.dumps({
            "ok": False,
            "error": "tavily-python 未安装，请执行 `uv add tavily-python` 安装依赖。",
        }, ensure_ascii=False)

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            max_results=max_results,
            include_answer=True,
        )
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "error": f"Tavily 搜索异常: {exc}",
        }, ensure_ascii=False)

    results: list[dict] = []
    for r in response.get("results", []) or []:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": (r.get("content") or "")[:2000],
            "score": r.get("score", 0.0),
        })

    return json.dumps({
        "ok": True,
        "query": query,
        "answer": (response.get("answer") or "")[:3000],
        "results": results,
    }, ensure_ascii=False)


WebSearchTool = StructuredTool.from_function(
    func=_web_search,
    name="WebSearch",
    description=(
        "使用 Tavily 搜索引擎进行网络搜索，返回相关网页的摘要、URL 和评分。"
        "参数: query (搜索查询字符串), max_results (最大结果数, 默认5)。"
    ),
)
