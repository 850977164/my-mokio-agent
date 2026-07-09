"""会话管理 —— 多轮对话的 session 持久化与上下文字符串构建.

为交互式 UI / 多轮对话场景提供会话状态管理：
- session.json 记录 session_id、turn_index、recent_turns
- SESSION_SUMMARY.md 生成人类可读的会话摘要
- build_session_context() 构建注入 intent_router / chat_responder 的上下文字符串

Usage::

    from mokioclaw.core.session import (
        load_or_create_session,
        append_user_turn,
        append_assistant_turn,
        save_session,
        build_session_context,
    )

    session = load_or_create_session(workspace)
    turn = append_user_turn(session, "帮我搭建一个 Flask 后台")
    append_assistant_turn(session, turn=turn, route="workflow", content="...", summary="创建了 Flask 项目")
    save_session(workspace, session)
    ctx = build_session_context(workspace, session)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# ═══════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════

SESSION_ROOT = ".mokioclaw/session"
SESSION_FILE = "session.json"
SESSION_SUMMARY_FILE = "SESSION_SUMMARY.md"
MAX_SESSION_CONTEXT = 7000
MAX_TURN_CONTENT = 4000

# workspace 文件清单中要排除的目录
_MANIFEST_EXCLUDE = {".mokioclaw", ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".eggs"}

# 构建上下文字符串时最多展示的文件数和轮次数
_MAX_CONTEXT_FILES = 30
_MAX_CONTEXT_TURNS = 10


# ═══════════════════════════════════════════════════════════════════
# load_or_create_session
# ═══════════════════════════════════════════════════════════════════

def load_or_create_session(workspace: Path) -> dict:
    """加载或创建 session.json，包含 session_id, turn_index, recent_turns.

    流程：
    1. 若 session.json 存在，加载并返回
    2. 否则创建全新的 session 字典

    Args:
        workspace: 工作区根目录（已 resolve 的绝对路径）。

    Returns:
        session 字典，包含 session_id, turn_index, recent_turns, created_at, updated_at。
    """
    session_dir = workspace / SESSION_ROOT
    session_path = session_dir / SESSION_FILE

    if session_path.is_file():
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
            # 确保必要字段存在（兼容旧版本）
            data.setdefault("session_id", uuid4().hex)
            data.setdefault("turn_index", 0)
            data.setdefault("recent_turns", [])
            data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            data.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
            return data
        except (json.JSONDecodeError, OSError):
            pass  # 文件损坏则重建

    # 创建新 session
    now = datetime.now(timezone.utc).isoformat()
    return {
        "session_id": uuid4().hex,
        "turn_index": 0,
        "recent_turns": [],
        "created_at": now,
        "updated_at": now,
    }


# ═══════════════════════════════════════════════════════════════════
# append_user_turn
# ═══════════════════════════════════════════════════════════════════

def append_user_turn(session: dict, content: str) -> int:
    """记录用户输入，返回 turn 编号。

    将 turn_index +1 后创建新的 user turn 条目并追加到 recent_turns。
    内容超过 MAX_TURN_CONTENT 时自动截断。

    Args:
        session: load_or_create_session() 返回的 session 字典（原地修改）。
        content: 用户输入的原始文本。

    Returns:
        新的 turn 编号。
    """
    session["turn_index"] = session.get("turn_index", 0) + 1
    turn_num = session["turn_index"]

    turn_entry = {
        "turn": turn_num,
        "role": "user",
        "content": _truncate_text(content, MAX_TURN_CONTENT),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    session.setdefault("recent_turns", []).append(turn_entry)
    return turn_num


# ═══════════════════════════════════════════════════════════════════
# append_assistant_turn
# ═══════════════════════════════════════════════════════════════════

def append_assistant_turn(
    session: dict,
    *,
    turn: int,
    route: str,
    content: str,
    summary: str = "",
) -> None:
    """记录助手回复，route="chat"|"workflow"。

    content 和 summary 超过 MAX_TURN_CONTENT 时自动截断。

    Args:
        session: load_or_create_session() 返回的 session 字典（原地修改）。
        turn: 对应的 turn 编号（应为 append_user_turn 的返回值）。
        route: 路由类型，``"chat"`` 或 ``"workflow"``。
        content: 助手回复的完整文本。
        summary: 可选的简短摘要，用于 SESSION_SUMMARY.md 和上下文字符串。
    """
    turn_entry = {
        "turn": turn,
        "role": "assistant",
        "route": route,
        "content": _truncate_text(content, MAX_TURN_CONTENT),
        "summary": _truncate_text(summary, MAX_TURN_CONTENT),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    session.setdefault("recent_turns", []).append(turn_entry)


# ═══════════════════════════════════════════════════════════════════
# save_session
# ═══════════════════════════════════════════════════════════════════

def save_session(workspace: Path, session: dict) -> dict:
    """保存 session.json，同时生成 SESSION_SUMMARY.md。

    确保 .mokioclaw/session/ 目录存在，写入 session.json 和
    人类可读的 SESSION_SUMMARY.md。

    Args:
        workspace: 工作区根目录（已 resolve 的绝对路径）。
        session: 当前 session 字典。

    Returns:
        更新了 updated_at 后的 session 字典。
    """
    session_dir = workspace / SESSION_ROOT
    session_dir.mkdir(parents=True, exist_ok=True)

    # 更新时间戳
    session["updated_at"] = datetime.now(timezone.utc).isoformat()

    # 写入 session.json
    session_path = session_dir / SESSION_FILE
    session_path.write_text(
        json.dumps(session, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # 生成 SESSION_SUMMARY.md
    summary_md = _build_session_summary_markdown(session)
    summary_path = session_dir / SESSION_SUMMARY_FILE
    summary_path.write_text(summary_md, encoding="utf-8")

    return session


# ═══════════════════════════════════════════════════════════════════
# build_session_context
# ═══════════════════════════════════════════════════════════════════

def build_session_context(workspace: Path, session: dict | None = None) -> str:
    """构建 session 上下文字符串，供 intent_router 和 chat_responder 使用。

    内容包含：
    - session_id, turn_index
    - workspace 文件清单（最近 30 个文件）
    - 最近 10 轮对话的摘要
    - 总长度不超过 MAX_SESSION_CONTEXT

    Args:
        workspace: 工作区根目录（已 resolve 的绝对路径）。
        session: 可选的 session 字典；为 None 时尝试从 workspace 加载。

    Returns:
        构建好的上下文字符串，供注入 LLM prompt 使用。
    """
    if session is None:
        session = _try_load_session(workspace)

    parts: list[str] = []

    # ── 1. Session 头部 ──
    if session:
        sid = session.get("session_id", "?")
        ti = session.get("turn_index", 0)
        parts.append(f"Session: {sid} | Turn: {ti}")
    else:
        parts.append("Session: (none)")

    # ── 2. Workspace 文件清单 ──
    file_listing = _build_file_listing(workspace, max_files=_MAX_CONTEXT_FILES)
    if file_listing:
        parts.append(f"\n--- Workspace Files (recent {_MAX_CONTEXT_FILES}) ---")
        parts.append(file_listing)

    # ── 3. Recent Turns 摘要 ──
    if session:
        turns_text = _build_turns_summary(session, max_turns=_MAX_CONTEXT_TURNS)
        if turns_text:
            parts.append(f"\n--- Recent Turns (last {_MAX_CONTEXT_TURNS}) ---")
            parts.append(turns_text)

    # 合并并截断
    full = "\n".join(parts)
    return _truncate_text(full, MAX_SESSION_CONTEXT)


# ═══════════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════════

def _truncate_text(text: str, limit: int) -> str:
    """超长文本截断，末尾加 "..." 标记（"..." 计入 limit 内）."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[:limit - 3] + "..."


def _try_load_session(workspace: Path) -> dict | None:
    """尝试从 workspace 加载 session，失败返回 None."""
    session_path = workspace / SESSION_ROOT / SESSION_FILE
    if not session_path.is_file():
        return None
    try:
        return json.loads(session_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _build_file_listing(workspace: Path, max_files: int) -> str:
    """生成 workspace 文件清单（按修改时间倒序，最多 max_files 个）。

    排除 .mokioclaw / .git 等目录内的文件。
    """
    entries: list[tuple[str, float]] = []  # (rel_path, mtime)
    try:
        for p in workspace.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(workspace)
            if rel.parts and rel.parts[0] in _MANIFEST_EXCLUDE:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            entries.append((str(rel).replace("\\", "/"), mtime))
    except OSError:
        return ""

    if not entries:
        return ""

    # 按修改时间倒序，取最近 max_files 个
    entries.sort(key=lambda e: e[1], reverse=True)
    lines = [f"- {path}" for path, _ in entries[:max_files]]
    return "\n".join(lines)


def _build_turns_summary(session: dict, max_turns: int) -> str:
    """从 recent_turns 中提取最近 N 轮的摘要文本。

    每个 turn 一行，格式：
        [Turn N] User: <截断内容>
        [Turn N] Assistant (route): <summary 或截断内容>
    """
    turns: list[dict] = session.get("recent_turns", [])
    if not turns:
        return ""

    # 取最近 max_turns 个条目（可能覆盖不到 max_turns 轮，因为是按条目数算的）
    recent = turns[-max_turns * 2:]  # 每轮有 user + assistant 两个条目

    lines: list[str] = []
    for entry in recent:
        turn_num = entry.get("turn", "?")
        role = entry.get("role", "?")
        if role == "user":
            content = _truncate_text(entry.get("content", ""), 150)
            lines.append(f"[Turn {turn_num}] User: {content}")
        elif role == "assistant":
            route = entry.get("route", "?")
            # 优先使用 summary，其次使用截断后的 content
            summary = entry.get("summary", "")
            if summary:
                text = _truncate_text(summary, 150)
            else:
                text = _truncate_text(entry.get("content", ""), 150)
            lines.append(f"[Turn {turn_num}] Assistant ({route}): {text}")

    return "\n".join(lines)


def _build_session_summary_markdown(session: dict) -> str:
    """生成人类可读的 SESSION_SUMMARY.md 内容."""
    sid = session.get("session_id", "?")
    ti = session.get("turn_index", 0)
    created = session.get("created_at", "?")
    updated = session.get("updated_at", "?")
    turns: list[dict] = session.get("recent_turns", [])

    lines = [
        f"# 📝 MokioClaw 会话摘要",
        "",
        f"**Session ID:** `{sid}`",
        f"**轮次:** {ti}",
        f"**创建时间:** {created}",
        f"**更新时间:** {updated}",
        "",
        "---",
        "",
    ]

    if not turns:
        lines.append("*(暂无对话记录)*")
        lines.append("")
        return "\n".join(lines)

    lines.append("## 💬 对话记录")
    lines.append("")
    lines.append("| Turn | 角色 | 路由 | 内容 | 时间 |")
    lines.append("|------|------|------|------|------|")

    for entry in turns:
        turn_num = entry.get("turn", "?")
        role = entry.get("role", "?")
        role_icon = "👤" if role == "user" else "🤖"
        route = entry.get("route", "-")
        ts = entry.get("timestamp", "")[:19].replace("T", " ")

        if role == "assistant":
            # 优先展示 summary
            text = entry.get("summary") or entry.get("content", "")
        else:
            text = entry.get("content", "")

        text = _truncate_text(text, 120)
        # 转义 Markdown 表格中的竖线
        text = text.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {turn_num} | {role_icon} {role} | {route} | {text} | {ts} |")

    lines.append("")
    return "\n".join(lines)
