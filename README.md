# MokioClaw — Context-Engineered Multi-Agent Framework

> 基于 LangGraph 的智能任务调度代理，引入 **Context Engineering** 解决长程任务的上下文膨胀问题

MokioClaw 是一个支持**多轮对话**和**复杂任务自动化**的 AI Agent 框架。它能智能识别用户意图，自动调度专业 Agent（searchAgent 网络研究、codeAgent 代码实现），并通过分层 Memory、上下文压缩、检查点恢复等机制确保长程任务稳定执行。

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| **智能意图路由** | 自动判断用户输入是「闲聊问答」还是「需要执行的任务」，chat 响应轻量对话，workflow 调度完整工作流 |
| **多 Agent 协作** | Planner 协调 searchAgent（网络研究）和 codeAgent（文件操作）分工协作 |
| **分层 Memory** | Rules Layer + Working Memory + History Summary Store 三层注入，让 Agent 无需遍历历史消息即可感知任务全貌 |
| **上下文压缩** | Token 数接近上限时自动用 LLM 压缩消息历史，持久化到 `HISTORY_SUMMARY.md`，防止上下文溢出 |
| **自愈式重试** | Verifier 验证失败后自动返回 Planner 修订计划、委派修复，最多重试 N 次 |
| **检查点恢复** | 支持 light / strict / off 三种模式，可从任意中断点恢复运行（git 快照 + state 持久化） |
| **执行追踪** | 全量事件流记录到 `.mokioclaw/traces/`，生成 `trace.json` + `timeline.md` 供事后复盘 |
| **命令审批** | 风险命令（pip install / curl 等）可配置 inline（交互审批）、auto（自动放行）、deny（拒绝） |
| **多轮对话** | Session 持久化到 `session.json`，支持连续对话上下文保持 |

---

## 🏗️ 架构设计

### 入口流程（Intent Router）

```
用户输入 → Intent Router → 判断路由
                          ↓
              ┌───────────┴───────────┐
              │                       │
          chat                    workflow
              │                       │
              ↓                       ↓
      ChatResponder              Planner → context_monitor → Verifier → Final
      （轻量对话）                    ↑          ↓                │
                                   └───┴──────────────────────┘
                                        （失败重试循环）
```

### Workflow 流程（复杂图）

```
START → Planner → context_monitor → Verifier → context_monitor → Final → END
            ↑          ↓ (compress?)     │           │
            │    context_compressor      │           │
            │          ↓                 │           │
            └──────────┴─────────────────┴───────────┘
                   （修订计划 & 重试）
```

---

## 📦 项目结构

```
src/mokioclaw/
├── __init__.py                    # 版本定义
├── __main__.py                    # python -m mokioclaw 入口
├── cli/
│   ├── app.py                     # Typer CLI 应用 + Rich 输出渲染
│   └── tui/                       # TUI 界面组件（Textual）
├── core/
│   ├── agent.py                   # stream_agent_events / stream_session_events（主事件流）
│   ├── state.py                   # RuntimeState 数据类
│   ├── paths.py                   # workspace 路径解析 + 安全检查
│   ├── approval.py                # 命令风险分类 + 审批机制
│   ├── checkpoint.py              # CheckpointManager（保存/恢复）
│   ├── trace.py                   # TraceRecorder（执行追踪）
│   └── session.py                 # Session 多轮对话管理
├── agents/
│   ├── code_agent.py              # codeAgent — 代码实现专家（File/Bash/Grep 工具）
│   └── search_agent.py            # searchAgent — 网络研究专家（WebSearch 工具）
├── graph/
│   ├── workflow.py                # build_workflow / build_complex_workflow / build_entry_workflow
│   ├── nodes.py                   # planner_node / verifier_node / context_monitor_node / context_compressor_node
│   │                             #   + intent_router_node / chat_responder_node + 路由函数
│   ├── state.py                   # MokioGraphState TypedDict + TodoItem / VerificationResult 等类型
│   └── memory.py                  # build_layered_memory / format_layered_memory_for_prompt（三层 Memory）
├── prompts/
│   ├── stage3.py                  # PLANNER_PROMPT / VERIFIER_PROMPT / INTENT_ROUTER_PROMPT / CHAT_RESPONDER_PROMPT
│   └── stage4.py                  # CONTEXT_COMPRESSION_PROMPT
├── providers/
│   └── openai_provider.py         # create_model（ChatOpenAI 工厂，支持自定义 endpoint）
├── tools/
│   ├── registry.py                # build_tools / build_read_only_tools（工具注册）
│   ├── file_tools.py              # FileRead / FileWrite / FileEdit
│   ├── grep_tool.py               # GrepTool（内容搜索）
│   ├── bash_tool.py               # BashTool（Shell 命令执行 + 审批）
│   ├── web_search_tool.py         # WebSearchTool（Tavily API）
│   └── structured_tools.py        # TodoWrite / TodoUpdate / ReportVerification（结构化工具桩）
```

---

## 🎯 三层 Memory 详解

```
┌──────────────────────────────────────────────────────────────┐
│                    LayeredMemory                              │
├──────────────────────────────────────────────────────────────┤
│  Rules Layer（固定规则）                                       │
│    • scope: workspace                                         │
│    • storage: internal                                        │
│    • rules: [workspace only, keep durable context...]         │
├──────────────────────────────────────────────────────────────┤
│  Working Memory（当前任务状态）                                │
│    • task, plan_summary, todos                                │
│    • acceptance_criteria, verification_commands               │
│    • research_notes, sources                                  │
│    • code_agent_summary, verifier_summary                     │
│    • attempts, max_attempts, last_error                       │
├──────────────────────────────────────────────────────────────┤
│  History Summary Store（持久化历史）                           │
│    • HISTORY_SUMMARY.md（压缩历史摘要）                        │
│    • NOTEPAD.md（关键决策/阻塞项持久笔记）                     │
│    • context_summary（最近压缩摘要）                           │
│    • compression_events（压缩事件日志）                        │
└──────────────────────────────────────────────────────────────┘
```

每个节点入口调用 `build_layered_memory(state, node="...")` → 格式化为 JSON → 注入到 System Prompt，使 Agent 立即可见任务全貌。

---

## 🔧 核心节点说明

| 节点 | 职责 | 工具/能力 |
|------|------|----------|
| **Intent Router** | 判断用户意图是「chat」还是「workflow」 | 仅 LLM 推理，无工具 |
| **Chat Responder** | 响应问候、感谢、帮助类轻量对话 | 仅 LLM 推理，无工具 |
| **Planner** | 制定计划、委托 searchAgent / codeAgent | TodoWrite, CallSearchAgent, CallCodeAgent |
| **Verifier** | 运行验证命令 + 只读检查文件，对照验收标准判定 | FileRead, Grep, ReportVerification |
| **Context Monitor** | 估算 token 数，判断是否需要压缩 | 无工具，纯计算 |
| **Context Compressor** | LLM 压缩消息历史，持久化摘要 | 仅 LLM 推理，产出 JSON 摘要 |
| **Final** | 格式化最终报告（passed/failed） | 无 LLM，纯字符串拼接 |

---

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/850977164/my-mokio-agent
cd my-mokio-agent
uv sync
```

### 配置环境变量

在项目根目录创建 `.env` 文件：

```env
API_KEY=your-api-key
BASE_URL=https://your-api-endpoint   # 可选，默认 OpenAI
MODEL=gpt-4o                          # 或 deepseek-v4-pro / claude-3-5 等
TAVILY_API_KEY=tvly-...               # 可选，用于 WebSearch
```

### 基础用法

```bash
# 执行任务（workflow 路由）
mokioclaw "帮我搭建一个 Flask 后台管理系统"

# 多轮对话（chat 路由）
mokioclaw "你好，介绍一下你的功能"

# 指定工作区、模型、重试次数
mokioclaw "重构 auth 模块" --workspace ./my-project --model gpt-4o --max-attempts 5

# 配置审批模式、检查点、追踪
mokioclaw "搭建项目" --approval-mode auto --checkpoint-mode strict --trace-mode on

# 从检查点恢复
mokioclaw --resume ./checkpoint-dir "继续之前的任务"
```

---

## ⚙️ CLI 参数说明

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `TASK` | — | 必填 | 自然语言任务描述 |
| `--workspace` | `-w` | `.mokioclaw/workspaces/default` | 工作区路径，所有文件操作限制在此目录 |
| `--model` | `-m` | 环境变量 `MODEL` → `gpt-4o` | LLM 模型名称 |
| `--max-attempts` | `-a` | `3` | 最大重试次数，验证失败后返回 Planner 修订 |
| `--approval-mode` | — | `inline` | 命令审批模式：`inline`（交互）/ `auto`（放行）/ `deny`（拒绝） |
| `--checkpoint-mode` | — | `light` | 检查点模式：`light`（节点切换）/ `strict`（每事件）/ `off`（不保存） |
| `--trace-mode` | — | `on` | 追踪模式：`on`（记录事件流）/ `off`（不记录） |
| `--resume` | — | `None` | 从指定检查点工作区恢复运行 |

---

## 📊 运行效果示例

```
🚀 MokioClaw v0.1.0
📂 workspace:        /path/to/workspace
🤖 model:            gpt-4o
🔁 max attempts:     3
🔐 approval mode:    inline
💾 checkpoint mode:  light
🔍 trace mode:       on
📋 task:             帮我搭建一个Flask后台管理系统

┌── 📋 Planner ──────────────────────────────────────────────┐
│ 搭建完整的Flask后台管理系统，包含项目结构、User模型、       │
│ 认证系统、REST API、Jinja2模板、配置文件和依赖管理。        │
│                                                            │
│ 📝 待办项:                                                 │
│   ⬜ [1] 初始化Flask项目结构                               │
│   ⬜ [2] 创建数据库模型（User）                            │
│   ⬜ [3] 实现用户认证模块                                  │
│                                                            │
│ ✔️  验收标准:                                              │
│   1. python app.py 启动服务                               │
│   2. 用户可注册新账号                                     │
│   3. 用户可用账号密码登录                                 │
│                                                            │
│ 🖥️  验证命令:                                             │
│   $ python app.py &                                       │
│   $ curl -X POST http://localhost:5000/api/register ...   │
└────────────────────────────────────────────────────────────┘

┌── ✅ Verifier ────────────────────────────────────────────┐
│ ✅ 验证通过  (第 1 次)                                     │
│                                                            │
│ 🖥️  验证命令:                                             │
│   ✅ $ python app.py (exit=0)                             │
│       Flask server running on http://127.0.0.1:5000       │
│                                                            │
│ 📊 验收明细:                                               │
│   ✅ 项目可运行 — 服务正常启动                            │
│   ✅ 用户注册功能 — POST /api/register 返回 201           │
│   ✅ 用户登录功能 — POST /api/login 返回 JWT token        │
└────────────────────────────────────────────────────────────┘

┌── 📝 最终结果 ────────────────────────────────────────────┐
│ ✅ 任务执行成功                                            │
│ 🏁 最终结果: PASSED — 所有验收标准均已满足。              │
└────────────────────────────────────────────────────────────┘
```

---

## 🗂️ 工作区文件结构

每次运行会在 workspace 下创建 `.mokioclaw/` 目录：

```
.mokioclaw/
├── checkpoints/
│   ├── checkpoint.json        # 检查点元数据
│   ├── RECOVERY.md            # 人类可读恢复指南
│   ├── state.json             # （strict 模式）完整状态
│   └── events.jsonl           # （strict 模式）事件流
├── traces/
│   └── trace-{id}/
│       ├── trace.json         # 统计概览 + 首尾事件
│       ├── events.jsonl       # 全量事件流
│       └── timeline.md        # 人类可读时间线
├── session/
│   ├── session.json           # 多轮对话会话状态
│   └── SESSION_SUMMARY.md     # 会话摘要
├── HISTORY_SUMMARY.md         # 上下文压缩摘要
├── NOTEPAD.md                 # 持久化关键笔记
└── TODO.md                    # 当前任务待办列表
```

---

## 🔐 命令审批机制

风险命令分类（`RISK_PATTERNS`）：

| 命令类型 | 示例 | 触发审批 |
|----------|------|----------|
| 包管理 | `pip install`, `uv add`, `npm install` | ✅ |
| 网络下载 | `curl`, `wget` | ✅ |
| 长运行服务 | `uvicorn`, `python -m http.server` | ✅ |

审批模式：

- **inline**：弹出交互提示，用户选择批准/拒绝/查看详情
- **auto**：自动批准所有风险命令
- **deny**：直接拒绝风险命令，返回错误

---

## 💾 检查点模式对比

| 模式 | 保存时机 | 保存内容 | 适用场景 |
|------|----------|----------|----------|
| **light** | 每次节点切换 | checkpoint.json + RECOVERY.md + git 快照 | 日常开发（推荐） |
| **strict** | 每个事件追加 | light + state.json + events.jsonl | 需要完整回放 |
| **off** | 不保存 | — | 一次性任务 |

恢复方式：

```bash
# 从检查点恢复
mokioclaw --resume /path/to/workspace "继续之前的任务"
```

恢复时自动：
1. 从 `checkpoint.json` 读取状态
2. 执行 `git checkout {commit_id} -- .` 恢复文件快照
3. 重建 `RuntimeState` + `MokioGraphState`
4. 继续执行未完成的节点

---

## 📝 执行追踪（Trace）

每次运行在 `.mokioclaw/traces/{trace-id}/` 生成：

- **trace.json**：统计摘要（节点访问次数、工具调用总数、失败次数、审批次数、耗时）
- **events.jsonl**：每条事件一行 JSON（tool_call / tool_result / handoff / checkpoint_saved 等）
- **timeline.md**：人类可读表格形式时间线

示例 `timeline.md`：

```markdown
# 📊 MokioClaw 执行追踪

**Trace ID:** `trace-abc123`
**任务:** 搭建 Flask 后台
**状态:** ✅ completed
**总耗时:** 42.5s

---

## 📈 统计摘要

| 指标 | 值 |
|------|-----|
| 🔹 节点 `planner` 访问次数 | 2 |
| 🔹 节点 `verifier` 访问次数 | 1 |
| 🔧 工具调用总数 | 15 |
| ❌ 失败工具调用 | 0 |
| 🔐 审批触发次数 | 2 |
| 💾 检查点保存次数 | 5 |

---

## ⏱️ 事件时间线

| # | 时间 | 类型 | 详情 |
|---|------|------|------|
| 1 | 2025-01-10 14:30:05 | `run_start` | 任务启动 |
| 2 | 2025-01-10 14:30:10 | `tool_call` | 调用 `TodoWrite` |
| 3 | 2025-01-10 14:30:12 | `handoff` | planner → codeAgent |
| ... | ... | ... | ... |
```

---

## 🧪 测试

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试
uv run pytest

# 代码检查
uv run ruff check src/

# 本地运行 CLI
uv run mokioclaw "hello world"
```

测试覆盖：

- `test_approval.py`：审批分类、模式标准化
- `test_checkpoint.py`：检查点保存/恢复、git 快照
- `test_trace.py`：追踪记录、统计计数、timeline 生成
- `test_session.py`：会话持久化、上下文构建
- `test_intent_router.py`：意图路由判断
- `test_mokioclaw_agent.py`：完整工作流集成测试

---

## 🛠️ 关键设计决策

| 决策 | 说明 |
|------|------|
| **Intent Router + Chat Responder** | 支持「闲聊」和「任务」双模式，避免轻量对话触发完整工作流 |
| **Planner 单阶段工具循环** | TodoWrite + CallSearchAgent + CallCodeAgent 同时可用，LLM 自由决定调用顺序 |
| **分层 Memory 注入每个节点** | 所有 Agent（Planner / codeAgent / Verifier）都能看到完整任务全貌 |
| **Context Monitor 实时监控** | Planner / Verifier 后都经过 monitor，确保 token 不溢出 |
| **Context Compressor 绕过 Monitor** | 刚压缩完 token 数已低，无需再次检查 |
| **Verifier 只用只读工具** | FileRead + Grep，不允许修改文件或执行写命令 |
| **结构化工具桩模式** | TodoWrite / ReportVerification 是 no-op 桩，数据从 function calling args 提取，比 JSON 输出更可靠 |
| **Final 纯格式化节点** | 不调用 LLM，保证任何异常下都有可读输出 |
| **所有工具路径沙箱化** | `safe_path()` 确保文件操作限制在 workspace，防止 `../` 逃逸 |
| **LLM 自动重试** | `max_retries=2` + `request_timeout=120s`，应对网络抖动 |

---

## 📦 依赖

核心依赖（`pyproject.toml`）：

| 包 | 用途 |
|----|------|
| `langgraph` | 状态图编排、工具循环、消息追加 reducer |
| `langchain` | Agent 抽象、消息类型 |
| `langchain-openai` | ChatOpenAI LLM 客户端 |
| `typer` | CLI 框架 |
| `rich` | 终端美化输出 |
| `textual` | TUI 界面 |
| `tavily-python` | WebSearch 工具 API |
| `python-dotenv` | 环境变量加载 |

---

## 📄 License

MIT

---

## 🙏 致谢

- [LangGraph](https://github.com/langchain-ai/langgraph) — 状态图编排
- [LangChain](https://github.com/langchain-ai/langchain) — Agent 工具抽象
- [Tavily](https://tavily.com/) — 搜索 API