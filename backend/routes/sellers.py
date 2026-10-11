"""
routes/sellers.py
-----------------
Seller details, search, and add / edit / delete.
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from google.cloud.firestore_v1.base_query import FieldFilter

from backend import crud
from backend.features import FEATURE_COLS, compute_seller_features, defect_breakdown, fetch_frames
from backend.firebase_client import db
from backend.ml_service import model_card, predict_one
from backend.routes.dashboard import get_dashboard_meta, get_scores, get_sellers, login_required

sellers_bp = Blueprint('sellers', __name__)


def _seller_form():
    """The classic seller form's field names -> table columns."""
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
    scores = get_scores(get_dashboard_meta().get('period'))
    matches = [(sid, s.get('sellerName', ''), s.get('sellerCity', ''), s.get('sellerState', ''), scores.get(sid))
               for sid, s in get_sellers().items()
               if q in sid.lower() or q in (s.get('sellerName') or '').lower()]

    if len(matches) == 1:
        return redirect(url_for('sellers.seller', seller_id=matches[0][0]))
    return render_template('search_results.html', query=query, results=matches)


@sellers_bp.route('/seller/new', methods=['GET', 'POST'])
@login_required
def add_seller():
    if request.method == 'POST':
        out = crud.create(crud.TABLES['sellers'], {'sellerId': request.form.get('seller_id', ''),
                                                   **_seller_form()})
        if not out.ok:
            flash(next(iter(out.errors.values())) if 'sellerId' not in out.errors
                  else f"Seller ID: {out.errors['sellerId']}")
            return render_template('add_seller.html'), 400
        flash(f"Seller {request.form.get('name', '').strip()} added.")
        return redirect(url_for('sellers.seller', seller_id=out.doc_id))
    return render_template('add_seller.html')


@sellers_bp.route('/seller/<seller_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_seller(seller_id):
    seller = crud.get(crud.TABLES['sellers'], seller_id)
    if seller is None:
        abort(404)
    if request.method == 'POST':
        zip_prefix = seller.get('sellerZipCodePrefix')
        out = crud.update(crud.TABLES['sellers'], seller_id,
                          {**_seller_form(), 'sellerZipCodePrefix': '' if zip_prefix is None else str(zip_prefix)})
        if not out.ok:
            flash(next(iter(out.errors.values())))
            return render_template('edit_seller.html', seller=seller, seller_id=seller_id), 400
        flash("Changes saved.")
        return redirect(url_for('sellers.seller', seller_id=seller_id))
    return render_template('edit_seller.html', seller=seller, seller_id=seller_id)


@sellers_bp.route('/seller/<seller_id>/delete', methods=['POST'])
@login_required
def delete_seller(seller_id):
    """Removes the seller and everything that points at it (see crud.delete_plan)."""
    plan = crud.delete_plan(crud.TABLES['sellers'], seller_id)
    if plan is None:
        abort(404)
    crud.delete(plan)
    flash(f"Seller {seller_id} deleted with {plan.total - 1:,} linked rows.")
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

    live_score, live_category = None, None
    if row is not None and row['total_orders'] > 0:
        live_score, live_category = predict_one(row[FEATURE_COLS])
    card = model_card()

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
            alerts.append((d.get('AlertDate'), d.get('AlertMessage'), d.get('EscalationLevel'),
                           d.get('Score'), d.get('EvaluationPeriod')))
    alerts.sort(key=lambda x: str(x[0]), reverse=True)

    return render_template(
        'seller.html',
        seller_id=seller_id,
        seller_info=seller_info,
        live_ml_category=live_category,
        live_ml_score=live_score,
        bands=(card or {}).get('bands', {'Medium': 40.0, 'High': 60.0}),
        features=features,
        defects=defect_breakdown(frames, seller_id),
        history=history,
        alerts=alerts,
    )
