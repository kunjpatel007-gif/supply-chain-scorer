"""
routes/sellers.py
-----------------
Seller details, search, and add / edit / delete.
"""
import re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.features import FEATURE_COLS, compute_seller_features, defect_breakdown, fetch_frames
from backend.firebase_client import db
from backend.ml_service import predict_one
from backend.routes.dashboard import cache_drop, cache_put, get_sellers, login_required

sellers_bp = Blueprint('sellers', __name__)

SELLER_ID_RE = re.compile(r'^[A-Za-z0-9_-]{3,64}$')


def _seller_form():
    return {
        'sellerName': (request.form.get('name') or '').strip(),
        'sellerCity': (request.form.get('city') or '').strip(),
        'sellerState': (request.form.get('state') or '').strip(),
    }


@sellers_bp.route('/search', methods=['POST'])
@login_required
def search():
    query = (request.form.get('seller_id') or '').strip()
    if not query:
        return redirect(url_for('dashboard.index'))

    q = query.lower()
    matches = [(sid, s.get('sellerName', ''), s.get('sellerCity', ''), s.get('sellerState', ''))
               for sid, s in get_sellers().items()
               if q in sid.lower() or q in (s.get('sellerName') or '').lower()]

    if len(matches) == 1:
        return redirect(url_for('sellers.seller', seller_id=matches[0][0]))
    return render_template('search_results.html', query=query, results=matches)


@sellers_bp.route('/seller/new', methods=['GET', 'POST'])
@login_required
def add_seller():
    if request.method == 'POST':
        seller_id = (request.form.get('seller_id') or '').strip()
        data = _seller_form()
        if not SELLER_ID_RE.match(seller_id):
            flash("Seller ID must be 3-64 letters, numbers, '-' or '_'.")
            return render_template('add_seller.html'), 400
        if not data['sellerName']:
            flash("Company name is required.")
            return render_template('add_seller.html'), 400
        ref = db.collection('sellers').document(seller_id)
        if ref.get().exists:
            flash(f"Seller {seller_id} already exists.")
            return render_template('add_seller.html'), 400
        ref.set({**data, 'sellerId': seller_id})
        cache_put(seller_id, data)
        flash(f"Seller {data['sellerName']} added.")
        return redirect(url_for('sellers.seller', seller_id=seller_id))
    return render_template('add_seller.html')


@sellers_bp.route('/seller/<seller_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_seller(seller_id):
    ref = db.collection('sellers').document(seller_id)
    doc = ref.get()
    if not doc.exists:
        abort(404)
    if request.method == 'POST':
        data = _seller_form()
        if not data['sellerName']:
            flash("Company name is required.")
            return render_template('edit_seller.html', seller=doc.to_dict(), seller_id=seller_id), 400
        ref.update(data)
        cache_put(seller_id, data)
        flash("Changes saved.")
        return redirect(url_for('sellers.seller', seller_id=seller_id))
    return render_template('edit_seller.html', seller=doc.to_dict(), seller_id=seller_id)


@sellers_bp.route('/seller/<seller_id>/delete', methods=['POST'])
@login_required
def delete_seller(seller_id):
    """Removes the seller plus its scores and alerts. Order history is kept."""
    batch = db.batch()
    batch.delete(db.collection('sellers').document(seller_id))
    for coll in ('risk_scores', 'alert_log'):
        for doc in db.collection(coll).where(filter=FieldFilter('SellerID', '==', seller_id)).stream():
            batch.delete(doc.reference)
    batch.commit()
    cache_drop(seller_id)
    flash(f"Seller {seller_id} deleted.")
    return redirect(url_for('dashboard.index'))


@sellers_bp.route('/seller/<seller_id>')
@login_required
def seller(seller_id):
    doc = db.collection('sellers').document(seller_id).get()
    if not doc.exists:
        abort(404)
    s = doc.to_dict() or {}
    seller_info = (s.get('sellerCity'), s.get('sellerState'), s.get('sellerName'))

    frames = fetch_frames(db, seller_id)
    feats = compute_seller_features(frames)
    row = feats.loc[seller_id] if seller_id in feats.index else None
    features = {f: (None if row is None or row[f] != row[f] else float(row[f])) for f in FEATURE_COLS}

    live_score, live_category = (None, None)
    if row is not None and row['total_orders'] > 0:
        live_score, live_category = predict_one(row[FEATURE_COLS])

    history = []
    for h in db.collection('risk_scores').where(filter=FieldFilter('SellerID', '==', seller_id)).stream():
        d = h.to_dict() or {}
        history.append((d.get('EvaluationPeriod'), d.get('WeightedRiskScore'),
                        d.get('RiskCategory'), d.get('RiskTrend')))
    history.sort(key=lambda x: str(x[0]), reverse=True)

    alerts = []
    for a in db.collection('alert_log').where(filter=FieldFilter('SellerID', '==', seller_id)).stream():
        d = a.to_dict() or {}
        if d.get('AlertStatus') == 'Open':
            alerts.append((d.get('AlertDate'), d.get('AlertMessage'), d.get('EscalationLevel')))
    alerts.sort(key=lambda x: str(x[0]), reverse=True)

    return render_template(
        'seller.html',
        seller_id=seller_id,
        seller_info=seller_info,
        live_ml_category=live_category,
        live_ml_score=live_score,
        features=features,
        defects=defect_breakdown(frames, seller_id),
        history=history,
        alerts=alerts,
    )
