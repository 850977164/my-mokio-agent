"""MokioClaw CLI —— Typer 应用入口.

用法:
    mokioclaw "帮我重构这个模块" --workspace /path/to/project
    mokioclaw "检查代码质量" --workspace ./my-project --model gpt-4o
    mokioclaw "写个测试" --workspace ./src --max-attempts 5
    mokioclaw "搭建项目" --approval-mode auto --checkpoint-mode strict --trace-mode on
    mokioclaw --resume /path/to/checkpoint "继续之前的任务"
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from mokioclaw.core.agent import stream_agent_events
from mokioclaw.core.paths import ensure_workspace, resolve_workspace

load_dotenv()

console = Console()

app = typer.Typer(
    name="mokioclaw",
    help="MokioClaw 智能调度代理 — 在指定工作区执行 AI 辅助开发任务",
)

Option = typer.Option


@app.command()
def main(
        ctx: typer.Context,
        task: Annotated[str, typer.Argument(help="任务描述，用自然语言告诉代理你要做什么")],
        workspace: Annotated[Path | None, Option("--workspace", "-w",
                                                 help="工作区路径，默认为当前目录下的 .mokioclaw/workspaces/default")] = None,
        model: Annotated[
            str | None, Option("--model", "-m", help="模型名称，默认从环境变量 MODEL 读取，回退到 gpt-4o")] = None,
        max_attempts: Annotated[int, Option("--max-attempts", "-a",
                                            help="最大重试次数，默认 3 次。验证失败后会返回 Planner 修订计划重试")] = 3,
        approval_mode: Annotated[Literal["inline", "auto", "deny"], Option("--approval-mode",
                                                                           help="审批模式: inline (交互审批) | auto (自动放行) | deny (禁止风险命令)")] = "inline",
        checkpoint_mode: Annotated[Literal["light", "strict", "off"], Option("--checkpoint-mode",
                                                                             help="检查点模式: light (节点切换时保存) | strict (每个事件都保存) | off (不保存)")] = "light",
        trace_mode: Annotated[
            Literal["on", "off"], Option("--trace-mode", help="追踪模式: on (记录执行追踪) | off (不记录)")] = "on",
        resume: Annotated[Path | None, Option("--resume", help="从指定检查点工作区恢复运行")] = None,
) -> None:
    """启动 MokioClaw 代理执行任务。

    示例:
        mokioclaw "阅读 README.md 并总结"
        mokioclaw "找出所有 TODO 注释" --workspace ./src
        mokioclaw "写测试" --workspace ./src --max-attempts 5
        mokioclaw "搭建项目" --approval-mode auto --checkpoint-mode strict
        mokioclaw --resume ./project "继续之前的任务"
    """
    # 解析模型名：CLI 参数 > 环境变量 MODEL > 默认 gpt-4o
    if model is None:
        model = os.getenv("MODEL", "gpt-4o")

    # 解析工作区
    ws_path = resolve_workspace(str(workspace) if workspace else None)
    ws_path = ensure_workspace(ws_path)

    # 解析恢复路径
    resume_path = resume.resolve() if resume else None

    console.print(Panel.fit(
        f"[bold cyan]🚀 MokioClaw v0.1.0[/]\n"
        f"📂 workspace:        {ws_path}\n"
        f"🤖 model:            {model}\n"
        f"🔁 max attempts:     {max_attempts}\n"
        f"🔐 approval mode:    {approval_mode}\n"
        f"💾 checkpoint mode:  {checkpoint_mode}\n"
        f"🔍 trace mode:       {trace_mode}\n"
        f"📋 task:             {task}"
        + (f"\n🔄 resume from:      {resume_path}" if resume_path else ""),
        title="MokioClaw",
        border_style="cyan",
    ))

    # 遍历 MultiAgent 事件流
    for event in stream_agent_events(
            task,
            workspace=ws_path,
            max_attempts=max_attempts,
            model=model,
            approval_mode=approval_mode,
            checkpoint_mode=checkpoint_mode,
            resume_workspace=resume_path,
            trace_mode=trace_mode,
    ):
        event_type = event["type"]

        if event_type == "graph_event":
            _display_graph_event(event["event"])

        elif event_type == "custom_event":
            # 透传的自定义事件，暂不渲染
            pass

    console.print()


# ═══════════════════════════════════════════════════════════════════════════
# 各节点输出渲染
# ═══════════════════════════════════════════════════════════════════════════

def _display_graph_event(event: dict) -> None:
    """分发 graph_event 到正确的渲染函数."""
    for node_name, node_output in event.items():
        if node_name == "planner":
            _display_planner(node_output)
        elif node_name == "verifier":
            _display_verifier(node_output)
        elif node_name == "final":
            _display_final(node_output)
        # context_monitor / context_compressor 等节点不渲染


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
