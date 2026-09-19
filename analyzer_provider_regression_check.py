"""Provider failure and fallback checks using offline responses only."""
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
import live_price_quality as q
import stock_analyzer as sa
import tradier_live as tl

class ProviderChecks(unittest.TestCase):
    def test_safe_actionable_http_diagnostics(self):
        for status,phrase in [(401,'Authentication rejected'),(403,'Access rejected'),(429,'rate limit'),(503,'server error')]:
            err=q.ProviderHTTPError('Alpaca',status)
            detail=q.provider_failure_detail(err,'alpaca','sip')
            self.assertIn(str(status),detail);self.assertIn(phrase,detail)
        result=q.provider_failure_detail(RuntimeError('HTTP 401 secret=never-print-this'),'alpaca')
        self.assertNotIn('never-print',result)

    def test_alpaca_http_status_survives_sanitization(self):
        error=HTTPError('https://data.alpaca.markets/private',403,'sensitive-body',{},None)
        with patch.object(sa,'_headers',return_value={}),patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaises(q.ProviderHTTPError) as caught:sa.get_json('https://data.alpaca.markets/test')
        self.assertEqual(q.provider_problem(caught.exception,'alpaca')['http_status'],403)
        self.assertNotIn('sensitive',str(caught.exception))

    def test_stale_rejections_include_age_and_limit(self):
        err=q.LivePriceRejected(['alpaca_iex_trade: price is stale (4800s old)','alpaca_iex_quote_midpoint: price is stale (4790s old)'])
        detail=q.provider_failure_detail(err,'alpaca','iex')
        for word in ['STALE','4790s','120s','IEX']:self.assertIn(word,detail)

    def test_missing_keys_are_distinct(self):
        with patch.dict(os.environ,{'ALPACA_API_KEY':'','ALPACA_SECRET_KEY':''}):
            with self.assertRaises(RuntimeError) as caught:sa._headers()
        self.assertEqual(q.provider_problem(caught.exception,'alpaca')['state'],'MISSING CREDENTIALS')

    def test_tradier_timesales_rejects_wrong_symbol_and_fault(self):
        now=datetime.now(timezone.utc)
        for payload in [{'series':{'data':[{'symbol':'WRONG','price':10,'timestamp':now.timestamp()}]}}, {'fault':{'message':'permission denied'}}, {'series':[]}, {}]:
            with patch.object(tl,'_request_json',return_value=payload):
                with self.assertRaises(RuntimeError):tl.get_timesales_bars('BNC','fixture',now-timedelta(minutes=5),now)

    def test_empty_bars_do_not_discard_fresh_quote_or_build_plan(self):
        import consistency_regression_check as cr
        cr._install_common_analyzer_stubs()
        now=datetime.now(timezone.utc).isoformat()
        quote={'symbol':'BNC','last':10,'trade_date':now,'bid':9.99,'ask':10.01,'bid_date':now,'ask_date':now}
        with patch.object(sa,'USE_TRADIER',True),patch.object(sa,'get_tradier_quotes',return_value={'BNC':quote}),patch.object(sa,'_tradier_regular_session_bars',return_value=[]),patch.object(sa,'snapshot') as fallback,patch.object(sa,'build_trade_plan') as plan:
            with self.assertRaisesRegex(RuntimeError,'LIVE INTRADAY DATA UNAVAILABLE.*fresh validated price'):sa.analyze('BNC')
            fallback.assert_not_called();plan.assert_not_called()

    def test_three_tickers_stale_both_providers_fail_closed(self):
        import consistency_regression_check as cr
        cr._install_common_analyzer_stubs()
        stamp=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        for symbol in ['BNC','SPY','AAPL']:
            tq={'symbol':symbol,'last':10,'trade_date':stamp,'bid':9.99,'ask':10.01,'bid_date':stamp,'ask_date':stamp}
            ap={'symbol':symbol,'latestTrade':{'p':10,'t':stamp},'latestQuote':{'bp':9.99,'ap':10.01,'t':stamp}}
            with patch.object(sa,'USE_TRADIER',True),patch.object(sa,'market_session_phase',return_value='afterhours'),patch.object(sa,'get_tradier_quotes',return_value={symbol:tq}),patch.object(sa,'_tradier_regular_session_bars',return_value=[]),patch.object(sa,'snapshot',return_value=ap),patch.object(sa,'build_trade_plan') as plan:
                with self.assertRaisesRegex(RuntimeError,'LIVE PRICE UNAVAILABLE.*STALE.*IEX.*120s'):sa.analyze(symbol)
                plan.assert_not_called()

    def test_partial_worker_credentials_clear_alias(self):
        import analyzer_launch_runtime as runtime
        with patch.dict(os.environ,{'TRADIER_ACCESS_TOKEN':'old','TRADIER_TOKEN':'old-alias'}),patch.object(runtime.subprocess,'Popen') as start:
            start.return_value.pid=12345
            result=runtime.start_analyzer_process('BNC',tradier_token='')
            env=start.call_args.kwargs['env']
            self.assertEqual(env['TRADIER_ACCESS_TOKEN'],'');self.assertNotIn('TRADIER_TOKEN',env)
            runtime._cleanup(result)

    def test_quotes_failure_can_use_valid_matching_timesales(self):
        import consistency_regression_check as cr
        cr._install_common_analyzer_stubs()
        rows=[dict(row,symbol='BNC') for row in cr._fresh_intraday_bars(10)]
        with patch.object(sa,'USE_TRADIER',True),patch.object(sa,'get_tradier_quotes',return_value={}),patch.object(sa,'_tradier_regular_session_bars',return_value=rows),patch.object(sa,'snapshot') as fallback:
            result=sa.analyze('BNC')
            self.assertEqual(result['market_provider'],'tradier')
            self.assertEqual(result['live_price_source'],'tradier_consolidated_timesales_bar')
            self.assertIn('NO DATA',result['live_price_fallback_reason'])
            fallback.assert_not_called()

    def test_conflicting_alpaca_symbol_alias_cannot_be_accepted(self):
        now=datetime.now(timezone.utc)
        candidates=sa._provider_price_candidates('BNC',{'symbol':'BNC','S':'SPY','p':10,'t':now.isoformat()},{},provider='alpaca',feed='iex')
        selected,_=q.select_freshest_live_price('BNC',candidates,now=now)
        self.assertIsNone(selected)

    def test_slow_response_uses_receipt_clock(self):
        import consistency_regression_check as cr
        cr._install_common_analyzer_stubs()
        now=datetime.now(timezone.utc)
        class Clock(datetime):
            calls=0
            @classmethod
            def now(cls,tz=None):
                cls.calls+=1
                current=now-timedelta(seconds=30) if cls.calls==1 else now
                return current.astimezone(tz) if tz else current.replace(tzinfo=None)
        snap={'symbol':'BNC','latestTrade':{'p':10,'t':now.isoformat()},'latestQuote':{}}
        with patch.object(sa,'datetime',Clock),patch.object(sa,'USE_TRADIER',False),patch.object(sa,'snapshot',return_value=snap),patch.object(sa,'latest_session_bars',return_value=cr._fresh_intraday_bars(10,now)):
            result=sa.analyze('BNC')
            self.assertTrue(result['live_price_available'])
            self.assertEqual(result['live_price_age_seconds'],0)

    def test_price_expiring_during_bar_fetch_blocks_metrics(self):
        import consistency_regression_check as cr
        cr._install_common_analyzer_stubs()
        now=datetime.now(timezone.utc)
        class Clock(datetime):
            calls=0
            @classmethod
            def now(cls,tz=None):
                cls.calls+=1
                current=now if cls.calls<3 else now+timedelta(seconds=121)
                return current.astimezone(tz) if tz else current.replace(tzinfo=None)
        snap={'symbol':'BNC','latestTrade':{'p':10,'t':now.isoformat()},'latestQuote':{}}
        with patch.object(sa,'datetime',Clock),patch.object(sa,'USE_TRADIER',False),patch.object(sa,'snapshot',return_value=snap),patch.object(sa,'latest_session_bars',return_value=cr._fresh_intraday_bars(10,now)),patch.object(sa,'build_trade_plan') as plan:
            with self.assertRaisesRegex(RuntimeError,'LIVE PRICE UNAVAILABLE.*expired'):sa.analyze('BNC')
            plan.assert_not_called()

    def test_future_mismatch_and_stale_remain_rejected(self):
        now=datetime.now(timezone.utc)
        for symbol,stamp in [('OTHER',now),('BNC',now+timedelta(seconds=16)),('BNC',now-timedelta(seconds=121))]:
            selected,_=q.select_freshest_live_price('BNC',[dict(symbol=symbol,price=10,timestamp=stamp.isoformat(),source='tradier_consolidated_trade')],now=now)
            self.assertIsNone(selected)

if __name__=='__main__':unittest.main()
