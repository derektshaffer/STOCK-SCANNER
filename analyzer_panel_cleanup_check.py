"""Real panel deltas under the Streamlit timer/fragment completion race.

Providers are synthetic. The actual ForwardMsgQueue changes the pending parent
completion to EARLY_FOR_RERUN; Tree follows installed frontend replacement and
fragment cleanup semantics, including retention of same-type block children.
"""
import copy
import inspect
import unittest
from unittest.mock import patch
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from streamlit.runtime.forward_msg_queue import ForwardMsgQueue
from streamlit.runtime.fragment import MemoryFragmentStorage
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.local_script_runner import LocalScriptRunner
import rendering_regression_check as rendering
from analyzer_overview import DETAILS


class Tree:
    def __init__(self, run='', kind='block', type=None, fragment='', text='', children=None):
        self.run, self.kind, self.type, self.fragment, self.text = run, kind, type, fragment, text
        self.children = children or []
        self.id = ''

    def get(self, path):
        node = self
        for index in path:
            if node.kind != 'block' or index >= len(node.children): return None
            node = node.children[index]
        return node

    def put(self, path, value, run):
        if self.kind != 'block': raise AssertionError('setIn on ElementNode')
        index, *rest = path
        if rest:
            self.children[index].put(rest, value, run)
        elif index == len(self.children): self.children.append(value)
        else: self.children[index] = value
        self.run = run

    def apply(self, msg, run):
        if msg.WhichOneof('type') != 'delta': return
        delta = msg.delta
        kind = delta.WhichOneof('type')
        path = list(msg.metadata.delta_path)
        if kind == 'add_block':
            old = self.get(path)
            type = delta.add_block.WhichOneof('type')
            children = old.children if old and old.kind == 'block' and old.type == type else []
            node = Tree(run, type=type, fragment=delta.fragment_id, children=children)
            node.id = delta.add_block.id
        elif kind == 'new_element':
            type = delta.new_element.WhichOneof('type')
            content = getattr(delta.new_element, type)
            node = Tree(run, 'element', type, delta.fragment_id, getattr(content, 'body', ''))
        else: raise AssertionError('Unhandled delta: ' + str(kind))
        self.put(path, node, run)

    def clean(self, run, fragments, inside=None):
        if self.kind == 'element':
            return self if (self.type == 'toast' or (fragments and (not self.fragment or not inside or self.run == run)) or self.run == run) else None
        if fragments:
            if inside and self.run != run: return None
            if self.fragment in fragments and self.run == run: inside = self.fragment
        elif self.run != run: return None
        self.children = [n for child in self.children if (n := child.clean(run, fragments, inside)) is not None]
        return self

    def text_content(self):
        return self.text + '\n' + '\n'.join(c.text_content() for c in self.children)

    def panels(self):
        return ([self] if self.id.endswith('-ao_detail_content') else []) + [n for child in self.children for n in child.panels()]


class PanelCleanupTests(unittest.TestCase):
    tearDown = rendering.RenderingTests.tearDown

    def captures(self, panels, overrides=None):
        captures = []
        original = LocalScriptRunner.run
        original_register = MemoryFragmentStorage.register
        def register(storage, key, function, **kwargs):
            real = inspect.getclosurevars(function).nonlocals.get('non_optional_func')
            if getattr(real, '__qualname__', '') == '_workspace_scanner_monitor':
                self.monitor_id = key
            return original_register(storage, key, function, **kwargs)
        def run(runner, *args, **kwargs):
            result = original(runner, *args, **kwargs)
            captures.append(copy.deepcopy(runner.forward_msgs()))
            return result
        at = AppTest.from_file(str(rendering.ROOT/'tests/rendering_fixture.py'), default_timeout=90)
        at.query_params.update(overview='live', history='1')
        at.session_state['fixture_long_patterns'] = True
        with patch.object(LocalScriptRunner, 'run', run), patch.object(MemoryFragmentStorage, 'register', register):
            for index, panel in enumerate(panels):
                if index and overrides:
                    result = copy.deepcopy(at.session_state['result'])
                    result.update(overrides)
                    at.session_state['result'] = result
                at.session_state['analyzer_detail'] = panel
                at.run()
                self.assertFalse(at.exception, [e.message for e in at.exception])
        return captures

    def initial_tree(self, messages):
        tree = Tree(children=[Tree() for _ in range(4)])
        for msg in messages: tree.apply(msg, 'initial')
        return tree

    def raced_transition(self, tree, messages, run):
        parent = next(m.delta.fragment_id for m in messages if m.WhichOneof('type') == 'delta' and m.delta.WhichOneof('type') == 'add_block' and m.delta.add_block.id.endswith('-analyzer_live_fragment'))
        monitor = self.monitor_id
        queue = ForwardMsgQueue()
        for msg in messages:
            if msg.WhichOneof('type') == 'delta' and msg.delta.fragment_id == parent:
                queue.enqueue(copy.deepcopy(msg))
        queue.enqueue(ForwardMsg(script_finished=ForwardMsg.FINISHED_FRAGMENT_RUN_SUCCESSFULLY))
        queue.clear(retain_lifecycle_msgs=True, fragment_ids_this_run=[monitor])
        delivered = queue.flush()
        self.assertEqual(delivered[-1].script_finished, ForwardMsg.FINISHED_EARLY_FOR_RERUN)
        for msg in delivered: tree.apply(msg, run)
        # The early completion performs no cleanup. The following unrelated
        # timer only cleans its own fragment, leaving the parent tail untouched.
        tree.clean('timer-' + run, [monitor])

    def test_patterns_sources_news_remove_inactive_content_without_parent_cleanup(self):
        patterns, sources, news = self.captures(['Patterns', 'Sources', 'News'])
        tree = Tree(children=[Tree() for _ in range(4)])
        for msg in patterns: tree.apply(msg, 'patterns')
        self.assertIn('STAIR-STEP STATE', tree.text_content())
        self.raced_transition(tree, sources, 'sources')
        self.assertIn('Source observations at analysis time', tree.text_content())
        self.assertNotIn('STAIR-STEP STATE', tree.text_content())
        self.assertNotIn('DROP ≥5% AFTER BOUNCE', tree.text_content())
        self.raced_transition(tree, news, 'news')
        self.assertIn('Offline panel news marker', tree.text_content())
        self.assertNotIn('Source observations at analysis time', tree.text_content())
        self.assertNotIn('STAIR-STEP STATE', tree.text_content())

    def test_all_100_panel_transitions_match_a_fresh_render(self):
        captures = dict(zip(DETAILS, self.captures(DETAILS)))
        for previous in DETAILS:
            for selected in DETAILS:
                with self.subTest(previous=previous, selected=selected):
                    tree = self.initial_tree(captures[previous])
                    self.raced_transition(tree, captures[selected], selected)
                    expected = self.initial_tree(captures[selected])
                    self.assertEqual(len(tree.panels()), 0 if selected == 'Overview' else 1)
                    self.assertEqual([n.text_content() for n in tree.panels()], [n.text_content() for n in expected.panels()])

    def assert_empty_transition(self, selected, overrides, expected_text=None):
        previous, current = self.captures(['Patterns', selected], overrides)
        tree = self.initial_tree(previous)
        self.raced_transition(tree, current, selected)
        self.assertFalse('STAIR-STEP STATE' in tree.text_content(), 'prior Patterns body retained')
        self.assertEqual(len(tree.panels()), 1)
        if expected_text: self.assertIn(expected_text, tree.panels()[0].text_content())

    def test_sources_without_provider_fields(self):
        # Keep the usable quote required to enter Analyzer; omit optional
        # source observations rather than disabling the live freshness gate.
        self.assert_empty_transition('Sources', {k:None for k in ('reference_price_source','reference_price_timestamp','live_provider_error','alpaca_fallback_error','bid','ask','volume_source','historical_feed')}, 'Source observations')

    def test_empty_news_removes_patterns(self):
        self.assert_empty_transition('News', {'news':[]}, 'No recent Alpaca news returned.')

    def test_unavailable_patterns_removes_news(self):
        before, after = self.captures(['News', 'Patterns'], {'bounce_sequence':{}, 'stair_step':{}, 'impulse_pullback':{}})
        tree = self.initial_tree(before)
        self.raced_transition(tree, after, 'patterns')
        self.assertNotIn('Offline panel news marker', tree.text_content())
        self.assertEqual(len(tree.panels()), 1)

    def test_provider_auth_error_is_visible_and_contains_no_old_panel(self):
        self.assert_empty_transition('ML diagnostics', {'ml_prediction':{'status':'history_unavailable','error':'provider HTTP 401','bar_count':0}}, 'provider HTTP 401')


if __name__ == '__main__': unittest.main()
