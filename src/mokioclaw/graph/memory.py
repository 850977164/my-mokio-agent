"""三层 Memory 系统 — Rules + Working Memory + History Summary Store.

Memory 作为 context engineer 的核心组件，在每个节点入口处调用
build_layered_memory() 构建三层记忆快照并注入至 LLM system prompt，
使 Agent 无需遍历全部历史消息即可感知任务全貌.

Usage:
    from mokioclaw.graph.memory import build_layered_memory, format_layered_memory_for_prompt
    memory = build_layered_memory(state, node="planner")
    prompt = format_layered_memory_for_prompt(memory)
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from mokioclaw.core.state import RuntimeState
from mokioclaw.graph.state import (
    LayeredMemory,
    MokioGraphState,
    SourceItem,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Rules Layer — 固定规则，所有节点共享
# ═══════════════════════════════════════════════════════════════════════════════

RULES_LAYER: dict[str, Any] = {
    "scope": "workspace",
    "storage": "internal",
    "rules": [
        "Work inside the current workspace only.",
        "Use paths relative to the workspace; do not prefix paths with workspace/",
        "Keep durable task context outside the raw messages transcript when possible.",
        (
            "Treat TODO.md as working plan state, "
            "NOTEPAD.md as durable notes, "
            "and HISTORY_SUMMARY.md as compressed history."
        ),
        "Do not expose memory write tools to agents; layered memory is assembled by the runtime.",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════

def _short_text(text: str, limit: int) -> str:
    """超长文本截断，末尾加 "..." 标记."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _trim_handoffs(handoffs: list[dict]) -> list[dict]:
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


def _trim_sources(sources: list[SourceItem]) -> list[dict]:
    """只保留 title + url，去掉 content."""
    if not sources:
        return []
    return [
        {"title": s.get("title", ""), "url": s.get("url", "")}
        for s in sources[:12]
    ]


def _read_notepad(runtime: RuntimeState) -> dict:
    """读取 NOTEPAD.md，返回 {exists, content}.

    文件可能不存在，调用方不抛异常。
    """
    path = runtime.workspace / "NOTEPAD.md"
    try:
        if not path.is_file():
            return {"exists": False, "content": ""}
        content = path.read_text(encoding="utf-8")
        return {"exists": True, "content": content}
    except OSError:
        return {"exists": False, "content": ""}


def _read_history_summary(runtime: RuntimeState) -> dict:
    """读取 HISTORY_SUMMARY.md，返回 {exists, content}.

    文件可能不存在，调用方不抛异常。
    """
    path = runtime.workspace / "HISTORY_SUMMARY.md"
    try:
        if not path.is_file():
            return {"exists": False, "content": ""}
        content = path.read_text(encoding="utf-8")
        return {"exists": True, "content": content}
    except OSError:
        return {"exists": False, "content": ""}


def _generate_session_id() -> str:
    """生成当前会话的稳定 ID。

    优先读 workspace 中的 SESSION_ID 文件，不存在则创建。
    """
    # 简单策略：使用 uuid4 的前 8 位作短 session id
    # 生产环境可改为持久化到文件
    return uuid.uuid4().hex[:8]


def _session_turn(state: MokioGraphState) -> int:
    """返回当前会话已完成的轮次（attempts 的累加值）。"""
    return state.get("attempts", 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 三层 Memory 构建
# ═══════════════════════════════════════════════════════════════════════════════

def build_layered_memory(
    state: MokioGraphState,
    *,
    node: str = "graph",
) -> LayeredMemory:
    """构建三层记忆快照。

    在每个节点入口处调用，将 state 中的相关字段
    摘要化为结构化的 LayeredMemory dict。

    Args:
        state: 当前图状态。
        node: 调用方节点名称，如 "planner" / "verifier"。

    Returns:
        LayeredMemory — 包含 rules / working_memory / history_summary_store 三层。
    """
    runtime: RuntimeState = state["runtime"]
    notepad = _read_notepad(runtime)
    history = _read_history_summary(runtime)
    history_summary_content = history.get("content", "")

    # ── 1. Working Memory ──
    working_memory: dict[str, Any] = {
        "node": node,
        "task": state.get("task", ""),
        "session_id": _generate_session_id(),
        "session_turn": _session_turn(state),
        "plan_summary": _short_text(state.get("plan_summary", ""), 800),
        "todos": state.get("todos", []),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "verification_commands": state.get("verification_commands", []),
        "research_notes": _short_text(state.get("research_notes", ""), 1600),
        "sources": _trim_sources(state.get("sources", [])),
        "agent_handoffs": _trim_handoffs(state.get("agent_handoffs", [])),
        "code_agent_summary": _short_text(state.get("code_agent_summary", ""), 1000),
        "verifier_summary": _short_text(state.get("verifier_summary", ""), 1000),
        "last_error": _short_text(state.get("last_error", ""), 1400),
        "attempts": state.get("attempts", 0),
        "max_attempts": state.get("max_attempts", 3),
    }

    # ── 2. History Summary Store ──
    history_summary_store: dict[str, Any] = {
        "history_path": "HISTORY_SUMMARY.md",
        "history_exists": history.get("exists", False),
        "history_summary": _short_text(history_summary_content, 2200),
        "notepad_path": "NOTEPAD.md",
        "notepad_exists": notepad.get("exists", False),
        "notepad": _short_text(notepad.get("content", ""), 1800),
        "context_summary": _short_text(state.get("context_summary", ""), 1600),
        "compression_events": (state.get("compression_events", []) or [])[-3:],
    }

    # ── 3. 合成 ──
    memory: LayeredMemory = {
        "rules": dict(RULES_LAYER),
        "working_memory": working_memory,
        "history_summary_store": history_summary_store,
    }

    # 同步回 state，供 debug/log 使用
    state["memory_snapshot"] = memory

    return memory


# ═══════════════════════════════════════════════════════════════════════════════
# 格式化输出
# ═══════════════════════════════════════════════════════════════════════════════

def memory_event(memory: LayeredMemory, *, node: str = "graph") -> dict:
    """创建 memory 注入事件，供 LangGraph StreamWriter 发射.

    Args:
        memory: build_layered_memory() 的返回值。
        node: 调用方节点名称。

    Returns:
        dict: {type: "memory_injection", node, memory}
    """
    return {
        "type": "memory_injection",
        "node": node,
        "memory": memory,
    }


def format_layered_memory_for_prompt(memory: LayeredMemory) -> str:
    """将 LayeredMemory 序列化为可嵌入 system prompt 的文本块.

    JSON 格式，确保 LLM 能正确解析结构化信息。

    Args:
        memory: build_layered_memory() 的返回值。

    Returns:
        格式化的 JSON 字符串，可直接追加到 system prompt 末尾。
    """
    return json.dumps(memory, ensure_ascii=False, indent=2)
