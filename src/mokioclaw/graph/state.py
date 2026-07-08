"""LangGraph 图状态定义 —— MultiAgent 架构的共享状态.

核心设计:
    - messages 字段使用 Annotated[list, add_messages] 注解，
      让 LangGraph 自动追加消息而非覆盖，保持完整对话历史。
    - TodoItem 记录每个计划步骤的进度。
    - VerificationResult 记录计划完成后的自动验证结果。
    - SourceItem 记录搜索来源。
    - AgentHandoff 记录 Agent 间委托。
    - CompressionEvent 记录上下文压缩事件。
    - LayeredMemory 三层记忆快照（Rules + Working + History Summary）。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from mokioclaw.core.state import RuntimeState


class TodoItem(TypedDict):
    """计划中的一个待办项。"""

    id: str
    content: str
    status: str  # "pending" | "in_progress" | "completed" | "blocked"
    note: str


class VerificationResult(TypedDict):
    """一条验证命令的执行结果。"""

    command: str
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str


class SourceItem(TypedDict, total=False):
    """一条搜索来源。"""

    title: str
    url: str
    content: str
    score: float


class AgentHandoff(TypedDict, total=False):
    """一次 Agent 间委托记录。"""

    from_agent: str
    to_agent: str
    instruction: str
    result: str


class CompressionEvent(TypedDict, total=False):
    """一次上下文压缩事件。

    记录何时因何原因压缩了上下文，以及压缩前后的 token 统计。
    """

    timestamp: str
    """压缩发生时间（ISO 8601 字符串）."""

    trigger: str
    """触发原因: "token_limit" | "manual" | "node_transition"."""

    node: str
    """触发压缩时所在的节点名称."""

    token_count_before: int
    """压缩前上下文 token 数."""

    token_count_after: int
    """压缩后上下文 token 数."""

    summary: str
    """压缩产出的摘要文本."""


class LayeredMemory(TypedDict, total=False):
    """三层记忆快照。

    由 build_layered_memory() 在节点入口处构建，
    作为 system prompt 的附加上下文注入给 LLM。
    """

    rules: dict
    """Rules Layer — 固定规则，从 RULES_LAYER 常量复制."""

    working_memory: dict
    """Working Memory — 当前任务状态（todos / plan / research_notes 等）."""

    history_summary_store: dict
    """History Summary Store — 持久化的压缩历史（NOTEPAD.md / HISTORY_SUMMARY.md）."""


class MokioGraphState(TypedDict, total=False):
    """MultiAgent 图的共享状态。"""

    # ── 输入 ──
    task: str
    runtime: RuntimeState

    # ── 对话历史 ──
    messages: Annotated[list[BaseMessage], add_messages]

    # ── Plan 阶段产出 ──
    plan_summary: str
    todos: list[TodoItem]
    acceptance_criteria: list[str]
    verification_commands: list[str]

    # ── 搜索研究产出 ──
    research_notes: str
    sources: list[SourceItem]

    # ── Agent 委托记录 ──
    agent_handoffs: list[AgentHandoff]

    # ── Code Agent 产出 ──
    code_agent_summary: str

    # ── Verifier 产出 ──
    verifier_summary: str

    # ── Verify 阶段产出 ──
    verification_results: list[VerificationResult]
    passed: bool

    # ── 循环控制 ──
    attempts: int
    max_attempts: int

    # ── Verify 阶段详细结果 ──
    verification_checks: list[dict]
    last_error: str

    # ── 最终产出 ──
    final_answer: str

    # ── 上下文管理 ──
    context_summary: str
    """最近一次压缩产出的上下文摘要."""

    context_token_count: int
    """当前上下文 token 数估算."""

    context_token_limit: int
    """上下文 token 上限."""

    context_should_compress: bool
    """是否需要压缩上下文."""

    context_next_node: str
    """压缩后应跳转到的下一个节点."""

    compression_events: list[CompressionEvent]
    """上下文压缩事件列表，保留最近 N 条."""

    memory_snapshot: dict
    """最近一次构建的 LayeredMemory 快照，供调试/日志使用."""

    history_summary: str
    """HISTORY_SUMMARY.md 中的压缩历史摘要文本."""

    # ── 压缩产出字段（由 context_compressor_node 填充）──
    active_goal: str
    """当前活跃目标，从压缩摘要中提取."""

    completed_work: str
    """已完成的工作，从压缩摘要中提取."""

    open_todos: str
    """仍未关闭的 todos，从压缩摘要中提取."""

    important_files: str
    """当前工作区中重要文件/产物的列表，从压缩摘要中提取."""

    tool_findings: str
    """关键工具调用发现，从压缩摘要中提取."""

    next_steps: str
    """推荐的下一步行动，从压缩摘要中提取."""

    risks: str
    """已知风险与阻塞项，从压缩摘要中提取."""
