"""One-shot: patch agent.py+nodes.py then test immediately."""
import sys, os

# ---- 1. Read all files ----
with open('src/mokioclaw/core/agent.py', 'r') as f:
    agent_lines = f.readlines()

with open('src/mokioclaw/graph/nodes.py', 'r') as f:
    nodes_text = f.read()

# ---- 2. Patch nodes.py ----
# Fix context_monitor_route
old_route = (
    '    if state.get("passed"):\n'
    '        return "final"\n'
    '    if state.get("context_should_compress"):\n'
    '        return "context_compressor"\n'
    '    return state.get("context_next_node", "verifier")'
)
new_route = (
    '    if state.get("passed"):\n'
    '        return "final"\n'
    '\n'
    '    # max_attempts cap\n'
    '    attempts = state.get("attempts", 0)\n'
    '    max_attempts = state.get("max_attempts", 3)\n'
    '    if attempts >= max_attempts:\n'
    '        return "final"\n'
    '\n'
    '    if state.get("context_should_compress"):\n'
    '        return "context_compressor"\n'
    '    return state.get("context_next_node", "verifier")'
)
nodes_text = nodes_text.replace(old_route, new_route)

# Fix verifier_node return: add context_next_node
old_vr = '        "messages": messages,\n    }'
new_vr = '        "messages": messages,\n        "context_next_node": "planner" if not passed else "verifier",\n    }'
nodes_text = nodes_text.replace(old_vr, new_vr)

with open('src/mokioclaw/graph/nodes.py', 'w') as f:
    f.write(nodes_text)
print(f"[nodes.py] max_attempts={'attempts >= max_attempts' in nodes_text}, ctx_next={'planner\" if not passed' in nodes_text}")

# ---- 3. Patch agent.py ----
# Replace lines 90-93 with delegation call
# Line 90 (0-indexed 89): comment line
# Line 91 (0-indexed 90): from ... import build_workflow
# Line 92 (0-indexed 91): blank
# Line 93 (0-indexed 92): graph = build_workflow()
agent_lines[89:93] = [
    '    # 4. 委托核心执行（复杂图，含 max_attempts 兜底）\n',
    '    yield from _stream_workflow_events(\n',
    '        inputs, runtime=runtime, task=task,\n',
    '        resumed=resumed, resume_event=resume_event,\n',
    '    )\n',
]

# Remove lines 94-172 (old inline loop: trace.start ... raise)
# Find the end of the old loop (the 'raise' inside KeyboardInterrupt)
for i in range(93, len(agent_lines)):
    if agent_lines[i].strip() == 'raise':
        del agent_lines[94:i+2]  # delete old loop AFTER the closing ')'
        break

# Add helper functions before the '# 内部辅助' section
helper_code = '''
# ═══════════════════════════════════════════════════════════════════
# 核心工作流执行（共享）
# ═══════════════════════════════════════════════════════════════════

def _stream_workflow_events(inputs, *, runtime, task, resumed=False, resume_event=None):
    manager = CheckpointManager(runtime, task=task)
    trace = TraceRecorder(runtime, task=task)
    trace.start(inputs, resumed=resumed, resume_event=resume_event)
    manager.save(inputs, status="started", latest_node="start")
    from mokioclaw.graph.workflow import build_complex_workflow
    graph = build_complex_workflow()
    latest_node, current_state = "start", dict(inputs)
    try:
        for event in graph.stream(inputs, stream_mode=["updates", "custom"], config={"recursion_limit": 50}):
            if isinstance(event, tuple) and len(event) == 2: mode, chunk = event
            else: mode, chunk = "updates", event
            if mode == "custom":
                ce = chunk if isinstance(chunk, dict) else {"data": chunk}
                trace.record_custom_event(ce)
                if isinstance(ce, dict) and ce.get("node"): latest_node = ce["node"]
                if _custom_event_needs_checkpoint(ce):
                    _merge_state(current_state, ce)
                    manager.save(current_state, status="running", latest_node=latest_node, event=ce)
                yield {"type": "custom_event", "event": ce}
            elif mode == "updates":
                for nn, no in chunk.items():
                    if nn == "__start__": continue
                    latest_node = nn
                    evt = _safe_event_dict(no, prefix=nn)
                    trace.record_graph_update(evt)
                    _merge_state(current_state, no)
                    manager.save(current_state, status="running", latest_node=nn, event=evt)
                    yield {"type": "graph_event", "event": {nn: no}}
        status = "completed" if current_state.get("passed", False) else "failed"
        manager.save(current_state, status=status, latest_node=latest_node)
        trace.end(status=status, latest_node=latest_node, final_state=current_state)
    except KeyboardInterrupt:
        manager.save(current_state, status="interrupted", latest_node=latest_node)
        trace.end(status="interrupted", latest_node=latest_node, final_state=current_state)
        raise
    return current_state


def _safe_event_dict(node_output, prefix=""):
    r = {"type": prefix}
    for k, v in node_output.items():
        if k in ("messages", "runtime"): continue
        if v is None or isinstance(v, (bool, int, float, str)): r[k] = v
        elif isinstance(v, (list, tuple)): r[k] = [i if isinstance(i, (bool, int, float, str, type(None))) else str(i)[:200] for i in list(v)[:20]]
        elif isinstance(v, dict): r[k] = {str(k2): _safe_value(v2) for k2, v2 in v.items()}
        else: r[k] = str(v)[:500]
    return r

def _safe_value(v):
    if v is None or isinstance(v, (bool, int, float, str)): return v
    if isinstance(v, (list, tuple)): return [_safe_value(x) for x in list(v)[:10]]
    if isinstance(v, dict): return {str(k): _safe_value(x) for k, x in list(v.items())[:20]}
    return str(v)[:200]

def _truncate_text(text, limit):
    if not text: return ""
    if len(text) <= limit: return text
    if limit <= 3: return text[:limit]
    return text[:limit - 3] + "..."


'''

# Find '# 内部辅助' section marker
insert_pos = None
for i, line in enumerate(agent_lines):
    if '# 内部辅助' in line:
        # Go back to find the ═══ line
        for j in range(i-1, 0, -1):
            if agent_lines[j].startswith('# ═'):
                insert_pos = j
                break
        break

if insert_pos:
    agent_lines.insert(insert_pos, helper_code)

agent_text = ''.join(agent_lines)

with open('src/mokioclaw/core/agent.py', 'w') as f:
    f.write(agent_text)
print(f"[agent.py] _stream_workflow_events={'_stream_workflow_events' in agent_text}, build_workflow={'build_workflow' in agent_text}, _safe_event_dict={'_safe_event_dict' in agent_text}")

# ---- 4. Clear cache and test ----
for mod in list(sys.modules.keys()):
    if 'mokioclaw' in mod:
        del sys.modules[mod]

sys.path.insert(0, 'src')

from mokioclaw.cli.app import app
from typer.testing import CliRunner

runner = CliRunner()
result = runner.invoke(app, ["test"])

for line in result.stdout.split('\n'):
    if any(kw in line for kw in ['尝试次数', 'max attempts', '最终结果', 'PASSED', 'FAILED', '验证通过', '验证失败']):
        print(f'>>> {line.strip()}')
