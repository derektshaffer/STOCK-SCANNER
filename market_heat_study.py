"""Offline observational regime/weekday analysis. Never calculates heat from outcomes.

Input windows use the explicit observational-label schema demonstrated by the
fixed continuation cohort. Prices are not executable entry returns. No network,
training, parameter fitting, snapshot mutation or provider backfill occurs here.
"""
import argparse
import json
import copy
from collections import defaultdict,Counter
from pathlib import Path
from statistics import median
from market_heat import validate_snapshot,instant,clock_metadata,num
from candidate_outcome_study import digest

REGIMES=('COLD','NORMAL','HOT','EXTREME','UNKNOWN')
BANDS=('20–50%','50–100%','100–200%','200%+','BELOW20','UNKNOWN')
WINDOWS=('m5','m15','m30','m60','active_session','same_day_rth','next_30m','next_rth')


def associate_publication(payload,published_at,labels):
    """Join existing passive_outcomes labels by the exact frozen candidate hash.

    Every publication candidate survives the join; missing labels remain empty
    (UNKNOWN), never dropped. The caller selects one outcome receipt/cutoff.
    """
    candidates=payload['candidates'];by_hash={digest(c):c for c in candidates};mapped={}
    for label in labels:
        identity=label['frozen_candidate_sha256']
        if identity not in by_hash or label['symbol']!=by_hash[identity]['symbol'] or identity in mapped:
            raise ValueError('outcome/candidate association mismatch or duplicate revision')
        if any(instant(w['start'])<instant(published_at) for w in label.get('windows',{}).values()):
            raise ValueError('outcome predates publication receipt')
        mapped[identity]=label
    result=[]
    for candidate in candidates:
        identity=digest(candidate);label=mapped.get(identity,{})
        result.append({'snapshot_id':digest([payload['scan_id'],identity]),'symbol':candidate['symbol'],
                       'scan_id':payload['scan_id'],'publication':published_at,'phase':payload['session_phase'],
                       'candidate_decision_time_utc':candidate.get('candidate_decision_time_utc',payload['decision_time_utc']),
                       'features':copy.deepcopy(candidate),'market_regime':copy.deepcopy(payload.get('market_regime')),
                       'market_regime_id':candidate.get('market_regime_id'),'windows':copy.deepcopy(label.get('windows',{})),
                       'reference':label.get('reference'),'canonical_entry':False,
                       'observation_kind':'OBSERVATIONAL_ONLY', 'outcome_candidate_sha256':identity})
    return result


def extension_band(value):
    value=num(value)
    if value is None:return 'UNKNOWN'
    if value<20:return 'BELOW20'
    if value<50:return '20–50%'
    if value<100:return '50–100%'
    if value<200:return '100–200%'
    return '200%+'


def regime_for(row):
    snapshot=row.get('market_regime')
    try:
        validate_snapshot(snapshot)
        when=instant(row.get('candidate_decision_time_utc') or row['publication'])
        if snapshot['snapshot_id']!=row.get('market_regime_id') or snapshot['scan_id']!=row['scan_id'] or instant(snapshot['as_of'])>when:
            raise ValueError('association/timing')
        return snapshot['regime'],snapshot['formula']['version']
    except (ValueError,KeyError,TypeError):return 'UNKNOWN','UNKNOWN'


def summarize(rows):
    result={'n':len(rows),'windows':{},'continuation_60m':{},'pullback_reclaim_60m':{},
            'new_high_continuation':{'witnessed':0,'complete_nonhit':0,'unknown':0},
            'interpretation':'OBSERVED_PRICE_CHANGES_NOT_TRADE_RETURNS'}
    for key in WINDOWS:
        wins=[r.get('windows',{}).get(key,{}) for r in rows]
        entry={'n':len(wins),'complete':sum(w.get('complete') is True for w in wins),
               'censored':sum(w.get('right_censored') is True for w in wins)}
        for metric in ('endpoint_change_pct','observed_mfe_pct','observed_mae_pct'):
            vals=[w[metric] for w in wins if num(w.get(metric)) is not None]
            entry[metric]={'known_n':len(vals),'unknown_n':len(wins)-len(vals),
                           'median':median(vals) if vals else None}
        result['windows'][key]=entry
    wins=[r.get('windows',{}).get('m60',{}) for r in rows]
    for threshold in (10,25,50,100):
        yes=no=0
        for w in wins:
            value=num(w.get('observed_high_change_pct'))
            if value is None:value=num(w.get('observed_mfe_pct'))
            if value is not None and value>=threshold:yes+=1
            elif value is not None and w.get('complete') is True:no+=1
        result['continuation_60m'][f'plus_{threshold}_pct']={'witnessed':yes,'complete_nonhit':no,'unknown':len(wins)-yes-no}
    for k in ('pullback','reclaim','failure'):
        result['pullback_reclaim_60m'][k]={'witnessed':sum(w.get(k) is True for w in wins),'complete_nonhit':sum(w.get(k) is False for w in wins),'unknown':sum(type(w.get(k)) is not bool for w in wins)}
    for window_name in ('active_session','next_rth'):
        result[window_name+'_continuation']={}
        for threshold in (10,25,50,100):
            yes=no=0
            for row in rows:
                w=row.get('windows',{}).get(window_name,{})
                value=num(w.get('observed_high_change_pct'))
                if value is None:value=num(w.get('observed_mfe_pct'))
                if value is not None and value>=threshold:yes+=1
                elif value is not None and w.get('complete') is True:no+=1
            result[window_name+'_continuation'][f'plus_{threshold}_pct']={'witnessed':yes,'complete_nonhit':no,'unknown':len(rows)-yes-no}
    for row in rows:
        f=row.get('features') or {};w=row.get('windows',{}).get('active_session',{})
        high=num(f.get('session_high'));outcome=num(w.get('observed_high'))
        try:
            valid=bool(f.get('session_high_source')) and instant(f['session_high_available_at'])<=instant(row.get('candidate_decision_time_utc') or row['publication'])
        except (KeyError,ValueError,TypeError):valid=False
        key='unknown'
        if valid and high is not None and high>0 and outcome is not None:
            if outcome>high:key='witnessed'
            elif w.get('complete') is True:key='complete_nonhit'
        result['new_high_continuation'][key]+=1
    return result


def build_study(rows):
    groups=defaultdict(list); weekday=defaultdict(list); conditional=defaultdict(list); tod=defaultdict(list)
    versions=Counter(); ids=[];seen=set()
    for row in rows:
        identity=row['snapshot_id']
        if identity in seen:raise ValueError('duplicate candidate observation')
        seen.add(identity)
        regime,version=regime_for(row); versions[version]+=1
        band=extension_band(row['features'].get('day_pct'))
        clock=clock_metadata(row.get('candidate_decision_time_utc') or row['publication'],row['phase'])
        time_bucket=clock['time_of_day'] if row.get('phase_valid',True) else 'UNKNOWN_PHASE_BOUNDARY'
        # Windows must occur at/after the candidate observation; reject accidental
        # backward labels instead of silently blending decision and outcome data.
        candidate_time=instant(row.get('candidate_decision_time_utc') or row['publication'])
        for window in row.get('windows',{}).values():
            if 'start' in window and instant(window['start'])<candidate_time:raise ValueError('outcome window predates candidate')
        groups[(regime,band,version)].append(row)
        weekday[clock['weekday']].append(row)
        conditional[(clock['weekday'],regime,version)].append(row)
        tod[(time_bucket,regime,version)].append(row)
        ids.append({'snapshot_id':identity,'symbol':row['symbol'],'band':band,'regime':regime,
                    'formula_version':version,'weekday':clock['weekday'],'time_of_day':time_bucket,
                    'market_regime_id':row.get('market_regime_id'),'candidate_time':candidate_time.isoformat()})
    # Empty regime cells are shown, not omitted as if they had zero returns.
    cells=[]
    for regime in REGIMES:
        for band in BANDS:
            matching=[k for k in groups if k[:2]==(regime,band)] or [(regime,band,'UNKNOWN')]
            for key in matching:cells.append({'regime':regime,'band':band,'formula_version':key[2],**summarize(groups[key])})
    return {'version':'market-heat-observational-study-v1','n':len(rows),'formula_versions':dict(versions),
            'all_regime_band_cells':cells,'weekday':[{'weekday':k,**summarize(v)} for k,v in weekday.items()],
            'weekday_conditioned_on_regime':[{'weekday':k[0],'regime':k[1],'formula_version':k[2],**summarize(v)} for k,v in conditional.items()],
            'time_of_day_regime':[{'time_of_day':k[0],'regime':k[1],'formula_version':k[2],**summarize(v)} for k,v in tod.items()],
            'candidate_associations':ids,'inference':'DESCRIPTIVE; repeated snapshots correlated; missing heat cannot identify weekday effects conditional on regime'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('labels');parser.add_argument('output');args=parser.parse_args()
    source=json.loads(Path(args.labels).read_text());rows=source['results']
    output={'all_snapshots':build_study(rows),'primary_symbol_days':build_study([r for r in rows if r.get('is_primary_symbol_day')])}
    path=Path(args.output)
    data=json.dumps(output,sort_keys=True,indent=2,allow_nan=False)+'\n'
    try:
        with path.open('x') as handle:handle.write(data)
    except FileExistsError:
        if path.read_text()!=data:raise ValueError('study version conflict; use a new output path')
    print(json.dumps({'snapshots':len(rows),'primary_symbol_days':output['primary_symbol_days']['n'],
                      'formula_versions':output['all_snapshots']['formula_versions']}))


if __name__=='__main__':main()
