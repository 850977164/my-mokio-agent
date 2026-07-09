"""执行追踪 —— 记录每次运行的事件、统计和人类可读时间线."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from mokioclaw.core.state import RuntimeState

# ═══════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════

VALID_TRACE_MODES: set[str] = {"on", "off"}

_MOKIOCLAW_DIR = ".mokioclaw"
_TRACES_DIR = "traces"

_HEAD_EVENTS = 20
_TAIL_EVENTS = 80


def normalize_trace_mode(mode: str | None) -> str:
    """标准化 trace 模式字符串，默认 "on"。"""
    if mode is None:
        return "on"
    if mode not in VALID_TRACE_MODES:
        return "on"
    return mode


# ═══════════════════════════════════════════════════════════════════
# TraceRecorder
# ═══════════════════════════════════════════════════════════════════

class TraceRecorder:
    """执行追踪记录器 —— 记录每次 MokioClaw 运行的全量事件流。

    用法::

        recorder = TraceRecorder(runtime, task="搭建 Flask 后台")
        recorder.start(inputs)
        # ... 在图执行过程中 ...
        recorder.record_graph_update({"type": "planner", ...})
        recorder.record_custom_event({"type": "tool_call", ...})
        recorder.end(status="completed", latest_node="final", final_state={...})

    输出文件 (位于 .mokioclaw/traces/{trace_id}/):
        - trace.json     — 统计概览 + 首尾事件
        - events.jsonl   — 每条事件一行 JSON
        - timeline.md    — 人类可读时间线
    """

    def __init__(self, runtime: RuntimeState, task: str = "") -> None:
        self.workspace: Path = runtime.workspace.resolve()
        self.mode: str = normalize_trace_mode(runtime.trace_mode)
        self.task: str = task
        self.trace_id: str = runtime.trace_id or f"trace-{uuid4().hex[:8]}"
        self.root: Path = self.workspace / _MOKIOCLAW_DIR / _TRACES_DIR / self.trace_id

        # ── 统计计数器 ──
        self.node_visits: dict[str, int] = {}  # 节点名 → 访问次数
        self.tool_calls: int = 0
        self.failed_tool_calls: int = 0
        self.approval_count: int = 0
        self.checkpoint_count: int = 0
        self.handoff_count: int = 0

        # ── 内部状态 ──
        self._started_at: str = ""
        self._ended_at: str = ""
        self._event_seq: int = 0
        self._events_file: Path | None = None
        self._all_events: list[dict] = []  # 内存中保留全部事件用于生成 timeline

    # ── 属性 ───────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """追踪是否启用."""
        return self.mode == "on"

    # ── start ───────────────────────────────────────────────────────

    def start(
        self,
        inputs: dict,
        *,
        resumed: bool = False,
        resume_event: dict | None = None,
    ) -> None:
        """记录 run_start 事件并初始化追踪目录.

        Args:
            inputs: 图执行的初始输入字典（runtime 字段会被排除以保持可序列化）。
            resumed: 是否从检查点恢复运行。
            resume_event: 恢复事件（如果有）。
        """
        if not self.enabled:
            return

        self.root.mkdir(parents=True, exist_ok=True)
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._events_file = self.root / "events.jsonl"

        # 提取 inputs 中的关键字段（排除不可序列化的 runtime 对象）
        inputs_summary: dict[str, Any] = {
            k: v for k, v in inputs.items()
            if k != "runtime" and not callable(v)
        }

        event: dict[str, Any] = {
            "type": "run_start",
            "task": self.task,
            "resumed": resumed,
            "trace_mode": self.mode,
            "inputs_summary": inputs_summary,
        }
        if resume_event:
            event["resume_event"] = resume_event

        self._append_event(event)

    # ── record_custom_event ─────────────────────────────────────────

    def record_custom_event(self, event: dict) -> None:
        """记录自定义事件并更新统计计数器.

        根据事件 ``type`` 和内容自动更新对应计数器:

        - ``type="tool_call"`` → ``tool_calls++``
        - ``type="tool_result"`` + ``ok=False`` → ``failed_tool_calls++``
        - ``type="tool_result"`` + ``requires_approval`` → ``approval_count++``
        - ``type="handoff"`` → ``handoff_count++``
        - ``type="checkpoint_saved"`` → ``checkpoint_count++``
        """
        if not self.enabled:
            return

        self._append_event(event)

        etype = event.get("type", "")

        if etype == "tool_call":
            self.tool_calls += 1

        elif etype == "tool_result":
            if not event.get("ok", True):
                self.failed_tool_calls += 1
            if event.get("requires_approval", False):
                self.approval_count += 1

        elif etype == "handoff":
            self.handoff_count += 1

        elif etype == "checkpoint_saved":
            self.checkpoint_count += 1

    # ── record_graph_update ─────────────────────────────────────────

    def record_graph_update(self, event: dict) -> None:
        """记录图节点更新事件并更新 node_visits 计数.

        事件的 ``type`` 字段作为节点名进行计数
        （如 ``planner``、``verifier``、``final`` 等）。
        """
        if not self.enabled:
            return

        self._append_event(event)

        node_name = event.get("type", "unknown")
        self.node_visits[node_name] = self.node_visits.get(node_name, 0) + 1

    # ── end ─────────────────────────────────────────────────────────

    def end(
        self,
        *,
        status: str,
        latest_node: str | None,
        final_state: dict | None = None,
    ) -> dict | None:
        """结束追踪，生成 trace.json 和 timeline.md.

        Args:
            status: 最终状态（``"completed"``、``"error"``、``"stopped"`` 等）。
            latest_node: 最后执行的图节点名称。
            final_state: 可选的最终 state 字典。

        Returns:
            生成的 ``trace.json`` 内容字典；追踪关闭时返回 ``None``。
        """
        if not self.enabled:
            return None

        self._ended_at = datetime.now(timezone.utc).isoformat()

        # 写入结束事件
        end_event: dict[str, Any] = {
            "type": "run_end",
            "status": status,
            "latest_node": latest_node,
        }
        if final_state:
            end_event["final_state_summary"] = _summarize_state(final_state)

        self._append_event(end_event)

        # 计算持续时间
        started_dt = datetime.fromisoformat(self._started_at)
        ended_dt = datetime.fromisoformat(self._ended_at)
        duration_ms = int((ended_dt - started_dt).total_seconds() * 1000)

        # 生成 timeline 分段
        total = len(self._all_events)
        omitted = max(0, total - _HEAD_EVENTS - _TAIL_EVENTS)
        timeline_head = self._all_events[:_HEAD_EVENTS]
        timeline_tail = self._all_events[-_TAIL_EVENTS:] if total > _HEAD_EVENTS else []

        # 构建 trace.json 内容
        trace_data: dict[str, Any] = {
            "trace_id": self.trace_id,
            "task": self.task,
            "status": status,
            "started_at": self._started_at,
            "ended_at": self._ended_at,
            "duration_ms": duration_ms,
            "node_visits": self.node_visits,
            "tool_calls": self.tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "approval_count": self.approval_count,
            "checkpoint_count": self.checkpoint_count,
            "handoff_count": self.handoff_count,
            "total_events": total,
            "timeline_head": timeline_head,
            "timeline_tail": timeline_tail,
            "timeline_omitted": omitted,
        }

        (self.root / "trace.json").write_text(
            json.dumps(trace_data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        # 生成 timeline.md
        timeline_md = _build_timeline_markdown(
            trace_id=self.trace_id,
            task=self.task,
            status=status,
            started_at=self._started_at,
            ended_at=self._ended_at,
            duration_ms=duration_ms,
            node_visits=self.node_visits,
            tool_calls=self.tool_calls,
            failed_tool_calls=self.failed_tool_calls,
            approval_count=self.approval_count,
            checkpoint_count=self.checkpoint_count,
            handoff_count=self.handoff_count,
            events=self._all_events,
            head_events=_HEAD_EVENTS,
            tail_events=_TAIL_EVENTS,
        )
        (self.root / "timeline.md").write_text(timeline_md, encoding="utf-8")

        return trace_data

    # ── 内部方法 ────────────────────────────────────────────────────

    def _append_event(self, event: dict) -> None:
        """向 events.jsonl 追加一条带序号和时间戳的事件记录."""
        self._event_seq += 1
        record: dict[str, Any] = {
            "seq": self._event_seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        self._all_events.append(record)

        if self._events_file:
            line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
            with self._events_file.open("a", encoding="utf-8") as f:
                f.write(line)


# ═══════════════════════════════════════════════════════════════════
# timeline.md 生成
# ═══════════════════════════════════════════════════════════════════

def _build_timeline_markdown(
    *,
    trace_id: str,
    task: str,
    status: str,
    started_at: str,
    ended_at: str,
    duration_ms: int,
    node_visits: dict[str, int],
    tool_calls: int,
    failed_tool_calls: int,
    approval_count: int,
    checkpoint_count: int,
    handoff_count: int,
    events: list[dict],
    head_events: int,
    tail_events: int,
) -> str:
    """生成人类可读的 timeline.md 内容."""
    status_emoji: dict[str, str] = {
        "completed": "✅",
        "error": "❌",
        "stopped": "⏹️",
        "running": "🔄",
    }

    lines: list[str] = [
        f"# 📊 MokioClaw 执行追踪",
        "",
        f"**Trace ID:** `{trace_id}`",
        f"**任务:** {task}",
        f"**状态:** {status_emoji.get(status, '❓')} {status}",
        f"**开始时间:** {started_at}",
        f"**结束时间:** {ended_at}",
        f"**总耗时:** {_format_duration(duration_ms)}",
        "",
        "---",
        "",
        "## 📈 统计摘要",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
    ]

    if node_visits:
        for node, count in sorted(node_visits.items()):
            lines.append(f"| 🔹 节点 `{node}` 访问次数 | {count} |")

    lines.extend([
        f"| 🔧 工具调用总数 | {tool_calls} |",
        f"| ❌ 失败工具调用 | {failed_tool_calls} |",
        f"| 🔐 审批触发次数 | {approval_count} |",
        f"| 💾 检查点保存次数 | {checkpoint_count} |",
        f"| 🤝 Agent 切换次数 | {handoff_count} |",
        f"| 📝 事件总数 | {len(events)} |",
        "",
        "---",
        "",
        "## ⏱️ 事件时间线",
        "",
    ])

    total = len(events)
    omitted = max(0, total - head_events - tail_events)

    # 表头
    lines.append("| # | 时间 | 类型 | 详情 |")
    lines.append("|---|------|------|------|")

    # 头部事件
    for evt in events[:head_events]:
        lines.append(_format_event_row(evt))

    # 省略标记
    if omitted > 0:
        lines.append(f"| ... | ... | *省略 {omitted} 条事件* | ... |")

    # 尾部事件
    for evt in events[-tail_events:] if total > head_events else []:
        lines.append(_format_event_row(evt))

    return "\n".join(lines)


def _format_event_row(evt: dict) -> str:
    """将单条事件格式化为 Markdown 表格行."""
    seq = evt.get("seq", "?")
    ts = evt.get("timestamp", "")[:19].replace("T", " ")  # 去掉时区信息，保留到秒
    etype = evt.get("type", "?")

    # 根据事件类型生成可读摘要
    detail = ""
    if etype == "run_start":
        detail = f"任务启动 (resumed={evt.get('resumed', False)})"
    elif etype == "run_end":
        detail = f"运行结束 (status={evt.get('status', '?')})"
    elif etype == "tool_call":
        detail = f"调用 `{evt.get('tool', '?')}`"
    elif etype == "tool_result":
        ok = "✅" if evt.get("ok", True) else "❌"
        detail = f"{ok} {evt.get('tool', '?')}"
    elif etype == "handoff":
        detail = f"{evt.get('from', '?')} → {evt.get('to', '?')}"
    elif etype == "checkpoint_saved":
        detail = f"节点={evt.get('latest_node', '?')} 模式={evt.get('mode', '?')}"
    elif etype in ("planner", "verifier", "final"):
        detail = f"图节点 `{etype}` 执行完成"
    else:
        # 通用：截取部分 JSON 作为预览
        preview = str({k: v for k, v in evt.items() if k not in ("seq", "timestamp")})
        detail = preview[:80] + ("..." if len(preview) > 80 else "")

    return f"| {seq} | {ts} | `{etype}` | {detail} |"


def _format_duration(ms: int) -> str:
    """将毫秒格式化为人类可读的时长字符串."""
    if ms < 1000:
        return f"{ms}ms"
    elif ms < 60_000:
        return f"{ms / 1000:.1f}s"
    else:
        minutes = ms // 60_000
        seconds = (ms % 60_000) / 1000
        return f"{minutes}m {seconds:.0f}s"


# ═══════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════

def _summarize_state(state: dict) -> dict[str, Any]:
    """提取 state 中可序列化的摘要字段（排除不可序列化对象）."""
    summary: dict[str, Any] = {}
    for key in (
        "task",
        "attempts",
        "max_attempts",
        "passed",
        "plan_summary",
        "latest_node",
        "last_error",
    ):
        if key in state:
            val = state[key]
            if val is not None and not isinstance(val, (bool, int, float, str, list, dict)):
                val = str(val)
            summary[key] = val
    return summary
