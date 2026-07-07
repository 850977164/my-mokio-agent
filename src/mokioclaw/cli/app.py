"""MokioClaw CLI —— Typer 应用入口.

用法:
    mokioclaw "帮我重构这个模块" --workspace /path/to/project
    mokioclaw "检查代码质量" --workspace ./my-project --model gpt-4o
    mokioclaw "写个测试" --workspace ./src --max-attempts 5
"""

from __future__ import annotations

import os

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mokioclaw.core.agent import stream_agent_events
from mokioclaw.core.paths import ensure_workspace, resolve_workspace

load_dotenv()

console = Console()

app = typer.Typer(
    name="mokioclaw",
    help="MokioClaw 智能调度代理 — 在指定工作区执行 AI 辅助开发任务",
)


@app.command()
def main(
    task: str = typer.Argument(..., help="任务描述，用自然语言告诉代理你要做什么"),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="工作区路径，默认为当前目录下的 .mokioclaw/workspaces/default",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="模型名称，默认从环境变量 MODEL 读取，回退到 gpt-4o",
    ),
    max_attempts: int = typer.Option(
        3,
        "--max-attempts",
        "-a",
        help="最大重试次数，默认 3 次。验证失败后会返回 Planner 修订计划重试",
    ),
) -> None:
    """启动 MokioClaw 代理执行任务。

    示例:
        mokioclaw "阅读 README.md 并总结"
        mokioclaw "找出所有 TODO 注释" --workspace ./src
        mokioclaw "写测试" --workspace ./src --max-attempts 5
    """
    # 解析模型名：CLI 参数 > 环境变量 MODEL > 默认 gpt-4o
    if model is None:
        model = os.getenv("MODEL", "gpt-4o")
    ws_path = resolve_workspace(workspace)
    ws_path = ensure_workspace(ws_path)

    console.print(Panel.fit(
        f"[bold cyan]🚀 MokioClaw v0.1.0[/]\n"
        f"📂 workspace:   {ws_path}\n"
        f"🤖 model:       {model}\n"
        f"🔁 max attempts: {max_attempts}\n"
        f"📋 task:        {task}",
        title="MokioClaw",
        border_style="cyan",
    ))

    # 遍历 Plan & Execute 事件流
    attempt_count = 0

    for event in stream_agent_events(
        task,
        workspace=ws_path,
        max_attempts=max_attempts,
        model=model,
    ):
        event_type = event["type"]

        if event_type == "planner":
            _display_planner(event)

        elif event_type == "actor":
            _display_actor(event)

        elif event_type == "verifier":
            _display_verifier(event)

        elif event_type == "final":
            _display_final(event)

        elif event_type == "custom":
            # 透传的自定义事件，暂不处理
            pass

    console.print()


# ═══════════════════════════════════════════════════════════════════════════
# 各节点输出渲染
# ═══════════════════════════════════════════════════════════════════════════

def _display_planner(event: dict) -> None:
    """渲染 Planner 节点产出：计划摘要 + todo 列表."""
    plan_summary = event.get("plan_summary", "")
    todos: list[dict] = event.get("todos", [])
    acceptance_criteria: list[str] = event.get("acceptance_criteria", [])
    verification_commands: list[str] = event.get("verification_commands", [])

    # ── 计划面板 ──
    content_lines = []
    if plan_summary:
        content_lines.append(plan_summary)
    if todos:
        content_lines.append("")
        content_lines.append("[bold]📝 待办项:[/]")
        for t in todos:
            tid = t.get("id", "?")
            todo_content = t.get("content", "")
            status = t.get("status", "pending")
            icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "blocked": "❌"}.get(status, "⬜")
            content_lines.append(f"  {icon} [{tid}] {todo_content}")
    if acceptance_criteria:
        content_lines.append("")
        content_lines.append("[bold]✔️  验收标准:[/]")
        for i, c in enumerate(acceptance_criteria, 1):
            content_lines.append(f"  {i}. {c}")
    if verification_commands:
        content_lines.append("")
        content_lines.append("[bold]🖥️  验证命令:[/]")
        for cmd in verification_commands:
            content_lines.append(f"  $ {cmd}")

    console.print()
    console.print(Panel(
        "\n".join(content_lines),
        title="📋 Planner",
        border_style="blue",
    ))


def _display_actor(event: dict) -> None:
    """渲染 Actor 节点产出：执行总结 + 更新后的 todo 状态."""
    last_summary = event.get("last_actor_summary", "")
    todos: list[dict] = event.get("todos", [])

    # ── 统计 ──
    total = len(todos)
    completed = sum(1 for t in todos if t.get("status") == "completed")
    blocked = sum(1 for t in todos if t.get("status") == "blocked")
    in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
    pending = sum(1 for t in todos if t.get("status") == "pending")

    stats = f"完成 {completed}/{total}"
    if blocked:
        stats += f"  [red]受阻 {blocked}[/]"
    if in_progress:
        stats += f"  [yellow]进行中 {in_progress}[/]"
    if pending:
        stats += f"  [dim]待办 {pending}[/]"

    content_lines = [stats, ""]
    if last_summary:
        content_lines.append(last_summary)

    console.print()
    console.print(Panel(
        "\n".join(content_lines),
        title="🔧 Actor",
        border_style="green",
    ))


def _display_verifier(event: dict) -> None:
    """渲染 Verifier 节点产出：passed/failed + 验证命令结果 + 验收检查明细."""
    passed: bool = event.get("passed", False)
    attempts: int = event.get("attempts", 0)
    verification_results: list[dict] = event.get("verification_results", [])
    verification_checks: list[dict] = event.get("verification_checks", [])
    last_error: str = event.get("last_error", "")

    # ── 判定图标 ──
    if passed:
        header = "✅ 验证通过"
        border = "green"
    else:
        header = "❌ 验证失败"
        border = "red"

    content_lines = [f"[bold]{header}[/]  (第 {attempts} 次)"]

    # ── 验证命令结果 ──
    if verification_results:
        content_lines.append("")
        content_lines.append("[bold]🖥️  验证命令:[/]")
        for vr in verification_results:
            cmd = vr.get("command", "?")
            ok = vr.get("ok", False)
            exit_code = vr.get("exit_code", None)
            stdout = vr.get("stdout", "")
            stderr = vr.get("stderr", "")
            icon = "✅" if ok else "❌"
            content_lines.append(f"  {icon} $ {cmd} (exit={exit_code})")
            if stdout:
                preview = "\n".join(stdout.strip().split("\n")[:3])
                content_lines.append(f"       [dim]{preview}[/]")
            if stderr:
                preview = "\n".join(stderr.strip().split("\n")[:3])
                content_lines.append(f"       [dim red]{preview}[/]")

    # ── 验收检查明细 ──
    if verification_checks:
        content_lines.append("")
        content_lines.append("[bold]📊 验收明细:[/]")
        for c in verification_checks:
            name = c.get("name", "?")
            check_passed = c.get("passed", False)
            detail = c.get("detail", "")
            icon = "✅" if check_passed else "❌"
            line = f"  {icon} {name}"
            if detail:
                line += f" — {detail}"
            content_lines.append(line)

    # ── 失败信息 ──
    if last_error:
        content_lines.append("")
        content_lines.append(f"[red]{last_error}[/]")

    console.print()
    console.print(Panel(
        "\n".join(content_lines),
        title="✅ Verifier" if passed else "❌ Verifier",
        border_style=border,
    ))


def _display_final(event: dict) -> None:
    """渲染 Final 节点产出：final_answer 文本."""
    final_answer: str = event.get("final_answer", "")

    console.print()
    if final_answer:
        # final_answer 已经是格式化的多行文本，直接展示
        console.print(Panel(
            final_answer,
            title="📝 最终结果",
            border_style="cyan",
        ))
