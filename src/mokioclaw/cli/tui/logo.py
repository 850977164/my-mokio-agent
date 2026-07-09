"""MokioClaw ASCII Art Logo —— Rich 渲染 + 启动动画.

启动时渲染带颜色的标志，支持静态展示和 Live 动画。

用法::

    from mokioclaw.cli.tui.logo import render_logo, render_welcome, animate_logo

    # 静态 Panel
    console.print(render_logo())

    # 带 workspace/model 信息
    console.print(render_welcome(workspace="/path/to/project", model="gpt-4o"))

    # 启动动画（逐行显示）
    animate_logo(console)
"""

from __future__ import annotations

import time
from pathlib import Path

from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.style import Style
from rich.text import Text


# ═══════════════════════════════════════════════════════════════════
# ASCII Art
# ═══════════════════════════════════════════════════════════════════

_MOKIOCLAW_AA = r"""
                          ▄▄▄▄▄▄▄▄▄▄▄▄▄
                     ▄▄█████████████████████▄▄
                 ▄▄███▀                    ▀███▄▄
              ▄▄██▀    ●              ●      ▀██▄▄
            ▄██▀          ▄▄▄▄▄▄▄▄▄            ▀██▄
          ▄██          ▄█████████████▄            ██▄
         ██▀         ▄███▀▀     ▀▀███▄           ▀██
        ██▀         ▄██▀    ◡    ▀██▄    ▄▄▄▄    ▀██
        ██         ▐██▀  ▄███▄  ▀██▌   █████▌    ██
       ▐█▌        ▐██▌  ▐████▌  ▐██▌   ██▐██    ▐█▌
       ▐█▌         ██▌  ██ ██  ▐██    ██ ██    ▐█▌
       ▐█▌          ▀██▄     ▄██▀     ██▐██    ▐█▌
        ██            ▀█████████▀      █████    ██
        ██▄               ▀▀▀           ▀▀    ▄██
         ███▄                                ▄███
          ▀███▄                            ▄███▀
            ▀████▄▄                    ▄▄████▀
               ▀▀█████▄▄          ▄▄█████▀▀
                    ▀▀██████████████▀▀
"""  # noqa: W605

_MOKIOCLAW_AA_SMALL = r"""
     ▄█████▄
    ███▀ ▀███      🐾  MokioClaw
    ██▌ ● ● ██
    ██▌  ◡  ██     ═══════════════════════════════
    ███▄   ▄███     Stage 6 · MultiAgent + Context/Harness
     ▀███████▀      ═══════════════════════════════
       ▀███▀
        ▐█▌
"""


# ═══════════════════════════════════════════════════════════════════
# 颜色调色板
# ═══════════════════════════════════════════════════════════════════

# 主色调：暖金色 + 深青色
_COLOR_ACCENT = "#e5b850"       # 暖金 — 标题高亮
_COLOR_SUBTITLE = "#7eb8c9"     # 青灰 — 副标题
_COLOR_PAW = "#d4a574"          # 棕 — ASCII 艺术
_COLOR_BORDER = "#5a7d8c"       # 暗青 — 面板边框
_COLOR_INFO = "#8899a6"         # 灰蓝 — 信息文字
_COLOR_EYE = "#f0c040"          # 亮金 — 眼睛/高亮点


# ═══════════════════════════════════════════════════════════════════
# 渲染函数
# ═══════════════════════════════════════════════════════════════════

def render_logo(
    *,
    style_paw: str = _COLOR_PAW,
    style_title: str = _COLOR_ACCENT,
    style_subtitle: str = _COLOR_SUBTITLE,
    style_border: str = _COLOR_BORDER,
) -> Panel:
    """返回静态 Logo Panel。

    Args:
        style_paw: ASCII 艺术的颜色。
        style_title: 标题颜色。
        style_subtitle: 副标题颜色。
        style_border: 面板边框颜色。

    Returns:
        包含完整 Logo 的 rich Panel。
    """
    # ── ASCII 画 ──
    aa_lines: list[Text] = []
    for line in _MOKIOCLAW_AA.strip("\n").split("\n"):
        aa_lines.append(Text(line, style=Style(color=style_paw)))

    # ── 标题 ──
    title = Text("🐾  MokioClaw", style=Style(color=style_title, bold=True))

    # ── 分隔线 ──
    sep = Text("━" * 46, style=Style(color=style_subtitle, dim=True))

    # ── 副标题 ──
    subtitle = Text(
        "Stage 6 · MultiAgent + Context/Harness",
        style=Style(color=style_subtitle),
    )

    content = Group(
        Text(""),  # 上间距
        *aa_lines,
        Text(""),
        Align.center(title),
        Text(""),
        Align.center(sep),
        Align.center(subtitle),
        Align.center(sep),
        Text(""),
    )

    return Panel(
        content,
        border_style=Style(color=style_border),
        padding=(1, 2),
    )


def render_welcome(
    *,
    workspace: str | Path | None = None,
    model: str | None = None,
    version: str = "0.1.0",
) -> Group:
    """返回欢迎信息 Group：Logo + 工作区/模型信息。

    用于 CLI 启动画面或 TUI 欢迎日志。

    Args:
        workspace: 工作区路径。
        model: 模型名称。
        version: 版本号。

    Returns:
        rich Group，可直接 console.print()。
    """
    parts: list[RenderableType] = [render_logo()]

    # ── 运行信息 ──
    info_lines: list[Text] = []
    if workspace:
        info_lines.append(
            Text(f"📂 workspace:  {workspace}", style=Style(color=_COLOR_INFO))
        )
    if model:
        info_lines.append(
            Text(f"🤖 model:      {model}", style=Style(color=_COLOR_INFO))
        )
    info_lines.append(
        Text(f"📦 version:    {version}", style=Style(color=_COLOR_INFO, dim=True))
    )
    info_lines.append(Text(""))

    if info_lines:
        parts.append(Group(*info_lines))

    return Group(*parts)


# ═══════════════════════════════════════════════════════════════════
# 动画
# ═══════════════════════════════════════════════════════════════════

def animate_logo(
    console: Console,
    *,
    workspace: str | Path | None = None,
    model: str | None = None,
    version: str = "0.1.0",
    frame_delay: float = 0.008,
    paw_color: str = _COLOR_PAW,
    title_color: str = _COLOR_ACCENT,
) -> None:
    """启动动画 —— 逐行渲染 Logo，模拟"画出"效果。

    使用 rich Live 上下文，每行 ASCII art 依次出现，
    给用户一种"正在启动"的反馈感。

    Args:
        console: rich Console 实例。
        workspace: 工作区路径。
        model: 模型名称。
        version: 版本号。
        frame_delay: 每行之间的延迟秒数（默认 8ms，较快）。
        paw_color: ASCII 艺术的颜色。
        title_color: 标题颜色。
    """
    aa_lines = _MOKIOCLAW_AA.strip("\n").split("\n")
    total_lines = len(aa_lines)

    # 构建空行模板（与最终 logo 行数一致）
    empty_lines = [Text("", style=Style(color=paw_color)) for _ in range(total_lines)]

    # 标题行（延迟显示）
    title = Text("🐾  MokioClaw", style=Style(color=title_color, bold=True))
    sep = Text("━" * 46, style=Style(color=_COLOR_SUBTITLE, dim=True))
    subtitle = Text(
        "Stage 6 · MultiAgent + Context/Harness",
        style=Style(color=_COLOR_SUBTITLE),
    )

    def _build_frame(visible_rows: int, show_title: bool = False) -> Group:
        """构建第 N 帧：前 visible_rows 行已显示，其余为空白."""
        lines: list[Text] = []
        for i in range(total_lines):
            if i < visible_rows:
                lines.append(Text(aa_lines[i], style=Style(color=paw_color)))
            else:
                lines.append(Text("", style=Style(color=paw_color)))

        content: list[RenderableType] = [Text(""), *lines, Text("")]
        if show_title:
            content.extend([
                Align.center(title),
                Text(""),
                Align.center(sep),
                Align.center(subtitle),
                Align.center(sep),
            ])
        return Group(*content)

    # ── Phase 1: 逐行画出 ASCII art ──
    for visible in range(1, total_lines + 1):
        frame = Panel(
            _build_frame(visible, show_title=False),
            border_style=Style(color=_COLOR_BORDER),
            padding=(1, 2),
        )
        console.print(frame, overflow="ignore", crop=False)
        console.clear_live()
        time.sleep(frame_delay)

    # ── Phase 2: 短暂停顿后显示完整 logo ──
    time.sleep(0.05)
    console.clear_live()

    final_frame = Panel(
        _build_frame(total_lines, show_title=True),
        border_style=Style(color=_COLOR_BORDER),
        padding=(1, 2),
    )
    console.print(final_frame, overflow="ignore", crop=False)

    # ── 运行信息 ──
    info: list[Text] = []
    if workspace:
        info.append(
            Text(f"📂 workspace:  {workspace}", style=Style(color=_COLOR_INFO))
        )
    if model:
        info.append(
            Text(f"🤖 model:      {model}", style=Style(color=_COLOR_INFO))
        )
    info.append(
        Text(f"📦 version:    {version}", style=Style(color=_COLOR_INFO, dim=True))
    )
    info.append(Text(""))

    console.print(Group(*info))


def animate_logo_live(
    console: Console,
    *,
    workspace: str | Path | None = None,
    model: str | None = None,
    version: str = "0.1.0",
    frame_delay: float = 0.015,
) -> None:
    """使用 rich Live 的流式动画 —— 更适合 TUI 启动。

    将所有帧构建好后通过 Live.update() 切换，
    不会产生清屏闪烁。

    Args:
        console: rich Console 实例。
        workspace: 工作区路径。
        model: 模型名称。
        version: 版本号。
        frame_delay: 每帧延迟秒数。
    """
    aa_lines = _MOKIOCLAW_AA.strip("\n").split("\n")
    total_lines = len(aa_lines)

    paw_style = Style(color=_COLOR_PAW)
    title_style = Style(color=_COLOR_ACCENT, bold=True)
    sub_style = Style(color=_COLOR_SUBTITLE)
    sub_dim_style = Style(color=_COLOR_SUBTITLE, dim=True)
    border_style = Style(color=_COLOR_BORDER)

    def _make_frame(visible: int, *, with_title: bool = False) -> Panel:
        content: list[RenderableType] = [Text("")]
        for i in range(total_lines):
            content.append(
                Text(aa_lines[i], style=paw_style)
                if i < visible
                else Text("")
            )
        content.append(Text(""))
        if with_title:
            content.extend([
                Align.center(Text("🐾  MokioClaw", style=title_style)),
                Text(""),
                Align.center(Text("━" * 46, style=sub_dim_style)),
                Align.center(
                    Text("Stage 6 · MultiAgent + Context/Harness", style=sub_style)
                ),
                Align.center(Text("━" * 46, style=sub_dim_style)),
            ])
        return Panel(Group(*content), border_style=border_style, padding=(1, 2))

    with Live(_make_frame(0), console=console, refresh_per_second=60) as live:
        # Phase 1: 逐行画出
        for visible in range(1, total_lines + 1):
            live.update(_make_frame(visible))
            time.sleep(frame_delay)

        # Phase 2: 停顿后显示标题
        time.sleep(0.1)
        live.update(_make_frame(total_lines, with_title=True))
        time.sleep(0.15)

    # 运行信息
    info_lines: list[Text] = []
    if workspace:
        info_lines.append(
            Text(f"📂 workspace:  {workspace}", style=Style(color=_COLOR_INFO))
        )
    if model:
        info_lines.append(
            Text(f"🤖 model:      {model}", style=Style(color=_COLOR_INFO))
        )
    info_lines.append(
        Text(f"📦 version:    {version}", style=Style(color=_COLOR_INFO, dim=True))
    )
    info_lines.append(Text(""))
    if info_lines:
        console.print(Group(*info_lines))


# ═══════════════════════════════════════════════════════════════════
# 快捷文本（供 TUI RichLog 使用）
# ═══════════════════════════════════════════════════════════════════

def logo_rich_text() -> Text:
    """返回单行 Rich Text，适合嵌入 TUI 欢迎日志。

    不包含完整的 ASCII 画，只返回标题 + 副标题行。
    """
    text = Text()
    text.append("🐾 ", style=Style(color=_COLOR_ACCENT))
    text.append("MokioClaw", style=Style(color=_COLOR_ACCENT, bold=True))
    text.append("  ·  ", style=Style(color=_COLOR_BORDER, dim=True))
    text.append(
        "Stage 6 · MultiAgent + Context/Harness",
        style=Style(color=_COLOR_SUBTITLE),
    )
    return text


def logo_header() -> str:
    """返回纯文本 header，供 TUI 场景使用（Textual 不支持 Rich 对象）。

    适用于 StatusBar 或其他 Textual widget。
    """
    return "🐾 MokioClaw · Stage 6 · MultiAgent + Context/Harness"
