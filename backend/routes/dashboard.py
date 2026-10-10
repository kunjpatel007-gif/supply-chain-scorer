"""
routes/dashboard.py
-------------------
Main dashboard, category listing, the seller cache and the login guard.
"""
import threading
from functools import wraps

from flask import Blueprint, redirect, render_template, session, url_for
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.firebase_client import db

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
                                  d.get('AlertMessage', ''), d.get('AlertDate')))
    except Exception:
        recent_alerts = []

    return render_template(
        'index.html',
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
    base = db.collection('risk_scores').where(filter=FieldFilter('RiskCategory', '==', level))
    try:  # only the current period, not every month ever scored
        docs = list(base.where(filter=FieldFilter('EvaluationPeriod', '==', period)).stream()) if period \
            else list(base.stream())
    except Exception:  # e.g. Firestore asks for an index: filter the period in Python instead
        docs = [d for d in base.stream() if not period or (d.to_dict() or {}).get('EvaluationPeriod') == period]

    results = []
    for r in docs:
        d = r.to_dict() or {}
        sid = d.get('SellerID')
        results.append((sid, seller_map.get(sid, 'Unknown'), d.get('WeightedRiskScore', 0),
                        d.get('RiskCategory'), d.get('RiskTrend')))
    results.sort(key=lambda x: x[2], reverse=True)
    return render_template('category_list.html', risk_level=level, results=results)
