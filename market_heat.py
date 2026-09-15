"""Provisional, point-in-time market context. No ranking, decisions or network.

v1 uses a known-at-publication projection of the existing Tradier sweep. Unknown
coverage is not a cold market. Formula constants are research definitions only.
"""
import copy
import gzip
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from live_price_quality import parse_market_timestamp, tradier_price_candidates
from candidate_outcome_study import canonical, digest

VERSION = 'market-heat-v1'
FORMULA = {
    'version': VERSION,
    'terms': [{'day_change_at_least_pct': p, 'saturation_count': n, 'points': 25}
              for p,n in [(20,100),(50,30),(100,10),(200,3)]],
    'regime_lower_bounds': {'COLD':0,'NORMAL':25,'HOT':50,'EXTREME':75},
    'minimum_requested_symbols':1000, 'minimum_roster_coverage_pct':90,
    'minimum_returned_stocks':1000,
    'minimum_fresh_stock_coverage_pct':80, 'maximum_price_age_seconds':120,
    'weekday_weight':0, 'status':'PROVISIONAL_NOT_OPTIMIZED_CONTEXT_ONLY',
    'component_policy':{'version':'market-components-v1','speculative_day_change_pct':20,
                         'elevated_saved_volume_pace':2,'minimum_saved_bounce_count':2,
                         'dollar_volume':'current price times reported cumulative volume proxy',
                         'prior_runners':'prior observed session >=20%; today >0%; matched symbols only'},
}
ET=ZoneInfo('America/New_York')
QUOTE_FIELDS=('symbol','type','last','trade_date','bid','ask','bid_date','ask_date',
              'prevclose','volume','average_volume','high','low','delay','delayed')
FEATURE_FIELDS=('symbol','day_pct','live_price_timestamp','radar_quote_timestamp',
                'live_price_source','live_price_state','action_data_integrity_ok','volume_pace','volume_pace_source',
                'above_vwap','vwap_reclaim','bounce_count','higher_low_streak',
                'stair_reaccelerating','breakout_holding','breakout_recent','failed_breakout',
                'live_spread_pct','liquidity_dollar_volume','current_pullback_pct')


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    t=parse_market_timestamp(value)
    if t is None:raise ValueError('missing/invalid timestamp')
    return t.astimezone(timezone.utc)


def num(value):
    return value if type(value) in (int,float) and math.isfinite(value) else None


def clean_scalar(value):
    if value is None or isinstance(value,(str,bool)) or num(value) is not None:return value
    return None


def project_batch(requested, rows, *, started_at, received_at, status='RECEIVED'):
    """Observe returned values without modifying provider rows or persisting secrets.

    Times are local return-boundary observations, not fabricated wire receipts.
    These batches never include headers, URLs, exception bodies or configuration.
    """
    if instant(received_at)<instant(started_at):raise ValueError('batch time order')
    if status not in {'RECEIVED','FAILED'}:raise ValueError('invalid batch status')
    projection=[]
    for symbol,q in sorted(rows.items()):
        if not isinstance(q,dict):continue
        projected={k:clean_scalar(q.get(k)) for k in QUOTE_FIELDS}
        projected['requested_symbol']=str(symbol)
        projection.append(projected)
    return {'requested_symbols':sorted(set(requested)), 'rows':projection,
            'started_at':started_at,'received_at':received_at,'status':status,
            'provider':'tradier','feed':'production_consolidated_endpoint',
            'receipt_semantics':'POST_CALL_RETURN_UPPER_BOUND','projection_sha256':digest(projection)}


def clock_metadata(as_of, phase):
    local=instant(as_of).astimezone(ET); mins=local.hour*60+local.minute
    if phase=='premarket':bucket='premarket'
    elif phase=='afterhours':bucket='after-hours'
    elif phase!='regular':bucket='closed/unsupported'
    elif mins<600:bucket='opening window'  # 09:30–10:00 ET
    elif mins<720:bucket='morning'         # 10:00–12:00
    elif mins<900:bucket='midday'          # 12:00–15:00
    else:bucket='final hour'              # 15:00–16:00 on a normal session
    return {'session_date':local.date().isoformat(),'weekday':local.strftime('%A'),
            'exchange_timezone':'America/New_York','exchange_time':local.isoformat(),
            'session_phase':phase,'time_of_day':bucket,
            'bucket_policy':'fixed_ET_windows_v1; early-close final-hour identity UNKNOWN'}


def component(value, n, scope, reason=None):
    return {'value':value,'n':n,'scope':scope,'status':'UNKNOWN' if value is None else 'OBSERVED',
            'reason':reason}


def _price(q, now, received_at):
    if str(q.get('type') or '').lower()!='stock':return None,'NON_STOCK'
    if q.get('symbol')!=q.get('requested_symbol'):return None,'SYMBOL_MISMATCH'
    if q.get('delayed') not in (None,False,0) or q.get('delay') not in (None,False,0):return None,'DELAYED_OR_UNKNOWN_DELAY'
    valid=[]
    for c in tradier_price_candidates(q['symbol'],q):
        try:
            event=instant(c['timestamp'])
            sides=c.get('side_timestamps') or {}
            times=[instant(t) for t in (sides.values() if isinstance(sides,dict) else sides)]
        except (ValueError,TypeError,KeyError):continue
        if event>now or event>instant(received_at) or any(t>now or t>instant(received_at) for t in times):continue
        if not 0<=(now-event).total_seconds()<=FORMULA['maximum_price_age_seconds']:continue
        if any((now-t).total_seconds()>FORMULA['maximum_price_age_seconds'] for t in times):continue
        if event.astimezone(ET).date()!=now.astimezone(ET).date():continue
        if num(c.get('price')) is not None and c['price']>0:valid.append(c)
    if not valid:return None,'NO_CURRENT_OBSERVATION'
    valid.sort(key=lambda x:instant(x['timestamp']),reverse=True)
    return valid[0],None


def calculate(bundle, *, scan_id, as_of, scan_started_at, phase, prior=(), formula=None):
    """Pure function over frozen inputs. Extra outcome/ticker fields are ignored.

    The heat formula uses no candidate features, weekday, history or outcomes.
    Additional components explicitly name their narrower sampled scope.
    """
    formula=copy.deepcopy(formula or FORMULA)
    if formula!=FORMULA:raise ValueError('unsupported formula: implement a new version explicitly')
    now,start=instant(as_of),instant(scan_started_at)
    if start>now:raise ValueError('decision before scan start')
    clock=clock_metadata(as_of,phase)
    requested=set(bundle.get('requested_symbols') or []); quotes={}; rejected=Counter(); all_batches=bundle.get('batches') or []
    admitted=[]
    for b in all_batches:
        try:
            bt,rt=instant(b['started_at']),instant(b['received_at'])
            valid=start<=bt<=rt<=now and digest(b['rows'])==b['projection_sha256']
        except (ValueError,KeyError,TypeError):valid=False
        if not valid:
            rejected['INVALID_OR_OUT_OF_TIME_BATCH']+=1;continue
        admitted.append(b)
        if not set(b['requested_symbols']).issubset(requested):
            rejected['INVALID_OR_OUT_OF_TIME_BATCH']+=1;continue
        if b['status']!='RECEIVED':rejected['FAILED_BATCH']+=1;continue
        for q in b['rows']:
            symbol=q.get('requested_symbol')
            if symbol not in b['requested_symbols']:rejected['UNREQUESTED_SYMBOL']+=1;continue
            # Latest received projection wins; receipt time remains bound.
            old=quotes.get(symbol)
            if old is None or instant(old[1])<rt:quotes[symbol]=(q,b['received_at'])
    eligible={}; stock_n=0; spread=[]
    for symbol,(q,received_at) in quotes.items():
        if str(q.get('type') or '').lower()=='stock':stock_n+=1
        selected,reason=_price(q,now,received_at)
        prev=num(q.get('prevclose'))
        if selected is None or prev is None or prev<=0:
            rejected[reason or 'UNKNOWN_PREVIOUS_CLOSE']+=1;continue
        price=selected['price']
        move=float((Decimal(str(price))/Decimal(str(prev))-1)*100)
        if not math.isfinite(move):rejected['INVALID_DAY_CHANGE']+=1;continue
        entry={'symbol':symbol,'day_pct':move,'price':price,'previous_close':prev,
               'event_at':selected['timestamp'],'received_at':received_at,
               'price_kind':selected.get('kind'),'provider_session_high':num(q.get('high')),
               'volume':num(q.get('volume')),'average_volume':num(q.get('average_volume'))}
        eligible[symbol]=entry
        try:
            bid,ask=num(q.get('bid')),num(q.get('ask'))
            times=[instant(q.get('bid_date')),instant(q.get('ask_date'))]
            if bid and ask and 0<bid<=ask and all(t<=instant(received_at) and 0<=(now-t).total_seconds()<=120 for t in times):
                spread.append((ask-bid)/((ask+bid)/2)*100)
        except ValueError:pass
    n=len(eligible);scope='fresh stock observations in actual full discovery request roster'
    comps={}
    for threshold in (20,50,100,200):
        comps[f'stocks_up_{threshold}_pct']=component(sum(v['day_pct']>=threshold for v in eligible.values()) if n else None,n,scope)
    roster_cov=100*len(quotes)/len(requested) if requested else None
    fresh_cov=100*n/stock_n if stock_n else None
    reasons=[]
    if len(requested)<formula['minimum_requested_symbols']:reasons.append('FULL_MARKET_ROSTER_UNAVAILABLE_OR_TOO_SMALL')
    if roster_cov is None or roster_cov<formula['minimum_roster_coverage_pct']:reasons.append('ROSTER_COVERAGE_BELOW_90_PERCENT')
    if fresh_cov is None or fresh_cov<formula['minimum_fresh_stock_coverage_pct']:reasons.append('FRESH_STOCK_COVERAGE_BELOW_80_PERCENT')
    if stock_n<formula['minimum_returned_stocks']:reasons.append('INSUFFICIENT_STOCK_OBSERVATIONS')
    if phase not in ('premarket','regular','afterhours'):reasons.append('UNSUPPORTED_SESSION')
    local=now.astimezone(ET); minute=local.hour*60+local.minute
    expected_phase='premarket' if 240<=minute<570 else 'regular' if 570<=minute<960 else 'afterhours' if 960<=minute<1200 else 'closed'
    if phase!=expected_phase or local.weekday()>=5:reasons.append('AS_OF_PHASE_OR_WEEKDAY_MISMATCH')
    if rejected['INVALID_OR_OUT_OF_TIME_BATCH']:reasons.append('OUT_OF_TIME_INPUT_REJECTED')
    value=None if reasons else round(sum(term['points']*min(comps[f"stocks_up_{term['day_change_at_least_pct']}_pct"]['value']/term['saturation_count'],1) for term in formula['terms']),2)
    regime='UNKNOWN' if value is None else next(name for name,lower in reversed(list(formula['regime_lower_bounds'].items())) if value>=lower)
    dv=[v['price']*v['volume'] for v in eligible.values() if v['day_pct']>=20 and num(v['volume']) is not None and v['volume']>=0]
    comps['speculative_dollar_volume_proxy']=component(sum(dv) if dv else None,len(dv),'fresh >=20% movers; price × reported cumulative volume, not summed traded dollar volume')
    comps['speculative_top5_dollar_volume_share_pct']=component(100*sum(sorted(dv,reverse=True)[:5])/sum(dv) if dv and sum(dv)>0 else None,len(dv),'same speculative proxy')
    comps['median_spread_pct']=component(median(spread) if spread else None,len(spread),scope+'; both quote sides <=120s')
    for ph in ('premarket','regular'):
        comps[f'{ph}_momentum_breadth']=component(comps['stocks_up_20_pct']['value'] if phase==ph else None,n if phase==ph else 0,scope, 'Separate phase observation; not a daily aggregate')

    # Derived candidate features existed at the consumer boundary but do not
    # have complete raw-bar ancestry. They never enter the numeric heat score.
    features=[]
    observed=bundle.get('features_observed_at')
    try:features_available=bool(observed) and start<=instant(observed)<=now
    except ValueError:features_available=False
    if features_available:
        for row in bundle.get('candidate_features') or []:
            event=row.get('live_price_timestamp') or row.get('radar_quote_timestamp')
            try:current=0<=(now-instant(event)).total_seconds()<=120
            except ValueError:current=False
            integrity=row.get('action_data_integrity_ok') is True or row.get('live_price_state') in ('LIVE','FALLBACK')
            if row.get('action_data_integrity_ok') is False:integrity=False
            if current and integrity:features.append(row)
    fs='analyzed candidate subset, current-price clock and passing saved price/integrity flag; derived consumer values, raw feature ancestry UNKNOWN'
    def aggregate(name,field,fn):
        vals=[r[field] for r in features if num(r.get(field)) is not None or (field in {'above_vwap','vwap_reclaim','breakout_holding'} and type(r.get(field)) is bool)]
        comps[name]=component(fn(vals) if vals else None,len(vals),fs)
    comps['momentum_candidate_breadth']=component(len(bundle.get('candidate_features') or []) if features_available else None,len(bundle.get('candidate_features') or []) if features_available else 0,'actual analyzed publication population, not whole market')
    aggregate('median_volume_pace','volume_pace',median)
    aggregate('elevated_volume_pace_count','volume_pace',lambda xs:sum(x>=2 for x in xs))
    aggregate('above_vwap_pct','above_vwap',lambda xs:100*sum(x is True or x==1 for x in xs)/len(xs))
    aggregate('vwap_reclaim_flag_pct','vwap_reclaim',lambda xs:100*sum(x==1 for x in xs)/len(xs))
    aggregate('repeated_continuation_legs_count','bounce_count',lambda xs:sum(x>=2 for x in xs))
    aggregate('breakout_holding_flag_pct','breakout_holding',lambda xs:100*sum(x==1 for x in xs)/len(xs))
    for name,why in [('new_session_high_count','Exact high-crossing events are not supplied'),
                     ('successful_vwap_reclaim_pct','A reclaim flag is not a verified successful subsequent hold'),
                     ('holding_after_first_major_pullback_pct','Ordered first-pullback/hold evidence unavailable'),
                     ('volatility_halt_count','No causally timestamped halt feed is connected')]:
        comps[name]=component(None,0,scope,why)
    history=[]
    for old in prior:
        try:
            if instant(old['published_at'])<=now and instant(old['snapshot']['as_of'])<now:
                validate_snapshot(old['snapshot']);history.append(old)
        except (ValueError,KeyError,TypeError):continue
    history.sort(key=lambda r:r['snapshot']['as_of'])
    previous_dates=sorted({h['snapshot']['clock']['session_date'] for h in history if h['snapshot']['clock']['session_date']<clock['session_date']},reverse=True)[:3]
    prior_sessions=[]
    for d in previous_dates:
        h=next(h for h in reversed(history) if h['snapshot']['clock']['session_date']==d)
        prior_sessions.append({'snapshot_id':h['snapshot']['snapshot_id'],'as_of':h['snapshot']['as_of'],'session_date':d,
                               'regime':h['snapshot']['regime'],'value':h['snapshot']['value'],'formula_version':h['snapshot']['formula']['version']})
    prior_sessions += [None]*(3-len(prior_sessions))
    previous=next((h for h in reversed(history) if h['snapshot']['clock']['session_date']<clock['session_date']),None)
    previous_runners={r['symbol'] for r in previous.get('observed_stocks',[]) if r['day_pct']>=20} if previous else set()
    matches=previous_runners & set(eligible)
    comps['prior_observed_session_runners_positive_today']=component(sum(eligible[s]['day_pct']>0 for s in matches) if matches else None,len(matches),'previous observed session >=20% runners matched today; not certified previous close or immediate calendar predecessor')
    body={'schema_version':1,'scan_id':scan_id,'as_of':as_of,'clock':clock,'formula':formula,
          'formula_sha256':digest(formula),'value':value,'regime':regime,'components':comps,
          'term_contributions':{f"up_{t['day_change_at_least_pct']}_pct":
              round(t['points']*min(comps[f"stocks_up_{t['day_change_at_least_pct']}_pct"]['value']/t['saturation_count'],1),6)
              if value is not None else None for t in formula['terms']},
          'coverage':{'requested':len(requested),'returned':len(quotes),'returned_stocks':stock_n,'usable_fresh_stocks':n,
                      'roster_pct':roster_cov,'fresh_stock_pct':fresh_cov,'rejections':dict(rejected),'reasons':reasons},
          'input_sha256':digest(bundle),'observed_stocks_sha256':digest(sorted(eligible.values(),key=lambda r:r['symbol'])),
          'prior_sessions':prior_sessions,
          'prior_session_semantics':'last 1/2/3 OBSERVED session dates; missing sessions/calendar continuity UNKNOWN',
          'prior_same_session_snapshot_ids':[h['snapshot']['snapshot_id'] for h in history if h['snapshot']['clock']['session_date']==clock['session_date']],
          'evidence_status':'POINT_IN_TIME_CONSUMER_PROJECTION; PROVIDER_REFERENCE_AND_WHOLE_PATH_UNCERTIFIED',
          'context_only':True,'canonical_entry':False}
    body['snapshot_id']=digest(body)
    return body,sorted(eligible.values(),key=lambda r:r['symbol'])


def validate_snapshot(snapshot):
    if not isinstance(snapshot,dict) or not isinstance(snapshot.get('formula'),dict):raise ValueError('invalid heat snapshot')
    required={'snapshot_id','as_of','scan_id','clock','formula_sha256','value','regime','components','coverage',
              'input_sha256','observed_stocks_sha256','prior_sessions','context_only','evidence_status'}
    if not required.issubset(snapshot):raise ValueError('incomplete heat snapshot')
    if not all(isinstance(snapshot[k],dict) for k in ('clock','components','coverage')):raise ValueError('invalid heat details')
    body={k:v for k,v in snapshot.items() if k!='snapshot_id'}
    if digest(body)!=snapshot.get('snapshot_id') or digest(snapshot['formula'])!=snapshot['formula_sha256']:
        raise ValueError('Market Heat identity mismatch')
    if snapshot.get('context_only') is not True:raise ValueError('not a context snapshot')
    if snapshot.get('regime') not in {'UNKNOWN','COLD','NORMAL','HOT','EXTREME'}:raise ValueError('invalid regime')
    if snapshot.get('regime')=='UNKNOWN':
        if snapshot.get('value') is not None:raise ValueError('unknown heat must have no score')
    elif num(snapshot.get('value')) is None or not 0<=snapshot['value']<=100:raise ValueError('invalid heat score')
    return snapshot


def _archive_root(scan_log_dir):return Path(scan_log_dir)/'market_heat'


def _write_once(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('xb') as handle:handle.write(raw)
    except FileExistsError:
        if path.read_bytes()!=raw:raise ValueError('immutable Market Heat conflict')


def load_archive(root,snapshot_id):
    if not isinstance(snapshot_id,str) or len(snapshot_id)!=64 or any(x not in '0123456789abcdef' for x in snapshot_id):raise ValueError('invalid heat ID')
    body=json.loads(gzip.decompress((Path(root)/(snapshot_id+'.json.gz')).read_bytes()))
    validate_snapshot(body['snapshot'])
    if body['snapshot']['snapshot_id']!=snapshot_id or digest(body['inputs'])!=body['snapshot']['input_sha256']:raise ValueError('heat input identity mismatch')
    if digest(body['observed_stocks'])!=body['snapshot']['observed_stocks_sha256']:raise ValueError('heat stock projection identity mismatch')
    return body


def history(scan_log_dir,as_of):
    root=_archive_root(scan_log_dir);db=root/'index.sqlite3'
    if not db.exists():return []
    # Indexed, bounded reads; never scan years of receipt files on a UI rerun.
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.1)) as conn:
        found=conn.execute('SELECT id,published_at,session_date FROM snapshots WHERE published_at<=? AND as_of<? ORDER BY as_of DESC LIMIT 2000',(instant(as_of).isoformat(),instant(as_of).isoformat())).fetchall()
    selected=[];seen=set();today=instant(as_of).astimezone(ET).date().isoformat()
    for identity,published,d in found:
        if d in seen:continue
        if d<today and sum(x<today for x in seen)>=3:continue
        seen.add(d);body=load_archive(root,identity)
        selected.append({'snapshot':body['snapshot'],'observed_stocks':body['observed_stocks'],'published_at':published})
    return selected


def attach_publication(payload,rows,*,batches,scan_log_dir,requested_symbols=()):
    """Called only after decisions/ranking; changes the serialized projection only."""
    as_of=payload['decision_time_utc']
    features=[{k:clean_scalar(r.get(k)) for k in FEATURE_FIELDS} for r in rows]
    bundle={'batches':copy.deepcopy(batches or []),'requested_symbols':sorted(set(requested_symbols)),
            'candidate_features':features,'features_observed_at':as_of}
    try:past=history(Path(scan_log_dir).resolve(),as_of)
    except (OSError,ValueError,sqlite3.Error):past=[]
    snapshot,stocks=calculate(bundle,scan_id=payload['scan_id'],as_of=as_of,scan_started_at=payload['scan_time_utc'],phase=payload['session_phase'],prior=past)
    archived={'snapshot':snapshot,'inputs':bundle,'observed_stocks':stocks}
    raw=gzip.compress(canonical(archived).encode(),compresslevel=1,mtime=0)
    _write_once(_archive_root(scan_log_dir)/(snapshot['snapshot_id']+'.json.gz'),raw)
    payload['market_regime']=snapshot
    for candidate in payload['candidates']:
        candidate['candidate_decision_time_utc']=as_of
        candidate['market_regime_id']=snapshot['snapshot_id']
        candidate['market_regime_formula_version']=snapshot['formula']['version']
        candidate['market_regime_components']=copy.deepcopy(snapshot['components'])
    return snapshot


def mark_published(payload,scan_log_dir,*,published_at=None):
    s=payload.get('market_regime')
    if not s:return
    validate_snapshot(s)
    at=instant(published_at or utcnow()).isoformat()
    if instant(at)<instant(s['as_of']):raise ValueError('heat publication precedes decision')
    root=_archive_root(scan_log_dir)
    load_archive(root,s['snapshot_id'])
    with closing(sqlite3.connect(root/'index.sqlite3',timeout=.1)) as conn:
        with conn:
            conn.execute('CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, scan_id TEXT, as_of TEXT, published_at TEXT, session_date TEXT)')
            conn.execute('CREATE INDEX IF NOT EXISTS snapshot_time ON snapshots(as_of,published_at)')
            conn.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?)',(s['snapshot_id'],payload['scan_id'],instant(s['as_of']).isoformat(),at,s['clock']['session_date']))


def launch_context(payload,symbol):
    """Pass the exact displayed context; never silently select another symbol/scan."""
    s=(payload or {}).get('market_regime')
    if not s:return None
    try:validate_snapshot(s)
    except (ValueError,KeyError,TypeError):return None
    match=next((r for r in payload.get('candidates',[]) if r.get('symbol')==symbol),None)
    if not match or match.get('market_regime_id')!=s['snapshot_id']:return None
    return {'snapshot':copy.deepcopy(s),'source_scan_id':payload['scan_id'],
            'payload_sha256':digest(payload),'symbol':symbol}


def validate_launch(context,binding,symbol):
    if not context or not binding:return None
    try:
        s=validate_snapshot(context['snapshot'])
        if context['source_scan_id']!=binding['scan_id'] or context['payload_sha256']!=binding['payload_sha256'] or context['symbol']!=symbol or s['scan_id']!=binding['scan_id']:return None
        return copy.deepcopy(context)
    except (ValueError,KeyError,TypeError):return None


def analyzer_context(symbol,as_of,*,scan_log_dir=None,environ=None):
    import os
    env=os.environ if environ is None else environ
    if env.get('ANALYZER_MARKET_REGIME_CONTEXT'):
        try:
            c=validate_launch(json.loads(env['ANALYZER_MARKET_REGIME_CONTEXT']),json.loads(env.get('ANALYZER_SOURCE_PUBLICATION','null')),symbol)
            if c and instant(c['snapshot']['as_of'])<=instant(as_of):return c
        except (ValueError,TypeError):pass
        return None  # Invalid explicit association never falls back to another scan.
    if env.get('ANALYZER_SOURCE_PUBLICATION'):return None
    try:
        root=Path(scan_log_dir or env.get('SCAN_LOG_DIR',str(Path(__file__).parent/'scan_logs'))).resolve()
        options=history(root,as_of)
        current=[h for h in options if h['snapshot']['clock']['session_date']==instant(as_of).astimezone(ET).date().isoformat()]
        if current:
            latest=max(current,key=lambda h:h['snapshot']['as_of'])
            return {'snapshot':latest['snapshot'],'symbol':symbol,'association':'LATEST_PRIOR_PUBLISHED_MARKET_CONTEXT_NOT_CANDIDATE_LINK'}
    except (ValueError,OSError,sqlite3.Error):pass
    return None
