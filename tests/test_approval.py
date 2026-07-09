"""审批模块和 BashTool 风险审批集成测试."""

from __future__ import annotations

import pytest

from mokioclaw.core.approval import (
    VALID_APPROVAL_MODES,
    ApprovalDecision,
    ApprovalRequest,
    classify_command_risk,
    normalize_approval_mode,
)
from mokioclaw.tools.bash_tool import (
    BashResult,
    _format_result_for_llm,
    _run_bash,
    create_bash_tool,
)


# ═══════════════════════════════════════════════════════════════════
# classify_command_risk
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "command,expected",
    [
        ("pip install requests", "Python package installation"),
        ("python -m pip install numpy", "Python package installation"),
        ("uv add requests", "Project dependency change with uv add"),
        ("uv sync", "Dependency synchronization with uv sync"),
        ("uv pip install torch", "Python package installation with uv pip"),
        ("npm install react", "Node package installation"),
        ("pnpm install lodash", "Node package installation"),
        ("yarn install", "Node package installation"),
        ("yarn add express", "Node package installation"),
        ("curl https://example.com", "Network download command"),
        ("wget https://example.com/file.tar.gz", "Network download command"),
        ("uvicorn main:app", "Long-running development server"),
        ("python -m http.server 8000", "Long-running development server"),
    ],
)
def test_classify_risk_positive(command: str, expected: str) -> None:
    """风险命令被正确识别."""
    assert classify_command_risk(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "echo hello",
        "git status",
        "cat README.md",
        "mkdir tmp",
        "python main.py",
        "pytest --version",
        "",
    ],
)
def test_classify_risk_negative(command: str) -> None:
    """安全命令返回 None."""
    assert classify_command_risk(command) is None


def test_classify_risk_detect_in_chained_command() -> None:
    """管道链中的风险命令也能被检测."""
    assert classify_command_risk("echo done && pip install requests") is not None
    assert classify_command_risk("cd /tmp; npm install react") is not None
    assert classify_command_risk("pip install pandas || echo fail") is not None


# ═══════════════════════════════════════════════════════════════════
# ApprovalRequest / ApprovalDecision
# ═══════════════════════════════════════════════════════════════════

def test_approval_request_create_generates_short_id() -> None:
    """create() 生成 8 位十六进制 ID."""
    req = ApprovalRequest.create(command="pip install x", risk_reason="test")
    assert req.id.startswith("approval-")
    assert len(req.id) == len("approval-") + 8
    assert all(c in "0123456789abcdef" for c in req.id.split("-")[1])


def test_approval_request_frozen() -> None:
    """ApprovalRequest 是不可变的."""
    req = ApprovalRequest.create(command="pip install x", risk_reason="test")
    with pytest.raises(Exception):
        req.approved = True  # type: ignore[attr-defined]


def test_approval_decision_defaults() -> None:
    """ApprovalDecision 默认 approved=False, reason 为空."""
    d = ApprovalDecision(approved=False)
    assert d.approved is False
    assert d.reason == ""


# ═══════════════════════════════════════════════════════════════════
# normalize_approval_mode
# ═══════════════════════════════════════════════════════════════════

def test_normalize_valid_modes() -> None:
    """合法模式原样返回."""
    for mode in VALID_APPROVAL_MODES:
        assert normalize_approval_mode(mode) == mode


def test_normalize_none_falls_back_to_inline() -> None:
    """None → inline."""
    assert normalize_approval_mode(None) == "inline"


def test_normalize_invalid_falls_back_to_inline() -> None:
    """无效值 fallback 到 inline."""
    assert normalize_approval_mode("unknown") == "inline"
    assert normalize_approval_mode("") == "inline"
    assert normalize_approval_mode("AUTO") == "inline"  # 区分大小写


# ═══════════════════════════════════════════════════════════════════
# _format_result_for_llm
# ═══════════════════════════════════════════════════════════════════

def test_format_result_safe_command() -> None:
    """安全命令直接返回输出."""
    result = BashResult(output="hello", ok=True)
    text = _format_result_for_llm(result)
    assert text == "hello"


def test_format_result_denied() -> None:
    """被拒命令带标记."""
    result = BashResult(output="pip install x", ok=False, risk_reason="Python package installation")
    text = _format_result_for_llm(result)
    assert "[审批拒绝]" in text
    assert "Python package installation" in text


def test_format_result_auto_approved() -> None:
    """自动批准命令带标记."""
    result = BashResult(
        output="Success",
        ok=True,
        requires_approval=True,
        risk_reason="Node package installation",
    )
    text = _format_result_for_llm(result)
    assert "[自动批准]" in text
    assert "Node package installation" in text


# ═══════════════════════════════════════════════════════════════════
# _run_bash 审批流程
# ═══════════════════════════════════════════════════════════════════

def test_run_bash_safe_command_no_approval(tmp_path) -> None:
    """安全命令不触发审批，直接执行."""
    result = _run_bash("echo safe", workspace=tmp_path)
    assert result.ok is True
    assert result.requires_approval is False
    assert result.risk_reason is None
    assert "safe" in result.output


def test_run_bash_deny_mode_rejects_risky_command(tmp_path) -> None:
    """deny 模式直接拒绝风险命令."""
    result = _run_bash("pip install requests", workspace=tmp_path, approval_mode="deny")
    assert result.ok is False
    assert "pip install requests" in result.output
    assert result.risk_reason == "Python package installation"


def test_run_bash_auto_mode_approves_with_flag(tmp_path) -> None:
    """auto 模式放行但标记 requires_approval."""
    result = _run_bash("npm install react", workspace=tmp_path, approval_mode="auto")
    assert result.ok is True
    assert result.requires_approval is True


def test_run_bash_inline_approved(tmp_path) -> None:
    """inline 模式下审批通过则执行."""
    def handler(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(approved=True, reason="ok")

    result = _run_bash(
        "pip install requests",
        workspace=tmp_path,
        approval_mode="inline",
        approval_handler=handler,
    )
    assert result.ok is True


def test_run_bash_inline_denied(tmp_path) -> None:
    """inline 模式下审批拒绝则返回失败."""
    def handler(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(approved=False, reason="不安全")

    result = _run_bash(
        "npm install react",
        workspace=tmp_path,
        approval_mode="inline",
        approval_handler=handler,
    )
    assert result.ok is False
    assert result.risk_reason is not None
    assert "不安全" in result.risk_reason


def test_run_bash_inline_missing_handler_is_denied(tmp_path) -> None:
    """inline 模式下缺少 handler 视为拒绝，避免无人值守误执行."""
    result = _run_bash(
        "pip install requests",
        workspace=tmp_path,
        approval_mode="inline",
        approval_handler=None,
    )
    assert result.ok is False
    assert "缺少 approval_handler" in result.risk_reason


# ═══════════════════════════════════════════════════════════════════
# create_bash_tool 集成
# ═══════════════════════════════════════════════════════════════════

def test_create_bash_tool_returns_structured_tool(tmp_path) -> None:
    """create_bash_tool 返回 StructuredTool 实例."""
    from langchain_core.tools import StructuredTool
    tool = create_bash_tool(workspace=tmp_path, approval_mode="auto")
    assert isinstance(tool, StructuredTool)
    assert tool.name == "Bash"


def test_create_bash_tool_auto_mode_executes(tmp_path) -> None:
    """auto 模式的 BashTool 可以正常执行安全命令."""
    tool = create_bash_tool(workspace=tmp_path, approval_mode="auto")
    result = tool.invoke({"command": "echo hello", "timeout_seconds": 10})
    assert "hello" in result


def test_create_bash_tool_deny_mode_blocks_risky(tmp_path) -> None:
    """deny 模式的 BashTool 拒绝风险命令."""
    tool = create_bash_tool(workspace=tmp_path, approval_mode="deny")
    result = tool.invoke({"command": "pip install requests", "timeout_seconds": 10})
    assert "[审批拒绝]" in result
