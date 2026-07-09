"""MokioClaw 交互式 TUI —— 基于 Textual 的多轮对话界面.

界面布局::

    ┌─────────────────────────────────────────────┐
    │ 🐾 MokioClaw                   session: xxx │  ← Header + 状态栏
    ├─────────────────────────────────────────────┤
    │  📋 计划: task summary                       │  ← Plan 面板
    │  ✅ [1] done  🔄 [2] in_progress  ⬜ [3] .. │
    ├─────────────────────────────────────────────┤
    │  🔧 FileWriteTool → app.py                  │  ← 事件流（可滚动）
    │  🔄 Handoff: planner → codeAgent            │
    │  💾 Checkpoint saved (node=planner)         │
    │  ✅ Final: task completed                   │
    ├─────────────────────────────────────────────┤
    │  💬 > ____________________________________  │  ← 输入框
    └─────────────────────────────────────────────┘

核心机制:
    1. 后台线程运行 stream_session_events
    2. 事件通过 AgentEventMessage(Message) 发送到 UI 线程
    3. UI 根据事件类型更新不同区域
    4. 审批通过 ApprovalGate + ApprovalModal 实现线程同步
    5. 输入框支持多轮对话，每次提交就是一个新的 session turn
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)

from mokioclaw.cli.tui.approval import (
    ApprovalGate,
    ApprovalModal,
    ApprovalRequestedMessage,
)
from mokioclaw.cli.tui.logo import logo_header, logo_rich_text
from mokioclaw.core.agent import stream_session_events
from mokioclaw.core.approval import ApprovalDecision, ApprovalRequest
from mokioclaw.core.paths import ensure_workspace, resolve_workspace
from mokioclaw.core.session import load_or_create_session, build_session_context


# ═══════════════════════════════════════════════════════════════════
# 跨线程消息
# ═══════════════════════════════════════════════════════════════════

class AgentEventMessage(Message):
    """工作线程 → UI 线程的 Agent 事件消息.

    Attributes:
        event: stream_session_events 产出的单个事件 dict。
    """

    def __init__(self, event: dict[str, Any]) -> None:
        self.event = event
        super().__init__()


class WorkerStartedMessage(Message):
    """工作线程开始执行."""

    def __init__(self, task: str) -> None:
        self.task = task
        super().__init__()


class WorkerDoneMessage(Message):
    """工作线程执行完成."""

    def __init__(self, error: str = "") -> None:
        self.error = error
        super().__init__()


# ═══════════════════════════════════════════════════════════════════
# PlanPanel —— 计划面板
# ═══════════════════════════════════════════════════════════════════

class PlanPanel(Static):
    """显示当前计划摘要和 Todo 列表.

    响应 plan_snapshot 事件进行更新。
    """

    plan_summary: reactive[str] = reactive("")
    todos_text: reactive[str] = reactive("")

    def render(self) -> str:
        parts: list[str] = []
        if self.plan_summary:
            parts.append(f"📋 计划: {self.plan_summary}")
        if self.todos_text:
            parts.append(self.todos_text)
        if not parts:
            parts.append("📋 计划: (暂无)")
        return "\n".join(parts)

    def update_from_event(self, event: dict) -> None:
        """从 planner 节点产出更新."""
        summary = event.get("plan_summary", "")
        todos: list[dict] = event.get("todos", [])

        if summary:
            self.plan_summary = summary

        if todos:
            icon_map = {
                "pending": "⬜",
                "in_progress": "🔄",
                "completed": "✅",
                "blocked": "🚫",
            }
            lines: list[str] = []
            for t in todos:
                tid = t.get("id", "?")
                content = t.get("content", "")[:60]
                status = t.get("status", "pending")
                icon = icon_map.get(status, "⬜")
                lines.append(f"  {icon} [{tid}] {content}")
            self.todos_text = "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# StatusBar —— 底部状态条
# ═══════════════════════════════════════════════════════════════════

class StatusBar(Static):
    """顶部状态条 —— 显示 Logo、session、turn、运行状态."""

    session_id: reactive[str] = reactive("")
    turn_index: reactive[int] = reactive(0)
    status: reactive[str] = reactive("⏳ 就绪")

    def render(self) -> str:
        parts = [logo_header()]
        if self.session_id:
            parts.append(f"session: {self.session_id[:8]}")
        if self.turn_index > 0:
            parts.append(f"turn: {self.turn_index}")
        parts.append(self.status)
        return " │ ".join(parts)


# ═══════════════════════════════════════════════════════════════════
# MokioClawTuiApp
# ═══════════════════════════════════════════════════════════════════

class MokioClawTuiApp(App[None]):
    """MokioClaw 交互式 TUI 应用.

    多轮对话界面，每次用户输入都会：
    1. 启动后台线程运行 stream_session_events
    2. 事件实时回传到 UI 线程
    3. 审批操作通过弹窗完成
    4. Session 自动持久化
    """

    CSS = """
    Screen {
        layout: grid;
        grid-rows: auto 1fr auto;
        grid-columns: 1fr;
    }

    #status-bar {
        dock: top;
        height: 1;
        background: $primary 20%;
        color: $text;
        padding: 0 1;
    }

    #main-area {
        layout: grid;
        grid-rows: auto 1fr;
        grid-columns: 1fr;
    }

    #plan-panel {
        height: auto;
        max-height: 6;
        border: solid $primary-darken-2;
        background: $surface;
        padding: 1 2;
        margin: 1 0;
    }

    #event-log {
        border: solid $primary-darken-2;
        background: $surface;
    }

    #input-container {
        dock: bottom;
        height: 3;
        border-top: solid $primary-darken-2;
        background: $surface;
    }

    #input-prompt {
        width: 4;
        padding: 0 1;
        color: $text-disabled;
    }

    #task-input {
        width: 1fr;
    }

    #footer {
        dock: bottom;
        height: 1;
    }

    /* 事件样式 */
    .event-tool {
        color: $accent;
    }
    .event-handoff {
        color: $warning;
    }
    .event-checkpoint {
        color: $success;
    }
    .event-error {
        color: $error;
    }
    .event-chat {
        color: $secondary;
    }
    .event-plan {
        color: $primary-lighten-2;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
        ("ctrl+c", "quit", "退出"),
        ("ctrl+l", "clear_log", "清屏"),
    ]

    def __init__(
        self,
        workspace: Path | None = None,
        model: str | None = None,
        max_attempts: int = 3,
        approval_mode: str = "inline",
        checkpoint_mode: str = "light",
        trace_mode: str = "on",
    ) -> None:
        super().__init__()
        self._workspace = resolve_workspace(workspace)
        self._model = model or os.getenv("MODEL", "gpt-4o")
        self._max_attempts = max_attempts
        self._approval_mode = approval_mode
        self._checkpoint_mode = checkpoint_mode
        self._trace_mode = trace_mode

        # 工作线程控制
        self._worker_thread: threading.Thread | None = None
        self._worker_running = False

        # session 信息
        self._session_id: str = ""
        self._turn_index: int = 0

        # widget 引用（on_mount 后可用）
        self._status_bar: StatusBar | None = None
        self._event_log: RichLog | None = None
        self._task_input: Input | None = None
        self._plan_panel: PlanPanel | None = None

    # ── compose ────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        with Container(id="main-area"):
            yield PlanPanel(id="plan-panel")
            yield RichLog(id="event-log", highlight=True, markup=True, wrap=True)
        with Horizontal(id="input-container"):
            yield Label("💬 >", id="input-prompt")
            yield Input(
                id="task-input",
                placeholder="输入任务或问题... (Enter 发送, Ctrl+Q 退出)",
            )
        yield Footer()

    # ── mount ─────────────────────────────────────────────────────

    def on_mount(self) -> None:
        """启动时初始化 session 和状态."""
        ensure_workspace(self._workspace)

        # 缓存 widget 引用
        self._status_bar = self.query_one("#status-bar", StatusBar)
        self._event_log = self.query_one("#event-log", RichLog)
        self._task_input = self.query_one("#task-input", Input)
        self._plan_panel = self.query_one("#plan-panel", PlanPanel)

        # 加载已有 session
        session = load_or_create_session(self._workspace)
        self._session_id = session.get("session_id", "")
        self._turn_index = session.get("turn_index", 0)

        # 更新状态栏
        self._status_bar.session_id = self._session_id
        self._status_bar.turn_index = self._turn_index

        # 显示欢迎信息
        self._event_log.write(logo_rich_text())
        self._event_log.write("")
        self._event_log.write(f"[dim]📂 workspace: {self._workspace}[/]")
        self._event_log.write(f"[dim]🤖 model: {self._model}[/]")
        if self._turn_index > 0:
            self._event_log.write(f"[dim]📝 已恢复 session (turn {self._turn_index})[/]")
        self._event_log.write("[dim]💡 输入任务开始工作流，或输入问题开始聊天[/]")
        self._event_log.write("")

        # 聚焦输入框
        self._task_input.focus()

    # ── 输入处理 ──────────────────────────────────────────────────

    @on(Input.Submitted, "#task-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """用户按下 Enter → 启动工作线程执行任务."""
        task = event.value.strip()
        if not task:
            return

        # 防止重复提交
        if self._worker_running:
            self._log_event("[yellow]⏳ 上一个任务仍在执行中，请等待...[/]")
            return

        # 清空输入框
        if self._task_input:
            self._task_input.value = ""

        # 更新状态
        self._worker_running = True
        if self._status_bar:
            self._status_bar.status = "🔄 思考中..."

        # 显示用户输入
        self._log_event(f"[bold]👤 You:[/] {task}")

        # 在后台线程中运行 Agent
        self._worker_thread = threading.Thread(
            target=self._run_agent_worker,
            args=(task,),
            daemon=True,
        )
        self._worker_thread.start()

    # ── 工作线程 ──────────────────────────────────────────────────

    def _run_agent_worker(self, task: str) -> None:
        """后台线程：运行 stream_session_events 并转发事件到 UI."""
        try:
            self.post_message(WorkerStartedMessage(task))

            events = stream_session_events(
                task,
                workspace=self._workspace,
                max_attempts=self._max_attempts,
                model=self._model,
                approval_mode=self._approval_mode,
                approval_handler=self._make_approval_handler(),
                checkpoint_mode=self._checkpoint_mode,
                trace_mode=self._trace_mode,
            )

            for evt in events:
                self.post_message(AgentEventMessage(evt))

            self.post_message(WorkerDoneMessage())

        except Exception as exc:
            self.post_message(WorkerDoneMessage(error=str(exc)))

    def _make_approval_handler(self):
        """创建审批回调 —— 通过 ApprovalGate 与 UI 线程同步.

        Returns:
            一个 callable，接收 ApprovalRequest，返回 ApprovalDecision。
            该 callable 在工作线程中被调用，会阻塞等待 UI 弹窗结果。
        """

        def approval_handler(request: ApprovalRequest) -> ApprovalDecision:
            gate = ApprovalGate(request)
            # 跨线程发送审批请求到 UI
            self.post_message(ApprovalRequestedMessage(gate))
            # 阻塞等待 UI 响应
            return gate.wait(timeout=120.0)

        return approval_handler

    # ── 事件处理 ──────────────────────────────────────────────────

    @on(WorkerStartedMessage)
    def on_worker_started(self, message: WorkerStartedMessage) -> None:
        """工作线程开始."""
        self._log_event(
            "[dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]"
        )

    @on(WorkerDoneMessage)
    def on_worker_done(self, message: WorkerDoneMessage) -> None:
        """工作线程完成."""
        self._worker_running = False

        if self._status_bar:
            self._status_bar.status = "⏳ 就绪"

        if message.error:
            self._log_event(f"[red]❌ 执行错误: {message.error}[/]")
        else:
            self._log_event("[green]✅ 完成[/]")

        # 更新 session 信息
        session = load_or_create_session(self._workspace)
        self._session_id = session.get("session_id", "")
        self._turn_index = session.get("turn_index", 0)
        if self._status_bar:
            self._status_bar.session_id = self._session_id
            self._status_bar.turn_index = self._turn_index

        self._log_event(
            "[dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]\n"
        )

        # 重新聚焦输入框
        if self._task_input:
            self._task_input.focus()

    @on(AgentEventMessage)
    def on_agent_event(self, message: AgentEventMessage) -> None:
        """路由 Agent 事件到对应处理器."""
        evt = message.event
        etype = evt.get("type", "")

        if etype == "session_event":
            self._handle_session_event(evt)

        elif etype == "graph_event":
            self._handle_graph_event(evt)

        elif etype == "custom_event":
            self._handle_custom_event(evt)

        else:
            self._log_event(f"[dim]📎 {evt}[/]")

    @on(ApprovalRequestedMessage)
    def on_approval_requested(self, message: ApprovalRequestedMessage) -> None:
        """收到审批请求 → 弹出 ApprovalModal 弹窗.

        注意：此方法在 UI 线程中执行，而工作线程阻塞在 gate.wait() 上。
             弹窗 dismiss 后 gate.resolve() 会解除工作线程的阻塞。
        """
        gate = message.gate
        self._log_event(
            f"[yellow]⚠️  审批请求: {gate.request.risk_reason}[/]\n"
            f"[dim]   命令: {gate.request.command[:100]}[/]"
        )

        def _on_dismiss(result: bool) -> None:
            if result:
                self._log_event("[green]   ✅ 已批准[/]")
            else:
                self._log_event("[red]   ❌ 已拒绝[/]")

        self.push_screen(
            ApprovalModal(gate, workspace_path=str(self._workspace)),
            callback=_on_dismiss,
        )

    # ── 事件处理器 ────────────────────────────────────────────────

    def _handle_session_event(self, evt: dict) -> None:
        """处理会话管理事件."""
        event_name = evt.get("event", "")

        if event_name == "user_turn_recorded":
            turn = evt.get("turn", "?")
            self._log_event(f"[bold]📝 Turn {turn} 已记录[/]")

        elif event_name == "intent_routed":
            route = evt.get("route", "?")
            reason = evt.get("reason", "")
            confidence = evt.get("confidence", 0.0)
            route_icon = "💬" if route == "chat" else "🔧"
            confidence_str = f"{confidence:.0%}"
            self._log_event(
                f"{route_icon} 意图路由: [bold]{route}[/] "
                f"(置信度: {confidence_str}) — {reason}"
            )

        elif event_name == "chat_response":
            content = evt.get("content", "")
            self._log_event(f"[secondary]🤖 MokioClaw:[/] {content}")

        elif event_name == "session_saved":
            turn = evt.get("turn", "?")
            route = evt.get("route", "?")
            if self._status_bar:
                self._status_bar.turn_index = turn
            self._log_event(f"[dim]💾 Session 已保存 (turn={turn}, route={route})[/]")

    def _handle_graph_event(self, evt: dict) -> None:
        """处理图节点产出事件."""
        event_data = evt.get("event", evt)

        for node_name, node_output in event_data.items():
            if node_name == "planner":
                self._handle_planner(node_output)
            elif node_name == "verifier":
                self._handle_verifier(node_output)
            elif node_name == "final":
                self._handle_final(node_output)
            elif node_name == "context_monitor":
                self._log_event(f"[dim]🔍 context_monitor: {node_output.get('passed', 'N/A')}[/]")
            elif node_name == "context_compressor":
                self._log_event(f"[dim]🗜️  context_compressor: 上下文已压缩[/]")

    def _handle_custom_event(self, evt: dict) -> None:
        """处理自定义事件（工具调用、Agent 交接等）."""
        event_data = evt.get("event", evt)
        etype = event_data.get("type", "")

        if etype == "tool_call":
            tool_name = event_data.get("tool", "unknown")
            args = event_data.get("args", {})
            preview = _format_tool_args(tool_name, args)
            self._log_event(f"[accent]🔧 {tool_name}[/] {preview}")

        elif etype == "tool_result":
            tool_name = event_data.get("tool", "unknown")
            ok = event_data.get("ok", True)
            icon = "✅" if ok else "❌"
            self._log_event(f"   {icon} 结果")

        elif etype == "handoff":
            from_agent = event_data.get("from", "?")
            to_agent = event_data.get("to", "?")
            instruction = event_data.get("instruction", "")[:100]
            self._log_event(
                f"[warning]🔄 Handoff:[/] {from_agent} → {to_agent}\n"
                f"[dim]   {instruction}[/]"
            )

        elif etype == "checkpoint_saved":
            node = event_data.get("latest_node", "?")
            mode = event_data.get("mode", "?")
            self._log_event(f"[green]💾 Checkpoint:[/] node={node} mode={mode}")

        elif etype == "memory_injection":
            node = event_data.get("node", "?")
            self._log_event(f"[dim]🧠 Memory injected (node={node})[/]")

        else:
            preview = str(event_data)[:200]
            self._log_event(f"[dim]📎 custom: {preview}[/]")

    def _handle_planner(self, event: dict) -> None:
        """处理 Planner 产出."""
        if self._plan_panel:
            self._plan_panel.update_from_event(event)

        summary = event.get("plan_summary", "")
        todos_count = len(event.get("todos", []))
        criteria_count = len(event.get("acceptance_criteria", []))
        cmds_count = len(event.get("verification_commands", []))

        self._log_event(
            f"[primary-lighten-2]📋 Planner:[/] {summary[:80]}\n"
            f"[dim]   todos={todos_count} criteria={criteria_count} cmds={cmds_count}[/]"
        )

    def _handle_verifier(self, event: dict) -> None:
        """处理 Verifier 产出."""
        passed = event.get("passed", False)
        attempts = event.get("attempts", 0)

        if passed:
            self._log_event(f"[green]✅ 验证通过 (尝试 {attempts} 次)[/]")
        else:
            self._log_event(f"[red]❌ 验证失败 (尝试 {attempts} 次)[/]")

        # 显示验证命令结果
        verification_results: list[dict] = event.get("verification_results", [])
        for vr in verification_results[:5]:
            cmd = vr.get("command", "?")
            ok = vr.get("ok", False)
            icon = "✅" if ok else "❌"
            self._log_event(f"   {icon} $ {cmd}")

        # 显示验收明细
        verification_checks: list[dict] = event.get("verification_checks", [])
        for c in verification_checks[:5]:
            name = c.get("name", "?")
            check_passed = c.get("passed", False)
            icon = "✅" if check_passed else "❌"
            self._log_event(f"   {icon} {name}")

    def _handle_final(self, event: dict) -> None:
        """处理 Final 节点产出."""
        final_answer = event.get("final_answer", "")
        if final_answer:
            # 显示摘要
            lines = final_answer.strip().split("\n")
            summary_line = ""
            for line in lines:
                if "最终结果" in line:
                    summary_line = line
                    break
            if summary_line:
                self._log_event(f"[bold cyan]🏁 {summary_line}[/]")
            else:
                preview = final_answer[:500]
                self._log_event(f"[cyan]📝 Final:[/]\n{preview}")

    # ── 辅助 ──────────────────────────────────────────────────────

    def _log_event(self, text: str) -> None:
        """安全地向事件日志追加文本."""
        try:
            if self._event_log:
                self._event_log.write(text)
        except Exception:
            pass  # 可能在 compose 前调用，静默丢弃

    def action_clear_log(self) -> None:
        """清空事件日志."""
        try:
            if self._event_log:
                self._event_log.clear()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# 工具参数格式化
# ═══════════════════════════════════════════════════════════════════

def _format_tool_args(tool_name: str, args: dict | Any) -> str:
    """根据工具名格式化参数预览."""
    if not isinstance(args, dict):
        return str(args)[:100]

    tool_name_lower = tool_name.lower()

    # 注意：检查顺序很重要 —— 更具体的关键词需放在前面
    # "TodoWrite" 必须在 "write" 之前检查，避免被文件类匹配
    if "todo" in tool_name_lower:
        action = args.get("action", args.get("status", ""))
        return f"{action}"[:120]

    if "notepad" in tool_name_lower:
        content = args.get("content", "")
        return f'"{content[:80]}"'

    if any(kw in tool_name_lower for kw in ("bash", "shell", "command")):
        cmd = args.get("command", "")
        return f"$ {cmd}"[:120]

    if any(kw in tool_name_lower for kw in ("file", "write", "read", "edit", "grep")):
        path = args.get("file_path", args.get("path", ""))
        return f"→ {path}"[:120]

    if "search" in tool_name_lower or "web" in tool_name_lower:
        query = args.get("query", args.get("q", ""))
        return f'"{query}"'[:120]

    # 通用：取第一个有意义的值
    for key in ("command", "path", "file_path", "query", "content", "instruction", "action"):
        if key in args:
            val = str(args[key])[:100]
            return f"{key}={val}"

    return str(args)[:100]


# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════

def run_tui(
    workspace: Path | None = None,
    model: str | None = None,
    max_attempts: int = 3,
    approval_mode: str = "inline",
    checkpoint_mode: str = "light",
    trace_mode: str = "on",
) -> None:
    """启动 MokioClaw 交互式 TUI.

    Args:
        workspace: 工作区路径，None 则自动解析。
        model: 模型名称，None 则从环境变量读取。
        max_attempts: 最大重试次数。
        approval_mode: 审批模式。
        checkpoint_mode: 检查点模式。
        trace_mode: 追踪模式。
    """
    app = MokioClawTuiApp(
        workspace=workspace,
        model=model,
        max_attempts=max_attempts,
        approval_mode=approval_mode,
        checkpoint_mode=checkpoint_mode,
        trace_mode=trace_mode,
    )
    app.run()
