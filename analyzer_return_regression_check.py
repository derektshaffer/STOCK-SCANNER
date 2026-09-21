"""One navigation widget event must complete one canonical Scanner transition.

Real app/UI with offline providers. No replacement navigation implementation.
"""
import unittest
from unittest.mock import patch
from streamlit.runtime.state.session_state import SessionState
import rendering_regression_check as rendering

class OneClickReturnTests(unittest.TestCase):
    app = rendering.RenderingTests.app
    clean = rendering.RenderingTests.clean
    analyze = rendering.RenderingTests.analyze
    tearDown = rendering.RenderingTests.tearDown

    def exercise(self, enabled, panels):
        at = self.app('success')
        at.toggle(key='auto_scan_enabled').set_value(enabled).run()
        for panel in panels:
            self.analyze(at)
            self.assertEqual(at.session_state['app_view'], 'Stock Analyzer')
            self.assertEqual(at.session_state['result']['symbol'], 'BNC')
            # AppTest does not expose a pills interaction adapter.
            at.session_state['analyzer_detail'] = panel
            at.run()
            self.assertTrue(any(h.value == panel for h in at.get('subheader')))
            self.clean(at)
            writes = []
            original = SessionState.__setitem__
            def write(state, key, value):
                if key in ('app_view', '_pending_app_view', '_rendered_app_view'):
                    writes.append((key, value))
                return original(state, key, value)
            with patch.object(SessionState, '__setitem__', write):
                # Exactly one widget event. No retry or second selection.
                at.radio(key='app_view').set_value('Momentum Scanner').run()
                self.clean(at)
                self.assertEqual(at.session_state['app_view'], 'Momentum Scanner')
                self.assertEqual(at.session_state['_rendered_app_view'], 'Momentum Scanner')
                self.assertEqual(at.toggle(key='auto_scan_enabled').value, enabled)
                self.assertTrue(any('Scan summary' in c.value for c in at.caption))
                self.assertFalse(any(h.value == panel for h in at.get('subheader')))
                # Later complete renders must not reconstruct Analyzer from
                # its retained symbol/cache or request another transition.
                for _ in range(2):
                    at.run(); self.clean(at)
                    self.assertEqual(at.session_state['app_view'], 'Momentum Scanner')
            self.assertEqual(writes.count(('_rendered_app_view', 'Momentum Scanner')), 1)
            self.assertFalse(any(value == 'Stock Analyzer' for key,value in writes))

    def test_one_event_returns_after_lower_menu_with_auto_scan_on(self):
        self.exercise(True, ['Setup & timeframe'])

    def test_one_event_returns_after_lower_menu_with_auto_scan_off(self):
        self.exercise(False, ['Patterns'])

    def test_repeated_opens_do_not_add_navigation_authorities(self):
        self.exercise(True, ['Patterns', 'Sources', 'Scenarios', 'Setup & timeframe'])

if __name__ == '__main__': unittest.main()
