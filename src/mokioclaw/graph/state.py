"""LangGraph 图状态定义 —— MultiAgent 架构的共享状态.

核心设计:
    - messages 字段使用 Annotated[list, add_messages] 注解，
      让 LangGraph 自动追加消息而非覆盖，保持完整对话历史。
    - TodoItem 记录每个计划步骤的进度。
    - VerificationResult 记录计划完成后的自动验证结果。
    - SourceItem 记录搜索来源。
    - AgentHandoff 记录 Agent 间委托。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from mokioclaw.core.state import RuntimeState


class TodoItem(TypedDict):
    """计划中的一个待办项。

    由 Planner 生成，由 codeAgent 逐个执行，
    进度通过 status 追踪。
    """

    id: str
    """唯一标识，如 "todo-1"、"todo-2"."""

    content: str
    """待办内容描述，用自然语言说明要做什么."""

    status: str
    """当前状态: "pending" | "in_progress" | "completed" | "blocked"."""

    note: str
    """执行笔记，codeAgent 完成后填入（成功/失败原因等）."""


class VerificationResult(TypedDict):
    """一条验证命令的执行结果。

    在 codeAgent 完成所有 todo 后，
    Verifier 节点逐个执行 acceptance_criteria 中列出的验证命令，
    每个命令产出一条 VerificationResult。
    """

    command: str
    """验证命令字符串（如 "python test.py"）."""

    ok: bool
    """命令是否通过验证（exit_code == 0 且输出符合预期）."""

    exit_code: int | None
    """命令退出码，0 表示成功。超时或被中断时为 None."""

    stdout: str
    """命令标准输出."""

    stderr: str
    """命令标准错误."""


class SourceItem(TypedDict, total=False):
    """一条搜索来源。

    由 searchAgent 返回，记录在 state.sources 中供后续引用。
    """

    title: str
    """来源标题."""

    url: str
    """来源 URL."""

    content: str
    """来源内容摘要."""

    score: float
    """相关性评分."""


class AgentHandoff(TypedDict, total=False):
    """一次 Agent 间委托记录。

    Planner 通过 CallSearchAgentTool / CallCodeAgentTool
    委托任务给子 Agent 时生成一条记录。
    """

    from_agent: str
    """委托方，通常为 "planner"."""

    to_agent: str
    """被委托方，"searchAgent" 或 "codeAgent"."""

    instruction: str
    """委托指令."""

    result: str
    """子 Agent 返回的结果摘要."""


class MokioGraphState(TypedDict, total=False):
    """MultiAgent 图的共享状态。

    所有节点（Planner / Verifier / Final）共享同一个 state dict，
    通过读写各自关心的字段完成协调。

    关键注解:
        - total=False: 所有字段都是可选的（TypedDict 的 partial 模式），
          因为不同节点按需填充字段，并非每个节点都填满全部字段。
        - messages: Annotated[list[BaseMessage], add_messages]:
          add_messages 是 LangGraph 内置的 reducer，
          当节点返回 {"messages": new_msgs} 时，
          不会覆盖原有消息，而是追加到列表末尾。
    """

    # ── 输入 ──
    task: str
    """用户的原始任务描述."""

    runtime: RuntimeState
    """运行时状态（workspace、model 等配置），所有工具节点共享."""

    # ── 对话历史 ──
    messages: Annotated[list[BaseMessage], add_messages]
    """完整的 LLM 对话历史。

    使用 add_messages reducer，节点写入时自动追加而非覆盖。
    每个节点（Planner / Verifier）都将自己的
    SystemMessage / HumanMessage / AIMessage / ToolMessage 追加到此列表。
    """

    # ── Plan 阶段产出 ──
    plan_summary: str
    """Planner 产出的计划摘要，用自然语言描述整体执行策略."""

    todos: list[TodoItem]
    """Planner 拆解的待办项列表，按执行顺序排列."""

    acceptance_criteria: list[str]
    """验收标准列表，每条描述一个可通过验证命令检查的条件."""

    verification_commands: list[str]
    """由 Planner 生成的验证命令列表（如 pytest、lint）."""

    # ── 搜索研究产出 ──
    research_notes: str
    """searchAgent 返回的研究笔记汇总，供 Planner 和 Verifier 参考."""

    sources: list[SourceItem]
    """searchAgent 收集的所有来源 URL 及摘要."""

    # ── Agent 委托记录 ──
    agent_handoffs: list[AgentHandoff]
    """Planner → 子 Agent 的每次委托记录."""

    # ── Code Agent 产出 ──
    code_agent_summary: str
    """codeAgent 执行后的总结，供 Verifier 评估."""

    # ── Verify 阶段产出 ──
    verification_results: list[VerificationResult]
    """Verifier 执行验证命令后产出的结果列表."""

    passed: bool
    """是否全部验收通过。True 表示任务成功完成."""

    # ── 循环控制 ──
    attempts: int
    """当前已尝试次数，用于 Verifier → Planner 的反馈循环."""

    max_attempts: int
    """最大尝试次数，超过后强制退出，防止无限循环."""

    # ── Verify 阶段详细结果 ──
    verification_checks: list[dict]
    """Verifier LLM 输出的逐项检查结果，每项含 name / passed / detail."""

    last_error: str
    """最近一次验证失败的原因 + 建议下一步，供 Planner 修订计划时使用."""

    # ── 最终产出 ──
    final_answer: str
    """任务完成后的最终汇总（文件改动、命令执行等）."""
