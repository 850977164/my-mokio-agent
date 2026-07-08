# MokioClaw

> MultiAgent 智能调度代理 —— Supervisor 委派专家 Agent 执行任务，自动验证，失败重试

MokioClaw 是一个基于 LangGraph 的 MultiAgent AI 编程代理。你给它一个自然语言任务描述，Supervisor（Planner）自动拆解为可执行的计划，通过工具调用委派 searchAgent 搜索信息、codeAgent 编写代码，然后 Verifier 运行验收命令验证成果——不通过就返回 Planner 修订重试，直到通过或达到上限。

---

## 架构设计

```
                    ┌─────────────┐
                    │   START     │
                    └──────┬──────┘
                           │
                           ▼
               ┌───────────────────────┐
               │  Planner (Supervisor) │
               │                       │
               │  TodoWrite            │  ← 发布/修订执行计划
               │  CallSearchAgent ──────────→ searchAgent (WebSearch)
               │  CallCodeAgent ────────────→ codeAgent   (File/Shell)
               │                       │
               └───────┬───────────────┘
                       │
                       ▼
               ┌──────────────┐
               │   Verifier   │  ← 运行验证命令，逐项检查验收标准
               └──────┬───────┘
                      │
               ┌──────┴──────┐
               │   passed?   │
               └──┬──────┬───┘
             yes  │      │  no
                  ▼      ▼
            ┌────────┐ ┌─────────┐
            │ Final  │ │ Planner │  ← 重新规划，仅修订失败项
            └───┬────┘ └─────────┘
                │           │
                ▼           │  (max_attempts 次后强制到 Final)
            ┌───────┐       │
            │  END  │◀──────┘
            └───────┘
```

### MultiAgent 委派模型

Planner 不直接操作文件或搜索网络，而是通过三个工具协调工作：

| 工具 | 作用 | 委派对象 |
|---|---|---|
| **TodoWrite** | 向系统提交执行计划（plan_summary + todos + acceptance_criteria + verification_commands） | 系统 |
| **CallSearchAgent** | 将网络研究任务委派给 searchAgent。searchAgent 使用 WebSearchTool（Tavily API）搜索互联网，返回研究笔记和来源 URL | searchAgent |
| **CallCodeAgent** | 将代码实现任务委派给 codeAgent。codeAgent 在 workspace 中使用 FileRead / FileWrite / FileEdit / Grep / Bash / TodoUpdate 完成开发工作 | codeAgent |

### 三节点详解

| 节点 | 职责 | 关键行为 |
|---|---|---|
| **Planner (Supervisor)** | 接收用户任务，制定执行计划，委派 searchAgent 和 codeAgent 工作 | 三种工具同时可用，LLM 自主决定调用顺序。首次生成完整计划 + 委派实现；修订轮只委派修复 |
| **Verifier** | 运行 shell 验证命令 + 用只读工具检查文件内容，对照验收标准逐项评估 | 双重验证：命令退出码 + LLM 审查。通过 ReportVerification 工具输出结构化结果 |
| **Final** | 将 passed/failed 状态格式化为人类可读的最终报告 | 纯格式化逻辑，不调用 LLM，确保任何异常状态下都有可读输出 |

### 状态流转

整个工作流共享一个 `MokioGraphState`（TypedDict），统一管理任务生命周期：

```
task                              # 用户原始任务
runtime                           # RuntimeState (workspace, model)
messages                          # 完整 LLM 对话历史 (add_messages reducer)
plan_summary / todos / acceptance_criteria / verification_commands  # Plan 阶段产出
research_notes / sources          # searchAgent 研究笔记和来源
agent_handoffs                    # Planner → 子 Agent 的每次委派记录
code_agent_summary                # codeAgent 执行总结
verification_results / verification_checks / passed  # Verify 阶段产出
attempts / max_attempts / last_error  # 循环控制
final_answer                      # 最终汇总
```

### 自愈式重试循环

Verifier 不简单地说「通过/失败」，而是输出具体的 `recommended_next_instruction`（失败原因 + 修复建议）。Planner 在修订轮把反馈转化为更新的 TodoWrite + 针对性的 CallCodeAgent 委派，形成带反馈的自愈循环，最多重试 `max_attempts` 次。

---

## 项目结构

```
src/mokioclaw/
├── __init__.py                  # 包入口，版本号
├── __main__.py                  # python -m mokioclaw 入口
│
├── agents/                      # 专家 Agent
│   ├── __init__.py              # 导出 run_code_agent / run_search_agent
│   ├── search_agent.py          # SearchAgent — WebSearch 搜索专家
│   └── code_agent.py            # CodeAgent  — 文件/Shell 代码实现专家
│
├── cli/
│   ├── __init__.py
│   └── app.py                   # Typer CLI，事件渲染（Rich Panel）
│
├── core/
│   ├── __init__.py
│   ├── agent.py                 # stream_agent_events() — 图事件流入口
│   ├── paths.py                 # workspace 路径解析与安全校验 (safe_path)
│   └── state.py                 # RuntimeState dataclass
│
├── graph/                       # LangGraph 图定义
│   ├── __init__.py              # 导出 state / nodes / workflow
│   ├── state.py                 # MokioGraphState, TodoItem, VerificationResult, SourceItem, AgentHandoff
│   ├── nodes.py                 # planner_node, verifier_node, verifier_route
│   └── workflow.py              # build_workflow() + final_node
│
├── prompts/
│   ├── __init__.py
│   ├── stage2.py                # Plan & Execute 四节点 prompt（上一版）
│   └── stage3.py                # MultiAgent prompt（当前使用）
│
├── providers/
│   ├── __init__.py
│   └── openai_provider.py       # create_model() — ChatOpenAI 工厂（OpenAI 兼容 API）
│
└── tools/
    ├── __init__.py              # 导出 build_tools / build_read_only_tools
    ├── registry.py              # build_tools() / build_read_only_tools()
    ├── bash_tool.py             # Bash  — shell 命令执行（120s 超时）
    ├── file_tools.py            # FileRead / FileWrite / FileEdit
    ├── grep_tool.py             # Grep  — 正则搜索文件内容
    ├── structured_tools.py      # TodoWrite / TodoUpdate / ReportVerification（结构化输出桩）
    └── web_search_tool.py       # WebSearch — Tavily API 网络搜索
```

---

## 安装

```bash
# 克隆仓库
git clone <repo-url>
cd my-mokio-agent

# 安装依赖
uv sync

# 配置环境变量（.env 文件或 export）
API_KEY="sk-..."           # OpenAI 兼容 API 密钥
BASE_URL="https://..."      # API 端点（可选）
MODEL="gpt-4o"             # 默认模型（可选）
TAVILY_API_KEY="tvly-..."  # Tavily 搜索 API 密钥（可选，用于 WebSearch）
```

## 使用

```bash
# 基本用法
mokioclaw "帮我实现一个 Conway's Game of Life，要求 TDD"

# 指定工作区
mokioclaw "重构 auth 模块" --workspace ./my-project

# 指定模型和重试次数
mokioclaw "帮我写一个 REST API" --model gpt-4o --max-attempts 5

# 简写形式
mokioclaw "找出所有 TODO 注释" -w ./src -a 3
```

### 参数说明

| 参数 | 简写 | 默认值 | 说明 |
|---|---|---|---|
| `TASK` | — | 必填 | 自然语言任务描述 |
| `--workspace` | `-w` | `.mokioclaw/workspaces/default` | 工作区路径，所有文件操作限制在此范围内 |
| `--model` | `-m` | 环境变量 `MODEL` → `gpt-4o` | LLM 模型名称 |
| `--max-attempts` | `-a` | `3` | 最大重试次数，验证失败后返回 Planner 修订重试 |

### 运行效果

```
🚀 MokioClaw v0.1.0
📂 workspace:   /path/to/workspace
🤖 model:       gpt-4o
🔁 max attempts: 3
📋 task:        搜索 React 19 新特性并写一篇总结

┌── 📋 Planner ──────────────────────────────────────┐
│ 1. 先用 TodoWrite 发布研究+写作计划
│ 2. 委派 searchAgent 搜索 React 19 新特性
│ 3. 委派 codeAgent 根据研究笔记撰写总结文档
│
│ 📝 待办项:
│   ⬜ [1] 搜索 React 19 的新特性
│   ⬜ [2] 整理研究笔记
│   ⬜ [3] 编写 React19-summary.md 文档
│
│ ✔️  验收标准:
│   1. React19-summary.md 存在且包含完整内容
│   2. 文档引用了可靠的来源 URL
└────────────────────────────────────────────────────┘

┌── ✅ Verifier ────────────────────────────────────┐
│ ✅ 验证通过  (第 1 次)
│
│ 📊 验收明细:
│   ✅ 文件存在且内容完整
│   ✅ 引用了 3 个来源 URL
└────────────────────────────────────────────────────┘

┌── 📝 最终结果 ────────────────────────────────────┐
│ 🏁 最终结果: PASSED — 所有验收标准均已满足。
└────────────────────────────────────────────────────┘
```

---

## 关键设计决策

### Supervisor + 专家 Agent 委派模型

传统 ReAct 循环让单一 LLM 边想边做，工具多了容易迷失。MultiAgent 架构将「规划/决策」与「执行」分离到不同 Agent：

- **Planner (Supervisor)** 拥有独立上下文窗口来思考全局策略，通过工具调用委派工作，不直接操作文件或搜索
- **searchAgent** 专注网络调研，只暴露 WebSearch 工具，不会被文件操作分心
- **codeAgent** 专注代码实现，只暴露文件/Shell 工具，在 workspace 内完成任务并通过 TodoUpdate 汇报进度

### 单阶段工具循环

Planner 不强制分阶段（先计划再委派），而是将 TodoWrite / CallSearchAgent / CallCodeAgent 三种工具同时开放。LLM 自由决定调用顺序，减少不必要的中间 human message 注入，让对话流程更自然。

### 所有工具路径沙箱化

每个工具都接收 `workspace: Path` 参数，通过 `safe_path()` 确保所有文件操作都在 workspace 内，防止 `../` 路径逃逸。

### final_node 不调用 LLM

Final 节点是纯文本格式化函数。因为 Planner / Verifier 都可能因 LLM 调用异常而产出不完整的状态，final_node 保证无论什么情况都能生成可读的最终报告。

### 结构化工具桩模式

TodoWrite、TodoUpdate、ReportVerification 是 no-op 桩工具——LLM 通过 function calling 调用它们，实际的结构化数据由调用方从 tool-call args 中提取。这比让 LLM 输出 JSON 字符串更可靠，因为 function calling 的参数解析由模型提供商保证 JSON Schema 合规。

---

## 开发

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试
uv run pytest

# 代码检查
uv run ruff check src/

# 本地运行
uv run mokioclaw "hello world"
```

## License

MIT
