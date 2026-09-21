"""Real app render paths survive pending full-render / Scanner timer overlap.

Providers are offline fixtures. Actual ForwardMsgQueue pruning is exercised;
Tree mirrors frontend path traversal and full-run stale-node removal.
"""
import copy
import inspect
import unittest
from unittest.mock import patch
from streamlit.runtime.forward_msg_queue import ForwardMsgQueue
from streamlit.runtime.fragment import MemoryFragmentStorage
from streamlit.testing.v1.local_script_runner import LocalScriptRunner
import rendering_regression_check as rendering

class Tree:
    def __init__(self, run='', element=False, children=None):
        self.run, self.element, self.children = run, element, children or []
    def put(self, path, value, run):
        if self.element:
            raise ValueError("'setIn' cannot be called on an ElementNode")
        index, *rest = path
        if rest:
            self.children[index].put(rest, value, run)
        elif index == len(self.children):
            self.children.append(value)
        else:
            old = self.children[index]
            if not old.element and not value.element:
                value.children = old.children
            self.children[index] = value
        self.run = run
    def prune(self, run):
        self.children = [n for n in self.children if n.run == run]
        for child in self.children:
            child.prune(run)
    def apply(self, message, run):
        if message.WhichOneof('type') != 'delta': return
        kind = message.delta.WhichOneof('type')
        if kind not in ('add_block', 'new_element'):
            raise AssertionError('Unhandled delta: ' + str(kind))
        self.put(list(message.metadata.delta_path), Tree(run, kind == 'new_element'), run)

class FragmentRaceTests(unittest.TestCase):
    tearDown = rendering.RenderingTests.tearDown
    clean = rendering.RenderingTests.clean
    app = rendering.RenderingTests.app
    analyze = rendering.RenderingTests.analyze
    def test_scanner_tick_before_full_render_delivery_keeps_quote_path_valid(self):
        captures, names = [], {}
        original_run = LocalScriptRunner.run
        original_register = MemoryFragmentStorage.register
        def run(runner, *args, **kwargs):
            result = original_run(runner, *args, **kwargs)
            captures.append(copy.deepcopy(runner.forward_msgs()))
            return result
        def register(storage, key, function, **kwargs):
            real = inspect.getclosurevars(function).nonlocals.get('non_optional_func')
            names[key] = getattr(real, '__qualname__', '')
            return original_register(storage, key, function, **kwargs)
        with patch.object(LocalScriptRunner, 'run', run), patch.object(MemoryFragmentStorage, 'register', register):
            at = self.app('success')
            scanner = captures[-1]
            self.analyze(at)
            at.run(); self.clean(at)
            analyzer = captures[-1]
        monitor_id = next(k for k,v in names.items() if v == '_workspace_scanner_monitor')
        quote_id = next(k for k,v in names.items() if '_render_fast_live_tape' in v)
        root = Tree(children=[Tree() for _ in range(4)])
        for msg in scanner: root.apply(msg, 'scanner')
        queue = ForwardMsgQueue()
        for msg in analyzer: queue.enqueue(msg)
        queue.clear(retain_lifecycle_msgs=True, fragment_ids_this_run=[monitor_id])
        for msg in queue.flush(): root.apply(msg, 'analyzer')
        for child in root.children: child.prune('analyzer')
        monitor = [m for m in analyzer if m.WhichOneof('type') == 'delta' and m.delta.fragment_id == monitor_id]
        quote = [m for m in analyzer if m.WhichOneof('type') == 'delta' and m.delta.fragment_id == quote_id]
        self.assertTrue(monitor); self.assertTrue(quote)
        for msg in monitor: root.apply(msg, 'monitor-tick')
        for msg in quote: root.apply(msg, 'quote-tick')
        self.assertEqual(at.session_state['result']['symbol'], 'BNC')

if __name__ == '__main__': unittest.main()
