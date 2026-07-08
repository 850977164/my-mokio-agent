"""mokioclaw 核心链路测试.

专注验证: 构建、编译、注入 — 不调用 LLM, 不依赖网络.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mokioclaw.core.state import RuntimeState
from mokioclaw.graph.memory import (
    build_layered_memory,
    format_layered_memory_for_prompt,
    memory_event,
)
from mokioclaw.graph.nodes import (
    _planner_input,
    _verifier_input,
    context_compressor_route,
    context_monitor_route,
    verifier_route,
)
from mokioclaw.graph.workflow import build_complex_workflow, build_workflow


# ═══════════════════════════════════════════════════════════════════
# 基础构建测试
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """创建临时工作区."""
    ws = tmp_path / "mokioclaw_test_ws"
    ws.mkdir()
    return ws


@pytest.fixture
def runtime(temp_workspace: Path) -> RuntimeState:
    """创建测试 RuntimeState (不调 API)."""
    return RuntimeState(workspace=temp_workspace, model="gpt-4o-mini")


@pytest.fixture
def state(runtime: RuntimeState) -> dict:
    """创建最小可用 state."""
    return {
        "task": "帮我搭建一个Flask后台管理系统",
        "runtime": runtime,
        "todos": [],
        "research_notes": "",
        "last_error": "",
        "acceptance_criteria": [],
        "code_agent_summary": "",
        "sources": [],
        "verification_commands": [],
        "attempts": 0,
        "max_attempts": 3,
        "context_next_node": "verifier",
    }


# ═══════════════════════════════════════════════════════════════════
# 工作流图编译
# ═══════════════════════════════════════════════════════════════════

def test_build_workflow_compiles(state: dict) -> None:
    """简单图编译成功且节点列表正确."""
    graph = build_workflow()
    assert graph is not None
    nodes = list(graph.nodes.keys())
    assert "planner" in nodes
    assert "verifier" in nodes
    assert "context_monitor" in nodes
    assert "final" in nodes
    assert "__start__" in nodes


def test_build_complex_workflow_compiles(state: dict) -> None:
    """复杂图编译成功且包含 context_compressor."""
    graph = build_complex_workflow()
    nodes = list(graph.nodes.keys())
    assert "planner" in nodes
    assert "verifier" in nodes
    assert "context_monitor" in nodes
    assert "context_compressor" in nodes
    assert "final" in nodes


# ═══════════════════════════════════════════════════════════════════
# 分层 Memory
# ═══════════════════════════════════════════════════════════════════

def test_build_layered_memory_has_three_layers(state: dict) -> None:
    """分层 memory 包含 rules / working_memory / history_summary_store."""
    memory = build_layered_memory(state, node="planner")
    assert "rules" in memory
    assert "working_memory" in memory
    assert "history_summary_store" in memory


def test_build_layered_memory_working_memory_has_task(state: dict) -> None:
    """Working Memory 包含任务描述."""
    memory = build_layered_memory(state, node="planner")
    wm = memory["working_memory"]
    assert "Flask后台管理" in wm["task"]
    assert wm["node"] == "planner"
    assert wm["attempts"] == 0
    assert wm["max_attempts"] == 3


def test_build_layered_memory_history_store_handles_missing_files(
    state: dict,
) -> None:
    """History Summary Store 在文件不存在时不抛异常."""
    memory = build_layered_memory(state, node="verifier")
    hs = memory["history_summary_store"]
    assert hs["history_exists"] is False
    assert hs["notepad_exists"] is False
    assert hs["history_summary"] == ""
    assert hs["notepad"] == ""


def test_format_layered_memory_for_prompt(state: dict) -> None:
    """序列化后的 memory 是有效 JSON 字符串."""
    memory = build_layered_memory(state, node="planner")
    text = format_layered_memory_for_prompt(memory)
    assert isinstance(text, str)
    assert len(text) > 100
    assert "rules" in text
    assert "working_memory" in text
    assert "history_summary_store" in text


def test_memory_event_has_expected_shape(state: dict) -> None:
    """memory_event 产出正确的事件结构."""
    memory = build_layered_memory(state, node="planner")
    event = memory_event(memory, node="planner")
    assert event["type"] == "memory_injection"
    assert event["node"] == "planner"
    assert event["memory"] is memory


# ═══════════════════════════════════════════════════════════════════
# Input builder 测试
# ═══════════════════════════════════════════════════════════════════

def test_planner_input_contains_memory_and_task(state: dict) -> None:
    """_planner_input 包含 memory 文本和任务."""
    memory = build_layered_memory(state, node="planner")
    text = _planner_input(state, memory)
    assert "分层记忆" in text
    assert "Flask后台管理" in text


def test_planner_input_first_time_has_call_to_action(state: dict) -> None:
    """首次调用时 _planner_input 包含 TodoWrite 指令."""
    memory = build_layered_memory(state, node="planner")
    text = _planner_input(state, memory)
    assert "TodoWrite" in text
    assert "CallSearchAgent" in text
    assert "CallCodeAgent" in text


def test_planner_input_retry_has_revision_instruction(state: dict) -> None:
    """修订调用时 _planner_input 包含当前计划和失败原因."""
    from mokioclaw.graph.state import TodoItem
    state["todos"] = [
        TodoItem(id="1", content="初始化项目", status="completed", note="ok"),
        TodoItem(id="2", content="创建模型", status="blocked", note="验证失败"),
    ]
    state["acceptance_criteria"] = ["服务可启动", "用户可注册"]
    state["last_error"] = "验证未通过: 缺少 app.py"
    memory = build_layered_memory(state, node="planner")
    text = _planner_input(state, memory)
    assert "修订计划" in text
    assert "上次验证失败" in text
    assert "缺少 app.py" in text
    assert "初始化项目" in text


def test_verifier_input_contains_memory_and_context(state: dict) -> None:
    """_verifier_input 包含 memory 和验收上下文."""
    memory = build_layered_memory(state, node="verifier")
    text = _verifier_input(state, memory, [])
    assert "分层记忆" in text
    assert "Flask后台管理" in text
    assert "验收标准" in text
    assert "ReportVerification" in text


# ═══════════════════════════════════════════════════════════════════
# 路由逻辑测试
# ═══════════════════════════════════════════════════════════════════

def test_verifier_route_passed_goes_final(state: dict) -> None:
    """验证通过 → final."""
    state["passed"] = True
    assert verifier_route(state) == "final"


def test_verifier_route_not_passed_under_max_goes_planner(state: dict) -> None:
    """验证未通过且未达上限 → planner."""
    state["passed"] = False
    state["attempts"] = 1
    state["max_attempts"] = 3
    assert verifier_route(state) == "planner"


def test_verifier_route_max_attempts_goes_final(state: dict) -> None:
    """验证未通过但已达上限 → final."""
    state["passed"] = False
    state["attempts"] = 3
    state["max_attempts"] = 3
    assert verifier_route(state) == "final"


def test_context_monitor_route_passed_goes_final(state: dict) -> None:
    """已通过验证 → final."""
    state["passed"] = True
    state["context_should_compress"] = True
    assert context_monitor_route(state) == "final"


def test_context_monitor_route_should_compress_goes_compressor(
    state: dict,
) -> None:
    """需要压缩 → context_compressor."""
    state["passed"] = False
    state["context_should_compress"] = True
    assert context_monitor_route(state) == "context_compressor"


def test_context_monitor_route_default_goes_context_next_node(
    state: dict,
) -> None:
    """默认 → context_next_node."""
    state["passed"] = False
    state["context_should_compress"] = False
    state["context_next_node"] = "verifier"
    assert context_monitor_route(state) == "verifier"


def test_context_compressor_route_passed_goes_final(state: dict) -> None:
    """压缩后已通过 → final."""
    state["passed"] = True
    assert context_compressor_route(state) == "final"


def test_context_compressor_route_default_goes_next_node(state: dict) -> None:
    """压缩后默认 → context_next_node."""
    state["passed"] = False
    state["context_next_node"] = "planner"
    assert context_compressor_route(state) == "planner"
