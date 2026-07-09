"""意图路由测试 —— 入口图编译 + 路由逻辑."""

from __future__ import annotations

from pathlib import Path

import pytest

from mokioclaw.core.state import RuntimeState
from mokioclaw.graph.workflow import build_entry_workflow
from mokioclaw.graph.nodes import intent_route_fn
from mokioclaw.prompts.stage3 import INTENT_ROUTER_PROMPT, CHAT_RESPONDER_PROMPT


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """创建临时 workspace."""
    w = tmp_path / "test_workspace"
    w.mkdir()
    import subprocess
    subprocess.run(
        ["git", "init"], cwd=str(w),
        capture_output=True, text=True, timeout=10,
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
    """创建 RuntimeState."""
    return RuntimeState(workspace=ws, model="gpt-4o-mini")


@pytest.fixture
def base_state(runtime: RuntimeState) -> dict:
    """最小可用 state（不含路由字段）."""
    return {
        "task": "你好",
        "runtime": runtime,
        "max_attempts": 3,
    }


# ═══════════════════════════════════════════════════════════════════
# 入口图编译
# ═══════════════════════════════════════════════════════════════════

def test_build_entry_workflow_compiles() -> None:
    """入口图编译成功，节点列表正确."""
    graph = build_entry_workflow()
    assert graph is not None
    nodes = set(graph.nodes.keys())
    assert "intent_router" in nodes
    assert "chat_responder" in nodes
    assert "__start__" in nodes


# ═══════════════════════════════════════════════════════════════════
# intent_route_fn 路由逻辑
# ═══════════════════════════════════════════════════════════════════

def test_route_chat_goes_chat_responder(base_state: dict) -> None:
    """intent_route='chat' → chat_responder."""
    base_state["intent_route"] = "chat"
    assert intent_route_fn(base_state) == "chat_responder"


def test_route_workflow_goes_planner(base_state: dict) -> None:
    """intent_route='workflow' → planner."""
    base_state["intent_route"] = "workflow"
    assert intent_route_fn(base_state) == "planner"


def test_route_missing_key_defaults_planner(base_state: dict) -> None:
    """无 intent_route 字段时返回 planner（默认工作流）."""
    # base_state 不含 intent_route
    assert intent_route_fn(base_state) == "planner"


def test_route_unexpected_value_goes_planner(base_state: dict) -> None:
    """非法值也走 planner."""
    base_state["intent_route"] = "unknown"
    assert intent_route_fn(base_state) == "planner"


# ═══════════════════════════════════════════════════════════════════
# Prompt 模板
# ═══════════════════════════════════════════════════════════════════

def test_intent_router_prompt_contains_route_keywords() -> None:
    """INTENT_ROUTER_PROMPT 包含 chat/workflow 路由说明."""
    assert "chat" in INTENT_ROUTER_PROMPT
    assert "workflow" in INTENT_ROUTER_PROMPT
    assert "intent router" in INTENT_ROUTER_PROMPT.lower()
    assert '"route"' in INTENT_ROUTER_PROMPT
    assert '"confidence"' in INTENT_ROUTER_PROMPT


def test_chat_responder_prompt_forbids_workspace_facts() -> None:
    """CHAT_RESPONDER_PROMPT 禁止编造 workspace 事实."""
    assert "do not invent workspace facts" in CHAT_RESPONDER_PROMPT.lower()
    assert "lightweight chat" in CHAT_RESPONDER_PROMPT.lower()


# ═══════════════════════════════════════════════════════════════════
# state 字段存在性
# ═══════════════════════════════════════════════════════════════════

def test_mokio_graph_state_has_intent_fields() -> None:
    """MokioGraphState TypedDict 包含意图路由字段."""
    from mokioclaw.graph.state import MokioGraphState
    annotations = MokioGraphState.__annotations__
    assert "last_user_input" in annotations
    assert "session_context" in annotations
    assert "intent_route" in annotations
    assert "intent_reason" in annotations
    assert "intent_confidence" in annotations
    assert "chat_response" in annotations
