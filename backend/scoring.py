"""
scoring.py
----------
Writes risk scores, alerts and the dashboard summary in ONE format, used by
both the monthly pipeline and the live simulator.

risk_scores/{SellerID}_{YYYY-MM}:
    SellerID, EvaluationPeriod, WeightedRiskScore (0-100), RiskCategory (Low/Medium/High),
    RiskTrend (UP/DOWN/STABLE/NEW), ModelVersion, UpdatedAt
system_config/dashboard_metadata:
    period, model_version, category_counts{High,Medium,Low}, top_risky[10], last_updated
"""
import datetime

from google.cloud.firestore_v1.base_query import FieldFilter

TREND_POINTS = 5.0     # score change (0-100 scale) needed to count as UP / DOWN
TOP_N = 10
CATEGORIES = ('High', 'Medium', 'Low')


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def current_period():
    return now_utc().strftime('%Y-%m')


def score_doc_id(seller_id, period):
    return f"{seller_id}_{period}"


def trend(current, previous):
    if previous is None:
        return 'NEW'
    if current - previous >= TREND_POINTS:
        return 'UP'
    if previous - current >= TREND_POINTS:
        return 'DOWN'
    return 'STABLE'


def previous_score(db, seller_id, period, model_version):
    """Latest score from an EARLIER period made by the same model version."""
    docs = db.collection('risk_scores').where(filter=FieldFilter('SellerID', '==', seller_id)).stream()
    best = None
    for doc in docs:
        d = doc.to_dict() or {}
        if d.get('EvaluationPeriod', '') < period and d.get('ModelVersion') == model_version:
            if best is None or d['EvaluationPeriod'] > best['EvaluationPeriod']:
                best = d
    return None if best is None else float(best.get('WeightedRiskScore', 0))


def score_record(seller_id, period, score, category, risk_trend, model_version):
    return {
        'SellerID': seller_id,
        'EvaluationPeriod': period,
        'WeightedRiskScore': float(score),
        'RiskCategory': category,
        'RiskTrend': risk_trend,
        'ModelVersion': model_version,
        'UpdatedAt': now_utc(),
    }


def top_entry(rec):
    return {k: rec[k] for k in ('SellerID', 'WeightedRiskScore', 'RiskCategory', 'RiskTrend')}


def write_single_score(db, seller_id, score, category, model_version):
    """
    Live update for one seller (simulator). Writes this month's score doc and
    patches dashboard_metadata so the dashboard moves immediately.
    Returns the record written.
    """
    period = current_period()
    ref = db.collection('risk_scores').document(score_doc_id(seller_id, period))
    old = ref.get()
    old_cat = (old.to_dict() or {}).get('RiskCategory') if old.exists else None

    rec = score_record(seller_id, period, score, category,
                       trend(score, previous_score(db, seller_id, period, model_version)),
                       model_version)
    ref.set(rec)

    meta_ref = db.collection('system_config').document('dashboard_metadata')
    meta_doc = meta_ref.get()
    meta = meta_doc.to_dict() if meta_doc.exists else {}
    if meta.get('period') not in (None, period):
        # Dashboard still shows last month's batch run; leave it alone.
        return rec
    counts = {c: int((meta.get('category_counts') or {}).get(c, 0)) for c in CATEGORIES}
    if old_cat in counts:
        counts[old_cat] = max(0, counts[old_cat] - 1)
    counts[category] += 1
    top = [t for t in (meta.get('top_risky') or []) if t.get('SellerID') != seller_id]
    top.append(top_entry(rec))
    top.sort(key=lambda t: t.get('WeightedRiskScore', 0), reverse=True)
    meta.update({'period': period, 'model_version': model_version,
                 'category_counts': counts, 'top_risky': top[:TOP_N],
                 'last_updated': now_utc().isoformat()})
    meta_ref.set(meta)
    return rec


def high_alert(seller_id, seller_name, rec, escalation):
    label = seller_name if seller_name else seller_id
    return {
        'SellerID': seller_id,
        'EvaluationPeriod': rec['EvaluationPeriod'],
        'AlertDate': now_utc(),
        'ThresholdCrossedCategory': 'High',
        'AlertMessage': f"Seller {label} scored {rec['WeightedRiskScore']:.1f} (High risk) "
                        f"for period {rec['EvaluationPeriod']}",
        'AlertStatus': 'Open',
        'EscalationLevel': escalation,
    }
