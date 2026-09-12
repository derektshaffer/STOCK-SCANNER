"""Offline live-price regressions. No model, holdout, strategy, or order work."""
import copy
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from live_price_quality import (price_view, stream_price_fields, provider_problem,
                                tradier_price_candidates, select_freshest_live_price, is_price_failure)

NOW = datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc)
def stamp(age=1, now=NOW): return (now-timedelta(seconds=age)).isoformat()
def record(age=1, source="tradier_consolidated_trade"):
    return dict(symbol="TEST", price=10, live_price_source=source, live_price_timestamp=stamp(age), live_price_available=True)
def stream(age=1, status="streaming", feed="iex"):
    return dict(symbol="TEST", status=status, feed=feed,
                last_trade=dict(price=10, timestamp=stamp(age)),
                last_quote=dict(bid=9.99, ask=10.01, timestamp=stamp(age)))
def snapshot(age=1):
    return dict(symbol="TEST", latestTrade=dict(p=10,t=stamp(age)), latestQuote=dict(bp=9.99,ap=10.01,t=stamp(age)))
def quote(age=1):
    return dict(symbol="TEST",last=10,bid=9.99,ask=10.01,trade_date=stamp(age),bid_date=stamp(age),ask_date=stamp(age),
                prevclose=9,high=11,low=8,volume=100000,average_volume=20000)


class PriceReliabilityTests(unittest.TestCase):
    def test_analyzer_auth_failure_blocks_before_plan(self):
        import consistency_regression_check as cr
        sa=cr.sa
        cr._install_common_analyzer_stubs()
        with patch.object(sa,'get_tradier_quotes',side_effect=RuntimeError('403 entitlement denied')), \
             patch.object(sa,'snapshot',side_effect=RuntimeError('401 unauthorized')), \
             patch.object(sa,'market_session_phase',return_value='regular'), \
             patch.object(sa,'build_trade_plan') as plan:
            with self.assertRaisesRegex(RuntimeError,'LIVE PRICE UNAVAILABLE.*FEED PERMISSION ERROR'):
                sa.analyze('TEST')
            plan.assert_not_called()

    def test_analyzer_auth_failure_preserves_valid_alternate_provider(self):
        import consistency_regression_check as cr
        sa=cr.sa
        cr._install_common_analyzer_stubs()
        with patch.object(sa,'get_tradier_quotes',side_effect=RuntimeError('403 entitlement denied')), \
             patch.object(sa,'latest_session_bars',return_value=cr._regular_bars()):
            result=sa.analyze('TEST')
        view=price_view(result)
        self.assertEqual(view['state'],'FALLBACK')
        self.assertTrue(view['source'].startswith('alpaca_'))
        self.assertEqual(view['errors'][0]['state'],'FEED PERMISSION ERROR')

    def test_primary_fresh_live(self):
        self.assertEqual(price_view(record(),now=NOW)["state"],"LIVE")

    def test_auth_errors_without_fallback(self):
        for code in (401,403):
            failure=provider_problem(HTTPError('https://example.invalid',code,'denied',None,None),'alpaca')
            fields=stream_price_fields({},dict(stream(),status='error',error=str(code)), 'alpaca',feed='sip',now=NOW)
            self.assertEqual(failure['state'],'FEED PERMISSION ERROR')
            self.assertEqual(fields['live_price_state'],'FEED PERMISSION ERROR')
            self.assertIsNone(fields['price'])

    def test_permission_failure_with_fresh_permitted_rest(self):
        fields=stream_price_fields({'symbol':'TEST'},dict(stream(),status='connection_limit',error='403 SIP entitlement'),
                                   'alpaca',feed='sip',rest=snapshot(),now=NOW)
        view=price_view(fields,now=NOW)
        self.assertEqual(view['state'],'FALLBACK')
        self.assertEqual(view['source'],'alpaca_sip_rest_trade')
        self.assertEqual(view['errors'][0]['state'],'FEED PERMISSION ERROR')

    def test_stale_fallback_is_not_current(self):
        fields=stream_price_fields({'symbol':'TEST'},stream(status='connection_limit'), 'alpaca',feed='iex',rest=snapshot(121),now=NOW)
        self.assertIsNone(fields['price'])
        self.assertEqual(fields['live_price_state'],'STALE')
        self.assertEqual(price_view(fields,now=NOW)['state'],'STALE')

    def test_cached_price_reaged_not_frozen_age(self):
        row=dict(record(),live_price_age_seconds=1)
        self.assertEqual(price_view(row,now=NOW+timedelta(seconds=121))['state'],'STALE')

    def test_premarket_and_afterhours_fallback(self):
        for hour in (9,23):
            now=NOW.replace(hour=hour)
            r=dict(record(source='tradier_consolidated_timesales_bar'),live_price_timestamp=stamp(2,now))
            self.assertEqual(price_view(r,now=now)['state'],'FALLBACK')

    def test_extended_hours_stale_fallback(self):
        for hour in (9,23):
            now=NOW.replace(hour=hour)
            r=dict(record(source='tradier_consolidated_timesales_bar'),live_price_timestamp=stamp(121,now))
            self.assertEqual(price_view(r,now=now)['state'],'STALE')

    def test_timeout_and_disconnected_discard_cached_trade(self):
        for status,error in [('error','request timed out'),('disconnected',None)]:
            s=dict(stream(),status=status,error=error)
            fields=stream_price_fields(record(),s,'tradier',now=NOW)
            self.assertIsNone(fields['price'])
            self.assertIn(fields['live_price_provider_errors'][0]['state'],('TIMEOUT','DISCONNECTED'))

    def test_scanner_analyzer_same_quotes_and_classification(self):
        with patch.dict(os.environ, {"TRADIER_ACCESS_TOKEN":"offline-test-only"}):
            import stock_scanner as ss
        import stock_analyzer as sa
        for q in (quote(),dict(quote(),trade_date=stamp(300))):
            with patch.object(ss,'USE_TRADIER',True):
                scanner=ss.analyze_snapshot('TEST',q,{},now_utc=NOW)
            selected,_=select_freshest_live_price('TEST',sa._tradier_price_candidates('TEST',q,[]),now=NOW)
            analyzer=dict(symbol='TEST',price=selected['price'],live_price_source=selected['source'],
                          live_price_timestamp=selected['timestamp'],live_price_is_fallback=selected['kind']!='trade')
            for key in ('state','source','timestamp','age_seconds','price'):
                self.assertEqual(price_view(scanner,now=NOW)[key],price_view(analyzer,now=NOW)[key])

    def test_scanner_missing_primary_labels_fresh_alpaca_trade_fallback(self):
        with patch.dict(os.environ, {"TRADIER_ACCESS_TOKEN":"offline-test-only"}):
            import stock_scanner as ss
        snap=dict(snapshot(),latestQuote={},dailyBar=dict(h=11,l=8,v=100000,vw=9.5),prevDailyBar={'c':9})
        with patch.object(ss,'USE_TRADIER',True),patch.object(ss,'LIVE_FEED','iex'):
            result=ss.analyze_snapshot('TEST',None,snap,now_utc=NOW)
        self.assertEqual(price_view(result,now=NOW)['state'],'FALLBACK')
        self.assertEqual(result['live_quote_source'],'alpaca_iex_fallback')

    def test_stream_connection_cannot_label_stale_price_live(self):
        from live_tape_ui import render_live_tape
        class UI:
            def __init__(self):self.text=[]
            def markdown(self,text,**kwargs):self.text.append(text)
            def caption(self,text):self.text.append(text)
        ui=UI()
        with patch('live_tape_ui.price_view',side_effect=lambda r:price_view(r,now=NOW)):
            render_live_tape(ui,dict(record(600),status='streaming',feed='SIP'))
        html=' '.join(ui.text)
        self.assertIn('STALE',html)
        self.assertNotIn('SIP LIVE',html)
        self.assertNotIn('LIVE PRICE',html)

    def test_failed_feed_cannot_borrow_other_provider_provenance(self):
        other=dict(record(source='tradier_consolidated_trade'),bid=20,ask=21,vwap=20)
        fields=stream_price_fields(other,dict(stream(),last_trade={},last_quote={}), 'alpaca',feed='sip',now=NOW)
        self.assertIsNone(fields['price'])
        self.assertIsNone(fields['bid'])

    def test_new_rest_failure_does_not_reuse_old_payload(self):
        import alpaca_live_stream as als
        with patch.dict(als._REST_FALLBACK,dict(symbol='TEST',feed='sip',last_poll=0,payload=snapshot(),error=None)), \
             patch.object(als.urllib.request,'urlopen',side_effect=HTTPError('https://example.invalid',403,'denied',None,None)):
            payload,error=als._rest_snapshot('TEST','sip')
            self.assertIsNone(payload)
            self.assertEqual(error,'FEED PERMISSION ERROR')
            self.assertIsNone(als._REST_FALLBACK['payload'])

    def test_failed_rest_symbol_switch_cannot_relabel_old_payload(self):
        import alpaca_live_stream as als
        with patch.dict(als._REST_FALLBACK,dict(symbol='OLD',feed='iex',last_poll=0,payload=snapshot(),error=None)), \
             patch.object(als.urllib.request,'urlopen',side_effect=TimeoutError()):
            payload,error=als._rest_snapshot('NEW','sip')
            self.assertIsNone(payload)
            self.assertEqual(error,'TIMEOUT')

    def test_stale_quote_side_rejects_midpoint(self):
        q=dict(quote(),trade_date=stamp(300),bid_date=stamp(300),ask_date=stamp(1))
        selected,_=select_freshest_live_price('TEST',tradier_price_candidates('TEST',q),now=NOW)
        self.assertIsNone(selected)

    def test_missing_quote_side_time_rejects_midpoint(self):
        q=dict(quote(),trade_date=stamp(300),bid_date=None)
        self.assertIsNone(select_freshest_live_price('TEST',tradier_price_candidates('TEST',q),now=NOW)[0])

    def test_future_quote_side_rejects_midpoint(self):
        q=dict(quote(),trade_date=stamp(300),ask_date=stamp(-16))
        self.assertIsNone(select_freshest_live_price('TEST',tradier_price_candidates('TEST',q),now=NOW)[0])

    def test_no_ambiguous_delayed_source(self):
        for source in ('unknown','delayed_sip','historical_close','alpaca_badfeed_trade'):
            self.assertFalse(price_view(record(source=source),now=NOW)['current'])

    def test_missing_or_future_time_fails(self):
        for timestamp in (None,stamp(-16),'not-a-date'):
            self.assertFalse(price_view(dict(record(),live_price_timestamp=timestamp),now=NOW)['current'])

    def test_cross_symbol_stream_and_rest_rejected(self):
        fields=stream_price_fields({'symbol':'TEST'},dict(stream(),symbol='WRONG'),'alpaca',feed='sip',now=NOW)
        self.assertIsNone(fields['price'])
        stale=stream_price_fields({'symbol':'TEST'},dict(stream(300),symbol='WRONG'),'alpaca',feed='sip',now=NOW)
        self.assertIsNone(stale.get('last_known_price'))
        fields=stream_price_fields({'symbol':'TEST'},stream(status='connection_limit'),'alpaca',feed='sip',rest=dict(snapshot(),symbol='WRONG'),now=NOW)
        self.assertIsNone(fields['price'])

    def test_sip_failure_200_response_remains_permission_error(self):
        import tradier_live as tl
        with patch.object(tl,'_request_form_json',return_value={'errors':{'error':'SIP entitlement permission denied'}}):
            with self.assertRaisesRegex(RuntimeError,'FEED PERMISSION ERROR'):tl.post_quotes(['TEST'],'synthetic')

    def test_malformed_provider_response_identifiable(self):
        import tradier_live as tl
        with patch.object(tl,'_request_form_json',return_value=[]):
            with self.assertRaisesRegex(RuntimeError,'Malformed'):tl.post_quotes(['TEST'],'synthetic')

    def test_malformed_stream_revokes_old_price(self):
        import tradier_live_stream as ts
        s=ts._TradierStream();s.symbol='TEST'
        s.state.update(stream())
        s._handle_message(s.generation,'not JSON')
        self.assertEqual(s.state['status'],'error')
        fields=stream_price_fields(record(),s.state,'tradier',now=NOW)
        self.assertIsNone(fields['price'])
        self.assertEqual(fields['live_price_provider_errors'][0]['state'],'MALFORMED RESPONSE')

    def test_failure_categories_redact_response_details(self):
        failure=provider_problem('403 secret=should-not-persist','alpaca')
        self.assertNotIn('should-not-persist',str(failure))
        self.assertEqual(failure['state'],'FEED PERMISSION ERROR')

    def test_cache_invalidation_includes_provider_errors(self):
        for error in ('401 Unauthorized','403 SIP entitlement','socket disconnected','request timeout','Malformed JSON','LIVE PRICE UNAVAILABLE'):
            self.assertTrue(is_price_failure(error))
        for file in ('app.py','analyzer_bootstrap.py','analyzer_ui_core.py'):
            self.assertIn('is_price_failure(',Path(file).read_text())

    def test_input_snapshots_not_mutated(self):
        r=record();s=stream();before=copy.deepcopy((r,s))
        price_view(r,now=NOW);stream_price_fields(r,s,'tradier',now=NOW)
        self.assertEqual((r,s),before)

    def test_declared_provider_cannot_borrow_price_source(self):
        self.assertFalse(price_view(dict(record(),market_provider='alpaca'),now=NOW)['current'])

    def test_router_keeps_permitted_fallback_identity(self):
        import live_market_stream as router
        r=dict(record(source='alpaca_sip_trade'),live_price_timestamp=stamp(now=datetime.now(timezone.utc)))
        fallback=dict(r,live_price_provider_errors=[])
        with patch.object(router,'tradier_configured',return_value=True), \
             patch.object(router,'get_tradier_overlay',return_value={'live_price_available':False,'live_price_provider_errors':[provider_problem('403','tradier')]}), \
             patch.object(router,'get_alpaca_overlay',return_value=fallback):
            result=router.get_live_overlay(r)
        self.assertEqual(result['provider'],'alpaca')
        self.assertEqual(result['live_price_source'],'alpaca_sip_trade')
        self.assertTrue(result['live_price_is_fallback'])
        self.assertEqual(result['live_price_provider_errors'][0]['state'],'FEED PERMISSION ERROR')

    def test_anonymous_stream_message_cannot_clear_permission_failure(self):
        import tradier_live_stream as ts
        s=ts._TradierStream();s.symbol='TEST'
        s.state.update(symbol='TEST',status='error',error='403 permission denied')
        s._handle_message(s.generation,'{"type":"trade","price":10,"date":1789138800000}')
        self.assertEqual(s.state['status'],'error')
        self.assertIsNone(s.state['last_trade'])


if __name__=='__main__':unittest.main()
