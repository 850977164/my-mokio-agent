"""工具注册 —— build_tools(state) 返回所有 StructuredTool 实例."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from mokioclaw.core.state import RuntimeState
from mokioclaw.tools.bash_tool import create_bash_tool
from mokioclaw.tools.file_tools import create_file_read_tool, create_file_write_tool, create_file_edit_tool
from mokioclaw.tools.grep_tool import create_grep_tool


def build_tools(state: RuntimeState) -> list[StructuredTool]:
    """构建并返回所有可用工具列表。

    每个工具在创建时绑定 state.workspace，
    确保文件/Grep/命令操作的路径安全。

    Args:
        state: 当前运行时状态。

    Returns:
        StructuredTool 列表，可直接传给 model.bind_tools()。
    """
    workspace = state.workspace

    return [
        create_file_read_tool(workspace=workspace),
        create_file_write_tool(workspace=workspace),
        create_file_edit_tool(workspace=workspace),
        create_grep_tool(workspace=workspace),
        create_bash_tool(workspace=workspace),
    ]


def build_read_only_tools(state: RuntimeState) -> list[StructuredTool]:
    """构建只读工具列表（FileRead + Grep），供 Verifier 使用。

    Verifier 只需要检查文件内容和搜索结果，不允许修改文件或执行命令。

    Args:
        state: 当前运行时状态。

    Returns:
        包含 FileRead 和 Grep 的 StructuredTool 列表。
    """
    workspace = state.workspace

    return [
        create_file_read_tool(workspace=workspace),
        create_grep_tool(workspace=workspace),
    ]
