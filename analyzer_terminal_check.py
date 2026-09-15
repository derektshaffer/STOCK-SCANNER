"""Offline authorization, causal ML admission and true-minute regressions."""
import io, os, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
import analyzer_provider_config as config
import analyzer_history_cache as hc
import stock_analyzer as sa
import analyzer_launch_runtime as launch
import analyzer_chart_history as history
from analyzer_ml_history import valid_training_bars, history_signature
from analyzer_overview import chart_frame, price_figure
import ml_predictor as ml

NOW = datetime(2026,9,15,16,tzinfo=timezone.utc)
def bar(t): return dict(t=t.isoformat(), o=10,h=12,l=9,c=11,v=100)
def training_rows(n=710):
    rows=[];dt=NOW-timedelta(days=25)
    while len(rows)<n:
        local=dt.astimezone(sa.ET)
        if local.weekday()<5 and 570 <= local.hour*60+local.minute<960: rows.append(bar(dt))
        dt+=timedelta(minutes=5)
    return rows

class AuthorizationTests(unittest.TestCase):
    def test_secrets_do_not_mix_with_environment_pair(self):
        env = {'ALPACA_API_KEY':'old-key', 'ALPACA_SECRET_KEY':'old-secret'}
        self.assertEqual(config.configured_alpaca_pair({'ALPACA_API_KEY':'new'},env),('new',''))
        config.preload_alpaca_pair({'ALPACA_API_KEY':'new'},env)
        self.assertEqual(config.alpaca_credentials(env),('new',''))
    def test_headers_resolve_rotated_pair_after_import(self):
        with patch.dict(os.environ,{'ALPACA_API_KEY':'new-key','ALPACA_SECRET_KEY':'new-secret'}), patch.object(sa,'API_KEY','old-key'),patch.object(sa,'API_SECRET','old-secret'):
            self.assertEqual(sa._headers()['APCA-API-KEY-ID'],'new-key')
            self.assertEqual(sa._headers()['APCA-API-SECRET-KEY'],'new-secret')
    def test_missing_half_does_not_reuse_imported_secret(self):
        with patch.dict(os.environ,{'ALPACA_API_KEY':'new-key','ALPACA_SECRET_KEY':''}), patch.object(sa,'API_SECRET','old-secret'):
            with self.assertRaisesRegex(RuntimeError,'Missing ALPACA'):sa._headers()
    def test_launch_replaces_pair_without_inheriting_stale_half(self):
        with patch.dict(os.environ,{'ALPACA_SECRET_KEY':'old-secret'}),patch.object(launch.subprocess,'Popen',return_value=MagicMock()) as spawn:
            state=launch.start_analyzer_process('TEST',alpaca_key='new-key',alpaca_historical_feed='iex')
        try:
            env=spawn.call_args.kwargs['env']
            self.assertEqual(env['ALPACA_SECRET_KEY'],'');self.assertEqual(env['ALPACA_HISTORICAL_FEED'],'iex')
        finally:launch._cleanup(state)
    def test_401_does_not_retry_same_identity_on_another_feed(self):
        with patch.object(sa,'bars',side_effect=RuntimeError('Alpaca HTTP 401')) as fetch,patch.object(sa,'LIVE_FEED','iex'):
            with self.assertRaisesRegex(RuntimeError,'401'):sa.try_sip_delayed_bars('TEST','5Min',NOW-timedelta(days=2),NOW)
        self.assertEqual(fetch.call_count,1)
    def test_403_can_use_existing_iex_fallback_and_retains_provenance(self):
        with patch.dict(os.environ,{'ALPACA_HISTORICAL_FEED':'sip'}),patch.object(sa,'bars',side_effect=[RuntimeError('Alpaca HTTP 403'),[bar(NOW-timedelta(days=1))]]) as fetch,patch.object(sa,'LIVE_FEED','iex'):
            rows,source=sa.try_sip_delayed_bars('TEST','5Min',NOW-timedelta(days=2),NOW)
        self.assertEqual(source,'IEX');self.assertEqual(fetch.call_args.kwargs['feed'],'iex');self.assertFalse(ml._consolidated_source(source))
    def test_explicit_historical_feed_is_honored(self):
        with patch.dict(os.environ,{'ALPACA_HISTORICAL_FEED':'iex'}),patch.object(sa,'bars',return_value=[]) as fetch:
            _,source=sa.try_sip_delayed_bars('TEST','5Min',NOW-timedelta(days=2),NOW)
        self.assertEqual(source,'IEX');self.assertEqual(fetch.call_args.kwargs['feed'],'iex')
    def test_error_body_is_never_exposed(self):
        with patch.dict(os.environ,{'ALPACA_API_KEY':'fixture','ALPACA_SECRET_KEY':'fixture'}),patch.object(sa.urllib.request,'urlopen',side_effect=HTTPError('https://data.alpaca.markets',401,'Unauthorized',{},io.BytesIO(b'SECRET'))):
            with self.assertRaisesRegex(RuntimeError,'Alpaca HTTP 401') as error:sa.get_json('https://data.alpaca.markets/v2/stocks/TEST/bars')
        self.assertNotIn('SECRET',str(error.exception))
        message=hc.history_error_summary(error.exception)
        self.assertIn('Streamlit Secrets',message);self.assertIn('Changing SIP to IEX cannot fix',message)
    def test_history_cache_isolated_on_credential_or_feed_change(self):
        with patch.dict(os.environ,{'ALPACA_API_KEY':'a','ALPACA_SECRET_KEY':'b','ALPACA_HISTORICAL_FEED':'sip'}):
            a=hc._cache_path('TEST');os.environ['ALPACA_API_KEY']='c';b=hc._cache_path('TEST');os.environ['ALPACA_HISTORICAL_FEED']='iex';c=hc._cache_path('TEST')
        self.assertEqual(len({a,b,c}),3)
    def test_no_tradier_ml_history_substitution(self):
        with patch.object(sa,'USE_TRADIER_HISTORY',True),patch.object(sa,'get_tradier_history_bars') as tradier,patch.object(sa,'bars',side_effect=RuntimeError('Alpaca HTTP 401')):
            with self.assertRaises(RuntimeError):sa.try_sip_delayed_bars('TEST','5Min',NOW-timedelta(days=400),NOW)
        tradier.assert_not_called()

class AdmissionTests(unittest.TestCase):
    def test_valid_minimum_unique_completed_bars(self):
        self.assertEqual(len(valid_training_bars(training_rows(700),'TEST',NOW,None,sa.ET)),700)
    def test_future_partial_outofwindow_and_duplicate_rows_cannot_fill_minimum(self):
        rows=training_rows(699);bad=[bar(NOW),bar(NOW-timedelta(days=600)),bar(NOW+timedelta(days=1))]
        with patch.object(ml,'_build_dataset',side_effect=AssertionError('training')):
            result=ml.predict_ml('TEST',NOW,{},lambda *a:(rows+bad+[rows[0]]*100,'delayed SIP'),sa.ET)
        self.assertEqual(result['status'],'insufficient_history');self.assertEqual(result['bar_count'],699)
    def test_malformed_wrong_symbol_conflict_rejected(self):
        for bad in (dict(bar(NOW-timedelta(days=1)),h=1),dict(bar(NOW-timedelta(days=1)),S='OTHER'),dict(bar(NOW-timedelta(days=1)),v=float('nan'))):
            with self.assertRaises(ValueError):valid_training_bars([bad],'TEST',NOW,None,sa.ET)
        t=NOW-timedelta(days=1)
        with self.assertRaises(ValueError):valid_training_bars([bar(t),dict(bar(t),c=10)],'TEST',NOW,None,sa.ET)
    def test_analysis_cutoff_blocks_later_bars(self):
        t=NOW-timedelta(days=1)
        self.assertEqual(valid_training_bars([bar(t)],'TEST',NOW,t.isoformat(),sa.ET),[])
    def test_cached_prediction_cannot_hide_history_failure(self):
        key=('TEST',5.,-4.,int(NOW.timestamp()//300))
        with patch.dict(ml._CACHE,{key:dict(stamp=ml.time.time(),value={'status':'ok'})}),patch.object(ml,'_build_dataset',side_effect=AssertionError('training')):
            result=ml.predict_ml('TEST',NOW,{},lambda *a:(_ for _ in ()).throw(RuntimeError('Alpaca HTTP 401')),sa.ET)
        self.assertNotEqual(result['status'],'ok');self.assertEqual(result['models'],{})
    def test_signature_changes_on_feed_and_candle_revision(self):
        rows=training_rows(2);a=history_signature(rows,'IEX');b=history_signature(rows,'delayed SIP');rows[0]['c']=10;c=history_signature(rows,'IEX')
        self.assertEqual(len({a,b,c}),3)

class MinuteChartTests(unittest.TestCase):
    def test_real_minute_history_request_and_retention(self):
        with patch.object(history,'get_cached_context',return_value=None),patch.object(history,'set_cached_context'),patch.object(history,'get_timesales_bars',return_value=[bar(NOW-timedelta(days=1))]) as fetch:
            result=history.load_chart_history('TEST',NOW,'2026-09-15',tradier_token='fixture',timeframe='1m')
        self.assertEqual(fetch.call_args.kwargs['interval'],'1min');self.assertEqual((fetch.call_args.args[3]-fetch.call_args.args[2]).days,14);self.assertEqual(result['timeframe'],'1m')
    def test_no_five_minute_upsampling(self):
        r=dict(symbol='TEST',as_of=NOW.isoformat(),overview_history=dict(symbol='TEST',status='ok',bars=[bar(NOW-timedelta(days=1))]))
        self.assertTrue(chart_frame(r,'1m').empty)
    def test_minute_preserves_missing_periods_and_pacific_time(self):
        rows=[bar(NOW-timedelta(minutes=i)) for i in (4,2,1)]
        r=dict(symbol='TEST',as_of=NOW.isoformat(),chart_data=dict(intraday=rows,intraday_interval='1m'))
        f=chart_frame(r,'1m');self.assertEqual(len(f),3);self.assertEqual(price_figure(r,'1m').data[0].x[-1],'2026-09-15 08:59')
    def test_future_bar_never_shown_even_with_future_asof(self):
        future=datetime.now(timezone.utc)+timedelta(days=2)
        r=dict(symbol='TEST',as_of=(future+timedelta(days=1)).isoformat(),chart_data=dict(intraday=[bar(future)],intraday_interval='1m'))
        self.assertTrue(chart_frame(r,'1m').empty)
    def test_subminute_naive_timestamp_not_displayed(self):
        r=dict(symbol='TEST',as_of=NOW.isoformat(),chart_data=dict(intraday=[dict(bar(NOW),t='2026-09-15T15:30:00')],intraday_interval='1m'))
        self.assertTrue(chart_frame(r,'1m').empty)
    def test_no_stale_live_marker_or_daily_vwap(self):
        r=dict(symbol='TEST',as_of=NOW.isoformat(),vwap=99,chart_data=dict(daily=[dict(bar(NOW),t='2026-09-14')]))
        f=price_figure(r,'D');self.assertTrue(any('Last bar' in a.text for a in f.layout.annotations));self.assertFalse(any(t.name=='Session VWAP' for t in f.data))

class LaunchIsolationRegressionTests(unittest.TestCase):
    def test_explicit_blank_pair_clears_inherited_credentials(self):
        import analyzer_launch_runtime as runtime
        with patch.dict(os.environ,{'ALPACA_API_KEY':'old-key','ALPACA_SECRET_KEY':'old-secret'}), patch.object(runtime.subprocess,'Popen') as popen:
            state=runtime.start_analyzer_process('TEST',alpaca_key='',alpaca_secret='')
            env=popen.call_args.kwargs['env']
            self.assertEqual((env['ALPACA_API_KEY'],env['ALPACA_SECRET_KEY']),('',''))
            runtime._cleanup(state)

    def test_invalid_explicit_publication_cannot_borrow_general_context(self):
        import analyzer_launch_runtime as runtime
        import market_heat
        with patch.object(runtime.subprocess,'Popen') as popen:
            state=runtime.start_analyzer_process('TEST',source_publication={'symbol':'OTHER'})
            env=popen.call_args.kwargs['env']
            with patch.object(market_heat,'history',side_effect=AssertionError('must not look up another scan')):
                self.assertIsNone(market_heat.analyzer_context('TEST',NOW.isoformat(),environ=env))
            runtime._cleanup(state)

if __name__=='__main__':unittest.main()
