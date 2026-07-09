"""断点保存和恢复 —— 支持 light / strict / off 三种检查点模式.

模式对比:
    light:  只保存 checkpoint.json + RECOVERY.md + git 快照（每次节点切换保存）
    strict: 额外保存 state.json + events.jsonl（每个事件都追加）
    off:    完全不保存
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mokioclaw.core.state import RuntimeState

# ═══════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════

VALID_CHECKPOINT_MODES: set[str] = {"light", "strict", "off"}

_MOKIOCLAW_DIR = ".mokioclaw"
_CHECKPOINTS_DIR = "checkpoints"

# workspace_manifest 中要排除的目录
_MANIFEST_EXCLUDE = {".mokioclaw", ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".eggs"}


def normalize_checkpoint_mode(mode: str | None) -> str:
    """标准化 checkpoint 模式字符串."""
    if mode is None:
        return "light"
    if mode not in VALID_CHECKPOINT_MODES:
        return "light"
    return mode


# ═══════════════════════════════════════════════════════════════════
# 辅助数据类
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FileEntry:
    """文件清单中的单条记录."""
    path: str
    size: int
    modified: str  # ISO 8601


@dataclass
class CheckpointPayload:
    """checkpoint.json 的完整负载."""
    task: str
    status: str
    mode: str
    saved_at: str
    latest_node: str | None
    attempts: int
    max_attempts: int
    passed: bool
    plan_summary: str
    todos_count: int
    verification_results_count: int
    git_commit_id: str
    git_commit_message: str
    workspace_root: str
    manifest: list[FileEntry]
    last_event: dict | None
    resume_command: str


@dataclass
class CheckpointSavedEvent:
    """save() 返回的事件."""
    type: str = "checkpoint_saved"
    saved_at: str = ""
    mode: str = ""
    status: str = ""
    latest_node: str | None = None
    git_commit_id: str = ""
    checkpoint_dir: str = ""


# ═══════════════════════════════════════════════════════════════════
# workspace_manifest — 文件清单
# ═══════════════════════════════════════════════════════════════════

def _build_workspace_manifest(workspace: Path) -> list[FileEntry]:
    """生成 workspace 内所有文件的清单（排除 .mokioclaw / .git 等目录）."""
    entries: list[FileEntry] = []
    try:
        for p in sorted(workspace.rglob("*")):
            if p.is_dir():
                continue
            # 跳过排除目录内的文件
            rel = p.relative_to(workspace)
            if rel.parts and rel.parts[0] in _MANIFEST_EXCLUDE:
                continue
            stat = p.stat()
            entries.append(FileEntry(
                path=str(rel).replace("\\", "/"),
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            ))
    except OSError:
        pass
    return entries


# ═══════════════════════════════════════════════════════════════════
# git 快照
# ═══════════════════════════════════════════════════════════════════

def _is_git_repo(workspace: Path) -> bool:
    """检查 workspace 是否是一个 git 仓库."""
    return (workspace / ".git").exists()


def _git_commit_snapshot(workspace: Path, message: str) -> tuple[str, str]:
    """对工作区做一次 git 快照提交.

    Returns:
        (commit_id 或 空字符串, commit_message).
    """
    if not _is_git_repo(workspace):
        return "", ""

    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except subprocess.CalledProcessError:
        return "", ""

    try:
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", message],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except subprocess.CalledProcessError:
        return "", ""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip(), message
    except subprocess.CalledProcessError:
        return "", message


def _git_restore_from_commit(workspace: Path, commit_id: str) -> bool:
    """从指定 git commit 恢复工作区文件.

    使用 git checkout 将工作区恢复到指定 commit 的状态。
    """
    if not _is_git_repo(workspace) or not commit_id:
        return False
    try:
        subprocess.run(
            ["git", "checkout", commit_id, "--", "."],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


# ═══════════════════════════════════════════════════════════════════
# 恢复命令 & RECOVERY.md
# ═══════════════════════════════════════════════════════════════════

def resume_command(workspace: Path) -> str:
    """生成恢复命令字符串."""
    return f"mokioclaw --resume {workspace}"


def build_recovery_markdown(payload: CheckpointPayload) -> str:
    """生成人类可读的 RECOVERY.md 内容."""
    lines = [
        f"# 🔄 MokioClaw 恢复指南",
        "",
        f"**任务:** {payload.task}",
        f"**状态:** {payload.status}",
        f"**检查点时间:** {payload.saved_at}",
        f"**检查点模式:** {payload.mode}",
        f"**最新节点:** {payload.latest_node or 'N/A'}",
        f"**尝试次数:** {payload.attempts} / {payload.max_attempts}",
        f"**验证通过:** {'✅ 是' if payload.passed else '❌ 否'}",
        "",
    ]

    if payload.plan_summary:
        lines.append("## 📋 计划摘要")
        lines.append("")
        lines.append(payload.plan_summary)
        lines.append("")

    if payload.git_commit_id:
        lines.append("## 🔖 Git 快照")
        lines.append(f"- **Commit:** `{payload.git_commit_id}`")
        lines.append(f"- **Message:** `{payload.git_commit_message}`")
        lines.append("")
        lines.append("要恢复到当前快照状态：")
        lines.append(f"```bash")
        lines.append(f"git checkout {payload.git_commit_id} -- .")
        lines.append(f"```")
        lines.append("")

    if payload.manifest:
        lines.append(f"## 📁 工作区文件清单 ({len(payload.manifest)} 个文件)")
        lines.append("")
        lines.append("| 路径 | 大小 | 修改时间 |")
        lines.append("|------|------|----------|")
        for entry in payload.manifest:
            size_kb = f"{entry.size / 1024:.1f} KB" if entry.size >= 1024 else f"{entry.size} B"
            lines.append(f"| `{entry.path}` | {size_kb} | {entry.modified} |")
        lines.append("")

    lines.append("## 🚀 恢复命令")
    lines.append("")
    lines.append("```bash")
    lines.append(payload.resume_command)
    lines.append("```")
    lines.append("")

    if payload.last_event:
        lines.append("## 📝 最后一个事件")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(payload.last_event, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# CheckpointManager
# ═══════════════════════════════════════════════════════════════════

class CheckpointManager:
    """检查点管理器 —— 保存和恢复 MokioClaw 运行状态.

    用法::

        cm = CheckpointManager(runtime, task="..."

        # 保存检查点（在节点切换或事件后调用）
        event = cm.save(state, status="running", latest_node="planner")

        # 恢复
        inputs, resume_event = CheckpointManager.load_resume_inputs(runtime)
    """

    def __init__(
        self,
        runtime: RuntimeState,
        task: str = "",
    ) -> None:
        self.workspace: Path = runtime.workspace.resolve()
        self.mode: str = normalize_checkpoint_mode(runtime.checkpoint_mode)
        self.task: str = task
        self.root: Path = self.workspace / _MOKIOCLAW_DIR / _CHECKPOINTS_DIR

        # 递增的事件计数器（strict 模式下用于 events.jsonl 排序）
        self._event_seq: int = 0

        # 确保根目录存在
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    # ── 属性 ───────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """检查点是否启用."""
        return self.mode != "off"

    # ── save ───────────────────────────────────────────────────────

    def save(
        self,
        state: dict,
        *,
        status: str = "running",
        latest_node: str | None = None,
        event: dict | None = None,
    ) -> CheckpointSavedEvent | None:
        """保存检查点.

        流程:
        1. 创建 self.root 目录
        2. strict 模式: 追加事件到 events.jsonl，保存完整 state.json
        3. 生成 workspace 文件清单
        4. git commit 工作区快照
        5. 保存 checkpoint.json（元数据 + 状态摘要）
        6. 生成 RECOVERY.md
        7. 返回 checkpoint_saved_event 或 None
        """
        if not self.enabled:
            return None

        self.root.mkdir(parents=True, exist_ok=True)
        saved_at = datetime.now(timezone.utc).isoformat()

        # ── strict 模式额外保存 ──
        if self.mode == "strict":
            if event:
                self._append_event(saved_at, event)
            self._save_full_state(state)

        # ── workspace 文件清单 ──
        manifest = _build_workspace_manifest(self.workspace)

        # ── git 快照 ──
        commit_msg = (
            f"[mokioclaw checkpoint] node={latest_node or 'unknown'} "
            f"status={status} attempts={state.get('attempts', 0)}"
        )
        commit_id, commit_msg = _git_commit_snapshot(self.workspace, commit_msg)

        # ── checkpoint.json ──
        payload = CheckpointPayload(
            task=self.task or state.get("task", ""),
            status=status,
            mode=self.mode,
            saved_at=saved_at,
            latest_node=latest_node,
            attempts=state.get("attempts", 0),
            max_attempts=state.get("max_attempts", 3),
            passed=state.get("passed", False),
            plan_summary=state.get("plan_summary", ""),
            todos_count=len(state.get("todos", [])),
            verification_results_count=len(state.get("verification_results", [])),
            git_commit_id=commit_id,
            git_commit_message=commit_msg,
            workspace_root=str(self.workspace),
            manifest=manifest,
            last_event=event,
            resume_command=resume_command(self.workspace),
        )
        self._save_checkpoint_json(payload)

        # ── RECOVERY.md ──
        recovery_md = build_recovery_markdown(payload)
        (self.root / "RECOVERY.md").write_text(recovery_md, encoding="utf-8")

        return CheckpointSavedEvent(
            type="checkpoint_saved",
            saved_at=saved_at,
            mode=self.mode,
            status=status,
            latest_node=latest_node,
            git_commit_id=commit_id,
            checkpoint_dir=str(self.root),
        )

    # ── 内部保存方法 ───────────────────────────────────────────────

    def _append_event(self, saved_at: str, event: dict) -> None:
        """向 events.jsonl 追加一条事件记录."""
        self._event_seq += 1
        record = {
            "seq": self._event_seq,
            "saved_at": saved_at,
            "event": event,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(line)

    def _save_full_state(self, state: dict) -> None:
        """保存完整 state.json（排除不可序列化的对象）."""
        serializable = _make_serializable(state)
        (self.root / "state.json").write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _save_checkpoint_json(self, payload: CheckpointPayload) -> None:
        """将 CheckpointPayload 序列化写入 checkpoint.json."""
        data = {
            "task": payload.task,
            "status": payload.status,
            "mode": payload.mode,
            "saved_at": payload.saved_at,
            "latest_node": payload.latest_node,
            "attempts": payload.attempts,
            "max_attempts": payload.max_attempts,
            "passed": payload.passed,
            "plan_summary": payload.plan_summary,
            "todos_count": payload.todos_count,
            "verification_results_count": payload.verification_results_count,
            "git_commit_id": payload.git_commit_id,
            "git_commit_message": payload.git_commit_message,
            "workspace_root": payload.workspace_root,
            "manifest": [asdict(e) for e in payload.manifest],
            "last_event": payload.last_event,
            "resume_command": payload.resume_command,
        }
        (self.root / "checkpoint.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    # ── load_resume_inputs（类方法）────────────────────────────────

    @classmethod
    def load_resume_inputs(
        cls,
        runtime: RuntimeState,
        task: str | None = None,
        max_attempts: int = 3,
    ) -> tuple[dict, dict] | None:
        """从检查点恢复运行时输入.

        Returns:
            (inputs, resume_event) 或 None（如果没有检查点）.
        """
        workspace = runtime.workspace.resolve()
        root = workspace / _MOKIOCLAW_DIR / _CHECKPOINTS_DIR
        checkpoint_file = root / "checkpoint.json"

        if not checkpoint_file.is_file():
            return None

        try:
            data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        # 恢复 git 快照
        commit_id = data.get("git_commit_id", "")
        if commit_id:
            _git_restore_from_commit(workspace, commit_id)

        # 重建 inputs 字典
        loaded_task = task or data.get("task", "") or ""
        inputs: dict[str, Any] = {
            "task": loaded_task,
            "runtime": runtime,
            "attempts": data.get("attempts", 0),
            "max_attempts": max_attempts,
            "passed": 0,
            "todos": [],
            "verification_results": [],
        }

        # 如果有完整 state.json（strict 模式），从中恢复更多字段
        state_file = root / "state.json"
        if state_file.is_file():
            try:
                full_state = json.loads(state_file.read_text(encoding="utf-8"))
                # 恢复可恢复的标量/列表字段
                for key in (
                    "plan_summary",
                    "research_notes",
                    "code_agent_summary",
                    "verifier_summary",
                    "context_summary",
                    "todos",
                    "sources",
                    "verification_results",
                    "verification_checks",
                    "last_error",
                ):
                    if key in full_state and full_state[key] is not None:
                        inputs[key] = full_state[key]
            except (json.JSONDecodeError, OSError):
                pass

        resume_event = {
            "type": "resume",
            "checkpoint_dir": str(root),
            "saved_at": data.get("saved_at", ""),
            "mode": data.get("mode", ""),
            "status": data.get("status", ""),
            "task": loaded_task,
            "git_commit_id": commit_id,
            "workspace_root": data.get("workspace_root", str(workspace)),
        }
        return inputs, resume_event


# ═══════════════════════════════════════════════════════════════════
# 序列化辅助
# ═══════════════════════════════════════════════════════════════════

def _make_serializable(obj: Any) -> Any:
    """递归地将不可序列化的对象转为可 JSON 序列化的形式."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(item) for item in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return _make_serializable(asdict(obj))
    # 其他类型退化为字符串
    return str(obj)
