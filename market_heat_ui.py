"""Compact display-only context; never writes to Scanner/Analyzer inputs."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from market_heat import validate_snapshot,instant


def view(snapshot,*,now=None):
    try:
        validate_snapshot(snapshot)
        at=instant(snapshot['as_of']);current=instant(now) if now else datetime.now(timezone.utc)
        if at>current:raise ValueError('future context')
    except (ValueError,TypeError,KeyError):
        return {'heading':'Market regime: UNKNOWN','caption':'Context only · no valid frozen market snapshot is linked.','details':None}
    age=(current-at).total_seconds()
    score=snapshot.get('value')
    heading=('Recorded market regime: ' if age>120 else 'Market regime: ')+snapshot['regime']
    if score is not None:heading+=f" · {score:g} / 100"
    clock=at.astimezone(ZoneInfo('America/Los_Angeles')).strftime('%b %d, %I:%M %p Pacific')
    return {'heading':heading,'caption':f'Context only · {clock} · provisional formula; recommendation unchanged.',
            'details':{'coverage':snapshot['coverage'],'components':snapshot['components'],
                       'formula':snapshot['formula'],'snapshot_id':snapshot['snapshot_id'],
                       'prior_sessions':snapshot['prior_sessions'],'session':snapshot['clock'],
                       'evidence':snapshot['evidence_status']}}


def render_market_heat(st,snapshot,*,now=None):
    model=view(snapshot,now=now)
    st.markdown('**'+model['heading']+'**')
    st.caption(model['caption'])
    if model['details'] is not None:
        with st.expander('Market Heat details',expanded=False):
            c=model['details']['coverage'];components=model['details']['components']
            st.write(f"Fresh stock observations: {c['usable_fresh_stocks']:,} / {c['returned_stocks']:,} returned stocks.")
            st.write('Observed large movers: '+', '.join(f"+{p}%: {components[f'stocks_up_{p}_pct']['value'] if components[f'stocks_up_{p}_pct']['value'] is not None else 'UNKNOWN'}" for p in (20,50,100,200)))
            if c['reasons']:st.caption('Heat is UNKNOWN because the required coverage or timing was not met.')
            st.caption('Counts describe the captured universe. Missing continuation, halt or input-ancestry evidence is not zero.')
            st.json(model['details'])
    return model
