"""执行追踪测试."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mokioclaw.core.trace import (
    VALID_TRACE_MODES,
    TraceRecorder,
    normalize_trace_mode,
)
from mokioclaw.core.state import RuntimeState


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """创建临时 workspace."""
    w = tmp_path / "test_workspace"
    w.mkdir()
    return w


@pytest.fixture
def runtime(ws: Path) -> RuntimeState:
    """创建带 trace_mode=on 和 trace_id 的 RuntimeState."""
    return RuntimeState(workspace=ws, model="gpt-4o-mini", trace_mode="on", trace_id="test-trace-001")


@pytest.fixture
def runtime_off(ws: Path) -> RuntimeState:
    """创建 trace_mode=off 的 RuntimeState."""
    return RuntimeState(workspace=ws, model="gpt-4o-mini", trace_mode="off")


@pytest.fixture
def runtime_no_id(ws: Path) -> RuntimeState:
    """创建 trace_mode=on 但没有 trace_id 的 RuntimeState."""
    return RuntimeState(workspace=ws, model="gpt-4o-mini", trace_mode="on", trace_id="")


@pytest.fixture
def sample_inputs() -> dict:
    """最小可用的图输入."""
    return {
        "task": "搭建 Flask 后台管理",
        "runtime": None,  # 占位，测试中会被 recorder 排除
        "max_attempts": 3,
    }


# ═══════════════════════════════════════════════════════════════════
# normalize_trace_mode
# ═══════════════════════════════════════════════════════════════════

def test_normalize_trace_valid_modes() -> None:
    """合法模式原样返回."""
    for mode in VALID_TRACE_MODES:
        assert normalize_trace_mode(mode) == mode


def test_normalize_trace_none_falls_back_to_on() -> None:
    """None → on."""
    assert normalize_trace_mode(None) == "on"


def test_normalize_trace_invalid_falls_back_to_on() -> None:
    """无效值 fallback 到 on."""
    assert normalize_trace_mode("unknown") == "on"
    assert normalize_trace_mode("") == "on"
    assert normalize_trace_mode("OFF") == "on"


# ═══════════════════════════════════════════════════════════════════
# TraceRecorder 属性
# ═══════════════════════════════════════════════════════════════════

def test_recorder_enabled_on(runtime: RuntimeState) -> None:
    """trace_mode=on 时 enabled=True."""
    rec = TraceRecorder(runtime, task="test")
    assert rec.enabled is True
    assert rec.mode == "on"


def test_recorder_disabled_off(runtime_off: RuntimeState) -> None:
    """trace_mode=off 时 enabled=False."""
    rec = TraceRecorder(runtime_off, task="test")
    assert rec.enabled is False
    assert rec.mode == "off"


def test_recorder_uses_provided_trace_id(runtime: RuntimeState) -> None:
    """使用 runtime 提供的 trace_id."""
    rec = TraceRecorder(runtime, task="test")
    assert rec.trace_id == "test-trace-001"


def test_recorder_generates_trace_id_when_empty(runtime_no_id: RuntimeState) -> None:
    """trace_id 为空时自动生成."""
    rec = TraceRecorder(runtime_no_id, task="test")
    assert rec.trace_id.startswith("trace-")
    assert len(rec.trace_id) == len("trace-") + 8


def test_recorder_root_under_traces_dir(runtime: RuntimeState) -> None:
    """root 路径在 .mokioclaw/traces/{trace_id} 下."""
    rec = TraceRecorder(runtime, task="test")
    assert rec.root == runtime.workspace / ".mokioclaw" / "traces" / rec.trace_id


def test_recorder_initial_stats_zero(runtime: RuntimeState) -> None:
    """初始化时所有统计计数器为 0."""
    rec = TraceRecorder(runtime, task="test")
    assert rec.node_visits == {}
    assert rec.tool_calls == 0
    assert rec.failed_tool_calls == 0
    assert rec.approval_count == 0
    assert rec.checkpoint_count == 0
    assert rec.handoff_count == 0


# ═══════════════════════════════════════════════════════════════════
# start
# ═══════════════════════════════════════════════════════════════════

def test_start_creates_root_dir(runtime: RuntimeState, sample_inputs: dict) -> None:
    """start() 创建 trace 根目录."""
    rec = TraceRecorder(runtime, task="test")
    rec.start(sample_inputs)
    assert rec.root.is_dir()


def test_start_creates_events_file(runtime: RuntimeState, sample_inputs: dict) -> None:
    """start() 创建 events.jsonl."""
    rec = TraceRecorder(runtime, task="test")
    rec.start(sample_inputs)
    assert (rec.root / "events.jsonl").is_file()


def test_start_writes_run_start_event(runtime: RuntimeState, sample_inputs: dict) -> None:
    """start() 写入 run_start 事件到 events.jsonl."""
    rec = TraceRecorder(runtime, task="搭建 Flask")
    rec.start(sample_inputs)

    lines = (rec.root / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["type"] == "run_start"
    assert record["task"] == "搭建 Flask"
    assert record["resumed"] is False
    assert record["trace_mode"] == "on"
    assert record["seq"] == 1
    assert "timestamp" in record


def test_start_resumed_flag(runtime: RuntimeState, sample_inputs: dict) -> None:
    """start() 在 resumed=True 时记录 flag."""
    rec = TraceRecorder(runtime, task="test")
    resume_evt = {"type": "resume", "checkpoint_dir": "/tmp/ckpt"}
    rec.start(sample_inputs, resumed=True, resume_event=resume_evt)

    lines = (rec.root / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    record = json.loads(lines[0])
    assert record["resumed"] is True
    assert record["resume_event"] == resume_evt


def test_start_excludes_runtime_from_inputs(runtime: RuntimeState) -> None:
    """start() 排除 inputs 中的 runtime 对象."""
    rec = TraceRecorder(runtime, task="test")
    inputs = {"task": "hello", "runtime": runtime, "other": 42}
    rec.start(inputs)

    lines = (rec.root / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    record = json.loads(lines[0])
    assert "runtime" not in record["inputs_summary"]
    assert record["inputs_summary"]["task"] == "hello"
    assert record["inputs_summary"]["other"] == 42


def test_start_disabled_mode_noop(runtime_off: RuntimeState, sample_inputs: dict) -> None:
    """trace_mode=off 时 start() 不做任何事."""
    rec = TraceRecorder(runtime_off, task="test")
    rec.start(sample_inputs)
    assert not rec.root.exists()


# ═══════════════════════════════════════════════════════════════════
# record_custom_event — 统计计数
# ═══════════════════════════════════════════════════════════════════

def test_record_tool_call_increments(runtime: RuntimeState) -> None:
    """tool_call 事件增加 tool_calls 计数."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})
    rec.record_custom_event({"type": "tool_call", "tool": "Bash", "args": {}})
    assert rec.tool_calls == 1


def test_record_tool_result_ok_does_not_increment_failed(runtime: RuntimeState) -> None:
    """ok=True 的 tool_result 不增加 failed_tool_calls."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})
    rec.record_custom_event({"type": "tool_result", "tool": "Bash", "ok": True})
    assert rec.tool_calls == 0
    assert rec.failed_tool_calls == 0


def test_record_tool_result_failed_increments(runtime: RuntimeState) -> None:
    """ok=False 的 tool_result 增加 failed_tool_calls."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})
    rec.record_custom_event({"type": "tool_result", "tool": "Bash", "ok": False})
    assert rec.failed_tool_calls == 1


def test_record_tool_result_requires_approval(runtime: RuntimeState) -> None:
    """带 requires_approval 的 tool_result 增加 approval_count."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})
    rec.record_custom_event({
        "type": "tool_result",
        "tool": "Bash",
        "ok": True,
        "requires_approval": True,
    })
    assert rec.approval_count == 1


def test_record_handoff_increments(runtime: RuntimeState) -> None:
    """handoff 事件增加 handoff_count."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})
    rec.record_custom_event({
        "type": "handoff",
        "from": "planner",
        "to": "code_agent",
    })
    assert rec.handoff_count == 1


def test_record_checkpoint_saved_increments(runtime: RuntimeState) -> None:
    """checkpoint_saved 事件增加 checkpoint_count."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})
    rec.record_custom_event({
        "type": "checkpoint_saved",
        "saved_at": "2026-07-01T00:00:00Z",
        "mode": "light",
    })
    assert rec.checkpoint_count == 1


def test_record_multiple_events_accumulates(runtime: RuntimeState) -> None:
    """多次记录事件时计数器累加."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})
    rec.record_custom_event({"type": "tool_call", "tool": "A", "args": {}})
    rec.record_custom_event({"type": "tool_call", "tool": "B", "args": {}})
    rec.record_custom_event({"type": "tool_call", "tool": "C", "args": {}})
    rec.record_custom_event({"type": "handoff", "from": "x", "to": "y"})
    rec.record_custom_event({"type": "handoff", "from": "y", "to": "z"})
    assert rec.tool_calls == 3
    assert rec.handoff_count == 2


def test_record_custom_event_disabled_noop(runtime_off: RuntimeState) -> None:
    """trace_mode=off 时 record_custom_event 不做任何事."""
    rec = TraceRecorder(runtime_off, task="test")
    rec.record_custom_event({"type": "tool_call", "tool": "Bash"})
    assert rec.tool_calls == 0
    assert rec.failed_tool_calls == 0


# ═══════════════════════════════════════════════════════════════════
# record_graph_update — 节点访问计数
# ═══════════════════════════════════════════════════════════════════

def test_record_graph_update_counts_nodes(runtime: RuntimeState) -> None:
    """record_graph_update 正确统计节点访问次数."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})
    rec.record_graph_update({"type": "planner", "plan_summary": "ok"})
    rec.record_graph_update({"type": "verifier", "passed": False})
    rec.record_graph_update({"type": "planner", "plan_summary": "retry"})
    rec.record_graph_update({"type": "verifier", "passed": True})
    rec.record_graph_update({"type": "final", "final_answer": "done"})

    assert rec.node_visits == {"planner": 2, "verifier": 2, "final": 1}


def test_record_graph_update_disabled_noop(runtime_off: RuntimeState) -> None:
    """trace_mode=off 时 record_graph_update 不做任何事."""
    rec = TraceRecorder(runtime_off, task="test")
    rec.record_graph_update({"type": "planner"})
    assert rec.node_visits == {}


# ═══════════════════════════════════════════════════════════════════
# end — trace.json
# ═══════════════════════════════════════════════════════════════════

def test_end_creates_trace_json(runtime: RuntimeState, sample_inputs: dict) -> None:
    """end() 创建 trace.json."""
    rec = TraceRecorder(runtime, task="搭建 Flask")
    rec.start(sample_inputs)
    result = rec.end(status="completed", latest_node="final")
    assert result is not None
    assert (rec.root / "trace.json").is_file()


def test_end_trace_json_has_all_fields(runtime: RuntimeState, sample_inputs: dict) -> None:
    """trace.json 包含所有必需字段."""
    rec = TraceRecorder(runtime, task="搭建 Flask")
    rec.start(sample_inputs)
    rec.record_graph_update({"type": "planner"})
    rec.record_custom_event({"type": "tool_call"})
    rec.record_custom_event({"type": "handoff", "from": "a", "to": "b"})

    result = rec.end(status="completed", latest_node="final")

    assert result is not None
    assert result["trace_id"] == "test-trace-001"
    assert result["task"] == "搭建 Flask"
    assert result["status"] == "completed"
    assert "started_at" in result
    assert "ended_at" in result
    assert result["duration_ms"] >= 0
    assert result["node_visits"] == {"planner": 1}
    assert result["tool_calls"] == 1
    assert result["failed_tool_calls"] == 0
    assert result["approval_count"] == 0
    assert result["handoff_count"] == 1
    assert result["checkpoint_count"] == 0
    assert isinstance(result["total_events"], int)
    assert isinstance(result["timeline_head"], list)
    assert isinstance(result["timeline_tail"], list)
    assert isinstance(result["timeline_omitted"], int)


def test_end_trace_json_on_disk_matches_return(runtime: RuntimeState, sample_inputs: dict) -> None:
    """end() 返回值和磁盘上的 trace.json 一致."""
    rec = TraceRecorder(runtime, task="test")
    rec.start(sample_inputs)
    returned = rec.end(status="error", latest_node="verifier")

    on_disk = json.loads((rec.root / "trace.json").read_text(encoding="utf-8"))
    assert on_disk == returned


def test_end_disabled_returns_none(runtime_off: RuntimeState, sample_inputs: dict) -> None:
    """trace_mode=off 时 end() 返回 None 且不创建文件."""
    rec = TraceRecorder(runtime_off, task="test")
    rec.start(sample_inputs)
    result = rec.end(status="completed", latest_node="final")
    assert result is None
    assert not rec.root.exists()


# ═══════════════════════════════════════════════════════════════════
# end — timeline.md
# ═══════════════════════════════════════════════════════════════════

def test_end_creates_timeline_md(runtime: RuntimeState, sample_inputs: dict) -> None:
    """end() 创建 timeline.md."""
    rec = TraceRecorder(runtime, task="搭建 Flask")
    rec.start(sample_inputs)
    rec.end(status="completed", latest_node="final")

    md_file = rec.root / "timeline.md"
    assert md_file.is_file()


def test_timeline_md_has_required_sections(runtime: RuntimeState, sample_inputs: dict) -> None:
    """timeline.md 包含必要章节."""
    rec = TraceRecorder(runtime, task="搭建 Flask")
    rec.start(sample_inputs)
    rec.record_graph_update({"type": "planner"})
    rec.end(status="completed", latest_node="final")

    content = (rec.root / "timeline.md").read_text(encoding="utf-8")
    assert "MokioClaw 执行追踪" in content
    assert "搭建 Flask" in content
    assert "✅" in content
    assert "completed" in content
    assert "统计摘要" in content
    assert "事件时间线" in content
    assert "test-trace-001" in content


def test_timeline_md_renders_events_table(runtime: RuntimeState, sample_inputs: dict) -> None:
    """timeline.md 以表格形式列出事件."""
    rec = TraceRecorder(runtime, task="test")
    rec.start(sample_inputs)
    rec.record_custom_event({"type": "tool_call", "tool": "Bash"})
    rec.end(status="completed", latest_node="final")

    content = (rec.root / "timeline.md").read_text(encoding="utf-8")
    assert "| # | 时间 | 类型 | 详情 |" in content
    assert "run_start" in content
    assert "tool_call" in content
    assert "run_end" in content


def test_timeline_omits_middle_events_for_large_trace(runtime: RuntimeState) -> None:
    """事件超过 100 条时省略中间部分."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})

    # 生成 120 个事件
    for i in range(120):
        rec.record_custom_event({"type": "tool_call", "tool": f"tool_{i}"})

    result = rec.end(status="completed", latest_node="final")
    assert result is not None
    assert result["timeline_omitted"] > 0
    assert len(result["timeline_head"]) == 20
    assert len(result["timeline_tail"]) == 80

    # timeline.md 中应有省略标记
    content = (rec.root / "timeline.md").read_text(encoding="utf-8")
    assert "省略" in content


# ═══════════════════════════════════════════════════════════════════
# events.jsonl
# ═══════════════════════════════════════════════════════════════════

def test_events_jsonl_has_all_events(runtime: RuntimeState, sample_inputs: dict) -> None:
    """events.jsonl 包含所有记录的事件（start + custom + graph + end）."""
    rec = TraceRecorder(runtime, task="test")
    rec.start(sample_inputs)
    rec.record_custom_event({"type": "tool_call", "tool": "Bash"})
    rec.record_graph_update({"type": "planner"})
    rec.record_custom_event({"type": "handoff", "from": "a", "to": "b"})
    rec.end(status="completed", latest_node="final")

    lines = (rec.root / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 5  # start + tool_call + planner + handoff + end


def test_events_jsonl_seq_increments(runtime: RuntimeState, sample_inputs: dict) -> None:
    """events.jsonl 中 seq 字段递增."""
    rec = TraceRecorder(runtime, task="test")
    rec.start(sample_inputs)
    rec.record_custom_event({"type": "a"})
    rec.record_custom_event({"type": "b"})
    rec.record_custom_event({"type": "c"})

    lines = (rec.root / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == [1, 2, 3, 4]


def test_events_jsonl_has_timestamps(runtime: RuntimeState) -> None:
    """events.jsonl 每条记录都带 timestamp."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})
    rec.end(status="completed", latest_node="final")

    lines = (rec.root / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    for line in lines:
        record = json.loads(line)
        assert "timestamp" in record
        assert "T" in record["timestamp"]  # ISO 8601 格式


# ═══════════════════════════════════════════════════════════════════
# end — final_state 摘要
# ═══════════════════════════════════════════════════════════════════

def test_end_includes_final_state_summary(runtime: RuntimeState) -> None:
    """end() 接受 final_state 并在 run_end 事件中包含摘要."""
    rec = TraceRecorder(runtime, task="test")
    rec.start({})
    state = {
        "task": "hello",
        "attempts": 2,
        "passed": True,
        "runtime": runtime,  # 不可序列化，应被排除
    }
    rec.end(status="completed", latest_node="final", final_state=state)

    lines = (rec.root / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    end_record = json.loads(lines[-1])
    assert end_record["type"] == "run_end"
    assert "final_state_summary" in end_record
    summary = end_record["final_state_summary"]
    assert summary["task"] == "hello"
    assert summary["attempts"] == 2
    assert summary["passed"] is True
    # runtime 不应出现在摘要中
    assert "runtime" not in summary


# ═══════════════════════════════════════════════════════════════════
# 集成 — 完整生命周期
# ═══════════════════════════════════════════════════════════════════

def test_full_lifecycle_produces_all_outputs(runtime: RuntimeState, sample_inputs: dict) -> None:
    """完整的 start → events → end 产生全部 3 个输出文件."""
    rec = TraceRecorder(runtime, task="完整测试")
    rec.start(sample_inputs)

    # 模拟完整执行：节点切换 + 工具调用 + 审批 + 检查点
    rec.record_graph_update({"type": "planner", "plan_summary": "方案 A"})
    rec.record_custom_event({"type": "tool_call", "tool": "Search", "args": {"q": "Flask"}})
    rec.record_custom_event({"type": "tool_result", "tool": "Search", "ok": True, "result_preview": "..."})
    rec.record_custom_event({"type": "handoff", "from": "planner", "to": "code_agent"})
    rec.record_custom_event({"type": "tool_call", "tool": "Bash", "args": {"command": "pip install flask"}})
    rec.record_custom_event({
        "type": "tool_result",
        "tool": "Bash",
        "ok": True,
        "requires_approval": True,
        "result_preview": "Success",
    })
    rec.record_graph_update({"type": "verifier", "passed": False})
    rec.record_graph_update({"type": "planner", "plan_summary": "方案 B"})
    rec.record_custom_event({"type": "checkpoint_saved", "saved_at": "2026-07-09T00:00:00Z", "mode": "light"})
    rec.record_graph_update({"type": "verifier", "passed": True})
    rec.record_graph_update({"type": "final", "final_answer": "完成"})

    result = rec.end(status="completed", latest_node="final")

    # 验证 trace.json
    assert result is not None
    assert result["status"] == "completed"
    assert result["node_visits"] == {"planner": 2, "verifier": 2, "final": 1}
    assert result["tool_calls"] == 2
    assert result["failed_tool_calls"] == 0
    assert result["approval_count"] == 1
    assert result["checkpoint_count"] == 1
    assert result["handoff_count"] == 1
    assert result["total_events"] == 13  # start + 10 events + run_end

    # 验证所有 3 个输出文件都存在
    assert (rec.root / "trace.json").is_file()
    assert (rec.root / "events.jsonl").is_file()
    assert (rec.root / "timeline.md").is_file()

    # 验证 events.jsonl 行数
    lines = (rec.root / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 13


def test_auto_generated_trace_id_isolation(runtime_no_id: RuntimeState) -> None:
    """每次创建 TraceRecorder 时自动生成的 trace_id 相互独立."""
    rec1 = TraceRecorder(runtime_no_id, task="test")
    rec2 = TraceRecorder(runtime_no_id, task="test")
    assert rec1.trace_id != rec2.trace_id
    assert rec1.root != rec2.root


def test_end_with_error_status(runtime: RuntimeState, sample_inputs: dict) -> None:
    """错误状态被正确记录."""
    rec = TraceRecorder(runtime, task="test")
    rec.start(sample_inputs)
    result = rec.end(status="error", latest_node="verifier")

    assert result is not None
    assert result["status"] == "error"

    content = (rec.root / "timeline.md").read_text(encoding="utf-8")
    assert "❌" in content
    assert "error" in content
