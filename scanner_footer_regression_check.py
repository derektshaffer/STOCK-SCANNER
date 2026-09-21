"""Offline summary lifecycle regressions; no credentials or provider requests."""
import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
TREE = ast.parse((ROOT / 'app.py').read_text())
HELPER = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == '_scanner_snapshot_summary')
SCOPE = {}
exec(compile(ast.Module(body=[HELPER], type_ignores=[]), 'app.py', 'exec'), SCOPE)
summary = SCOPE['_scanner_snapshot_summary']


class FooterTests(unittest.TestCase):
    def test_new_committed_generation_replaces_previous(self):
        self.assertIn('snapshot first', summary({'scan_time_et': 'first', 'candidates': [{}]}, 1))
        text = summary({'scan_time_et': 'second', 'candidates': [{}, {}]}, 2)
        self.assertIn('2 shown · 2 saved candidates · snapshot second', text)
        self.assertNotIn('first', text)

    def test_na_and_real_zero_are_rows_not_valid_price_statistics(self):
        payload = {'scan_time_et': 'generation', 'candidates': [
            {'symbol': 'NA', 'day_pct': None, 'live_price_available': False},
            {'symbol': 'ZERO', 'day_pct': 0.0, 'live_price_available': True}],
            'records': [{}] * 99}
        before = copy.deepcopy(payload)
        self.assertIn('2 shown · 2 saved candidates', summary(payload, 2))
        self.assertEqual(payload, before)

    def test_filtered_count_and_saved_count_are_distinct(self):
        self.assertIn('1 shown · 3 saved candidates', summary({'candidates': [{}, {}, {}]}, 1))

    def test_offhours_generation_and_missing_snapshot(self):
        self.assertIn('snapshot daily', summary({'generated_at_et': 'daily'}, 0))
        self.assertEqual(summary({}, 0), 'Scan summary · no saved scanner snapshot yet.')
        self.assertIn('snapshot unknown', summary({'candidates': [{}]}, 1))

    def test_summary_has_no_reads_providers_scans_or_session_cache(self):
        calls = {ast.unparse(n.func) for n in ast.walk(HELPER) if isinstance(n, ast.Call)}
        self.assertEqual(calls, {'payload.get', 'len'})

    def test_cards_and_summary_share_timer_and_source_object(self):
        fragment = next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)
                        and n.name == '_render_compact_scanner_candidates')
        self.assertEqual(ast.unparse(fragment.decorator_list[0]), 'st.fragment(run_every=_candidate_refresh_every)')
        calls = [n for n in ast.walk(fragment) if isinstance(n, ast.Call)]
        summaries = [n for n in calls if ast.unparse(n.func) == '_scanner_snapshot_summary']
        self.assertEqual(len(summaries), 1)
        self.assertEqual(ast.unparse(summaries[0].args[0]), 'offhours_source_payload if offhours_candidates else live_scan_payload')
        self.assertEqual(ast.unparse(summaries[0].args[1]), 'len(candidates)')
        self.assertEqual(sum(ast.unparse(n.func) == '_read_latest_scan_payload' for n in calls), 1)

    def test_legacy_quick_mode_does_not_read_another_snapshot(self):
        tree = ast.parse((ROOT / 'scanner_app.py').read_text())
        renderer = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'render_scanner_results')
        quick = next(n for n in ast.walk(renderer) if isinstance(n, ast.If) and ast.unparse(n.test) == 'not show_details')
        self.assertFalse(any(isinstance(n, ast.Call) for n in ast.walk(quick)))

    def test_normal_rerun_updates_cards_and_footer_without_reload_or_provider_call(self):
        from streamlit.testing.v1 import AppTest
        import datetime as dt
        original_datetime = dt.datetime
        source = (ROOT / 'tests/rendering_fixture.py').read_text()
        source = source.replace('ROOT=Path(__file__).resolve().parents[1]', f'ROOT=Path({str(ROOT)!r})')
        source = source.replace('return json.dumps(snapshot())', '''
        payload=snapshot()
        generation=st.session_state.get('footer_generation', 1)
        payload['scan_time_et']=f'2026-09-11T15:59:{generation:02d}+00:00'
        payload['candidates']=payload['candidates'][:generation]
        return json.dumps(payload)''')
        try:
            at = AppTest.from_string(source, default_timeout=30).run()
            self.assertFalse(at.exception)
            self.assertTrue(any('1 shown · 1 saved candidates · snapshot 2026-09-11T15:59:01' in c.value for c in at.caption))
            at.session_state['footer_generation'] = 2
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(len([m for m in at.markdown if 'data-symbol="' in m.value]), 2)
            self.assertTrue(any('2 shown · 2 saved candidates · snapshot 2026-09-11T15:59:02' in c.value for c in at.caption))
            self.assertFalse(any('snapshot 2026-09-11T15:59:01' in c.value for c in at.caption))
        finally:
            dt.datetime = original_datetime


if __name__ == '__main__':
    unittest.main()
