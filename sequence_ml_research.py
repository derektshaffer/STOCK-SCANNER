"""Walk-forward research benchmark for ordered candle-sequence ML.

Compares three models on exactly the same historical Scanner observations:
1. current structured Scanner features;
2. ordered five-minute candle sequence only;
3. hybrid structured + sequence features.

This script is research-only. It never writes a production model and cannot
change Scanner rank, Analyzer entry, stop, target, action, or confidence.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path

import scanner_ml_ranker as scanner_ml
from sequence_features import (
    SEQUENCE_BAR_FEATURES,
    SEQUENCE_INPUT_VERSION,
    SEQUENCE_MAX_BARS,
    flat_feature_names,
    flatten_sequence,
)


MODEL_VERSION="sequence-ml-research-v1-xgb-ordered-5m"
DEFAULT_REPLAY_PATH=Path("outcome_reports/outcomes_historical_replay.json")
DEFAULT_SEQUENCE_PATH=Path("outcome_reports/sequence_replay_training.json.gz")
DEFAULT_OUTPUT_PATH=Path("outcome_reports/sequence_ml_validation.json")


def _num(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _auc(y_true,probs):
    pairs=[
        (float(p),int(y))
        for y,p in zip(y_true,probs)
        if _num(p) is not None and y in (0,1)
    ]
    n_pos=sum(y for _p,y in pairs)
    n_neg=len(pairs)-n_pos
    if n_pos==0 or n_neg==0:
        return None
    pairs.sort(key=lambda item:item[0])
    rank_sum=0.0
    rank=1
    i=0
    while i<len(pairs):
        j=i+1
        while j<len(pairs) and pairs[j][0]==pairs[i][0]:
            j+=1
        avg_rank=(rank+(rank+(j-i)-1))/2.0
        rank_sum+=avg_rank*sum(pairs[k][1] for k in range(i,j))
        rank+=j-i
        i=j
    return (rank_sum-n_pos*(n_pos+1)/2.0)/(n_pos*n_neg)


def _brier(y_true,probs):
    vals=[
        (float(p)-int(y))**2
        for y,p in zip(y_true,probs)
        if _num(p) is not None and y in (0,1)
    ]
    return sum(vals)/len(vals) if vals else None


def _top_bucket(y_true,probs,fraction=0.10):
    pairs=sorted(
        [(float(p),int(y)) for y,p in zip(y_true,probs) if _num(p) is not None],
        reverse=True,
    )
    if not pairs:
        return {"n":0,"positive_rate_pct":None}
    n=max(1,int(math.ceil(len(pairs)*fraction)))
    chosen=pairs[:n]
    return {
        "n":n,
        "positive_rate_pct":round(sum(y for _p,y in chosen)/n*100.0,2),
    }


def _metrics(y_true,probs):
    auc=_auc(y_true,probs)
    brier=_brier(y_true,probs)
    base=(sum(y_true)/len(y_true)) if y_true else None
    top=_top_bucket(y_true,probs)
    return {
        "n":len(y_true),
        "auc":round(auc,6) if auc is not None else None,
        "brier":round(brier,6) if brier is not None else None,
        "base_positive_rate_pct":round(base*100.0,2) if base is not None else None,
        "top_decile":top,
        "top_decile_lift_pp":(
            round(top["positive_rate_pct"]-base*100.0,2)
            if top.get("positive_rate_pct") is not None and base is not None
            else None
        ),
    }


def chronological_day_folds(rows):
    dates=sorted({str(row.get("session_date") or "") for row in rows if row.get("session_date")})
    if len(dates)<8:
        return []
    ratios=(0.50,0.65,0.80,0.90,1.00)
    bounds=[]
    for ratio in ratios:
        value=int(round(len(dates)*ratio))
        value=max(1,min(len(dates),value))
        if not bounds or value>bounds[-1]:
            bounds.append(value)
    if bounds[-1]!=len(dates):
        bounds.append(len(dates))
    folds=[]
    for i in range(len(bounds)-1):
        train_end=bounds[i]
        val_end=bounds[i+1]
        if train_end<4 or val_end<=train_end:
            continue
        train_dates=set(dates[:train_end])
        val_dates=set(dates[train_end:val_end])
        train=[idx for idx,row in enumerate(rows) if row.get("session_date") in train_dates]
        val=[idx for idx,row in enumerate(rows) if row.get("session_date") in val_dates]
        if train and val:
            folds.append({
                "train_indices":train,
                "validation_indices":val,
                "train_start":dates[0],
                "train_end":dates[train_end-1],
                "validation_start":dates[train_end],
                "validation_end":dates[val_end-1],
            })
    return folds


def _load_rows(replay_path,sequence_path):
    replay=json.loads(Path(replay_path).read_text(encoding="utf-8"))
    observations={
        str(row.get("observation_id")):row
        for row in replay.get("observations") or []
        if row.get("observation_id")
    }
    with gzip.open(sequence_path,"rt",encoding="utf-8") as handle:
        sequence=json.load(handle)

    if sequence.get("sequence_version")!=SEQUENCE_INPUT_VERSION:
        raise RuntimeError(
            "Sequence input version mismatch: "
            f"{sequence.get('sequence_version')} != {SEQUENCE_INPUT_VERSION}"
        )

    rows=[]
    for record in sequence.get("records") or []:
        observation_id=str(record.get("observation_id") or "")
        obs=observations.get(observation_id)
        if not obs:
            continue
        outcome=_num(obs.get("return_60m_pct"))
        if outcome is None:
            continue
        sequence_payload={
            "sequence":record.get("sequence") or [],
            "sequence_version":sequence.get("sequence_version"),
        }
        seq=flatten_sequence(sequence_payload)
        if len(seq)!=SEQUENCE_MAX_BARS*len(SEQUENCE_BAR_FEATURES):
            continue
        structured_dict=scanner_ml._feature_dict(obs,obs.get("scan_time_et"))
        structured=[structured_dict.get(name) for name in scanner_ml.FEATURES]
        rows.append({
            "observation_id":observation_id,
            "session_date":record.get("session_date") or str(obs.get("scan_time_et") or "")[:10],
            "symbol":record.get("symbol") or obs.get("symbol"),
            "label":1 if outcome>=3.0 else 0,
            "return_60m_pct":outcome,
            "bars_available":int(record.get("bars_available") or 0),
            "structured":structured,
            "sequence":seq,
        })
    return rows,replay,sequence


def _matrix(rows,key,indices,np):
    data=[]
    labels=[]
    for idx in indices:
        row=rows[idx]
        vector=row.get(key)
        if key=="hybrid":
            vector=list(row.get("structured") or [])+list(row.get("sequence") or [])
        values=[
            float(value) if _num(value) is not None else np.nan
            for value in (vector or [])
        ]
        data.append(values)
        labels.append(int(row["label"]))
    return np.asarray(data,dtype=float),np.asarray(labels,dtype=int)


def _fit_predict(X_train,y_train,X_val):
    import numpy as np
    from xgboost import XGBClassifier

    positives=int(y_train.sum())
    negatives=int(len(y_train)-positives)
    if positives<5 or negatives<5:
        return None
    model=XGBClassifier(
        n_estimators=180,
        max_depth=4,
        learning_rate=0.04,
        min_child_weight=5,
        subsample=0.85,
        colsample_bytree=0.75,
        reg_lambda=2.0,
        reg_alpha=0.15,
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        n_jobs=2,
        random_state=42,
    )
    model.fit(X_train,y_train)
    return model.predict_proba(X_val)[:,1]


def run_benchmark(replay_path,sequence_path):
    import numpy as np

    rows,replay,sequence=_load_rows(replay_path,sequence_path)
    folds=chronological_day_folds(rows)
    results=[]
    aggregate={
        "structured":{"y":[],"p":[]},
        "sequence":{"y":[],"p":[]},
        "hybrid":{"y":[],"p":[]},
    }

    for fold_number,fold in enumerate(folds,1):
        train_idx=fold["train_indices"]
        val_idx=fold["validation_indices"]
        y_train=np.asarray([rows[i]["label"] for i in train_idx],dtype=int)
        y_val=[rows[i]["label"] for i in val_idx]
        if sum(y_train)<5 or (len(y_train)-sum(y_train))<5:
            continue
        if sum(y_val)<2 or (len(y_val)-sum(y_val))<2:
            continue

        fold_result={
            "fold":fold_number,
            "train_start":fold["train_start"],
            "train_end":fold["train_end"],
            "validation_start":fold["validation_start"],
            "validation_end":fold["validation_end"],
            "train_n":len(train_idx),
            "validation_n":len(val_idx),
            "models":{},
        }

        for key in ("structured","sequence","hybrid"):
            X_train,_=_matrix(rows,key,train_idx,np)
            X_val,_=_matrix(rows,key,val_idx,np)
            probs=_fit_predict(X_train,y_train,X_val)
            if probs is None:
                continue
            values=[float(x) for x in probs]
            metrics=_metrics(y_val,values)
            fold_result["models"][key]=metrics
            aggregate[key]["y"].extend(y_val)
            aggregate[key]["p"].extend(values)

        if len(fold_result["models"])==3:
            results.append(fold_result)

    overall={
        key:_metrics(value["y"],value["p"])
        for key,value in aggregate.items()
    }
    structured=overall.get("structured") or {}
    sequence_only=overall.get("sequence") or {}
    hybrid=overall.get("hybrid") or {}

    auc_lift=(
        (hybrid["auc"]-structured["auc"])
        if hybrid.get("auc") is not None and structured.get("auc") is not None
        else None
    )
    brier_delta=(
        (hybrid["brier"]-structured["brier"])
        if hybrid.get("brier") is not None and structured.get("brier") is not None
        else None
    )
    folds_beating=0
    comparable_folds=0
    for fold in results:
        base=(fold.get("models") or {}).get("structured") or {}
        candidate=(fold.get("models") or {}).get("hybrid") or {}
        if base.get("auc") is None or candidate.get("auc") is None:
            continue
        comparable_folds+=1
        if candidate["auc"]>base["auc"]:
            folds_beating+=1

    unique_dates=sorted({row["session_date"] for row in rows})
    unique_symbols={row.get("symbol") for row in rows if row.get("symbol")}
    class_positive=sum(row["label"] for row in rows)
    class_negative=len(rows)-class_positive

    candidate_gate=bool(
        len(rows)>=1000
        and len(unique_dates)>=10
        and class_positive>=100
        and class_negative>=100
        and len(results)>=3
        and auc_lift is not None
        and auc_lift>=0.01
        and brier_delta is not None
        and brier_delta<=0.002
        and folds_beating>=max(2,math.ceil(comparable_folds*0.60))
    )

    report={
        "schema_version":1,
        "model_version":MODEL_VERSION,
        "sequence_input_version":SEQUENCE_INPUT_VERSION,
        "status":"candidate_for_live_shadow" if candidate_gate else "experimental_no_validated_lift",
        "research_only":True,
        "production_enabled":False,
        "can_change_scanner_rank":False,
        "can_change_analyzer_trade_plan":False,
        "target":">= +3% at 60 minutes",
        "dataset":{
            "samples":len(rows),
            "unique_dates":len(unique_dates),
            "start_date":unique_dates[0] if unique_dates else None,
            "end_date":unique_dates[-1] if unique_dates else None,
            "unique_symbols":len(unique_symbols),
            "positive_class":class_positive,
            "negative_class":class_negative,
            "bar_resolution":sequence.get("bar_resolution"),
            "sequence_bars":SEQUENCE_MAX_BARS,
            "bar_features":list(SEQUENCE_BAR_FEATURES),
            "sequence_flat_features":len(flat_feature_names()),
            "structured_features":list(scanner_ml.FEATURES),
            "median_bars_available":(
                float(np.median([row["bars_available"] for row in rows]))
                if rows else None
            ),
        },
        "validation":{
            "split_unit":"whole_trading_day",
            "walk_forward":True,
            "folds":results,
            "fold_count":len(results),
            "structured_baseline":overall.get("structured"),
            "sequence_only":sequence_only,
            "hybrid":hybrid,
            "hybrid_minus_structured_auc":round(auc_lift,6) if auc_lift is not None else None,
            "hybrid_minus_structured_brier":round(brier_delta,6) if brier_delta is not None else None,
            "hybrid_auc_winning_folds":folds_beating,
            "comparable_folds":comparable_folds,
            "candidate_gate_passed":candidate_gate,
            "candidate_gate":{
                "min_samples":1000,
                "min_unique_dates":10,
                "min_each_class":100,
                "min_folds":3,
                "min_hybrid_auc_lift":0.01,
                "max_hybrid_brier_delta":0.002,
                "min_fraction_auc_winning_folds":0.60,
            },
        },
        "integrity":{
            "future_candles_visible":False,
            "sequence_cutoff":"matching historical replay decision candle",
            "normalization_scope":"bars at or before decision timestamp only",
            "labels_not_in_sequence":True,
            "validation_day_overlap":False,
        },
        "limitations":[
            "Historical Scanner replay currently uses consolidated Tradier 5-minute bars, while the live Analyzer may use finer one-minute bars.",
            "Historical bid/ask spread is not reconstructed.",
            "Point-in-time news/catalyst text is not reconstructed in this first sequence benchmark.",
            "Point-in-time options IV/open-interest/skew/Greeks are not yet reconstructed and are not included.",
            "The historical replay universe retains current-listed/current-liquid survivorship limitations.",
            "This is an ordered numerical candle model, not a chart-image/vision model.",
        ],
        "next_step":(
            "Run as a live shadow model and collect strictly later confirmation before any production influence."
            if candidate_gate
            else "Keep research-only; do not add production influence. Iterate only if a causal feature/data improvement is justified."
        ),
    }
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--replay",default=str(DEFAULT_REPLAY_PATH))
    parser.add_argument("--sequences",default=str(DEFAULT_SEQUENCE_PATH))
    parser.add_argument("--output",default=str(DEFAULT_OUTPUT_PATH))
    args=parser.parse_args()

    report=run_benchmark(Path(args.replay),Path(args.sequences))
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding="utf-8")

    validation=report.get("validation") or {}
    print("SEQUENCE_ML_STATUS="+str(report.get("status")))
    print("SEQUENCE_ML_SAMPLES="+str((report.get("dataset") or {}).get("samples")))
    print("SEQUENCE_ML_FOLDS="+str(validation.get("fold_count")))
    print("SEQUENCE_STRUCTURED_AUC="+str((validation.get("structured_baseline") or {}).get("auc")))
    print("SEQUENCE_ONLY_AUC="+str((validation.get("sequence_only") or {}).get("auc")))
    print("SEQUENCE_HYBRID_AUC="+str((validation.get("hybrid") or {}).get("auc")))
    print("SEQUENCE_HYBRID_AUC_LIFT="+str(validation.get("hybrid_minus_structured_auc")))
    print("SEQUENCE_HYBRID_BRIER_DELTA="+str(validation.get("hybrid_minus_structured_brier")))
    print("SEQUENCE_CANDIDATE_GATE="+str(bool(validation.get("candidate_gate_passed"))).lower())


if __name__=="__main__":
    main()
