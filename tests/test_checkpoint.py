"""断点保存和恢复测试."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mokioclaw.core.checkpoint import (
    VALID_CHECKPOINT_MODES,
    CheckpointManager,
    CheckpointPayload,
    CheckpointSavedEvent,
    FileEntry,
    build_recovery_markdown,
    normalize_checkpoint_mode,
    resume_command,
    _build_workspace_manifest,
)
from mokioclaw.core.state import RuntimeState


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """创建临时 workspace 并初始化 git 仓库."""
    w = tmp_path / "test_workspace"
    w.mkdir()
    # 初始化 git
    import subprocess
    subprocess.run(
        ["git", "init"],
        cwd=str(w), capture_output=True, text=True, timeout=10,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(w), capture_output=True, text=True, timeout=10,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(w), capture_output=True, text=True, timeout=10,
    )
    return w


@pytest.fixture
def runtime(ws: Path) -> RuntimeState:
    """创建带 light 模式的 RuntimeState."""
    return RuntimeState(workspace=ws, model="gpt-4o-mini", checkpoint_mode="light")


@pytest.fixture
def runtime_strict(ws: Path) -> RuntimeState:
    """创建带 strict 模式的 RuntimeState."""
    return RuntimeState(workspace=ws, model="gpt-4o-mini", checkpoint_mode="strict")


@pytest.fixture
def runtime_off(ws: Path) -> RuntimeState:
    """创建带 off 模式的 RuntimeState."""
    return RuntimeState(workspace=ws, model="gpt-4o-mini", checkpoint_mode="off")


@pytest.fixture
def sample_state() -> dict:
    """最小可用的 graph state."""
    return {
        "task": "搭建 Flask 后台管理",
        "todos": [
            {"id": "1", "content": "初始化项目", "status": "completed", "note": "ok"},
        ],
        "plan_summary": "初始化 Flask 项目并创建目录结构",
        "attempts": 1,
        "max_attempts": 3,
        "passed": False,
        "verification_results": [],
        "last_error": "",
        "research_notes": "",
        "code_agent_summary": "",
    }


# ═══════════════════════════════════════════════════════════════════
# normalize_checkpoint_mode
# ═══════════════════════════════════════════════════════════════════

def test_normalize_valid_modes() -> None:
    """合法模式原样返回."""
    for mode in VALID_CHECKPOINT_MODES:
        assert normalize_checkpoint_mode(mode) == mode


def test_normalize_none_falls_back_to_light() -> None:
    """None → light."""
    assert normalize_checkpoint_mode(None) == "light"


def test_normalize_invalid_falls_back_to_light() -> None:
    """无效值 fallback 到 light."""
    assert normalize_checkpoint_mode("unknown") == "light"
    assert normalize_checkpoint_mode("") == "light"
    assert normalize_checkpoint_mode("STRICT") == "light"


# ═══════════════════════════════════════════════════════════════════
# CheckpointManager 属性
# ═══════════════════════════════════════════════════════════════════

def test_manager_enabled_light(runtime: RuntimeState) -> None:
    """light 模式 enabled=True."""
    cm = CheckpointManager(runtime, task="test")
    assert cm.enabled is True
    assert cm.mode == "light"


def test_manager_enabled_strict(runtime_strict: RuntimeState) -> None:
    """strict 模式 enabled=True."""
    cm = CheckpointManager(runtime_strict, task="test")
    assert cm.enabled is True
    assert cm.mode == "strict"


def test_manager_disabled_off(runtime_off: RuntimeState) -> None:
    """off 模式 enabled=False."""
    cm = CheckpointManager(runtime_off, task="test")
    assert cm.enabled is False
    assert cm.mode == "off"


def test_manager_creates_root_dir(runtime: RuntimeState) -> None:
    """构造时自动创建 root 目录."""
    cm = CheckpointManager(runtime, task="test")
    assert cm.root.is_dir()
    assert cm.root.name == "checkpoints"


# ═══════════════════════════════════════════════════════════════════
# save — off 模式
# ═══════════════════════════════════════════════════════════════════

def test_save_off_mode_returns_none(
    runtime_off: RuntimeState, sample_state: dict,
) -> None:
    """off 模式直接返回 None."""
    cm = CheckpointManager(runtime_off, task=sample_state["task"])
    result = cm.save(sample_state, status="running", latest_node="planner")
    assert result is None


# ═══════════════════════════════════════════════════════════════════
# save — light 模式
# ═══════════════════════════════════════════════════════════════════

def test_save_light_creates_checkpoint_json(
    runtime: RuntimeState, sample_state: dict,
) -> None:
    """light 模式创建 checkpoint.json."""
    cm = CheckpointManager(runtime, task=sample_state["task"])
    cm.save(sample_state, status="running", latest_node="planner")

    ckpt = cm.root / "checkpoint.json"
    assert ckpt.is_file()
    data = json.loads(ckpt.read_text(encoding="utf-8"))
    assert data["task"] == "搭建 Flask 后台管理"
    assert data["status"] == "running"
    assert data["mode"] == "light"
    assert data["latest_node"] == "planner"
    assert data["attempts"] == 1
    assert data["max_attempts"] == 3


def test_save_light_creates_recovery_md(
    runtime: RuntimeState, sample_state: dict,
) -> None:
    """light 模式创建 RECOVERY.md."""
    cm = CheckpointManager(runtime, task=sample_state["task"])
    cm.save(sample_state, status="running", latest_node="verifier")

    recovery = cm.root / "RECOVERY.md"
    assert recovery.is_file()
    content = recovery.read_text(encoding="utf-8")
    assert "MokioClaw 恢复指南" in content
    assert "搭建 Flask 后台管理" in content
    assert "verifier" in content


def test_save_light_no_state_json(
    runtime: RuntimeState, sample_state: dict,
) -> None:
    """light 模式不保存 state.json."""
    cm = CheckpointManager(runtime, task=sample_state["task"])
    cm.save(sample_state, status="running")
    assert not (cm.root / "state.json").exists()


def test_save_light_no_events_jsonl(
    runtime: RuntimeState, sample_state: dict,
) -> None:
    """light 模式不保存 events.jsonl."""
    cm = CheckpointManager(runtime, task=sample_state["task"])
    cm.save(sample_state, status="running")
    assert not (cm.root / "events.jsonl").exists()


def test_save_light_includes_manifest(
    runtime: RuntimeState, sample_state: dict,
) -> None:
    """light 模式的 checkpoint.json 包含文件清单."""
    # 创建一个测试文件
    (runtime.workspace / "hello.py").write_text("print('hi')")
    cm = CheckpointManager(runtime, task=sample_state["task"])
    cm.save(sample_state, status="running")

    data = json.loads((cm.root / "checkpoint.json").read_text(encoding="utf-8"))
    assert "manifest" in data
    assert isinstance(data["manifest"], list)
    paths = [e["path"] for e in data["manifest"]]
    assert "hello.py" in paths


def test_save_light_manifest_excludes_mokioclaw_internals(
    runtime: RuntimeState, sample_state: dict,
) -> None:
    """文件清单排除 .mokioclaw 内部目录."""
    # 创建 .mokioclaw 内部文件
    internal = runtime.workspace / ".mokioclaw" / "internal.txt"
    internal.parent.mkdir(parents=True, exist_ok=True)
    internal.write_text("internal")

    cm = CheckpointManager(runtime, task=sample_state["task"])
    cm.save(sample_state, status="running")

    data = json.loads((cm.root / "checkpoint.json").read_text(encoding="utf-8"))
    paths = [e["path"] for e in data["manifest"]]
    assert "internal.txt" not in paths
    assert ".mokioclaw/internal.txt" not in paths


def test_save_light_git_commits_snapshot(
    runtime: RuntimeState, sample_state: dict,
) -> None:
    """light 模式对工作区做 git commit 快照."""
    # 有文件变更才能 commit
    (runtime.workspace / "readme.md").write_text("# Test")
    cm = CheckpointManager(runtime, task=sample_state["task"])
    cm.save(sample_state, status="running", latest_node="planner")

    data = json.loads((cm.root / "checkpoint.json").read_text(encoding="utf-8"))
    assert data["git_commit_id"] != ""
    assert "mokioclaw checkpoint" in data["git_commit_message"]


def test_save_light_returns_event(
    runtime: RuntimeState, sample_state: dict,
) -> None:
    """save() 返回 CheckpointSavedEvent."""
    cm = CheckpointManager(runtime, task=sample_state["task"])
    event = cm.save(sample_state, status="running", latest_node="planner")
    assert event is not None
    assert event.type == "checkpoint_saved"
    assert event.mode == "light"
    assert event.status == "running"
    assert event.latest_node == "planner"
    assert event.checkpoint_dir == str(cm.root)


# ═══════════════════════════════════════════════════════════════════
# save — strict 模式
# ═══════════════════════════════════════════════════════════════════

def test_save_strict_saves_state_json(
    runtime_strict: RuntimeState, sample_state: dict,
) -> None:
    """strict 模式额外保存 state.json."""
    cm = CheckpointManager(runtime_strict, task=sample_state["task"])
    cm.save(sample_state, status="running")

    state_file = cm.root / "state.json"
    assert state_file.is_file()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["task"] == "搭建 Flask 后台管理"


def test_save_strict_appends_events_jsonl(
    runtime_strict: RuntimeState, sample_state: dict,
) -> None:
    """strict 模式在有 event 时追加 events.jsonl."""
    cm = CheckpointManager(runtime_strict, task=sample_state["task"])
    evt = {"type": "planner", "plan_summary": "ok"}
    cm.save(sample_state, status="running", latest_node="planner", event=evt)

    events_file = cm.root / "events.jsonl"
    assert events_file.is_file()
    lines = events_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["seq"] == 1
    assert record["event"]["type"] == "planner"


def test_save_strict_events_jsonl_increments_seq(
    runtime_strict: RuntimeState, sample_state: dict,
) -> None:
    """多次调用 save 时 events.jsonl 序号递增."""
    cm = CheckpointManager(runtime_strict, task=sample_state["task"])
    cm.save(sample_state, status="running", latest_node="planner", event={"type": "a"})
    cm.save(sample_state, status="running", latest_node="verifier", event={"type": "b"})
    cm.save(sample_state, status="running", latest_node="final", event={"type": "c"})

    events_file = cm.root / "events.jsonl"
    lines = events_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == [1, 2, 3]


def test_save_strict_no_event_no_jsonl_append(
    runtime_strict: RuntimeState, sample_state: dict,
) -> None:
    """strict 模式但无 event 时不写入 events.jsonl."""
    cm = CheckpointManager(runtime_strict, task=sample_state["task"])
    cm.save(sample_state, status="running", event=None)
    assert not (cm.root / "events.jsonl").exists()


# ═══════════════════════════════════════════════════════════════════
# resume_command
# ═══════════════════════════════════════════════════════════════════

def test_resume_command_format(runtime: RuntimeState) -> None:
    """生成标准的 mokioclaw --resume 命令."""
    cmd = resume_command(runtime.workspace)
    assert cmd.startswith("mokioclaw --resume ")
    assert str(runtime.workspace) in cmd


# ═══════════════════════════════════════════════════════════════════
# build_recovery_markdown
# ═══════════════════════════════════════════════════════════════════

def test_build_recovery_markdown_has_sections() -> None:
    """RECOVERY.md 包含必要章节."""
    payload = CheckpointPayload(
        task="测试任务",
        status="error",
        mode="strict",
        saved_at="2026-07-01T00:00:00+00:00",
        latest_node="verifier",
        attempts=2,
        max_attempts=3,
        passed=False,
        plan_summary="初始化 + 创建模型",
        todos_count=3,
        verification_results_count=0,
        git_commit_id="abc123",
        git_commit_message="[mokioclaw checkpoint] node=verifier",
        workspace_root="/tmp/test",
        manifest=[
            FileEntry(path="app.py", size=100, modified="2026-07-01T00:00:00+00:00"),
        ],
        last_event={"type": "verifier", "passed": False},
        resume_command="mokioclaw --resume /tmp/test",
    )

    md = build_recovery_markdown(payload)
    assert "# 🔄 MokioClaw 恢复指南" in md
    assert "测试任务" in md
    assert "error" in md
    assert "abc123" in md
    assert "app.py" in md
    assert "mokioclaw --resume" in md
    assert "ResumeCommand" not in md  # 没有 raw 类名
    assert "```json" in md


def test_build_recovery_markdown_no_git_commit() -> None:
    """无 git commit 时不显示该章节."""
    payload = CheckpointPayload(
        task="test",
        status="running",
        mode="light",
        saved_at="",
        latest_node=None,
        attempts=0,
        max_attempts=3,
        passed=False,
        plan_summary="",
        todos_count=0,
        verification_results_count=0,
        git_commit_id="",
        git_commit_message="",
        workspace_root="/tmp",
        manifest=[],
        last_event=None,
        resume_command="mokioclaw --resume /tmp",
    )
    md = build_recovery_markdown(payload)
    assert "Git 快照" not in md


def test_build_recovery_markdown_no_plan_summary() -> None:
    """无计划摘要时不显示该章节."""
    payload = CheckpointPayload(
        task="test", status="running", mode="light", saved_at="",
        latest_node=None, attempts=0, max_attempts=3, passed=False,
        plan_summary="", todos_count=0, verification_results_count=0,
        git_commit_id="", git_commit_message="", workspace_root="/tmp",
        manifest=[], last_event=None,
        resume_command="mokioclaw --resume /tmp",
    )
    md = build_recovery_markdown(payload)
    assert "计划摘要" not in md


# ═══════════════════════════════════════════════════════════════════
# _build_workspace_manifest
# ═══════════════════════════════════════════════════════════════════

def test_build_manifest_captures_files(ws: Path) -> None:
    """_build_workspace_manifest 正确列出文件."""
    (ws / "a.py").write_text("hello")
    (ws / "b.py").write_text("world")
    manifest = _build_workspace_manifest(ws)
    paths = {e.path for e in manifest}
    assert "a.py" in paths
    assert "b.py" in paths


def test_build_manifest_excludes_internals(ws: Path) -> None:
    """_build_workspace_manifest 排除 .mokioclaw / .git 等目录."""
    (ws / "src.py").write_text("src")
    (ws / ".mokioclaw" / "x").mkdir(parents=True, exist_ok=True)
    (ws / ".mokioclaw" / "x" / "internal.txt").write_text("x")
    (ws / ".git" / "HEAD").parent.mkdir(parents=True, exist_ok=True)
    (ws / "__pycache__" / "c.pyc").parent.mkdir(parents=True, exist_ok=True)

    manifest = _build_workspace_manifest(ws)
    paths = {e.path for e in manifest}
    assert "src.py" in paths
    # 排除项不应出现
    for p in paths:
        assert not p.startswith(".mokioclaw/")
        assert not p.startswith(".git/")
        assert not p.startswith("__pycache__/")


def test_build_manifest_empty_workspace(ws: Path) -> None:
    """空 workspace 返回空列表."""
    manifest = _build_workspace_manifest(ws)
    assert isinstance(manifest, list)


# ═══════════════════════════════════════════════════════════════════
# load_resume_inputs
# ═══════════════════════════════════════════════════════════════════

def test_load_resume_no_checkpoint_returns_none(runtime: RuntimeState) -> None:
    """无检查点时返回 None."""
    result = CheckpointManager.load_resume_inputs(runtime)
    assert result is None


def test_load_resume_restores_inputs(
    runtime: RuntimeState, sample_state: dict,
) -> None:
    """保存后能恢复 inputs."""
    cm = CheckpointManager(runtime, task=sample_state["task"])
    cm.save(sample_state, status="running", latest_node="planner")

    result = CheckpointManager.load_resume_inputs(runtime)
    assert result is not None
    inputs, resume_event = result
    assert inputs["task"] == "搭建 Flask 后台管理"
    assert inputs["attempts"] == 1
    assert inputs["max_attempts"] == 3
    assert inputs["runtime"] is runtime
    assert resume_event["type"] == "resume"
    assert resume_event["mode"] == "light"


def test_load_resume_strict_mode_has_more_fields(
    runtime_strict: RuntimeState, sample_state: dict,
) -> None:
    """strict 模式恢复更多字段."""
    sample_state["plan_summary"] = "详细的计划"
    sample_state["research_notes"] = "Flask 文档研究"
    cm = CheckpointManager(runtime_strict, task=sample_state["task"])
    cm.save(sample_state, status="running", latest_node="verifier")

    result = CheckpointManager.load_resume_inputs(runtime_strict)
    assert result is not None
    inputs, _ = result
    assert inputs["plan_summary"] == "详细的计划"
    assert inputs["research_notes"] == "Flask 文档研究"


def test_load_resume_strict_no_state_json_graceful(
    runtime_strict: RuntimeState, sample_state: dict,
) -> None:
    """strict 模式但 state.json 被删除时优雅降级."""
    cm = CheckpointManager(runtime_strict, task=sample_state["task"])
    cm.save(sample_state, status="running")

    # 删除 state.json
    (cm.root / "state.json").unlink()

    result = CheckpointManager.load_resume_inputs(runtime_strict)
    assert result is not None
    inputs, _ = result
    assert inputs["task"] == sample_state["task"]


def test_load_resume_corrupted_checkpoint_json(
    runtime: RuntimeState,
) -> None:
    """checkpoint.json 损坏时返回 None."""
    cm = CheckpointManager(runtime, task="test")
    # 写入损坏的 JSON
    (cm.root / "checkpoint.json").write_text("{corrupted", encoding="utf-8")
    result = CheckpointManager.load_resume_inputs(runtime)
    assert result is None


def test_load_resume_override_task(
    runtime: RuntimeState, sample_state: dict,
) -> None:
    """load_resume_inputs 的 task 参数可覆盖已保存的任务."""
    cm = CheckpointManager(runtime, task=sample_state["task"])
    cm.save(sample_state, status="running")

    result = CheckpointManager.load_resume_inputs(runtime, task="覆盖的任务")
    assert result is not None
    inputs, _ = result
    assert inputs["task"] == "覆盖的任务"


# ═══════════════════════════════════════════════════════════════════
# CheckpointPayload / CheckpointSavedEvent 数据类
# ═══════════════════════════════════════════════════════════════════

def test_checkpoint_payload_defaults() -> None:
    """CheckpointPayload 默认字段."""
    payload = CheckpointPayload(
        task="",
        status="",
        mode="light",
        saved_at="",
        latest_node=None,
        attempts=0,
        max_attempts=3,
        passed=False,
        plan_summary="",
        todos_count=0,
        verification_results_count=0,
        git_commit_id="",
        git_commit_message="",
        workspace_root="",
        manifest=[],
        last_event=None,
        resume_command="",
    )
    assert payload.status == ""
    assert payload.mode == "light"


def test_checkpoint_saved_event_defaults() -> None:
    """CheckpointSavedEvent 默认字段."""
    evt = CheckpointSavedEvent()
    assert evt.type == "checkpoint_saved"
    assert evt.saved_at == ""
    assert evt.mode == ""
