"""
routes/sellers.py
-----------------
Seller details, search, and CRUD operations.
"""
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for
from backend.firebase_client import db
from backend.ml_service import predict_risk_category, FEATURE_COLS
from pipeline.risk_scoring_pipeline import get_seller_features
from backend.routes.dashboard import login_required

sellers_bp = Blueprint('sellers', __name__)

@sellers_bp.route('/search', methods=['POST'])
@login_required
def search():
    query = request.form.get('seller_id', '').strip()
    if not query:
        return redirect(url_for('dashboard.index'))

    sellers_ref = db.collection('sellers').get()
    matches = []
    for s in sellers_ref:
        doc = s.to_dict()
        if query.lower() in s.id.lower() or query.lower() in doc.get('sellerName', '').lower():
            matches.append((s.id, doc.get('sellerName', ''), doc.get('sellerCity', ''), doc.get('sellerState', '')))

    if len(matches) == 1:
        return redirect(url_for('sellers.seller', seller_id=matches[0][0]))

    return render_template('search_results.html', query=query, results=matches)

@sellers_bp.route('/seller/new', methods=['GET', 'POST'])
@login_required
def add_seller():
    if request.method == 'POST':
        seller_id = request.form.get('seller_id')
        name = request.form.get('name')
        data = {
            'sellerName': name,
            'sellerCity': request.form.get('city'),
            'sellerState': request.form.get('state'),
        }
        db.collection('sellers').document(seller_id).set(data)
        
        # Invalidate/Update the Cache so it appears instantly!
        from backend.routes.dashboard import SELLER_MAP_CACHE
        if SELLER_MAP_CACHE is not None:
            SELLER_MAP_CACHE[seller_id] = name
            
        return redirect(url_for('dashboard.index'))
    return render_template('add_seller.html')

@sellers_bp.route('/seller/<seller_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_seller(seller_id):
    doc_ref = db.collection('sellers').document(seller_id)
    if request.method == 'POST':
        name = request.form.get('name')
        data = {
            'sellerName': name,
            'sellerCity': request.form.get('city'),
            'sellerState': request.form.get('state'),
        }
        doc_ref.update(data)
        
        # Update the Cache
        from backend.routes.dashboard import SELLER_MAP_CACHE
        if SELLER_MAP_CACHE is not None:
            SELLER_MAP_CACHE[seller_id] = name
            
        return redirect(url_for('sellers.seller', seller_id=seller_id))
    
    doc = doc_ref.get()
    if not doc.exists:
        return "Seller not found", 404
    
    return render_template('edit_seller.html', seller=doc.to_dict(), seller_id=seller_id)

@sellers_bp.route('/seller/<seller_id>/delete', methods=['POST'])
@login_required
def delete_seller(seller_id):
    db.collection('sellers').document(seller_id).delete()
    
    # Update the Cache
    from backend.routes.dashboard import SELLER_MAP_CACHE
    if SELLER_MAP_CACHE is not None and seller_id in SELLER_MAP_CACHE:
        del SELLER_MAP_CACHE[seller_id]
        
    return redirect(url_for('dashboard.index'))

@sellers_bp.route('/seller/<seller_id>')
@login_required
def seller(seller_id):
    doc = db.collection('sellers').document(seller_id).get()
    if not doc.exists:
        return "Seller not found", 404
    
    seller_data = doc.to_dict()
    seller_info = (seller_data.get('sellerCity'), seller_data.get('sellerState'), seller_data.get('sellerName'))
    
    # Fetch real features from Firestore
    seller_features_df = get_seller_features(db, seller_id)
    if not seller_features_df.empty:
        df = seller_features_df.copy()
        df.columns = [c.lower() for c in df.columns]
        features_dict = {f: float(df[f].iloc[0]) for f in FEATURE_COLS if f in df.columns}
        for f in FEATURE_COLS:
            if f not in features_dict:
                features_dict[f] = 0.0
                df[f] = 0.0
    else:
        features_dict = {f: 0.0 for f in FEATURE_COLS}
        df = pd.DataFrame([features_dict])
        
    live_ml_category = predict_risk_category(df)

    history_ref = db.collection('risk_scores').where('SellerID', '==', seller_id).get()
    history = []
    for h in history_ref:
        d = h.to_dict()
        history.append((d.get('EvaluationPeriod'), d.get('WeightedRiskScore'), d.get('RiskCategory'), d.get('RiskTrend')))
    history.sort(key=lambda x: str(x[0]), reverse=True)

    alerts_ref = db.collection('alert_log').where('SellerID', '==', seller_id).where('AlertStatus', '==', 'Open').get()
    alerts = []
    for a in alerts_ref:
        d = a.to_dict()
        alerts.append((d.get('AlertDate'), d.get('AlertMessage'), d.get('EscalationLevel')))
    alerts.sort(key=lambda x: str(x[0]), reverse=True)

    return render_template(
        'seller.html',
        seller_id=seller_id,
        seller_info=seller_info,
        live_ml_category=live_ml_category,
        features=features_dict,
        defects=[], # Add defects list if needed
        history=history,
        alerts=alerts,
    )
