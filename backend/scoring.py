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

HIST_BINS = 50        # score histogram on the dashboard: 50 bins of 2 points
TREND_POINTS = 5.0     # score change (0-100 scale) needed to count as UP / DOWN
TOP_N = 10
CATEGORIES = ('High', 'Medium', 'Low')


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def current_period():
    return now_utc().strftime('%Y-%m')


def hist_bin(score):
    return min(HIST_BINS - 1, max(0, int(float(score) // (100 / HIST_BINS))))


def score_histogram(scores):
    h = [0] * HIST_BINS
    for sc in scores:
        h[hist_bin(sc)] += 1
    return h


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
    old_rec = (old.to_dict() or {}) if old.exists else {}
    old_cat = old_rec.get('RiskCategory')
    old_score = old_rec.get('WeightedRiskScore')

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
    hist = list(meta.get('score_histogram') or [])
    if len(hist) == HIST_BINS:
        if old_score is not None:
            hist[hist_bin(old_score)] = max(0, hist[hist_bin(old_score)] - 1)
        hist[hist_bin(score)] += 1
        meta['score_histogram'] = hist
    top = [t for t in (meta.get('top_risky') or []) if t.get('SellerID') != seller_id]
    top.append(top_entry(rec))
    top.sort(key=lambda t: t.get('WeightedRiskScore', 0), reverse=True)
    if old_cat is None and 'sellers_scored' in meta:
        meta['sellers_scored'] = int(meta['sellers_scored']) + 1
    meta.update({'period': period, 'model_version': model_version,
                 'category_counts': counts, 'top_risky': top[:TOP_N],
                 'last_updated': now_utc().isoformat()})
    meta_ref.set(meta)
    return rec


def remove_from_meta(db, rec):
    """A score was deleted: take it back out of the dashboard counts, histogram and top list."""
    meta_ref = db.collection('system_config').document('dashboard_metadata')
    meta_doc = meta_ref.get()
    meta = meta_doc.to_dict() if meta_doc.exists else {}
    if not rec or meta.get('period') != rec.get('EvaluationPeriod'):
        return
    counts = {c: int((meta.get('category_counts') or {}).get(c, 0)) for c in CATEGORIES}
    if rec.get('RiskCategory') in counts:
        counts[rec['RiskCategory']] = max(0, counts[rec['RiskCategory']] - 1)
    hist = list(meta.get('score_histogram') or [])
    if len(hist) == HIST_BINS and rec.get('WeightedRiskScore') is not None:
        b = hist_bin(rec['WeightedRiskScore'])
        hist[b] = max(0, hist[b] - 1)
        meta['score_histogram'] = hist
    if 'sellers_scored' in meta:
        meta['sellers_scored'] = max(0, int(meta['sellers_scored']) - 1)
    meta['top_risky'] = [t for t in (meta.get('top_risky') or []) if t.get('SellerID') != rec.get('SellerID')]
    meta.update({'category_counts': counts, 'last_updated': now_utc().isoformat()})
    meta_ref.set(meta)


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
        'Score': rec['WeightedRiskScore'],
    }
