# MokioClaw — Context-Engineered Multi-Agent Framework with Harness Engineering

> 基于 LangGraph 的 Multi-Agent 智能调度代理，引入 **Context Engineering** 机制解决长程任务的上下文膨胀问题，通过 **Harness Engineering** 三大措施（人类在环审批 + Checkpoint 断点恢复 + Trace 链路追踪）提供生产级安全网和可观测性。

MokioClaw 接收一个自然语言任务描述，Supervisor（Planner）自动拆解为可执行的计划，通过工具调用委派 **searchAgent** 搜索信息、**codeAgent** 编写代码。**Verifier** 运行验收命令验证成果 — 不通过就返回 Planner 修订重试，直到通过或达到上限。在每个关键节点入口注入分层 Memory，让每个 Agent 无需遍历全部历史消息即可感知任务全貌。

---

## 设计目标

### Context Engineering — 解决上下文膨胀

Agent 执行长程任务时，消息历史会越来越长，最终撑爆上下文窗口。MokioClaw 引入三大措施：

| 措施 | 机制 | 解决的问题 |
|------|------|-----------|
| **压缩机制** | 当 token 数接近上限（默认 400K）时，`context_compressor` 节点用 LLM 自动压缩消息历史，将冗长的工具调用和中间推理替换为精简的结构化摘要，持久化到 `HISTORY_SUMMARY.md` | 消息历史无限增长 → token 溢出 |
| **Notepad 持久笔记** | 关键信息（决策、发现、阻塞项）通过 `NotepadAppendTool` 写入 `NOTEPAD.md`，后续节点通过 `NotepadReadTool` 和分层 Memory 恢复上下文 | 压缩可能丢失细节 → 关键信息不依赖消息历史 |
| **分层 Memory** | 每个节点入口注入三层记忆快照：**Rules 层**（固定规则）+ **Working Memory 层**（当前任务状态：todos、plan、acceptance_criteria、research_notes 等）+ **History Summary 层**（压缩历史 + NOTEPAD 笔记） | 每个 Agent 需要重复获取上下文 → 一次注入，全貌可见 |

### Harness Engineering — 生产级安全网

Agent 在生产环境中需要"安全网"和"可观测性"。引入三大措施：

| 措施 | 机制 | 解决的问题 |
|------|------|-----------|
| **人类在环审批** | 高风险命令（`pip install`、`curl`、`npm install` 等）在执行前经过 `classify_command_risk` 分类风险 → 可配置 `inline`（交互审批）/ `auto`（自动放行）/ `deny`（直接拒绝）三种模式 | 不安全命令未经审核就执行 → 多级风险控制 |
| **Checkpoint 检查点** | 定期保存运行状态到 `.mokioclaw/checkpoints/`，支持 `light`（节点切换时保存）/ `strict`（每个事件保存）/ `off` 三种模式。中断后可通过 `--resume` 恢复运行 | 长任务中断丢失进度 → 断点续跑 |
| **Trace 链路追踪** | 详细记录每一步执行日志到 `.mokioclaw/traces/{trace_id}/`，产出 `trace.json`（统计概览）+ `events.jsonl`（全量事件）+ `timeline.md`（人类可读时间线） | 黑盒执行无法复盘 → 全链路可观测 |

---

## 架构图

### 审批流程

```
┌──────────┐     ┌──────────────┐     ┌──────────────┐
│ codeAgent │────▶│   BashTool   │────▶│ classify_risk│
└──────────┘     │   run_bash()  │     └──────┬───────┘
                 └──────────────┘            │
                                      ┌──────┴──────┐
                                      │  risk_level  │
                                      └──┬──────┬───┘
                                    safe │      │ risky
                                         ▼      ▼
                                    ┌────────┐ ┌──────────────┐
                                    │ 直接执行│ │approval_handler│
                                    └────────┘ └──────┬───────┘
                                                       │
                                                ┌──────┴──────┐
                                                │   人类决策   │
                                                └──┬──────┬───┘
                                              approve │      │ deny
                                                    ▼      ▼
                                               ┌────────┐ ┌────────┐
                                               │  执行   │ │  拒绝   │
                                               └────────┘ └────────┘
```

### 风险分类规则

| 风险类别 | 匹配模式 | 审批行为 |
|----------|----------|----------|
| Python 包安装 | `pip install`, `uv add`, `uv pip install` | `inline` 模式需确认 |
| Node 包安装 | `npm install`, `pnpm install`, `yarn install/add` | `inline` 模式需确认 |
| 网络下载 | `curl`, `wget` | `inline` 模式需确认 |
| 长运行服务 | `uvicorn`, `python -m http.server` | `inline` 模式需确认 |
| 安全命令 | `ls`, `echo`, `git status`, `cat`, `mkdir`, `pytest` | 直接执行 |

### 多 Agent 工作流

```
                    ┌─────────────┐
                    │   START     │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Planner   │  ← 发布计划 + 委托 searchAgent / codeAgent
                    └──────┬──────┘
                           │
                           ▼
               ┌───────────────────────┐
               │   Context Monitor      │  ← 估算 token 数（messages + memory_payload）
               └───────┬───────────────┘
                       │
              ┌────────┴────────┐
              │ should_compress?│
              └──┬──────────┬───┘
              no  │          │  yes
                  │          ▼
                  │   ┌──────────────────┐
                  │   │Context Compressor │  ← LLM 压缩消息历史，持久化到 HISTORY_SUMMARY.md
                  │   └────────┬─────────┘
                  │            │
                  ▼            ▼
           ┌─────────────┐  ┌─────────────┐
           │  Verifier    │  │   Planner   │  ← 压缩后重新进入 planner
           └──────┬──────┘  └─────────────┘
                  │
           ┌──────┴──────┐
           │   passed?   │
           └──┬──────┬───┘
         yes  │      │  no（未达最大重试）
              ▼      ▼
        ┌────────┐ ┌─────────┐
        │ Final  │ │ Planner │  ← 修订计划，委托修复
        └───┬────┘ └─────────┘
            │           │
            ▼           │
        ┌───────┐       │
        │  END  │◀──────┘（passed 或达到 max_attempts）
        └───────┘
```

### Harness 生命周期

```
stream_agent_events()
  │
  ├─ 1. RuntimeState（注入 approval_mode, approval_handler, checkpoint_mode, trace_mode）
  ├─ 2. CheckpointManager + TraceRecorder
  ├─ 3. _prepare_inputs（优先从检查点恢复）
  ├─ 4. build_workflow() 编译图
  ├─ 5. trace.start(inputs) + 初始 checkpoint
  │
  ├─ 6. for (mode, chunk) in graph.stream():
  │     ├─ custom → trace.record_custom_event() + 条件 checkpoint
  │     └─ updates → trace.record_graph_update() + checkpoint
  │
  ├─ 7. 正常结束 → checkpoint(status) + trace.end(status)
  └─ 8. KeyboardInterrupt → checkpoint("interrupted") + trace.end("interrupted")
```

---

## Harness Engineering 详解

### 审批机制

**三种模式：**

| 模式 | 行为 | 适用场景 |
|------|------|----------|
| `inline` | 高风险命令回调 `approval_handler`，等待人类决策 | 交互式终端，需要人工审核 |
| `auto` | 高风险命令自动放行（标记 `requires_approval=True`） | 受信任的沙箱环境 |
| `deny` | 直接拒绝所有高风险命令 | 只读任务、安全审计 |

**使用方式：**

```bash
# 交互审批模式（通过 Rich 终端交互）
mokioclaw "搭建项目" --approval-mode inline

# 自动放行
mokioclaw "写测试" --approval-mode auto

# 严格模式，禁止任何风险命令
mokioclaw "审计代码" --approval-mode deny
```

### Checkpoint 检查点

**三种模式：**

| 模式 | 保存内容 | 保存时机 | 恢复能力 |
|------|----------|----------|----------|
| `light` | `checkpoint.json` + `RECOVERY.md` + git 快照 | 每次节点切换 | 恢复计划/进度/文件 |
| `strict` | light 全部 + `state.json` + `events.jsonl` | 每个事件发生后 | 完整状态恢复 |
| `off` | 无 | — | — |

**断点恢复：**

```bash
# 从上次中断继续
mokioclaw --resume ./project "继续之前的任务" --checkpoint-mode strict
```

**输出文件：**

```
.mokioclaw/checkpoints/
├── checkpoint.json     # 元数据 + 状态摘要（task / status / attempts / plan_summary …）
├── RECOVERY.md         # 人类可读恢复指南
├── state.json          # 完整序列化状态（strict 模式）
└── events.jsonl        # 事件日志（strict 模式）
```

### Trace 链路追踪

**输出文件：**

```
.mokioclaw/traces/{trace_id}/
├── trace.json     # 统计概览：timeline_head(前20条) + timeline_tail(后80条) + 计数器
├── events.jsonl   # 逐条事件：seq + timestamp + 事件内容
└── timeline.md    # Markdown 表格，人类可读时间线
```

**trace.json 统计字段：**

| 字段 | 说明 |
|------|------|
| `node_visits` | 每个图节点的访问次数 |
| `tool_calls` | 工具调用总数 |
| `failed_tool_calls` | 失败的工具调用次数 |
| `approval_count` | 审批触发次数 |
| `checkpoint_count` | 检查点保存次数 |
| `handoff_count` | Agent 切换次数 |
| `duration_ms` | 总执行耗时 |
| `timeline_omitted` | 被省略的中间事件数 |

**使用方式：**

```bash
# 默认开启
mokioclaw "任务" --trace-mode on

# 关闭追踪
mokioclaw "任务" --trace-mode off
```

---

## 分层 Memory 结构

```
┌──────────────────────────────────────────┐
│              LayeredMemory               │
├──────────────────────────────────────────┤
│  Rules Layer                             │
│    • scope: workspace                    │
│    • storage: internal                   │
│    • rules: [workspace only, ...]        │
├──────────────────────────────────────────┤
│  Working Memory                          │
│    • task, plan_summary, todos           │
│    • acceptance_criteria                 │
│    • research_notes, sources             │
│    • code_agent_summary, last_error      │
│    • attempts, max_attempts              │
├──────────────────────────────────────────┤
│  History Summary Store                    │
│    • HISTORY_SUMMARY.md (压缩历史)        │
│    • NOTEPAD.md (持久笔记)                │
│    • context_summary (最近压缩摘要)        │
│    • compression_events (最近 3 条)       │
└──────────────────────────────────────────┘
```

*每个节点入口调用 `build_layered_memory(state, node=...)` → `format_layered_memory_for_prompt(memory)` → 拼入 HumanMessage。Agent 无需遍历全部历史消息即可感知任务全貌。*

---

## MultiAgent 委派模型

Planner 不直接操作文件或搜索网络，而是通过三个工具协调工作：

| 工具 | 作用 | 委派对象 |
|------|------|----------|
| **TodoWrite** | 向系统提交执行计划（plan_summary + todos + acceptance_criteria + verification_commands） | 系统 |
| **CallSearchAgent** | 将网络研究任务委派给 searchAgent。searchAgent 使用 WebSearchTool（Tavily API）搜索互联网 | searchAgent |
| **CallCodeAgent** | 将代码实现任务委派给 codeAgent。codeAgent 在 workspace 中使用 FileRead / FileWrite / FileEdit / Grep / Bash / TodoUpdate | codeAgent |

### 三节点详解

| 节点 | 职责 | 关键行为 |
|------|------|----------|
| **Planner (Supervisor)** | 接收用户任务，制定执行计划，委派 searchAgent 和 codeAgent 工作。注入分层 Memory | 三种工具同时可用，LLM 自主决定调用顺序。首次生成完整计划 + 委派实现；修订轮只委派修复 |
| **Verifier** | 运行 shell 验证命令 + 用只读工具检查文件内容，对照验收标准逐项评估。注入分层 Memory | 双重验证：命令退出码 + LLM 审查。通过 ReportVerification 工具输出结构化结果 |
| **Final** | 将 passed/failed 状态格式化为人类可读的最终报告 | 纯格式化逻辑，不调用 LLM，确保任何异常状态下都有可读输出 |

### 上下文工程节点

| 节点 | 职责 | 触发时机 |
|------|------|----------|
| **Context Monitor** | 估算当前 token 数（messages + memory_payload），判断是否需要压缩 | Planner 之后、Verifier 之后都经过 monitor |
| **Context Compressor** | 用 LLM 压缩消息历史，产出的摘要替换全部消息 → 持久化 HISTORY_SUMMARY.md | token 数 > context_token_limit（默认 400K） |

### 自愈式重试循环

Verifier 不简单地说「通过/失败」，而是输出具体的 `recommended_next_instruction`。Planner 在修订轮把反馈转化为更新的 TodoWrite + 针对性的 CallCodeAgent 委派，形成带反馈的自愈循环，最多重试 `max_attempts` 次。

---

## 状态流转

整个工作流共享一个 `MokioGraphState`（TypedDict），统一管理任务生命周期：

```
task                                        # 用户原始任务
runtime                                     # RuntimeState (workspace, model, checkpoint_mode, trace_mode, approval_mode, ...)
messages                                    # 完整 LLM 对话历史 (add_messages reducer)

# Plan 阶段产出
plan_summary / todos / acceptance_criteria / verification_commands

# 搜索研究产出
research_notes / sources

# Agent 委托记录
agent_handoffs

# Code Agent 产出
code_agent_summary

# Verify 阶段产出
verification_results / verification_checks / passed

# 循环控制
attempts / max_attempts / last_error

# 上下文管理
context_summary / context_token_count / context_token_limit
context_should_compress / context_next_node
compression_events / history_summary / memory_snapshot

# 压缩产出字段（由 context_compressor_node 填充）
active_goal / completed_work / open_todos / important_files
tool_findings / next_steps / risks

# 最终产出
final_answer
```

---

## 项目结构

```
src/mokioclaw/
├── __init__.py
├── __main__.py                  # python -m mokioclaw 入口
├── cli/
│   ├── __init__.py
│   └── app.py                   # Typer CLI 应用（支持 --approval-mode, --checkpoint-mode, --trace-mode, --resume）
├── core/
│   ├── __init__.py
│   ├── agent.py                 # stream_agent_events — 完整 Harness 生命周期（start → event loop → end/interrupt）
│   ├── approval.py              # 命令风险分类 + ApprovalRequest/ApprovalDecision + 审批模式
│   ├── checkpoint.py            # CheckpointManager — light/strict/off + 断点恢复 + RECOVERY.md
│   ├── trace.py                 # TraceRecorder — trace.json/events.jsonl/timeline.md 执行追踪
│   ├── paths.py                 # workspace 路径解析/安全检查 + 项目根目录查找
│   └── state.py                 # RuntimeState 数据类（workspace / model / approval / checkpoint / trace）
├── agents/
│   ├── __init__.py
│   ├── code_agent.py            # codeAgent — 代码实现专家（文件/Shell 工具）
│   └── search_agent.py          # searchAgent — 网络调研专家（WebSearch 工具）
├── graph/
│   ├── __init__.py              # Graph 层统一导出
│   ├── nodes.py                 # 核心节点实现（~870 行）
│   │                           #   planner_node, verifier_node,
│   │                           #   context_monitor_node, context_compressor_node,
│   │                           #   verifier_route, context_monitor_route, context_compressor_route,
│   │                           #   _planner_input, _verifier_input
│   ├── state.py                 # MokioGraphState TypedDict + TodoItem 等辅助类型
│   ├── memory.py                # 三层 Memory 构建 + 格式化 + memory_event
│   └── workflow.py              # build_workflow / build_complex_workflow + final_node
├── prompts/
│   ├── __init__.py
│   ├── stage2.py                # ReAct 阶段 prompts（保留）
│   ├── stage3.py                # PLANNER_PROMPT / VERIFIER_PROMPT
│   └── stage4.py                # CONTEXT_COMPRESSION_PROMPT
├── providers/
│   ├── __init__.py
│   └── openai_provider.py       # ChatOpenAI 工厂（max_retries + request_timeout）
└── tools/
    ├── __init__.py
    ├── bash_tool.py             # BashTool — shell 命令执行（集成审批机制）
    ├── file_tools.py            # FileRead / FileWrite / FileEdit
    ├── grep_tool.py             # GrepTool — 内容搜索
    ├── registry.py              # build_tools / build_read_only_tools（透传审批参数）
    ├── structured_tools.py      # TodoWrite / TodoUpdate / ReportVerification（结构化输出桩）
    └── web_search_tool.py       # WebSearchTool (Tavily)

tests/
├── test_mokioclaw_agent.py      # 19 tests — workflow 编译 + 分层 Memory + input builder + 路由
├── test_approval.py             # 40 tests — 风险分类 + 审批流程 + BashTool 集成
├── test_checkpoint.py           # 34 tests — light/strict/off + git 快照 + 断点恢复 + 清单
└── test_trace.py                # 40 tests — 追踪生命周期 + 统计计数 + timeline.md + events.jsonl
```

---

## 快速开始

### 安装

```bash
git clone <repo-url>
cd my-mokio-agent
uv sync
```

### 配置

在项目根目录创建 `.env` 文件：

```env
API_KEY=your-api-key
BASE_URL=https://your-api-endpoint
MODEL=deepseek-v4-pro
TAVILY_API_KEY=tvly-...        # 可选，用于 WebSearch
```

### 运行

```bash
# 基础用法
mokioclaw "帮我实现一个 Conway's Game of Life，要求 TDD"

# 指定工作区和模型
mokioclaw "重构 auth 模块" --workspace ./my-project --model gpt-4o

# 控制最大重试次数
mokioclaw "写测试用例" -w ./src -a 5

# Harness Engineering 选项
mokioclaw "搭建项目" --approval-mode auto --checkpoint-mode strict --trace-mode on

# 从检查点恢复
mokioclaw --resume ./project "继续之前的任务"
```

### 参数说明

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `TASK` | — | 必填 | 自然语言任务描述 |
| `--workspace` | `-w` | `.mokioclaw/workspaces/default` | 工作区路径 |
| `--model` | `-m` | 环境变量 `MODEL` → `gpt-4o` | LLM 模型名称 |
| `--max-attempts` | `-a` | `3` | 最大重试次数 |
| `--approval-mode` | — | `inline` | 审批模式：`inline`（交互审批）/ `auto`（自动放行）/ `deny`（禁止风险命令） |
| `--checkpoint-mode` | — | `light` | 检查点模式：`light`（节点切换时保存）/ `strict`（每个事件保存）/ `off`（不保存） |
| `--trace-mode` | — | `on` | 追踪模式：`on`（记录执行追踪）/ `off`（不记录） |
| `--resume` | — | `None` | 从指定检查点工作区恢复运行 |

### 运行效果

```
╭─────────────────────────── MokioClaw ────────────────────────────╮
│ 🚀 MokioClaw v0.1.0                                              │
│ 📂 workspace:        /path/to/workspace                          │
│ 🤖 model:            deepseek-v4-pro                             │
│ 🔁 max attempts:     3                                           │
│ 🔐 approval mode:    auto                                        │
│ 💾 checkpoint mode:  light                                       │
│ 🔍 trace mode:       on                                          │
│ 📋 task:             帮我搭建一个Flask后台管理系统                │
╰──────────────────────────────────────────────────────────────────╯

┌── 📋 Planner ───────────────────────────────────────────────────┐
│ 搭建一个完整的Flask后台管理系统，包含：项目结构初始化、          │
│ 数据库模型（User）、用户认证系统、REST API接口、                 │
│ Jinja2前端模板页面、以及相关的配置文件和依赖管理。               │
│                                                                  │
│ 📝 待办项:                                                       │
│   ⬜ [1] 初始化Flask项目结构                                      │
│   ⬜ [2] 创建数据库模型（User）                                   │
│   ⬜ [3] 实现用户认证模块                                         │
│   ...                                                            │
│                                                                  │
│ ✔️  验收标准:                                                     │
│   1. 项目可运行：python app.py 启动服务                          │
│   2. 用户可注册新账号                                             │
│   3. 用户可用账号密码登录                                         │
│   ...                                                            │
│                                                                  │
│ 🖥️  验证命令:                                                     │
│   $ python app.py &                                              │
│   $ curl -X POST http://localhost:5000/api/register ...          │
└──────────────────────────────────────────────────────────────────┘

┌── ✅ Verifier ─────────────────────────────────────────────────┐
│ ✅ 验证通过  (第 1 次)                                           │
│ ...                                                              │
└──────────────────────────────────────────────────────────────────┘

┌── 📝 最终结果 ─────────────────────────────────────────────────┐
│ 🏁 最终结果: PASSED — 所有验收标准均已满足。                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 关键设计决策

| 决策 | 说明 |
|------|------|
| **Planner 单阶段工具循环** | TodoWrite + CallSearchAgent + CallCodeAgent 同时可用，LLM 自由决定调用顺序，减少不必要的中间 human message 注入 |
| **context_monitor 在每次节点后运行** | Planner → monitor → Verifier → monitor，确保 token 数实时受控不溢出 |
| **compressor 绕过 monitor** | 刚压缩完 token 数已经很低，无需再次经过 monitor 检查 |
| **Verifier 只用只读工具** | FileRead + Grep，不允许修改文件或执行写命令 |
| **分层 Memory 注入到每个节点** | 所有 Agent（Planner / codeAgent / Verifier）都能看到完整任务全貌 |
| **所有工具路径沙箱化** | `safe_path()` 确保文件操作限制在 workspace 内，防止 `../` 路径逃逸 |
| **final_node 不调用 LLM** | 纯格式化函数，保证即使 LLM 调用失败也有可读输出 |
| **结构化工具桩模式** | TodoWrite / TodoUpdate / ReportVerification 是 no-op 桩，结构数据从 function calling args 提取，比 JSON 输出更可靠 |
| **LLM 自动重试** | `max_retries=2` + `request_timeout=120s`，应对网络抖动 |
| **审批三层级** | `classify_command_risk` 正则分类 → 三种 approval_mode → 人类决策 / 自动放行 / 直接拒绝 |
| **检查点粒度可控** | `light` 模式只做 git 快照，`strict` 模式逐事件序列化完整状态 |
| **Trace 首尾截断** | 事件 > 100 条时只保留前 20 + 后 80，避免 trace.json 过大 |
| **Trace 与 Checkpoint 解耦** | 各自独立开关，互不依赖。Trace 保存事件到内存 + JSONL，Checkpoint 保存状态到文件 |

---

## 开发

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试（134 tests）
uv run pytest

# 代码检查
uv run ruff check src/

# 本地运行
uv run mokioclaw "hello world"
```

## License

MIT
