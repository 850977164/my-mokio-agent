"""Logo 模块测试 —— 渲染、动画、文本输出."""

from __future__ import annotations

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from mokioclaw.cli.tui.logo import (
    render_logo,
    render_welcome,
    logo_rich_text,
    logo_header,
    _MOKIOCLAW_AA,
    _MOKIOCLAW_AA_SMALL,
)


class TestLogoConstants:
    """测试常量."""

    def test_aa_art_non_empty(self):
        assert len(_MOKIOCLAW_AA.strip()) > 0
        assert "●" in _MOKIOCLAW_AA  # 眼睛

    def test_aa_small_non_empty(self):
        assert len(_MOKIOCLAW_AA_SMALL.strip()) > 0
        assert "🐾" in _MOKIOCLAW_AA_SMALL


class TestRenderLogo:
    """测试 render_logo()."""

    def test_returns_panel(self):
        logo = render_logo()
        assert isinstance(logo, Panel)

    def test_accepts_color_overrides(self):
        logo = render_logo(
            style_paw="#ffffff",
            style_title="#ff0000",
            style_subtitle="#00ff00",
            style_border="#0000ff",
        )
        assert isinstance(logo, Panel)

    def test_renderable_does_not_raise(self):
        """验证 logo 可以在 console 上渲染."""
        console = Console(width=80, force_terminal=True, color_system="truecolor")
        logo = render_logo()
        # 不应抛异常
        with console.capture() as capture:
            console.print(logo)
        output = capture.get()
        assert "MokioClaw" in output


class TestRenderWelcome:
    """测试 render_welcome()."""

    def test_includes_logo_and_info(self):
        result = render_welcome(workspace="/tmp/test", model="gpt-4o")
        assert result is not None

    def test_renders_to_console(self):
        console = Console(width=80, force_terminal=True, color_system="truecolor")
        welcome = render_welcome(workspace="/home/user/project", model="claude-opus")
        with console.capture() as capture:
            console.print(welcome)
        output = capture.get()
        # 应包含 workspace 和 model
        assert "/home/user/project" in output
        assert "claude-opus" in output

    def test_handles_missing_info(self):
        result = render_welcome()  # 无 workspace/model
        assert result is not None
        console = Console(width=80, force_terminal=True, color_system="truecolor")
        with console.capture() as capture:
            console.print(result)
        # 不应崩溃
        assert capture.get()


class TestLogoRichText:
    """测试 logo_rich_text()."""

    def test_returns_text(self):
        text = logo_rich_text()
        assert isinstance(text, Text)

    def test_contains_brand(self):
        text = logo_rich_text()
        plain = text.plain
        assert "MokioClaw" in plain
        assert "Stage 6" in plain

    def test_has_multiple_styles(self):
        text = logo_rich_text()
        # Text 对象至少要有 2 种不同样式 (title + subtitle)
        spans = text.spans
        assert len(spans) >= 2


class TestLogoHeader:
    """测试 logo_header()."""

    def test_returns_string(self):
        header = logo_header()
        assert isinstance(header, str)

    def test_contains_key_info(self):
        header = logo_header()
        assert "MokioClaw" in header
        assert "Stage 6" in header
