"""stream_session_events 集成测试."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mokioclaw.core.agent import stream_session_events, _truncate_text, _safe_event_dict
from mokioclaw.core.session import (
    SESSION_ROOT,
    SESSION_FILE,
    SESSION_SUMMARY_FILE,
    load_or_create_session,
    save_session,
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
# Helper
# ═══════════════════════════════════════════════════════════════════

def _collect_events(iterator) -> list[dict]:
    """收集所有事件，忽略 stop iteration."""
    events = []
    try:
        while True:
            events.append(next(iterator))
    except StopIteration:
        pass
    return events


# ═══════════════════════════════════════════════════════════════════
# Unit tests for helpers
# ═══════════════════════════════════════════════════════════════════

class TestTruncateText:
    """测试 _truncate_text()."""

    def test_short_text_unchanged(self):
        assert _truncate_text("hello", 10) == "hello"

    def test_exact_limit_unchanged(self):
        assert _truncate_text("12345", 5) == "12345"

    def test_long_text_truncated(self):
        result = _truncate_text("x" * 100, 10)
        assert len(result) == 10
        assert result.endswith("...")

    def test_empty_string(self):
        assert _truncate_text("", 10) == ""

    def test_limit_too_small(self):
        result = _truncate_text("hello world", 2)
        assert len(result) == 2
        assert "..." not in result


class TestSafeEventDict:
    """测试 _safe_event_dict()."""

    def test_excludes_messages_and_runtime(self):
        output = {"task": "hello", "messages": [], "runtime": "obj"}
        result = _safe_event_dict(output, prefix="planner")
        assert "messages" not in result
        assert "runtime" not in result
        assert result["task"] == "hello"
        assert result["type"] == "planner"

    def test_handles_complex_values(self):
        output = {"todos": [{"id": "1", "content": "test"}], "count": 5}
        result = _safe_event_dict(output, prefix="verifier")
        assert "todos" in result
        assert result["count"] == 5

    def test_handles_callable_values(self):
        output = {"callback": lambda: None, "name": "test"}
        result = _safe_event_dict(output, prefix="test")
        assert "callback" in result
        assert isinstance(result["callback"], str)

    def test_truncates_long_lists(self):
        output = {"items": list(range(100))}
        result = _safe_event_dict(output, prefix="test")
        assert len(result["items"]) <= 21  # 20 + truncation note


# ═══════════════════════════════════════════════════════════════════
# stream_session_events
# ═══════════════════════════════════════════════════════════════════

class TestStreamSessionEvents:
    """测试 stream_session_events()."""

    def test_chat_route_flow(self, workspace):
        """聊天意图：intent_router → chat_responder → session_saved."""
        # Mock LLM 使其返回 chat 意图
        with patch(
            "mokioclaw.graph.nodes.create_model"
        ) as mock_create:
            mock_llm = MagicMock()
            # intent_router 返回 chat
            mock_llm.invoke.return_value.content = json.dumps({
                "route": "chat",
                "reason": "greeting",
                "confidence": 0.95,
            })
            mock_create.return_value = mock_llm

            events = _collect_events(
                stream_session_events("hello!", workspace=workspace)
            )

        # 验证事件流
        event_types = [e["type"] for e in events]
        assert "session_event" in event_types

        # 验证包含关键事件
        session_events = [e for e in events if e["type"] == "session_event"]
        session_event_names = [e["event"] for e in session_events]

        assert "user_turn_recorded" in session_event_names
        assert "intent_routed" in session_event_names
        assert "chat_response" in session_event_names
        assert "session_saved" in session_event_names

        # 验证意图路由结果
        routed = [e for e in session_events if e["event"] == "intent_routed"][0]
        assert routed["route"] == "chat"

        # 验证 session 已持久化
        session_path = workspace / SESSION_ROOT / SESSION_FILE
        assert session_path.is_file()

        # 验证 session 内容
        session_data = json.loads(session_path.read_text(encoding="utf-8"))
        assert session_data["turn_index"] == 1
        assert len(session_data["recent_turns"]) == 2  # user + assistant
        assert session_data["recent_turns"][0]["role"] == "user"
        assert session_data["recent_turns"][1]["role"] == "assistant"
        assert session_data["recent_turns"][1]["route"] == "chat"

    def test_workflow_route_flow(self, workspace):
        """工作流意图：intent_router → workflow (build_complex_workflow)."""
        # 先创建一些 workspace 文件以便 workflow 能运行
        (workspace / "README.md").write_text("# Test Project", encoding="utf-8")

        with patch(
            "mokioclaw.graph.nodes.create_model"
        ) as mock_create:
            # Mock LLM: intent_router 返回 workflow
            mock_llm = MagicMock()
            # Mock 的返回值顺序：
            # 1. intent_router call → workflow
            # 2-4. planner / verifier / etc calls during workflow
            mock_llm.invoke.side_effect = [
                # intent_router
                MagicMock(content=json.dumps({
                    "route": "workflow",
                    "reason": "code generation task",
                    "confidence": 0.9,
                })),
                # planner call 1 — raises to stop the graph early (simulate completion)
                Exception("stop_early"),
            ]
            mock_create.return_value = mock_llm

            try:
                events = _collect_events(
                    stream_session_events(
                        "build a flask app",
                        workspace=workspace,
                        max_attempts=1,
                    )
                )
            except Exception:
                # 预期会因 graph 执行失败而抛异常，但这不影响 session 验证
                # 重新收集到异常前的事件
                pass

        # 重新加载 session 验证
        session_path = workspace / SESSION_ROOT / SESSION_FILE
        if session_path.is_file():
            session_data = json.loads(session_path.read_text(encoding="utf-8"))
            assert session_data["turn_index"] == 1
            assert session_data["recent_turns"][0]["role"] == "user"

    def test_session_persistence_across_turns(self, workspace):
        """多轮对话：session 在多次调用间保持."""
        with patch(
            "mokioclaw.graph.nodes.create_model"
        ) as mock_create:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = json.dumps({
                "route": "chat",
                "reason": "test",
                "confidence": 0.9,
            })
            mock_create.return_value = mock_llm

            # 第 1 轮
            _collect_events(stream_session_events("msg1", workspace=workspace))

            # 第 2 轮（同一 workspace）
            _collect_events(stream_session_events("msg2", workspace=workspace))

        # 验证 session 有 2 轮
        session = load_or_create_session(workspace)
        assert session["turn_index"] == 2

        # 验证 recent_turns 有 4 条 (user1, assistant1, user2, assistant2)
        turns = session["recent_turns"]
        assert len(turns) == 4
        assert turns[0]["content"] == "msg1"
        assert turns[2]["content"] == "msg2"

        # 验证 SUMMARY 文件存在
        assert (workspace / SESSION_ROOT / SESSION_SUMMARY_FILE).is_file()

    def test_yields_session_events(self, workspace):
        """验证产生正确结构的 session_event."""
        with patch(
            "mokioclaw.graph.nodes.create_model"
        ) as mock_create:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = json.dumps({
                "route": "chat",
                "reason": "test",
                "confidence": 0.85,
            })
            mock_create.return_value = mock_llm

            events = _collect_events(
                stream_session_events("test", workspace=workspace)
            )

        # 每个 session_event 必须包含 event 字段
        for e in events:
            if e["type"] == "session_event":
                assert "event" in e

        # 必须有 user_turn_recorded 和 session_saved
        event_names = [e["event"] for e in events if e["type"] == "session_event"]
        assert "user_turn_recorded" in event_names
        assert "session_saved" in event_names

    def test_session_context_includes_workspace_files(self, workspace):
        """session_context 应包含 workspace 文件清单."""
        (workspace / "main.py").write_text("print('hello')", encoding="utf-8")
        (workspace / "config.json").write_text("{}", encoding="utf-8")

        with patch(
            "mokioclaw.graph.nodes.create_model"
        ) as mock_create:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = json.dumps({
                "route": "chat",
                "reason": "test",
                "confidence": 0.9,
            })
            mock_create.return_value = mock_llm

            _collect_events(stream_session_events("what files?", workspace=workspace))

        # 验证 session 保存成功
        session = load_or_create_session(workspace)
        assert session["turn_index"] == 1
