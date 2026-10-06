"""
routes/dashboard.py
-------------------
Main dashboard and category listing routes.
"""
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, session
from firebase_admin import firestore
from backend.firebase_client import db

dashboard_bp = Blueprint('dashboard', __name__)

SELLER_MAP_CACHE = None

def get_seller_map():
    global SELLER_MAP_CACHE
    if SELLER_MAP_CACHE is None:
        sellers_ref = db.collection('sellers').get()
        SELLER_MAP_CACHE = {s.id: s.to_dict().get('sellerName', 'Unknown') for s in sellers_ref}
    return SELLER_MAP_CACHE

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

@dashboard_bp.route('/')
@login_required
def index():
    seller_map = get_seller_map()
    total_sellers = len(seller_map)
    
    # Sort for the dropdown if needed, though we don't pass all_sellers directly anymore except for backwards compat
    all_sellers = [(sid, name) for sid, name in seller_map.items()]

    risk_scores_ref = db.collection('risk_scores').get()
    category_counts = {}
    top_risky = []
    for r in risk_scores_ref:
        doc = r.to_dict()
        cat = doc.get('RiskCategory', 'Unknown')
        category_counts[cat] = category_counts.get(cat, 0) + 1
        
        seller_id = doc.get('SellerID')
        seller_name = "Unknown"
        for s in all_sellers:
            if s[0] == seller_id:
                seller_name = s[1]
                break
        
        top_risky.append((
            seller_id,
            seller_name,
            doc.get('WeightedRiskScore', 0),
            cat,
            doc.get('RiskTrend', 'Stable')
        ))
    
    top_risky.sort(key=lambda x: x[2], reverse=True)
    top_risky = top_risky[:10]

    try:
        alerts_ref = db.collection('alert_log').order_by('AlertDate', direction=firestore.Query.DESCENDING).limit(5).get()
    except Exception:
        alerts_ref = []
    recent_alerts = []
    for a in alerts_ref:
        doc = a.to_dict()
        seller_id = doc.get('SellerID')
        seller_name = "Unknown"
        for s in all_sellers:
            if s[0] == seller_id:
                seller_name = s[1]
                break
        
        recent_alerts.append((
            seller_id,
            seller_name,
            doc.get('AlertMessage', ''),
            doc.get('AlertDate')
        ))

    return render_template(
        'index.html',
        total_sellers=total_sellers,
        category_counts=category_counts,
        top_risky=top_risky,
        recent_alerts=recent_alerts,
        all_sellers=all_sellers,
    )

@dashboard_bp.route('/category/<risk_level>')
@login_required
def category_list(risk_level):
    risk_level_title = risk_level.capitalize()
    if risk_level_title not in ['High', 'Medium', 'Low']:
        return redirect(url_for('dashboard.index'))

    seller_map = get_seller_map()

    risk_scores = db.collection('risk_scores').where(filter=firestore.FieldFilter('RiskCategory', '==', risk_level_title)).get()
    results = []
    for r in risk_scores:
        d = r.to_dict()
        sid = d.get('SellerID')
        results.append((
            sid,
            seller_map.get(sid, 'Unknown'),
            d.get('WeightedRiskScore', 0),
            d.get('RiskCategory'),
            d.get('RiskTrend')
        ))
    
    results.sort(key=lambda x: x[2], reverse=True)
    return render_template('category_list.html', risk_level=risk_level_title, results=results)
