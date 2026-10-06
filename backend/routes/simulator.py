from flask import Blueprint, request, redirect, url_for, session, flash, render_template
from backend.firebase_client import db
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import uuid
import datetime

simulator_bp = Blueprint('simulator', __name__)
analyzer = SentimentIntensityAnalyzer()

@simulator_bp.route('/simulate', methods=['GET', 'POST'])
def simulate_event():
    # Security: Ensure only logged in admins can access this page
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        seller_id = request.form.get('seller_id')
        delay_days = request.form.get('delay_days')
        review_text = request.form.get('review_text')

        if not seller_id or not delay_days or not review_text:
            flash("All fields are required to run the simulation.")
            return redirect(url_for('simulator.simulate_event'))

        try:
            delay_days = int(delay_days)
        except ValueError:
            flash("Delay must be a number.")
            return redirect(url_for('simulator.simulate_event'))

        # Generate a shared Purchase Order ID for this simulated event
        po_id = f"SIM-PO-{uuid.uuid4().hex[:8].upper()}"

        # 1. NLP VADER Processing
        scores = analyzer.polarity_scores(review_text)
        compound_score = scores['compound']
        
        # In our ML model, VaderSeverityWeight needs to be high for negative reviews.
        # VADER compound: -1 (negative) to 1 (positive).
        # We invert it so -1 becomes +1 severity penalty.
        severity_weight = -compound_score

        # 2. Inject Delivery Event
        if delay_days > 0:
            db.collection('deliveries').add({
                'poId': po_id,
                'delayDays': delay_days,
                'timestamp': datetime.datetime.utcnow().isoformat()
            })

        # 3. Inject NLP Quality Inspection Event
        db.collection('quality_inspections').add({
            'poId': po_id,
            'reviewScore': 1 if compound_score < -0.5 else 3 if compound_score < 0 else 5,
            'rejectionFlag': 'Negative' if compound_score < -0.2 else 'None',
            'VaderSeverityWeight': severity_weight,
            'rawReviewText': review_text,
            'timestamp': datetime.datetime.utcnow().isoformat()
        })

        # Also inject a mock po_lines entry so the pipeline can JOIN them
        db.collection('po_lines').add({
            'poId': po_id,
            'sellerId': seller_id,
            'lineNo': 1,
            'productId': 'SIM-PROD-1',
            'unitPriceAtOrder': 500.0,
            'freightValue': 50.0
        })

        # --- LIVE ML INFERENCE ---
        # The user requested that we instantly update the ML risk score for this specific seller
        # so they don't have to wait for the nightly batch Job during their presentation.
        from pipeline.risk_scoring_pipeline import get_seller_features
        from backend.ml_service import predict_risk_category
        import pandas as pd
        import json

        # 1. Fetch the newly updated behavioral features for just this one seller
        df = get_seller_features(db, seller_id=seller_id)
        
        if not df.empty:
            # 2. Run the trained XGBoost model in memory
            risk_category = predict_risk_category(df)
            
            # 3. Calculate a basic numerical score based on the NLP severity
            # (In the real batch pipeline, this uses MinMaxScaler, but for the live demo we simulate the drop)
            base_score = 75.0
            penalty = (severity_weight * 30.0) if severity_weight > 0 else 0
            final_score = max(0, min(100, base_score - penalty))

            # 4. Save the Live Inference result to Firestore
            risk_data = {
                'sellerId': seller_id,
                'compositeScore': final_score,
                'riskCategory': risk_category,
                'lastUpdated': datetime.datetime.utcnow().isoformat(),
                'trend': 'Deteriorating' if severity_weight > 0 else 'Stable'
            }
            db.collection('risk_scores').document(seller_id).set(risk_data)
        
            flash(f"Simulation logged for {seller_id}! NLP Severity: {severity_weight:.2f}. Live XGBoost Inference predicted category: {risk_category}")
        else:
            flash(f"Simulation logged for {seller_id}! NLP Severity: {severity_weight:.2f}. (ML Inference skipped, insufficient history).")

        return redirect(url_for('dashboard.index'))

    # GET request: Fetch sellers from the ultra-fast RAM Cache
    from backend.routes.dashboard import get_seller_map
    seller_map = get_seller_map()
    sellers = [{'id': sid, 'name': name} for sid, name in seller_map.items()]
    
    return render_template('simulate.html', sellers=sellers)
