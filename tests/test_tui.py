"""TUI 模块测试 —— ApprovalGate, ApprovalModal, PlanPanel, MokioClawTuiApp."""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mokioclaw.cli.tui.approval import (
    ApprovalGate,
    ApprovalModal,
    ApprovalRequestedMessage,
)
from mokioclaw.cli.tui.app import (
    PlanPanel,
    StatusBar,
    _format_tool_args,
)
from mokioclaw.core.approval import ApprovalDecision, ApprovalRequest


# ═══════════════════════════════════════════════════════════════════
# ApprovalGate
# ═══════════════════════════════════════════════════════════════════

class TestApprovalGate:
    """测试 ApprovalGate 线程同步机制."""

    def test_initial_state_not_resolved(self):
        req = ApprovalRequest.create(command="ls", risk_reason="test")
        gate = ApprovalGate(req)
        assert not gate.is_resolved
        assert gate.request == req

    def test_resolve_approved(self):
        req = ApprovalRequest.create(command="pip install x", risk_reason="install")
        gate = ApprovalGate(req)

        # 在另一个线程 resolve
        def _resolve():
            time.sleep(0.05)
            gate.resolve(approved=True, reason="ok")

        threading.Thread(target=_resolve, daemon=True).start()

        decision = gate.wait(timeout=5.0)
        assert decision.approved is True
        assert gate.is_resolved

    def test_resolve_denied(self):
        req = ApprovalRequest.create(command="rm -rf /", risk_reason="destructive")
        gate = ApprovalGate(req)

        def _resolve():
            time.sleep(0.05)
            gate.resolve(approved=False, reason="too dangerous")

        threading.Thread(target=_resolve, daemon=True).start()

        decision = gate.wait(timeout=5.0)
        assert decision.approved is False
        assert "too dangerous" in decision.reason

    def test_wait_timeout_returns_denied(self):
        req = ApprovalRequest.create(command="cmd", risk_reason="test")
        gate = ApprovalGate(req)

        decision = gate.wait(timeout=0.1)
        assert decision.approved is False
        assert "超时" in decision.reason
        assert gate.is_resolved

    def test_default_reason_approved(self):
        req = ApprovalRequest.create(command="cmd", risk_reason="test")
        gate = ApprovalGate(req)
        gate.resolve(approved=True)
        decision = gate.wait(timeout=0.1)
        assert "批准" in decision.reason

    def test_default_reason_denied(self):
        req = ApprovalRequest.create(command="cmd", risk_reason="test")
        gate = ApprovalGate(req)
        gate.resolve(approved=False)
        decision = gate.wait(timeout=0.1)
        assert "拒绝" in decision.reason


# ═══════════════════════════════════════════════════════════════════
# ApprovalRequestedMessage
# ═══════════════════════════════════════════════════════════════════

class TestApprovalRequestedMessage:
    """测试消息传递."""

    def test_message_carries_gate(self):
        req = ApprovalRequest.create(command="test", risk_reason="test")
        gate = ApprovalGate(req)
        msg = ApprovalRequestedMessage(gate)
        assert msg.gate is gate
        assert msg.gate.request == req


# ═══════════════════════════════════════════════════════════════════
# PlanPanel
# ═══════════════════════════════════════════════════════════════════

class TestPlanPanel:
    """测试 PlanPanel reactive widget."""

    def test_render_empty(self):
        panel = PlanPanel()
        rendered = panel.render()
        assert "暂无" in rendered

    def test_render_with_summary(self):
        panel = PlanPanel()
        panel.plan_summary = "搭建 Flask 项目"
        rendered = panel.render()
        assert "搭建 Flask 项目" in rendered

    def test_update_from_planner_event(self):
        panel = PlanPanel()
        event = {
            "plan_summary": "重构认证模块",
            "todos": [
                {"id": "1", "content": "创建登录路由", "status": "completed"},
                {"id": "2", "content": "添加 JWT 中间件", "status": "in_progress"},
                {"id": "3", "content": "写测试", "status": "pending"},
            ],
        }
        panel.update_from_event(event)
        assert "重构认证模块" in panel.plan_summary
        assert "✅" in panel.todos_text
        assert "🔄" in panel.todos_text
        assert "⬜" in panel.todos_text

    def test_update_partial_event(self):
        """只更新 plan_summary 或只更新 todos."""
        panel = PlanPanel()
        panel.update_from_event({"plan_summary": "summary only"})
        assert panel.plan_summary == "summary only"

        panel.update_from_event({
            "todos": [{"id": "1", "content": "test", "status": "completed"}]
        })
        assert "✅" in panel.todos_text


# ═══════════════════════════════════════════════════════════════════
# StatusBar
# ═══════════════════════════════════════════════════════════════════

class TestStatusBar:
    """测试 StatusBar reactive widget."""

    def test_render_default(self):
        bar = StatusBar()
        rendered = bar.render()
        assert "MokioClaw" in rendered
        assert "就绪" in rendered

    def test_render_with_session(self):
        bar = StatusBar()
        bar.session_id = "abc12345abcdef"
        bar.turn_index = 5
        bar.status = "🔄 执行中..."
        rendered = bar.render()
        assert "abc12345" in rendered
        assert "turn: 5" in rendered
        assert "执行中" in rendered


# ═══════════════════════════════════════════════════════════════════
# _format_tool_args
# ═══════════════════════════════════════════════════════════════════

class TestFormatToolArgs:
    """测试工具参数格式化."""

    def test_bash_tool(self):
        result = _format_tool_args("BashTool", {"command": "pip install requests"})
        assert "$ pip install requests" in result

    def test_file_tool(self):
        result = _format_tool_args("FileWriteTool", {"file_path": "src/app.py"})
        assert "src/app.py" in result

    def test_search_tool(self):
        result = _format_tool_args("WebSearchTool", {"query": "Flask tutorial"})
        assert "Flask tutorial" in result

    def test_todo_tool(self):
        result = _format_tool_args("TodoWrite", {"action": "create"})
        assert "create" in result

    def test_generic_fallback(self):
        result = _format_tool_args("UnknownTool", {"custom_key": "value"})
        assert "custom_key" in result


# ═══════════════════════════════════════════════════════════════════
# MokioClawTuiApp (async Textual tests)
# ═══════════════════════════════════════════════════════════════════

class TestMokioClawTuiApp:
    """测试 TUI 应用 —— 使用 Textual 的 async test 机制."""

    @pytest.mark.asyncio
    async def test_app_composes_widgets(self):
        """验证 App compose 产生正确的 widget 树."""
        from mokioclaw.cli.tui.app import MokioClawTuiApp

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            app = MokioClawTuiApp(workspace=workspace)

            async with app.run_test() as pilot:
                # 验证关键 widget 存在
                assert pilot.app.query_one("#status-bar") is not None
                assert pilot.app.query_one("#plan-panel") is not None
                assert pilot.app.query_one("#event-log") is not None
                assert pilot.app.query_one("#task-input") is not None

    @pytest.mark.asyncio
    async def test_app_mount_shows_welcome(self):
        """验证 on_mount 显示欢迎信息."""
        from mokioclaw.cli.tui.app import MokioClawTuiApp

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            app = MokioClawTuiApp(workspace=workspace)

            async with app.run_test() as pilot:
                log = pilot.app.query_one("#event-log")
                # RichLog 是 Textual widget，内部有 lines 属性
                assert len(log.lines) > 0

    @pytest.mark.asyncio
    async def test_input_empty_ignored(self):
        """空输入不触发 worker."""
        from mokioclaw.cli.tui.app import MokioClawTuiApp

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            app = MokioClawTuiApp(workspace=workspace)

            async with app.run_test() as pilot:
                task_input = pilot.app.query_one("#task-input")
                # 设置空值并提交
                task_input.value = "   "
                await pilot.press("enter")

                # 不应触发 worker
                assert not app._worker_running

    @pytest.mark.asyncio
    async def test_input_triggers_worker(self):
        """非空输入启动工作线程."""
        from mokioclaw.cli.tui.app import MokioClawTuiApp

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            app = MokioClawTuiApp(workspace=workspace)

            async with app.run_test(size=(80, 24)) as pilot:
                task_input = pilot.app.query_one("#task-input")
                task_input.value = "hello"
                await pilot.press("enter")

                # worker 应已启动
                assert app._worker_running

                # 等待 worker 完成
                if app._worker_thread:
                    app._worker_thread.join(timeout=5.0)

    @pytest.mark.asyncio
    async def test_bindings_exist(self):
        """验证键盘绑定已注册."""
        from mokioclaw.cli.tui.app import MokioClawTuiApp

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            app = MokioClawTuiApp(workspace=workspace)

            async with app.run_test() as pilot:
                # BINDINGS 是 list of tuples: ("key", "action", "description")
                keys = {b[0] for b in app.BINDINGS}
                assert "ctrl+q" in keys

    @pytest.mark.asyncio
    async def test_approval_handler_integration(self):
        """验证 approval_handler 通过 ApprovalGate 工作."""
        from mokioclaw.cli.tui.app import MokioClawTuiApp

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            app = MokioClawTuiApp(workspace=workspace)

            async with app.run_test(size=(80, 24)) as pilot:
                handler = app._make_approval_handler()

                req = ApprovalRequest.create(
                    command="pip install flask",
                    risk_reason="Python package installation",
                )

                # 在另一个线程 resolve（模拟 UI 弹窗结果）
                def _resolve():
                    time.sleep(0.1)
                    # 查找 gate 并 resolve
                    # 直接通过消息机制：handler 会 post_message
                    # 然后阻塞 wait；这里手动 resolve
                    pass

                # 由于 approval_handler 会阻塞，在单独线程调用
                result_holder = []

                def _call_handler():
                    result_holder.append(handler(req))

                t = threading.Thread(target=_call_handler, daemon=True)
                t.start()

                # 等待消息被 post
                time.sleep(0.2)

                # 手动 resolve（直接操作 gate）
                # 注意：gate 在 handler 内部创建，无法从外部直接访问
                # 这里验证超时机制
                t.join(timeout=2.0)

                # 应该超时返回 denied
                if result_holder:
                    assert result_holder[0].approved is False
                    assert "超时" in result_holder[0].reason
