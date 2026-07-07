# MokioClaw

> Plan & Execute 智能调度代理 —— 制定计划、执行任务、自动验证、失败重试

MokioClaw 是一个基于 LangGraph 的 AI 编程代理。你给它一个自然语言任务描述，它会自动拆解为可执行的步骤，用工具（读写文件、运行命令、搜索代码）逐步完成，然后运行验收命令验证成果 —— 不通过就重试，直到通过或达到上限。

---

## 架构设计

```
                     ┌─────────────┐
                     │   START     │
                     └──────┬──────┘
                            │
                            ▼
                     ┌─────────────┐
                     │   Planner   │  ← 制定计划、写验收标准
                     └──────┬──────┘
                            │
                            ▼
                     ┌─────────────┐
                     │    Actor    │  ← 按计划执行，使用工具
                     └──────┬──────┘
                            │
                            ▼
                     ┌─────────────┐
                     │  Verifier   │  ← 运行验收命令，判断是否通过
                     └──────┬──────┘
                            │
                     ┌──────┴──────┐
                     │   passed?   │
                     └──┬──────┬───┘
                   yes  │      │  no
                        ▼      ▼
                 ┌─────────┐  ┌─────────┐
                 │  Final   │  │ Planner │  ← 重新规划修复方案
                 └────┬────┘  └─────────┘
                      │            │
                      ▼            │
                 ┌─────────┐      │
                 │   END   │◀─────┘
                 └─────────┘  (max_attempts 次后也到 END)
```

### 四节点详解

| 节点 | 职责 | 使用策略 |
|---|---|---|
| **Planner** | 将用户任务拆解为有序的 todo 列表，每项包含具体的验收标准和可执行的验证命令 | 首次调用时从头规划；后续调用时根据 Verifier 的错误反馈修订计划，保留已完成的项，只为失败项生成新的 todo |
| **Actor** | 按 Planner 的计划逐步执行，使用文件/Bash/Grep 工具完成每个 todo，通过 TodoUpdate 标记进度 | 首次执行看到完整计划；重试轮只看到未完成的 todo + Verifier 反馈，避免受到历史对话的干扰 |
| **Verifier** | 先在 workspace 运行 shell 验证命令（pytest、mypy 等），再用只读工具检查文件内容是否符合验收标准 | 双重验证：命令执行结果 + LLM 逐项人工审查，两者都通过才算 passed |
| **Final** | 将 passed/failed 状态格式化为可读的最终报告 | 纯格式化逻辑，不调用 LLM，保证任何异常状态下都有可读输出 |

### 状态流转

整个工作流共享一个 `MokioGraphState`（TypedDict），包含 task、plan、todos、verification_results、passed、attempts 等字段。LangGraph 在每个节点间自动传递和 merge 状态更新。`messages` 字段使用了 `add_messages` reducer，自动追加而不是覆盖对话历史。

---

## 安装

```bash
# 克隆仓库
git clone <repo-url>
cd my-mokio-agent

# 安装依赖
uv sync

# 设置 API Key
export OPENAI_API_KEY="sk-..."
export MODEL="gpt-4o"          # 可选，默认 gpt-4o
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
📋 task:        帮我实现一个 Conway's Game of Life，要求 TDD

┌── 📋 Planner ──────────────────────────────────────┐
│ 实现 Conway's Game of Life，TDD 方式：先写测试...
│
│ 📝 待办项:
│   ⬜ [1] 编写 conftest.py 和 pytest 配置
│   ⬜ [2] 编写 Grid 类的测试
│   ⬜ [3] 实现 Grid 类
│   ⬜ [4] 编写 next_generation 的测试
│   ...
│
│ ✔️  验收标准:
│   1. pytest 运行所有测试通过
│   2. Blinker 周期振荡器正常工作
│   ...
└────────────────────────────────────────────────────┘

┌── 🔧 Actor ───────────────────────────────────────┐
│ 完成 9/9  受阻 0  进行中 0  待办 0
│ 已创建 test_game_of_life.py (24 个测试)...
└────────────────────────────────────────────────────┘

┌── ✅ Verifier ────────────────────────────────────┐
│ ✅ 验证通过  (第 1 次)
│
│ 🖥️  验证命令:
│   ✅ $ pytest test_game_of_life.py -v (exit=0)
│
│ 📊 验收明细:
│   ✅ 24 tests all pass
│   ✅ Blinker period-2 oscillator
│   ...
└────────────────────────────────────────────────────┘

┌── 📝 最终结果 ────────────────────────────────────┐
│ 🏁 最终结果: PASSED — 所有验收标准均已满足。
└────────────────────────────────────────────────────┘
```

---

## 项目结构

```
src/mokioclaw/
├── __init__.py              # 包入口，版本号
├── __main__.py              # python -m mokioclaw 入口
│
├── cli/
│   ├── __init__.py
│   └── app.py               # Typer CLI，事件渲染（Rich Panel）
│
├── core/
│   ├── __init__.py
│   ├── agent.py             # stream_agent_events() — 图事件流入口
│   ├── paths.py             # workspace 路径解析与安全校验
│   └── state.py             # RuntimeState dataclass
│
├── graph/
│   ├── __init__.py           # 导出 state / nodes / workflow
│   ├── state.py              # MokioGraphState, TodoItem, VerificationResult
│   ├── nodes.py              # planner_node, actor_node, verifier_node, verifier_route
│   └── workflow.py           # build_workflow() + final_node
│
├── prompts/
│   ├── __init__.py
│   └── stage2.py             # PLANNER / ACTOR / VERIFIER / FINAL 四个 prompt
│
├── providers/
│   ├── __init__.py
│   └── openai_provider.py    # create_model() — ChatOpenAI 工厂
│
└── tools/
    ├── __init__.py
    ├── registry.py           # build_tools() / build_read_only_tools()
    ├── bash_tool.py          # Bash 命令执行（120s 超时）
    ├── file_tools.py         # FileRead / FileWrite / FileEdit
    └── grep_tool.py          # 正则搜索
```

---

## 关键设计决策

### Plan & Execute 而非一次性 ReAct

传统的 ReAct 循环让 LLM 边想边做，遇到复杂任务容易迷失方向或遗漏步骤。Plan & Execute 强制「先规划、再执行、后验证」三阶段分离：
- **Planner** 有独立的上下文窗口来思考全局策略
- **Actor** 只关注执行，不被规划分心
- **Verifier** 以怀疑者视角审视成果，不信任 Actor 的自我报告

### 自愈式重试循环

Verifier 不是简单地说「通过/失败」，而是输出具体的 `recommended_next_instruction`（失败原因 + 修复建议）。Planner 在修订轮把这些反馈转化为新的 todo，Actor 再执行。这形成了一个 **带反馈的自愈循环**，最多重试 `max_attempts` 次。

### 所有工具路径沙箱化

每个工具都接收 `workspace: Path` 参数，通过 `safe_path()` 确保所有文件操作都在 workspace 内。Agent 不能访问工作区外的文件。

### 重试轮 Actor 专用 Prompt

Actor 在重试轮收到的消息与首次不同：只列出未完成的 todo + Verifier 失败反馈，不会看到已完成 TODO 的完整上下文。这防止了 LLM 的"我上次做完了"惯性。

### final_node 不调用 LLM

Final 节点是纯文本格式化函数。因为 Planner / Actor / Verifier 都可能因为 LLM 调用异常而产出不完整的状态，final_node 保证无论什么情况都能生成一份可读的最终报告。

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
