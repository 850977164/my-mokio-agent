"""审批弹窗与线程同步机制 —— TUI 与 Agent 工作线程之间的审批桥接.

ApprovalGate:
    工作线程创建 gate → 发送 ApprovalRequestedMessage 到 UI → 阻塞 wait()
    → UI 弹出 ApprovalModal → 用户点击 Approve/Deny → 调用 gate.resolve()
    → 工作线程解除阻塞 → 返回 ApprovalDecision.

Usage::

    # 在工作线程中（approval_handler 回调内）：
    gate = ApprovalGate(request)
    app.post_message(ApprovalRequestedMessage(gate))
    decision = gate.wait()  # 阻塞直到用户响应
    return decision
"""

from __future__ import annotations

import threading
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Center, Grid, Horizontal
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from mokioclaw.core.approval import ApprovalDecision, ApprovalRequest


# ═══════════════════════════════════════════════════════════════════
# ApprovalGate —— 线程同步原语
# ═══════════════════════════════════════════════════════════════════

class ApprovalGate:
    """审批门闩 —— 在工作线程和 UI 线程之间同步审批结果.

    工作线程::

        gate = ApprovalGate(request)
        app.post_message(ApprovalRequestedMessage(gate))
        decision = gate.wait(timeout=120)  # blocks here

    UI 线程 (在 ApprovalModal 中)::

        gate.resolve(approved=True)

    """

    def __init__(self, request: ApprovalRequest) -> None:
        self.request: ApprovalRequest = request
        self._event = threading.Event()
        self._approved: bool = False
        self._reason: str = ""

    def wait(self, timeout: float = 120.0) -> ApprovalDecision:
        """阻塞等待用户审批决策.

        Args:
            timeout: 超时秒数，超时后自动拒绝。

        Returns:
            ApprovalDecision 包含批准状态和原因。
        """
        if not self._event.wait(timeout=timeout):
            self._reason = "审批超时，自动拒绝"
            self._approved = False
            self._event.set()  # 标记为已决议（尽管是超时拒绝）
        return ApprovalDecision(approved=self._approved, reason=self._reason)

    def resolve(self, approved: bool, reason: str = "") -> None:
        """由 UI 线程调用，设置决策并解除阻塞.

        Args:
            approved: 是否批准。
            reason: 可选的拒绝原因。
        """
        self._approved = approved
        self._reason = reason or ("批准" if approved else "用户拒绝")
        self._event.set()

    @property
    def is_resolved(self) -> bool:
        """是否已决策."""
        return self._event.is_set()


# ═══════════════════════════════════════════════════════════════════
# ApprovalRequestedMessage —— 跨线程消息
# ═══════════════════════════════════════════════════════════════════

class ApprovalRequestedMessage(Message):
    """工作线程 → UI 线程的审批请求消息.

    Attributes:
        gate: ApprovalGate 实例，UI 模态框通过它回传决策。
    """

    def __init__(self, gate: ApprovalGate) -> None:
        self.gate = gate
        super().__init__()


# ═══════════════════════════════════════════════════════════════════
# ApprovalModal —— 审批弹窗
# ═══════════════════════════════════════════════════════════════════

class ApprovalModal(ModalScreen[bool]):
    """审批弹窗 —— 显示命令风险信息，等待用户批准或拒绝.

    布局::

        ┌──────────────────────────────────────┐
        │         ⚠️  审批确认                 │
        │                                      │
        │  🔧 工具: BashTool                   │
        │  ⚡ 风险: Python package installation │
        │  📂 命令:                            │
        │  ┌────────────────────────────────┐  │
        │  │ pip install requests           │  │
        │  └────────────────────────────────┘  │
        │                                      │
        │     [✅ Approve]   [❌ Deny]         │
        │                                      │
        └──────────────────────────────────────┘

    键盘快捷键:
        Y / Enter → 批准
        N / Escape → 拒绝
    """

    CSS = """
    ApprovalModal {
        align: center middle;
    }

    #approval-dialog {
        width: 60;
        max-height: 28;
        background: $surface;
        border: thick $warning;
        padding: 1 2;
    }

    #approval-title {
        text-align: center;
        text-style: bold;
        color: $warning;
        padding-bottom: 1;
    }

    #approval-info {
        padding-bottom: 1;
    }

    #approval-command-box {
        width: 100%;
        height: auto;
        min-height: 3;
        background: $boost;
        border: solid $primary-darken-2;
        padding: 0 1;
        margin-bottom: 1;
    }

    #approval-buttons {
        width: 100%;
        align: center middle;
        height: 3;
    }

    #approve-btn {
        margin-right: 2;
    }
    """

    def __init__(self, gate: ApprovalGate, workspace_path: str = "") -> None:
        super().__init__()
        self.gate = gate
        self.workspace_path = workspace_path

    def compose(self) -> ComposeResult:
        req = self.gate.request
        with Center(id="approval-dialog"):
            yield Label("⚠️  审批确认", id="approval-title")

            info_lines = [
                f"🔧 工具: {req.tool_name}",
                f"⚡ 风险: {req.risk_reason}",
            ]
            if self.workspace_path:
                info_lines.append(f"📂 工作区: {self.workspace_path}")
            info_lines.append(f"🆔 请求 ID: {req.id}")
            yield Label("\n".join(info_lines), id="approval-info")

            yield Label("📋 完整命令:", id="approval-command-label")
            yield Static(req.command, id="approval-command-box")

            with Horizontal(id="approval-buttons"):
                yield Button("✅ Approve", id="approve-btn", variant="success")
                yield Button("❌ Deny", id="deny-btn", variant="error")

    def on_mount(self) -> None:
        """自动聚焦批准按钮."""
        btn = self.query_one("#approve-btn", Button)
        btn.focus()

    @on(Button.Pressed, "#approve-btn")
    def _on_approve(self) -> None:
        self.gate.resolve(approved=True, reason="用户批准")
        self.dismiss(True)

    @on(Button.Pressed, "#deny-btn")
    def _on_deny(self) -> None:
        self.gate.resolve(approved=False, reason="用户拒绝")
        self.dismiss(False)

    def key_y(self) -> None:
        """Y 键 → 批准."""
        self.gate.resolve(approved=True, reason="用户批准")
        self.dismiss(True)

    def key_n(self) -> None:
        """N 键 → 拒绝."""
        self.gate.resolve(approved=False, reason="用户拒绝")
        self.dismiss(False)

    def key_escape(self) -> None:
        """Esc → 拒绝."""
        self.gate.resolve(approved=False, reason="用户取消")
        self.dismiss(False)
