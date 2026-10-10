"""
risk_scoring_pipeline.py
------------------------
Monthly batch job: score every seller and refresh alerts + dashboard summary.

    python pipeline/risk_scoring_pipeline.py

Reads every order document (~400k Firestore reads), so run it once per period,
not repeatedly.
"""
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from google.cloud.firestore_v1.base_query import FieldFilter  # noqa: E402

from backend import scoring  # noqa: E402
from backend.features import compute_seller_features, fetch_frames  # noqa: E402
from backend.ml_service import get_model, score_frame  # noqa: E402

BATCH = 400


def _commit_in_batches(db, ops):
    """ops: list of (kind, ref, data). kind = 'set' | 'delete'."""
    batch, n = db.batch(), 0
    for kind, ref, data in ops:
        if kind == 'set':
            batch.set(ref, data)
        else:
            batch.delete(ref)
        n += 1
        if n >= BATCH:
            batch.commit()
            batch, n = db.batch(), 0
    if n:
        batch.commit()


def previous_scores(db, period, model_version):
    """{SellerID: latest earlier-period score from the same model version}."""
    prev = {}
    for doc in db.collection('risk_scores').where(filter=FieldFilter('EvaluationPeriod', '<', period)).stream():
        d = doc.to_dict() or {}
        if d.get('ModelVersion') != model_version:
            continue
        sid, ep = d.get('SellerID'), d.get('EvaluationPeriod')
        if sid not in prev or ep > prev[sid][0]:
            prev[sid] = (ep, float(d.get('WeightedRiskScore', 0)))
    return {sid: v[1] for sid, v in prev.items()}


def run_pipeline(db=None):
    if db is None:
        from backend.firebase_client import db
    start = time.time()
    _, card = get_model()                    # fail fast if the model isn't trained
    version = card['model_version']
    period = scoring.current_period()
    print(f"Scoring period {period} with model {version}")

    print("Fetching data from Firestore...")
    frames = fetch_frames(db)
    feats = compute_seller_features(frames)
    feats = feats[feats['total_orders'] > 0]   # no orders = nothing to judge
    print(f"Sellers with orders: {len(feats)}")

    scored = feats.join(score_frame(feats))
    prev = previous_scores(db, period, version)

    records, ops = {}, []
    for sid, row in scored.iterrows():
        rec = scoring.score_record(sid, period, row['RiskScore'], row['RiskCategory'],
                                   scoring.trend(row['RiskScore'], prev.get(sid)), version)
        records[sid] = rec
        ops.append(('set', db.collection('risk_scores').document(scoring.score_doc_id(sid, period)), rec))
    print(f"Writing {len(ops)} risk scores...")
    _commit_in_batches(db, ops)

    # --- alerts: replace this period's High alerts --------------------------
    prev_high, stale = set(), []
    for doc in db.collection('alert_log').where(
            filter=FieldFilter('ThresholdCrossedCategory', '==', 'High')).stream():
        d = doc.to_dict() or {}
        if d.get('EvaluationPeriod', '') < period:
            prev_high.add(d.get('SellerID'))
        elif d.get('EvaluationPeriod') == period:
            stale.append(('delete', doc.reference, None))
    _commit_in_batches(db, stale)

    high = scored[scored['RiskCategory'] == 'High']
    alert_ops = []
    for sid, row in high.iterrows():
        alert = scoring.high_alert(sid, row['sellerName'], records[sid], 2 if sid in prev_high else 1)
        alert_ops.append(('set', db.collection('alert_log').document(), alert))
    print(f"Writing {len(alert_ops)} High-risk alerts...")
    _commit_in_batches(db, alert_ops)

    # --- dashboard summary ---------------------------------------------------
    counts = scored['RiskCategory'].value_counts().to_dict()
    top = scored.nlargest(scoring.TOP_N, 'RiskScore').index
    db.collection('system_config').document('dashboard_metadata').set({
        'period': period,
        'model_version': version,
        'category_counts': {c: int(counts.get(c, 0)) for c in scoring.CATEGORIES},
        'top_risky': [scoring.top_entry(records[sid]) for sid in top],
        'last_updated': scoring.now_utc().isoformat(),
    })

    print("-" * 50)
    print(f"Scored {len(scored)} sellers: "
          f"{counts.get('Low', 0)} Low, {counts.get('Medium', 0)} Medium, {counts.get('High', 0)} High")
    for i, sid in enumerate(top[:5], 1):
        r = records[sid]
        print(f"{i}. {sid}  score {r['WeightedRiskScore']:.1f}  ({r['RiskCategory']}, {r['RiskTrend']})")
    print(f"Elapsed {time.time() - start:.1f}s")
    return scored


if __name__ == '__main__':
    run_pipeline()
