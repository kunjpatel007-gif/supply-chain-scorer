"""
routes/dashboard.py
-------------------
Main dashboard, category listing, the seller cache and the login guard.
"""
import threading
from functools import wraps

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.firebase_client import db
from backend import scoring
from backend.ml_service import model_card

dashboard_bp = Blueprint('dashboard', __name__)

# In-memory seller cache: {sellerId: {sellerName, sellerCity, sellerState}}.
# Per process; add/edit/delete keep it in sync.
_SELLERS = None
_SELLERS_LOCK = threading.Lock()


def get_sellers():
    global _SELLERS
    with _SELLERS_LOCK:
        if _SELLERS is None:
            _SELLERS = {s.id: s.to_dict() or {} for s in db.collection('sellers').stream()}
        return _SELLERS


def cache_put(seller_id, data):
    with _SELLERS_LOCK:
        if _SELLERS is not None:
            _SELLERS[seller_id] = {**_SELLERS.get(seller_id, {}), **data}


def cache_drop(seller_id):
    with _SELLERS_LOCK:
        if _SELLERS is not None:
            _SELLERS.pop(seller_id, None)


# Current-period scores {sellerId: (score, category, trend)} for lists, search and the dropdown.
# One read of this period's risk_scores, then served from memory for SCORE_TTL seconds.
SCORE_TTL = 600
_SCORES = {'period': None, 'at': 0.0, 'data': {}}
_SCORES_LOCK = threading.Lock()


def get_scores(period):
    import time
    if not period:
        return {}
    with _SCORES_LOCK:
        if _SCORES['period'] != period or time.time() - _SCORES['at'] > SCORE_TTL:
            data = {}
            for d in db.collection('risk_scores').where(
                    filter=FieldFilter('EvaluationPeriod', '==', period)).stream():
                r = d.to_dict() or {}
                if r.get('SellerID'):
                    data[r['SellerID']] = (float(r.get('WeightedRiskScore', 0)), r.get('RiskCategory'),
                                           r.get('RiskTrend'))
            _SCORES.update(period=period, at=time.time(), data=data)
        return _SCORES['data']


def scores_put(rec):
    """Keep the score cache in step after a live (simulator) re-score."""
    with _SCORES_LOCK:
        if _SCORES['period'] == rec.get('EvaluationPeriod'):
            _SCORES['data'][rec['SellerID']] = (float(rec['WeightedRiskScore']), rec['RiskCategory'],
                                                rec['RiskTrend'])


def scores_drop(seller_id):
    """A seller's score was deleted (or the seller was)."""
    with _SCORES_LOCK:
        _SCORES['data'].pop(seller_id, None)


def get_seller_map():
    """{sellerId: sellerName} - kept for templates/older callers."""
    return {sid: d.get('sellerName', 'Unknown') for sid, d in get_sellers().items()}


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def get_dashboard_meta():
    doc = db.collection('system_config').document('dashboard_metadata').get()
    return doc.to_dict() if doc.exists else {}


@dashboard_bp.route('/')
@login_required
def index():
    seller_map = get_seller_map()
    meta = get_dashboard_meta()

    top_risky = [(r.get('SellerID'), seller_map.get(r.get('SellerID'), 'Unknown'),
                  r.get('WeightedRiskScore', 0), r.get('RiskCategory', 'Unknown'),
                  r.get('RiskTrend', 'NEW'))
                 for r in meta.get('top_risky', [])]

    try:
        alerts = (db.collection('alert_log').order_by('AlertDate', direction='DESCENDING')
                  .limit(5).stream())
        recent_alerts = []
        for a in alerts:
            d = a.to_dict() or {}
            sid = d.get('SellerID')
            recent_alerts.append((sid, seller_map.get(sid, 'Unknown'),
                                  d.get('AlertMessage', ''), d.get('AlertDate'), d.get('Score')))
    except Exception:
        recent_alerts = []

    card = model_card()
    return render_template(
        'index.html',
        meta=meta,
        card=card,
        bands=(card or {}).get('bands', {'Medium': 40.0, 'High': 60.0}),
        histogram=meta.get('score_histogram') or (scoring.score_histogram(
            [v[0] for v in get_scores(meta.get('period')).values()]) if meta.get('period') else None),
        total_sellers=len(seller_map),
        category_counts=meta.get('category_counts', {}),
        period=meta.get('period'),
        top_risky=top_risky,
        recent_alerts=recent_alerts,
        all_sellers=list(seller_map.items()),
    )


@dashboard_bp.route('/category/<risk_level>')
@login_required
def category_list(risk_level):
    level = risk_level.capitalize()
    if level not in ('High', 'Medium', 'Low'):
        return redirect(url_for('dashboard.index'))

    period = get_dashboard_meta().get('period')
    seller_map = get_seller_map()
    results = [(sid, seller_map.get(sid, 'Unknown'), sc, cat, tr)
               for sid, (sc, cat, tr) in get_scores(period).items() if cat == level]
    results.sort(key=lambda x: x[2], reverse=True)
    card = model_card()
    return render_template('category_list.html', risk_level=level, results=results, period=period,
                           bands=(card or {}).get('bands', {'Medium': 40.0, 'High': 60.0}),
                           total=sum((get_dashboard_meta().get('category_counts') or {}).values()))


@dashboard_bp.route('/api/sellers')
@login_required
def api_sellers():
    """Seller search for the dropdown. Served from the in-memory cache: 0 Firestore reads."""
    q = (request.args.get('q') or '').strip().lower()
    scores = get_scores(get_dashboard_meta().get('period')) if q else {}
    out = []
    if q:
        for sid, s in get_sellers().items():
            name = s.get('sellerName') or ''
            if q in sid.lower() or q in name.lower():
                sc = scores.get(sid)
                out.append({'id': sid, 'name': name, 'city': s.get('sellerCity') or '',
                            'state': s.get('sellerState') or '',
                            'score': sc[0] if sc else None, 'category': sc[1] if sc else None})
                if len(out) >= 12:
                    break
    return jsonify(out)
