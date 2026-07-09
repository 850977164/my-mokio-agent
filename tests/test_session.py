"""会话管理单元测试."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mokioclaw.core.session import (
    SESSION_FILE,
    SESSION_ROOT,
    SESSION_SUMMARY_FILE,
    MAX_SESSION_CONTEXT,
    MAX_TURN_CONTENT,
    load_or_create_session,
    append_user_turn,
    append_assistant_turn,
    save_session,
    build_session_context,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def workspace():
    """创建临时 workspace 目录."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# ═══════════════════════════════════════════════════════════════════
# load_or_create_session
# ═══════════════════════════════════════════════════════════════════

class TestLoadOrCreateSession:
    """测试 load_or_create_session()."""

    def test_creates_new_session_when_no_file(self, workspace):
        session = load_or_create_session(workspace)
        assert "session_id" in session
        assert session["turn_index"] == 0
        assert session["recent_turns"] == []
        assert "created_at" in session
        assert "updated_at" in session
        # 不产生文件（save 后才写入）
        assert not (workspace / SESSION_ROOT / SESSION_FILE).exists()

    def test_loads_existing_session(self, workspace):
        # 先保存一个 session
        session = load_or_create_session(workspace)
        append_user_turn(session, "hello")
        save_session(workspace, session)

        # 重新加载
        loaded = load_or_create_session(workspace)
        assert loaded["session_id"] == session["session_id"]
        assert loaded["turn_index"] == 1
        assert len(loaded["recent_turns"]) == 1

    def test_recovers_from_corrupted_file(self, workspace):
        session_dir = workspace / SESSION_ROOT
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / SESSION_FILE).write_text("not valid json{{{", encoding="utf-8")

        session = load_or_create_session(workspace)
        assert session["turn_index"] == 0
        assert session["recent_turns"] == []


# ═══════════════════════════════════════════════════════════════════
# append_user_turn
# ═══════════════════════════════════════════════════════════════════

class TestAppendUserTurn:
    """测试 append_user_turn()."""

    def test_returns_incremented_turn_number(self, workspace):
        session = load_or_create_session(workspace)
        assert append_user_turn(session, "msg1") == 1
        assert append_user_turn(session, "msg2") == 2
        assert append_user_turn(session, "msg3") == 3

    def test_appends_to_recent_turns(self, workspace):
        session = load_or_create_session(workspace)
        append_user_turn(session, "hello world")
        assert len(session["recent_turns"]) == 1
        entry = session["recent_turns"][0]
        assert entry["turn"] == 1
        assert entry["role"] == "user"
        assert entry["content"] == "hello world"
        assert "timestamp" in entry

    def test_truncates_long_content(self, workspace):
        session = load_or_create_session(workspace)
        long_msg = "x" * (MAX_TURN_CONTENT + 100)
        append_user_turn(session, long_msg)
        entry = session["recent_turns"][0]
        assert len(entry["content"]) <= MAX_TURN_CONTENT
        assert entry["content"].endswith("...")


# ═══════════════════════════════════════════════════════════════════
# append_assistant_turn
# ═══════════════════════════════════════════════════════════════════

class TestAppendAssistantTurn:
    """测试 append_assistant_turn()."""

    def test_appends_chat_route(self, workspace):
        session = load_or_create_session(workspace)
        turn = append_user_turn(session, "how are you?")
        append_assistant_turn(session, turn=turn, route="chat", content="I'm fine!")

        assert len(session["recent_turns"]) == 2
        entry = session["recent_turns"][1]
        assert entry["turn"] == turn
        assert entry["role"] == "assistant"
        assert entry["route"] == "chat"
        assert entry["content"] == "I'm fine!"

    def test_appends_workflow_route(self, workspace):
        session = load_or_create_session(workspace)
        turn = append_user_turn(session, "build a flask app")
        append_assistant_turn(
            session,
            turn=turn,
            route="workflow",
            content="Created Flask app with routes...",
            summary="搭建了 Flask 项目骨架",
        )

        entry = session["recent_turns"][1]
        assert entry["route"] == "workflow"
        assert entry["summary"] == "搭建了 Flask 项目骨架"

    def test_truncates_long_content_and_summary(self, workspace):
        session = load_or_create_session(workspace)
        turn = append_user_turn(session, "test")
        long_text = "y" * (MAX_TURN_CONTENT + 200)
        append_assistant_turn(
            session, turn=turn, route="chat", content=long_text, summary=long_text
        )
        entry = session["recent_turns"][1]
        assert len(entry["content"]) <= MAX_TURN_CONTENT
        assert len(entry["summary"]) <= MAX_TURN_CONTENT


# ═══════════════════════════════════════════════════════════════════
# save_session
# ═══════════════════════════════════════════════════════════════════

class TestSaveSession:
    """测试 save_session()."""

    def test_creates_directory_and_files(self, workspace):
        session = load_or_create_session(workspace)
        append_user_turn(session, "hello")
        save_session(workspace, session)

        session_path = workspace / SESSION_ROOT / SESSION_FILE
        summary_path = workspace / SESSION_ROOT / SESSION_SUMMARY_FILE
        assert session_path.is_file()
        assert summary_path.is_file()

    def test_session_json_is_valid(self, workspace):
        session = load_or_create_session(workspace)
        append_user_turn(session, "test message")
        saved = save_session(workspace, session)

        # 从文件重新读取验证
        session_path = workspace / SESSION_ROOT / SESSION_FILE
        loaded = json.loads(session_path.read_text(encoding="utf-8"))
        assert loaded["session_id"] == session["session_id"]
        assert loaded["turn_index"] == 1
        assert loaded["recent_turns"][0]["content"] == "test message"

    def test_updates_timestamp(self, workspace):
        session = load_or_create_session(workspace)
        original_ts = session["updated_at"]
        saved = save_session(workspace, session)
        assert saved["updated_at"] != original_ts

    def test_generates_summary_markdown(self, workspace):
        session = load_or_create_session(workspace)
        append_user_turn(session, "帮我搭建Flask后台")
        append_assistant_turn(
            session, turn=1, route="workflow",
            content="已创建 Flask 项目...", summary="创建 Flask 项目骨架"
        )
        save_session(workspace, session)

        summary_path = workspace / SESSION_ROOT / SESSION_SUMMARY_FILE
        md = summary_path.read_text(encoding="utf-8")
        assert "MokioClaw 会话摘要" in md
        assert "Flask" in md
        assert "👤" in md
        assert "🤖" in md


# ═══════════════════════════════════════════════════════════════════
# build_session_context
# ═══════════════════════════════════════════════════════════════════

class TestBuildSessionContext:
    """测试 build_session_context()."""

    def test_returns_session_header(self, workspace):
        session = load_or_create_session(workspace)
        ctx = build_session_context(workspace, session)
        assert "Session:" in ctx
        assert session["session_id"] in ctx
        assert "Turn: 0" in ctx

    def test_includes_turns_summary(self, workspace):
        session = load_or_create_session(workspace)
        append_user_turn(session, "hello")
        append_assistant_turn(
            session, turn=1, route="chat",
            content="Hi there!", summary="打招呼"
        )
        ctx = build_session_context(workspace, session)
        assert "Recent Turns" in ctx
        assert "hello" in ctx
        assert "打招呼" in ctx

    def test_loads_session_when_none_provided(self, workspace):
        session = load_or_create_session(workspace)
        append_user_turn(session, "test")
        save_session(workspace, session)

        ctx = build_session_context(workspace, session=None)
        assert "test" in ctx

    def test_handles_no_session_gracefully(self, workspace):
        ctx = build_session_context(workspace, session=None)
        assert "Session: (none)" in ctx

    def test_respects_max_context_length(self, workspace):
        session = load_or_create_session(workspace)
        # 添加大量对话
        for i in range(50):
            append_user_turn(session, f"message {i} " + "padding " * 20)
            append_assistant_turn(
                session, turn=i + 1, route="chat",
                content=f"reply {i} " + "data " * 20,
                summary=f"summary {i}"
            )
        ctx = build_session_context(workspace, session)
        assert len(ctx) <= MAX_SESSION_CONTEXT

    def test_includes_workspace_file_listing(self, workspace):
        # 创建一些测试文件
        (workspace / "README.md").write_text("# Test", encoding="utf-8")
        (workspace / "src").mkdir(exist_ok=True)
        (workspace / "src" / "main.py").write_text("print('hello')", encoding="utf-8")

        session = load_or_create_session(workspace)
        ctx = build_session_context(workspace, session)
        assert "Workspace Files" in ctx
        assert "README.md" in ctx
        assert "src/main.py" in ctx

    def test_excludes_mokioclaw_dir_from_file_listing(self, workspace):
        (workspace / "README.md").write_text("# Test", encoding="utf-8")
        mokioclaw_dir = workspace / ".mokioclaw"
        mokioclaw_dir.mkdir(parents=True, exist_ok=True)
        (mokioclaw_dir / "internal.json").write_text("{}", encoding="utf-8")

        session = load_or_create_session(workspace)
        ctx = build_session_context(workspace, session)
        assert "README.md" in ctx
        assert "internal.json" not in ctx


# ═══════════════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════════════

class TestSessionIntegration:
    """端到端集成测试."""

    def test_full_session_lifecycle(self, workspace):
        # 1. 创建 session
        session = load_or_create_session(workspace)
        sid = session["session_id"]
        assert session["turn_index"] == 0

        # 2. 第1轮：打招呼
        t1 = append_user_turn(session, "你好，请帮我搭建Flask后台")
        assert t1 == 1
        append_assistant_turn(
            session, turn=t1, route="workflow",
            content="好的，我来搭建Flask后台管理系统...",
            summary="搭建Flask后台项目骨架，包含路由和模板"
        )

        # 3. 第2轮：追问
        t2 = append_user_turn(session, "添加用户登录功能")
        assert t2 == 2
        append_assistant_turn(
            session, turn=t2, route="workflow",
            content="已添加登录路由、JWT认证...",
            summary="实现用户登录和JWT认证"
        )

        # 4. 第3轮：闲聊
        t3 = append_user_turn(session, "这个项目用了什么技术栈？")
        assert t3 == 3
        append_assistant_turn(
            session, turn=t3, route="chat",
            content="项目使用Flask + SQLAlchemy + JWT...",
            summary="解释项目技术栈"
        )

        # 5. 保存
        saved = save_session(workspace, session)
        assert saved["turn_index"] == 3
        assert len(saved["recent_turns"]) == 6  # 3 user + 3 assistant

        # 6. 验证文件
        assert (workspace / SESSION_ROOT / SESSION_FILE).is_file()
        assert (workspace / SESSION_ROOT / SESSION_SUMMARY_FILE).is_file()

        # 7. 重新加载
        loaded = load_or_create_session(workspace)
        assert loaded["session_id"] == sid
        assert loaded["turn_index"] == 3
        assert len(loaded["recent_turns"]) == 6

        # 8. 构建 context
        ctx = build_session_context(workspace, loaded)
        assert "Flask" in ctx or "flask" in ctx
        assert len(ctx) <= MAX_SESSION_CONTEXT

        # 9. 验证 context 包含关键信息
        assert loaded["session_id"] in ctx
        assert "Turn: 3" in ctx

    def test_session_persistence_across_reloads(self, workspace):
        # 首次使用
        s1 = load_or_create_session(workspace)
        append_user_turn(s1, "msg1")
        save_session(workspace, s1)

        # 模拟"重启"后重新加载
        s2 = load_or_create_session(workspace)
        assert s2["session_id"] == s1["session_id"]
        assert s2["turn_index"] == 1

        # 继续追加
        append_user_turn(s2, "msg2")
        save_session(workspace, s2)

        s3 = load_or_create_session(workspace)
        assert s3["turn_index"] == 2
        assert len(s3["recent_turns"]) == 2
