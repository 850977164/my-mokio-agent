"""结构化输出工具 —— 供 Planner / codeAgent / Verifier 共用.

这些工具本身是 no-op stub，LLM 通过 function calling 调用它们，
实际的结构化数据由调用方从 tool-call args 中提取。
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool


def _write_todos(
    plan_summary: str,
    todos: list[dict],
    acceptance_criteria: list[str],
    verification_commands: list[str],
) -> str:
    """No-op: Planner 从 tool-call args 提取结构化计划数据."""
    return f"计划已记录: {len(todos)} 个步骤, {len(acceptance_criteria)} 条验收标准"


def _update_todo(id: str, status: str, note: str = "") -> str:
    """No-op: codeAgent 通过 tool-call args 追踪 todo 状态变更."""
    return f"Todo {id} → {status}" + (f": {note}" if note else "")


def _report_verification(
    passed: bool,
    reason: str,
    checks: list[dict],
    recommended_next_instruction: str,
) -> str:
    """No-op: Verifier 从 tool-call args 提取结构化验证结果."""
    status = "通过" if passed else "未通过"
    return f"验证{status}: {reason}"


TodoWriteTool = StructuredTool.from_function(
    func=_write_todos,
    name="TodoWrite",
    description=(
        "向系统提交完整的执行计划。"
        "参数: plan_summary (计划摘要), todos (待办列表, 每项含 id/content), "
        "acceptance_criteria (验收标准列表), verification_commands (验证命令列表)。"
    ),
)

TodoUpdateTool = StructuredTool.from_function(
    func=_update_todo,
    name="TodoUpdate",
    description=(
        "更新某个 todo 的执行状态。"
        "参数: id (todo ID), status (pending|in_progress|completed|blocked), "
        "note (可选, 执行笔记)。"
    ),
)

ReportVerificationTool = StructuredTool.from_function(
    func=_report_verification,
    name="ReportVerification",
    description=(
        "提交验证报告。"
        "参数: passed (bool, 是否全部通过), reason (总结原因), "
        "checks (列表, 每项含 name/passed/detail), "
        "recommended_next_instruction (如未通过, 给 Planner 的具体修订建议)。"
    ),
)
