"""One-shot: patch + test with debug traces."""
import sys, os
sys.path.insert(0, 'src')

# Clear all mokioclaw cache
for mod in list(sys.modules.keys()):
    if 'mokioclaw' in mod:
        del sys.modules[mod]

# ---- Patch nodes.py ----
with open('src/mokioclaw/graph/nodes.py', 'r') as f:
    nc = f.read()

# Fix context_monitor_route with debug
old_r = ('    if state.get("passed"):\n'
         '        return "final"\n'
         '    if state.get("context_should_compress"):\n'
         '        return "context_compressor"\n'
         '    return state.get("context_next_node", "verifier")')
new_r = (
    '    if state.get("passed"):\n'
    '        return "final"\n'
    '\n'
    '    attempts = state.get("attempts", 0)\n'
    '    max_attempts = state.get("max_attempts", 3)\n'
    '    print(f"[ROUTE #{state.get(\'attempts\',0)}] attempts={attempts} max={max_attempts} passed={state.get(\'passed\')} next={state.get(\'context_next_node\', \'?\')}", file=sys.stderr)\n'
    '    if attempts >= max_attempts:\n'
    '        print(f"[ROUTE] CAP HIT -> final", file=sys.stderr)\n'
    '        return "final"\n'
    '\n'
    '    if state.get("context_should_compress"):\n'
    '        return "context_compressor"\n'
    '    return state.get("context_next_node", "verifier")')
nc = nc.replace(old_r, new_r)

# Fix verifier_node return
old_vr = '        "messages": messages,\n    }'
new_vr = '        "messages": messages,\n        "context_next_node": "planner" if not passed else "verifier",\n    }'
nc = nc.replace(old_vr, new_vr)

with open('src/mokioclaw/graph/nodes.py', 'w') as f:
    f.write(nc)

# ---- Patch agent.py ----
with open('src/mokioclaw/core/agent.py', 'r') as f:
    ac = f.read()

old_bw = ('    from mokioclaw.graph.workflow import build_workflow\n'
          '\n'
          '    graph = build_workflow()')
new_bw = ('    print("[AGENT] Delegating to _stream_workflow_events (build_complex_workflow)", file=sys.stderr)\n'
          '    yield from _stream_workflow_events(\n'
          '        inputs, runtime=runtime, task=task,\n'
          '        resumed=resumed, resume_event=resume_event,\n'
          '    )')
ac = ac.replace(old_bw, new_bw)

# Remove old inline loop
old_loop_start = '    # 5. 记录 start 事件 + 初始检查点'
old_loop_end = '        raise\n'
if old_loop_start in ac and old_loop_end in ac:
    i1 = ac.index(old_loop_start)
    i2 = ac.index(old_loop_end, i1) + len(old_loop_end)
    ac = ac[:i1] + ac[i2:]

# Insert helpers
helpers = '''
def _stream_workflow_events(inputs, *, runtime, task, resumed=False, resume_event=None):
    manager = CheckpointManager(runtime, task=task)
    trace = TraceRecorder(runtime, task=task)
    trace.start(inputs, resumed=resumed, resume_event=resume_event)
    manager.save(inputs, status="started", latest_node="start")
    from mokioclaw.graph.workflow import build_complex_workflow
    print("[AGENT] build_complex_workflow() compiled", file=sys.stderr)
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
                    if nn == "verifier":
                        print(f"[AGENT] verifier_node output: attempts={no.get('attempts','?')} passed={no.get('passed','?')} ctx_next={no.get('context_next_node','?')}", file=sys.stderr)
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
old_marker = '# ═' + '═' * 70 + '\n# 内部辅助'
# Find the marker more robustly
if '# 内部辅助' in ac:
    idx = ac.index('# 内部辅助')
    # Find the line start
    line_start = ac.rfind('\n', 0, idx)
    section_start = ac.rfind('# ═', 0, line_start)
    ac = ac[:section_start] + helpers + '\n' + ac[section_start:]

with open('src/mokioclaw/core/agent.py', 'w') as f:
    f.write(ac)

# ---- Reimport all ----
for mod in list(sys.modules.keys()):
    if 'mokioclaw' in mod:
        del sys.modules[mod]

# ---- Run test ----
from mokioclaw.cli.app import app
from typer.testing import CliRunner

print("=" * 50, file=sys.stderr)
print("TEST START", file=sys.stderr)
print("=" * 50, file=sys.stderr)

runner = CliRunner()
result = runner.invoke(app, ["hi"])

print(file=sys.stderr)
print("=" * 50, file=sys.stderr)
print("TEST OUTPUT:", file=sys.stderr)
for line in result.stdout.split('\n'):
    if any(kw in line for kw in ['尝试次数', 'max attempts', '最终结果', 'PASSED', 'FAILED']):
        print(line.strip())
