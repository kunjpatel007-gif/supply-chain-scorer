"""
routes/simulator.py
-------------------
Inject one simulated order (delivery delay + written review) for a seller,
then re-score that seller live so the dashboard changes straight away.

Writes use exactly the field names the pipeline and feature code read.
"""
import uuid

from flask import Blueprint, flash, redirect, render_template, request, url_for
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from backend import scoring
from backend.features import compute_seller_features, fetch_frames
from backend.firebase_client import db
from backend.ml_service import model_version, predict_one
from backend.routes.dashboard import get_seller_map, login_required

simulator_bp = Blueprint('simulator', __name__)
analyzer = SentimentIntensityAnalyzer()

MAX_DELAY_DAYS = 365
MAX_REVIEW_CHARS = 2000


def review_from_text(text):
    """VADER compound (-1..1) -> (review score 1-5, severity 0-5 on the same scale as the NLP data)."""
    compound = analyzer.polarity_scores(text)['compound']
    severity = round(max(0.0, -compound) * 5.0, 2)
    review_score = 1 if compound <= -0.5 else 2 if compound < -0.2 else 3 if compound < 0.2 else 5
    return compound, review_score, severity


@simulator_bp.route('/simulate', methods=['GET', 'POST'])
@login_required
def simulate_event():
    if request.method == 'GET':
        sellers = [{'id': sid, 'name': name} for sid, name in get_seller_map().items()]
        return render_template('simulate.html', sellers=sellers)

    seller_id = (request.form.get('seller_id') or '').strip()
    review_text = (request.form.get('review_text') or '').strip()
    try:
        delay_days = int(request.form.get('delay_days', ''))
    except ValueError:
        flash("Delay must be a whole number of days.")
        return redirect(url_for('simulator.simulate_event'))

    if not seller_id or not review_text:
        flash("All fields are required to run the simulation.")
        return redirect(url_for('simulator.simulate_event'))
    if not 0 <= delay_days <= MAX_DELAY_DAYS:
        flash(f"Delay must be between 0 and {MAX_DELAY_DAYS} days.")
        return redirect(url_for('simulator.simulate_event'))
    if len(review_text) > MAX_REVIEW_CHARS:
        flash(f"Review is too long (max {MAX_REVIEW_CHARS} characters).")
        return redirect(url_for('simulator.simulate_event'))
    if not db.collection('sellers').document(seller_id).get().exists:
        flash(f"Unknown seller: {seller_id}")
        return redirect(url_for('simulator.simulate_event'))

    compound, review_score, severity = review_from_text(review_text)
    po_id = f"SIM-PO-{uuid.uuid4().hex[:8].upper()}"
    now = scoring.now_utc()

    # One simulated order, written the same way the ETL loader writes real ones
    batch = db.batch()
    batch.set(db.collection('purchase_orders').document(po_id),
              {'poId': po_id, 'orderDate': now, 'orderStatus': 'delivered', 'simulated': True})
    batch.set(db.collection('po_lines').document(),
              {'poId': po_id, 'sellerId': seller_id, 'lineNo': 1,
               'productId': 'SIM-PROD-1', 'simulated': True})  # no price: keeps avg_price honest
    batch.set(db.collection('deliveries').document(),
              {'poId': po_id, 'delayDays': delay_days, 'actualDeliveryDate': now, 'simulated': True})
    batch.set(db.collection('quality_inspections').document(),
              {'poId': po_id, 'inspectionDate': now, 'reviewScore': review_score,
               'rejectionFlag': 'Negative' if review_score <= 3 else 'Positive',
               'vaderSeverityWeight': severity, 'reviewCommentMessage': review_text,
               'simulated': True})
    batch.commit()

    # Live re-score of just this seller
    feats = compute_seller_features(fetch_frames(db, seller_id))
    score, category = predict_one(feats.loc[seller_id]) if seller_id in feats.index else (None, None)
    if score is None:
        flash(f"Event logged for {seller_id} (sentiment {compound:+.2f}, severity {severity:.1f}/5). "
              "Live scoring skipped: the model hasn't been retrained yet.")
        return redirect(url_for('sellers.seller', seller_id=seller_id))

    version = model_version()
    period = scoring.current_period()
    old = db.collection('risk_scores').document(scoring.score_doc_id(seller_id, period)).get()
    was_high = old.exists and (old.to_dict() or {}).get('RiskCategory') == 'High'
    rec = scoring.write_single_score(db, seller_id, score, category, version)
    if category == 'High' and not was_high:
        name = get_seller_map().get(seller_id)
        db.collection('alert_log').document().set(scoring.high_alert(seller_id, name, rec, 1))

    flash(f"Event logged for {seller_id}: sentiment {compound:+.2f}, severity {severity:.1f}/5. "
          f"New risk score {score:.1f} ({category}, trend {rec['RiskTrend']}).")
    return redirect(url_for('sellers.seller', seller_id=seller_id))
