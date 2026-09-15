"""Bounded offline tests; fixtures are explicitly synthetic, never market evidence."""
import ast
import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch

import market_heat as heat
import market_heat_study as study
import market_heat_ui as ui
from candidate_outcome_study import digest

START='2026-09-15T14:00:00+00:00'
RECEIVED='2026-09-15T14:00:10+00:00'
ASOF='2026-09-15T14:00:20+00:00'


def fixture():
    rows={}
    event=int(heat.instant(START).timestamp()*1000)
    for i in range(1000):
        symbol=f'TEST{i:04}'
        price=40. if i<3 else 25. if i<10 else 17. if i<30 else 13. if i<100 else 10.
        rows[symbol]=dict(symbol=symbol,type='stock',last=price,trade_date=event,
                          prevclose=10.,bid=price-.01,ask=price+.01,bid_date=event,ask_date=event,
                          high=price,low=9.,volume=1000.,average_volume=500.,delay=0)
    batch=heat.project_batch(list(rows),rows,started_at=START,received_at=RECEIVED)
    return {'requested_symbols':list(rows),'batches':[batch],'candidate_features':[],
            'features_observed_at':ASOF}


def calculate(bundle,**kwargs):
    return heat.calculate(bundle,scan_id='scan-one',as_of=ASOF,scan_started_at=START,phase='regular',**kwargs)


class CalculationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.base=fixture()
    def setUp(self):self.bundle=copy.deepcopy(self.base)

    def test_formula_exact_and_transparent(self):
        s,_=calculate(self.bundle)
        self.assertEqual(s['value'],100);self.assertEqual(s['regime'],'EXTREME')
        self.assertEqual([s['components'][f'stocks_up_{x}_pct']['value'] for x in (20,50,100,200)],[100,30,10,3])
        self.assertEqual(s['formula']['version'],'market-heat-v1')
        self.assertEqual(s['formula_sha256'],digest(s['formula']))

    def test_exact_twenty_percent_boundary_is_included(self):
        b=self.bundle['batches'][0]
        for q in b['rows']:q.update(last=12.,bid=12.,ask=12.)
        b['projection_sha256']=digest(b['rows'])
        self.assertEqual(calculate(self.bundle)[0]['components']['stocks_up_20_pct']['value'],1000)

    def test_deterministic_full_snapshot(self):
        self.assertEqual(calculate(self.bundle),calculate(copy.deepcopy(self.bundle)))

    def test_no_input_mutation(self):
        before=digest(self.bundle);calculate(self.bundle);self.assertEqual(before,digest(self.bundle))

    def test_future_receipt_rejected(self):
        self.bundle['batches'][0]['received_at']='2026-09-15T15:00:00Z'
        s,_=calculate(self.bundle);self.assertIsNone(s['value']);self.assertEqual(s['regime'],'UNKNOWN')

    def test_prior_run_not_reused(self):
        self.bundle['batches'][0]['started_at']='2026-09-15T13:00:00Z'
        self.assertIsNone(calculate(self.bundle)[0]['value'])

    def test_future_trade_and_sides_excluded(self):
        b=self.bundle['batches'][0]
        for q in b['rows']:
            for k in ('trade_date','bid_date','ask_date'):q[k]=int(heat.instant('2026-09-15T15:00:00Z').timestamp()*1000)
        b['projection_sha256']=digest(b['rows'])
        s,_=calculate(self.bundle);self.assertEqual(s['coverage']['usable_fresh_stocks'],0)
        self.assertIsNone(s['components']['stocks_up_20_pct']['value'])

    def test_event_after_receipt_rejected(self):
        b=self.bundle['batches'][0]
        for q in b['rows']:
            for k in ('trade_date','bid_date','ask_date'):q[k]=int(heat.instant('2026-09-15T14:00:15Z').timestamp()*1000)
        b['projection_sha256']=digest(b['rows'])
        self.assertIsNone(calculate(self.bundle)[0]['value'])

    def test_unobserved_is_not_cold(self):
        s,_=calculate({'batches':[],'candidate_features':[]})
        self.assertEqual(s['regime'],'UNKNOWN');self.assertIsNone(s['value'])
        self.assertIsNone(s['components']['median_volume_pace']['value'])

    def test_spread_cannot_use_sides_after_receipt_even_with_valid_trade(self):
        b=self.bundle['batches'][0]
        for q in b['rows']:
            for k in ('bid_date','ask_date'):q[k]=int(heat.instant('2026-09-15T14:00:15Z').timestamp()*1000)
        b['projection_sha256']=digest(b['rows'])
        s,_=calculate(self.bundle)
        self.assertEqual(s['coverage']['usable_fresh_stocks'],1000)
        self.assertIsNone(s['components']['median_spread_pct']['value'])

    def test_cold_requires_observations(self):
        b=self.bundle['batches'][0]
        for q in b['rows']:q.update(last=10.,bid=9.99,ask=10.01)
        b['projection_sha256']=digest(b['rows'])
        s,_=calculate(self.bundle);self.assertEqual(s['regime'],'COLD');self.assertEqual(s['value'],0)

    def test_missing_batch_keeps_real_roster_denominator(self):
        self.bundle['requested_symbols'] += [f'MISSING{i}' for i in range(1000)]
        s,_=calculate(self.bundle);self.assertEqual(s['coverage']['roster_pct'],50);self.assertIsNone(s['value'])

    def test_top30_cannot_impersonate_market(self):
        b=self.bundle['batches'][0];b['rows']=b['rows'][:30];b['requested_symbols']=b['requested_symbols'][:30]
        b['projection_sha256']=digest(b['rows']);self.bundle['requested_symbols']=b['requested_symbols']
        self.assertIsNone(calculate(self.bundle)[0]['value'])

    def test_stale_price_rejected(self):
        s,_=heat.calculate(self.bundle,scan_id='late',as_of='2026-09-15T14:03:00Z',scan_started_at=START,phase='regular')
        self.assertIsNone(s['value'])

    def test_one_fresh_side_cannot_refresh_old_side(self):
        b=self.bundle['batches'][0]
        for q in b['rows']:
            q['trade_date']=int(heat.instant('2026-09-15T13:00:00Z').timestamp()*1000)
            q['bid_date']=q['trade_date']
        b['projection_sha256']=digest(b['rows'])
        self.assertIsNone(calculate(self.bundle)[0]['value'])

    def test_delayed_feed_not_silently_live(self):
        b=self.bundle['batches'][0]
        for q in b['rows']:q['delay']=15
        b['projection_sha256']=digest(b['rows'])
        self.assertIsNone(calculate(self.bundle)[0]['value'])

    def test_outcomes_and_example_tickers_have_no_formula_role(self):
        original=calculate(self.bundle)[0]
        self.bundle['outcomes']={'FTFT':10000,'RETO':9999,'VEEA':9999}
        self.bundle['candidate_features']=[{'symbol':'FTFT','day_pct':900,'outcome':999999}]
        changed=calculate(self.bundle)[0]
        self.assertEqual(original['value'],changed['value']);self.assertEqual(original['formula'],changed['formula'])

    def test_weekday_only_metadata(self):
        original=calculate(self.bundle)[0]
        def shifted(s):return (heat.instant(s)+timedelta(days=1)).isoformat()
        b=self.bundle['batches'][0]
        b['started_at']=shifted(START);b['received_at']=shifted(RECEIVED)
        for q in b['rows']:
            for k in ('trade_date','bid_date','ask_date'):q[k]+=86400000
        b['projection_sha256']=digest(b['rows']);self.bundle['features_observed_at']=shifted(ASOF)
        new,_=heat.calculate(self.bundle,scan_id='wednesday',as_of=shifted(ASOF),scan_started_at=shifted(START),phase='regular')
        self.assertEqual(new['value'],original['value']);self.assertNotEqual(new['clock']['weekday'],original['clock']['weekday'])

    def test_no_derived_future_values(self):
        self.bundle['candidate_features']=[{'symbol':'FTFT','volume_pace':999,'above_vwap':True,'live_price_timestamp':ASOF}]
        self.bundle['features_observed_at']='2026-09-15T15:00:00Z'
        s,_=calculate(self.bundle);self.assertIsNone(s['components']['median_volume_pace']['value'])
        self.assertIsNone(s['components']['momentum_candidate_breadth']['value'])

    def test_unsupported_halts_and_first_pullback_unknown(self):
        s,_=calculate(self.bundle)
        for k in ['volatility_halt_count','holding_after_first_major_pullback_pct','new_session_high_count']:
            self.assertIsNone(s['components'][k]['value'])

    def test_future_prior_snapshot_not_used(self):
        s,stocks=calculate(self.bundle)
        later=copy.deepcopy(s);later['as_of']='2026-09-16T14:00:20Z';later.pop('snapshot_id');later['snapshot_id']=digest(later)
        out,_=calculate(self.bundle,prior=[{'snapshot':later,'observed_stocks':stocks,'published_at':'2026-09-16T14:00:21Z'}])
        self.assertEqual(out['prior_sessions'],[None,None,None])

    def test_clock_buckets(self):
        cases=[('12:00','premarket','premarket'),('13:45','regular','opening window'),('14:30','regular','morning'),('17:00','regular','midday'),('19:30','regular','final hour'),('21:00','afterhours','after-hours')]
        for t,p,expected in cases:self.assertEqual(heat.clock_metadata('2026-09-15T'+t+':00Z',p)['time_of_day'],expected)

    def test_hash_tamper_rejected(self):
        s,_=calculate(self.bundle);s['value']=1
        with self.assertRaises(ValueError):heat.validate_snapshot(s)


class ArchiveAndUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.b=fixture()
        self.rows=[{'symbol':'TEST0000','score':80,'scanner_action':'WAIT PULLBACK','tradeability_score':90,'explosion_score':70}]
        self.payload={'scan_id':'scan-one','scan_time_utc':START,'decision_time_utc':ASOF,'session_phase':'regular','candidates':copy.deepcopy(self.rows)}

    def tearDown(self):self.tmp.cleanup()

    def attach(self):
        return heat.attach_publication(self.payload,self.rows,batches=self.b['batches'],requested_symbols=self.b['requested_symbols'],scan_log_dir=self.root)

    def test_candidate_exact_link_and_no_rank_mutation(self):
        old=copy.deepcopy(self.rows);s=self.attach();self.assertEqual(self.rows,old)
        r=self.payload['candidates'][0]
        self.assertEqual(r['market_regime_id'],s['snapshot_id']);self.assertEqual(r['market_regime_components'],s['components'])
        for k,v in old[0].items():self.assertEqual(r[k],v)

    def test_input_archive_roundtrip(self):
        s=self.attach();loaded=heat.load_archive(self.root/'market_heat',s['snapshot_id'])
        rebuilt,_=heat.calculate(loaded['inputs'],scan_id=s['scan_id'],as_of=s['as_of'],scan_started_at=START,phase='regular')
        self.assertEqual(rebuilt,s)

    def test_prepared_not_published_history(self):
        self.attach();self.assertEqual(heat.history(self.root,'2026-09-15T14:01:00Z'),[])

    def test_published_history_and_future_cutoff(self):
        self.attach();heat.mark_published(self.payload,self.root,published_at='2026-09-15T14:00:25Z')
        self.assertEqual(heat.history(self.root,'2026-09-15T14:00:23Z'),[])
        self.assertEqual(len(heat.history(self.root,'2026-09-15T14:00:30Z')),1)

    def test_old_snapshot_survives_formula_version_change(self):
        old=self.attach();path=self.root/'market_heat'/(old['snapshot_id']+'.json.gz');raw=path.read_bytes()
        new_formula=copy.deepcopy(heat.FORMULA);new_formula['version']='test-future-v2'
        with patch.object(heat,'FORMULA',new_formula):new=self.attach()
        self.assertNotEqual(old['snapshot_id'],new['snapshot_id']);self.assertEqual(path.read_bytes(),raw)
        self.assertEqual(heat.load_archive(self.root/'market_heat',old['snapshot_id'])['snapshot']['formula']['version'],'market-heat-v1')

    def test_future_candidate_outcomes_cannot_rewrite_context(self):
        original=self.attach()
        self.rows[0]['outcomes']={'future_price':99999,'future_winner':True}
        self.assertEqual(self.attach(),original)

    def test_outcome_adapter_preserves_missing_and_validates_exact_candidate(self):
        self.attach();candidate=self.payload['candidates'][0]
        label={'frozen_candidate_sha256':digest(candidate),'symbol':candidate['symbol'],'windows':{}}
        out=study.associate_publication(self.payload,'2026-09-15T14:00:25Z',[label])
        self.assertEqual(study.regime_for(out[0])[0],'EXTREME')
        self.assertEqual(len(study.associate_publication(self.payload,'2026-09-15T14:00:25Z',[])),1)
        with self.assertRaises(ValueError):study.associate_publication(self.payload,'2026-09-15T14:00:25Z',[dict(label,frozen_candidate_sha256='wrong')])

    def test_prior_session_refs_are_actual_published_observations(self):
        first=self.attach();heat.mark_published(self.payload,self.root,published_at='2026-09-15T14:00:25Z')
        p=copy.deepcopy(self.payload);p['scan_id']='next-day';p['scan_time_utc']='2026-09-16T14:00:00Z';p['decision_time_utc']='2026-09-16T14:00:20Z'
        later=heat.attach_publication(p,self.rows,batches=[],requested_symbols=[],scan_log_dir=self.root)
        self.assertEqual(later['prior_sessions'][0]['snapshot_id'],first['snapshot_id'])
        self.assertEqual(later['prior_sessions'][1:],[None,None]);self.assertEqual(later['regime'],'UNKNOWN')

    def test_conflict_does_not_overwrite(self):
        p=self.root/'test';heat._write_once(p,b'one')
        with self.assertRaises(ValueError):heat._write_once(p,b'two')
        self.assertEqual(p.read_bytes(),b'one')

    def test_launch_exact_binding_and_future_rejection(self):
        self.attach();c=heat.launch_context(self.payload,'TEST0000')
        binding={'scan_id':'scan-one','symbol':'TEST0000','payload_sha256':digest(self.payload)}
        self.assertEqual(heat.validate_launch(c,binding,'TEST0000'),c)
        self.assertIsNone(heat.validate_launch(c,binding,'OTHER'))
        env={'ANALYZER_SOURCE_PUBLICATION':json.dumps(binding),'ANALYZER_MARKET_REGIME_CONTEXT':json.dumps(c)}
        self.assertEqual(heat.analyzer_context('TEST0000','2026-09-15T14:01:00Z',environ=env),c)
        self.assertIsNone(heat.analyzer_context('TEST0000',START,environ=env))

    def test_invalid_explicit_context_never_uses_latest(self):
        self.attach();heat.mark_published(self.payload,self.root,published_at='2026-09-15T14:00:25Z')
        self.assertIsNone(heat.analyzer_context('TEST0000','2026-09-15T14:01:00Z',scan_log_dir=self.root,environ={'ANALYZER_MARKET_REGIME_CONTEXT':'bad'}))

    def test_display_is_context_only_and_does_not_mutate(self):
        s=self.attach();before=digest(s);v=ui.view(s,now='2026-09-15T14:00:30Z')
        self.assertIn('EXTREME',v['heading']);self.assertIn('Context only',v['caption']);self.assertEqual(before,digest(s))
        self.assertIn('Recorded market regime',ui.view(s,now='2026-09-15T15:00:00Z')['heading'])
        self.assertIn('UNKNOWN',ui.view(None)['heading'])

    def test_market_projection_rejects_secret_material_by_omission(self):
        q={'TEST':{'symbol':'TEST','type':'stock','Authorization':'fixture-secret','api_key':'fixture-key'}}
        b=heat.project_batch(['TEST'],q,started_at=START,received_at=RECEIVED)
        self.assertNotIn('fixture-secret',json.dumps(b));self.assertNotIn('fixture-key',json.dumps(b))


    def test_runtime_clears_inherited_context_and_passes_only_exact_binding(self):
        import analyzer_launch_runtime as runtime
        self.attach();context=heat.launch_context(self.payload,'TEST0000')
        binding={'scan_id':'scan-one','symbol':'TEST0000','payload_sha256':digest(self.payload),'publication_kind':'live_momentum'}
        with patch.dict(os.environ,{'ANALYZER_MARKET_REGIME_CONTEXT':'old-context','ANALYZER_SOURCE_PUBLICATION':'old-binding'},clear=True),patch.object(runtime.subprocess,'Popen') as popen:
            state=runtime.start_analyzer_process('TEST0000',source_publication=binding,market_regime_context=context)
            env=popen.call_args.kwargs['env']
            self.assertEqual(json.loads(env['ANALYZER_MARKET_REGIME_CONTEXT']),context);runtime._cleanup(state)
            state=runtime.start_analyzer_process('OTHER')
            env=popen.call_args.kwargs['env']
            self.assertNotIn('ANALYZER_MARKET_REGIME_CONTEXT',env);self.assertNotIn('ANALYZER_SOURCE_PUBLICATION',env);runtime._cleanup(state)


class BoundaryAndStudyTests(unittest.TestCase):


    def test_extension_bands_preserve_all_cases(self):
        self.assertEqual([study.extension_band(v) for v in [None,19,20,49.9,50,100,200]],['UNKNOWN','BELOW20','20–50%','20–50%','50–100%','100–200%','200%+'])

    def test_empty_comparison_is_unknown_not_zero_returns(self):
        s=study.summarize([])
        self.assertIsNone(s['windows']['m5']['endpoint_change_pct']['median'])

    def test_partial_witness_and_nonhit_unknown(self):
        rows=[{'windows':{'m60':{'observed_high_change_pct':30,'complete':False}}}]
        s=study.summarize(rows)['continuation_60m']
        self.assertEqual(s['plus_25_pct']['witnessed'],1);self.assertEqual(s['plus_50_pct']['unknown'],1)

    def test_legacy_regime_not_backfilled(self):
        self.assertEqual(study.regime_for({'symbol':'FTFT','features':{'day_pct':300}}),('UNKNOWN','UNKNOWN'))



class ReleaseBoundaryTests(unittest.TestCase):
    def test_all_production_decision_functions_and_ranking_unchanged(self):
        baseline=json.loads(Path('tests/market_heat_production_baseline.json').read_text())['protected']
        for file,hashes in baseline.items():
            if not file.endswith('.py'):continue
            functions={n.name:n for n in ast.parse(Path(file).read_text()).body if isinstance(n,ast.FunctionDef)}
            for name,expected in hashes.items():
                self.assertEqual(hashlib.sha256(ast.dump(functions[name],include_attributes=False).encode()).hexdigest(),expected,file+':'+name)

    def test_complete_analyzer_decision_prefix_matches_production(self):
        baseline=json.loads(Path('tests/market_heat_production_baseline.json').read_text())['protected']
        node=next(n for n in ast.parse(Path('stock_analyzer.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='analyze')
        # Exact reviewed chart tail is independently locked; all prior decisions
        # retain the production AST, including research/live trade-plan branches.
        tail=node.body[-6:]
        self.assertIsInstance(tail[0],ast.ImportFrom)
        self.assertEqual(tail[0].module,'analyzer_provider_config')
        self.assertEqual([ast.unparse(n.targets[0]) for n in tail[1:-1]],['chart_feed',"metrics['overview_history']","metrics['overview_minute_history']","metrics['chart_data']['intraday_interval']"])
        self.assertEqual(ast.unparse(tail[-1]),'return metrics')
        node.body=node.body[:-6]
        self.assertEqual(hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest(),baseline['analyzer_decision_prefix'])

    def test_heat_hook_is_after_final_contract_and_only_context(self):
        baseline=json.loads(Path('tests/market_heat_production_baseline.json').read_text())['protected']
        wrapper=next(n for n in ast.parse(Path('analyzer_v2_integration.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='install_v2_analysis')
        enhanced=next(n for n in wrapper.body if isinstance(n,ast.FunctionDef) and n.name=='enhanced_analyze')
        hooks=[n for n in enhanced.body if isinstance(n,ast.Try) and any(isinstance(x,ast.ImportFrom) and x.module=='market_heat' for x in ast.walk(n))]
        self.assertEqual(len(hooks),1)
        self.assertGreater(enhanced.body.index(hooks[0]),next(i for i,n in enumerate(enhanced.body) if '_finalize_trade_plan_contract' in ast.unparse(n)))
        self.assertEqual({ast.unparse(t) for n in ast.walk(hooks[0]) if isinstance(n,ast.Assign) for t in n.targets},{"metrics['market_regime_context']"})
        enhanced.body.remove(hooks[0])
        self.assertEqual(hashlib.sha256(ast.dump(wrapper,include_attributes=False).encode()).hexdigest(),baseline['analyzer_wrapper'])

    def test_identity_is_exact_and_rejects_malformed_binding(self):
        from scanner_publication_identity import launch_publication,validate_launch_publication
        payload={'scan_id':'real-id','candidates':[{'symbol':'TEST'}]}
        binding=launch_publication(payload,'TEST')
        self.assertEqual(validate_launch_publication(binding,'TEST'),binding)
        self.assertIsNone(validate_launch_publication(binding,'OTHER'))
        self.assertIsNone(validate_launch_publication(dict(binding,payload_sha256='wrong'),'TEST'))
        self.assertIsNone(launch_publication(payload,'OTHER'))
        self.assertNotEqual(launch_publication(dict(payload,scan_id='new-id'),'TEST'),binding)

class PublicationReleaseParityTests(unittest.TestCase):
    def test_actual_publication_preserves_every_legacy_json_csv_value(self):
        import csv,io
        from contextlib import redirect_stdout,nullcontext
        with patch.dict(os.environ,{'TRADIER_ACCESS_TOKEN':'offline-fixture-only'}):import stock_scanner as scanner
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return heat.instant(ASOF)
        old_scope=dict(vars(scanner));old_scope['datetime']=Clock
        exec(json.loads(Path('tests/market_heat_production_baseline.json').read_text())['writer_source'],old_scope)
        modern=dict(vars(scanner));modern['datetime']=Clock
        node=next(n for n in ast.parse(Path('stock_scanner.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='write_scan_logs')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'current-writer','exec'),modern)
        results=[]
        extra={'candidate_decision_time_utc','market_regime_id','market_regime_formula_version','market_regime_components'}
        for label,scope in [('production',old_scope),('heat',modern),('capture-failure',modern)]:
            with tempfile.TemporaryDirectory() as tmp:
                scope['SCAN_LOG_DIR']=tmp
                rows=[{'symbol':'TEST','price':10,'score':62,'setup_grade':'C','scanner_action':'WAIT PULLBACK'}]
                before=copy.deepcopy(rows)
                with patch.dict(os.environ,{'GITHUB_RUN_ID':'fixture','GITHUB_RUN_ATTEMPT':'1'}),redirect_stdout(io.StringIO()):
                    with patch.object(heat,'attach_publication',side_effect=OSError) if label=='capture-failure' else nullcontext():
                        paths=scope['write_scan_logs'](rows,heat.instant(START),heat.instant(START),[])
                self.assertEqual(rows,before)
                self.assertEqual(len(paths),3)
                payload=json.loads(Path(paths[0]).read_text())
                if label=='heat':
                    snap=payload['market_regime']
                    self.assertEqual(snap['regime'],'UNKNOWN')
                    self.assertEqual(payload['candidates'][0]['market_regime_id'],snap['snapshot_id'])
                    self.assertEqual(len(list((Path(tmp)/'market_heat').glob('*.json.gz'))),1)
                    self.assertEqual(len(heat.history(tmp,heat.utcnow())),1)
                for key in ('market_regime','market_regime_capture_status','scan_time_semantics','decision_time_utc','decision_manifest'):payload.pop(key,None)
                for candidate in payload['candidates']:
                    for key in extra:candidate.pop(key,None)
                with Path(paths[1]).open() as handle:csvrows=[{k:v for k,v in row.items() if k not in extra} for row in csv.DictReader(handle)]
                results.append((payload,csvrows))
        self.assertEqual(results[0],results[1])
        self.assertEqual(results[0],results[2])

if __name__=='__main__':unittest.main(verbosity=2)
