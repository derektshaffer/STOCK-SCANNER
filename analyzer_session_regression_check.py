"""Analyzer session lifecycle regressions; all providers are simulated."""
import unittest
import rendering_regression_check as rendering
from streamlit.testing.v1 import AppTest
from pathlib import Path
from unittest.mock import Mock, patch
import json, tempfile
import analyzer_launch_runtime as runtime


class SessionTests(unittest.TestCase):
    app = rendering.RenderingTests.app
    clean = rendering.RenderingTests.clean
    analyze = rendering.RenderingTests.analyze
    tearDown = rendering.RenderingTests.tearDown

    def test_ten_handoffs_preserve_symbol_return_and_single_launch(self):
        source = (Path(__file__).parent/'tests/rendering_fixture.py').read_text()
        source = source.replace('ROOT=Path(__file__).resolve().parents[1]', 'ROOT=Path(' + repr(str(Path(__file__).resolve().parent)) + ')')
        source = source.replace('def start(symbol,*args,**kwargs):', 'def start(symbol,*args,**kwargs):\n    st.session_state.setdefault("launches", []).append(symbol)')
        at = AppTest.from_string(source, default_timeout=30)
        at.session_state['fixture_mode'] = 'success'
        at.run()
        at.toggle(key='auto_scan_enabled').set_value(True).run()
        for index in range(10):
            symbol = ('BNC', 'PDSB', 'TEST')[index % 3]
            next(b for b in at.button if b.label == 'Analyze ' + symbol).click().run()
            self.clean(at)
            self.assertEqual(at.session_state['ticker'], symbol)
            self.assertEqual(at.session_state['result']['symbol'], symbol)
            self.assertFalse(at.session_state['_analyzer_loading'])
            self.assertIsNone(at.session_state['_analyzer_bootstrap_launch_state'])
            at.run()
            self.assertEqual(len(at.session_state['launches']), index+1)
            at.radio(key='app_view').set_value('Momentum Scanner').run()
            self.clean(at)
            self.assertTrue(at.toggle(key='auto_scan_enabled').value)
            self.assertTrue(any('Scan summary' in c.value for c in at.caption))

    def test_terminal_failure_is_visible_without_automatic_retry(self):
        at = self.app('failure')
        self.analyze(at)
        for _ in range(3):
            at.run(); self.clean(at)
            self.assertTrue(at.error)
            self.assertFalse(at.session_state['_analyzer_loading'])
            self.assertIsNone(at.session_state['_analyzer_bootstrap_launch_state'])
            self.assertNotIn('result', at.session_state)

    def test_provider_failure_categories_show_terminal_state_and_allow_return(self):
        messages = ["missing quote", "stale quote", "missing previous close",
                    "missing history", "empty candles", "malformed provider response",
                    "provider timeout", "unsupported symbol", "temporary API failure"]
        original = (Path(__file__).parent/'tests/rendering_fixture.py').read_text()
        original = original.replace('ROOT=Path(__file__).resolve().parents[1]',
                                    'ROOT=Path(' + repr(str(Path(__file__).resolve().parent)) + ')')
        for message in messages:
            with self.subTest(message=message):
                source = original.replace('LIVE PRICE UNAVAILABLE — fixture stale quote', message)
                at = AppTest.from_string(source, default_timeout=30)
                at.session_state['fixture_mode'] = 'failure'
                at.run(); self.analyze(at)
                self.assertTrue(any(message in e.value for e in at.error))
                self.assertFalse(at.session_state['_analyzer_loading'])
                self.assertIsNone(at.session_state['_analyzer_bootstrap_launch_state'])
                at.run(); self.clean(at)
                self.assertTrue(any(message in e.value for e in at.error))
                at.radio(key='app_view').set_value('Momentum Scanner').run()
                self.clean(at)
                self.assertTrue(any('Scan summary' in c.value for c in at.caption))

    def test_auto_scan_choice_survives_analyzer_and_return(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                at = self.app('success')
                at.toggle(key='auto_scan_enabled').set_value(enabled).run()
                self.analyze(at)
                at.run()
                self.assertEqual(at.session_state['auto_scan_enabled'], enabled)
                at.radio(key='app_view').set_value('Momentum Scanner').run()
                self.clean(at)
                self.assertEqual(at.toggle(key='auto_scan_enabled').value, enabled)


class WorkerContainmentTests(unittest.TestCase):
    def setUp(self):
        import importlib
        importlib.reload(runtime)

    def test_timeout_cancels_worker_and_returns_visible_error(self):
        process = Mock(); process.poll.return_value = None
        with patch.object(runtime.time, 'time', return_value=1000), patch.object(runtime, '_cleanup'):
            outcome = runtime.poll_analyzer_process(dict(process=process, started_at=1, timeout_seconds=180, symbol='TEST'))
        self.assertTrue(outcome['done']); self.assertFalse(outcome['ok'])
        self.assertIn('timeout', outcome['message']); process.terminate.assert_called_once()

    def test_absent_process_is_contained(self):
        self.assertFalse(runtime.poll_analyzer_process({})['ok'])

    def test_bad_worker_results_and_provider_failures_are_contained(self):
        cases = [None, [], {}, {'result': []}, {'error': 'provider timeout'},
                 {'error': 'missing candles'}, {'error': 'unsupported symbol'},
                 {'error': 'LIVE PRICE UNAVAILABLE — stale quote'}]
        for payload in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'result.json'
                path.write_text(json.dumps(payload))
                process = Mock(); process.poll.return_value = 1 if isinstance(payload, dict) and 'error' in payload else 0
                outcome = runtime.poll_analyzer_process(dict(process=process, result_path=str(path), symbol='TEST'))
                self.assertTrue(outcome['done']); self.assertFalse(outcome['ok'])
                self.assertTrue(outcome['message'])

    def test_valid_worker_result_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'result.json'
            path.write_text(json.dumps({'result': {'symbol': 'TEST', 'price': 10}}))
            process = Mock(); process.poll.return_value = 0
            outcome = runtime.poll_analyzer_process(dict(process=process, result_path=str(path), symbol='TEST'))
            self.assertTrue(outcome['ok']); self.assertEqual(outcome['result']['symbol'], 'TEST')
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
