"""MokioClaw CLI —— Typer 应用入口.

用法:
    mokioclaw "帮我重构这个模块" --workspace /path/to/project
    mokioclaw "检查代码质量" --workspace ./my-project --model gpt-4o
"""

from __future__ import annotations

import os

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

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
    max_loops: int = typer.Option(
        10,
        "--max-loops",
        "-l",
        help="最大推理轮数，默认 10",
    ),
) -> None:
    """启动 MokioClaw 代理执行任务。

    示例:
        mokioclaw "阅读 README.md 并总结"
        mokioclaw "找出所有 TODO 注释" --workspace ./src
    """
    # 解析模型名：CLI 参数 > 环境变量 MODEL > 默认 gpt-4o
    if model is None:
        model = os.getenv("MODEL", "gpt-4o")
    ws_path = resolve_workspace(workspace)
    ws_path = ensure_workspace(ws_path)

    console.print(Panel.fit(
        f"[bold cyan]🚀 MokioClaw v0.1.0[/]\n"
        f"📂 workspace: {ws_path}\n"
        f"🤖 model:     {model}\n"
        f"📋 task:      {task}",
        title="MokioClaw",
        border_style="cyan",
    ))

    # 遍历 ReAct 事件流
    tool_call_count = 0
    for event in stream_agent_events(
        task,
        workspace=ws_path,
        max_loops=max_loops,
        model=model,
    ):
        event_type = event["type"]

        if event_type == "ai_message":
            content = event["content"]
            if content:
                console.print()
                console.print(Markdown(content))

        elif event_type == "tool_call":
            name: str = event["name"]
            args: dict = event["args"]
            tool_call_count += 1
            # 截断过长的参数显示
            args_repr = _format_args(args)
            console.print(
                f"  [yellow]🔧 #{tool_call_count} {name}[/]",
                f"  [dim]{args_repr}[/]",
                sep="\n",
            )

        elif event_type == "tool_result":
            name: str = event["name"]
            result: str = event["result"]
            # 截断过长的结果
            preview = _truncate(result, max_lines=5)
            console.print(f"  [green]✅ {name}[/]")
            if preview:
                console.print(f"  [dim]{preview}[/]")

        elif event_type == "final_answer":
            # final_answer 与 ai_message 可能重复，检查是否需要再显示
            pass

    console.print()


def _truncate(text: str, max_lines: int = 5) -> str:
    """截断过长文本，只保留前 max_lines 行。"""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n  ... (共 {len(lines)} 行)"


def _format_args(args: dict, max_len: int = 120) -> str:
    """格式化工具参数为紧凑字符串，过长则截断。"""
    raw = ", ".join(f"{k}={v!r}" for k, v in args.items())
    if len(raw) <= max_len:
        return raw
    # 截断到 max_len，保证不断开 UTF-8 字符
    return raw[:max_len - 3] + "..."


if __name__ == "__main__":
    app()
