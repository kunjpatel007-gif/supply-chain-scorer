"""
rescore.py
----------
Live re-score of one seller after its data changed (simulator, or any create /
edit / delete of its orders, lines, deliveries or reviews).

Same steps as the monthly pipeline, for one seller: read its rows from
Firestore -> 13 features -> live model -> this period's risk_scores doc ->
dashboard counts, histogram and top list -> a High alert if it just turned High.
"""
from dataclasses import dataclass

from backend import scoring
from backend.features import FEATURE_COLS, compute_seller_features, fetch_frames
from backend.firebase_client import db
from backend.ml_service import model_version, predict_one


@dataclass
class Result:
    seller_id: str
    before: float = None          # this period's score before the change (None = wasn't scored)
    rec: dict = None              # the new score record, or None
    removed: bool = False         # seller has no orders left, so its score was taken out
    model_ready: bool = True

    def describe(self):
        if not self.model_ready:
            return "Live scoring skipped: the model hasn't been retrained yet."
        if self.removed:
            return f"{self.seller_id} has no orders left, so its score was removed."
        if self.rec is None:
            return ''
        new = self.rec['WeightedRiskScore']
        change = f"{self.before:.1f} → {new:.1f}" if self.before is not None else f"{new:.1f}"
        return f"Risk score {change} ({self.rec['RiskCategory']})."


def _remember(seller_id, before, after):
    """Keep the last few score moves in the user's session so the seller page can animate them."""
    try:
        from flask import has_request_context, session
        if not has_request_context() or before is None or after is None:
            return
        moves = {k: v for k, v in (session.get('score_moves') or {}).items() if k != seller_id}
        moves[seller_id] = [round(float(before), 2), round(float(after), 2)]
        session['score_moves'] = dict(list(moves.items())[-8:])   # keeps the cookie small
    except Exception:
        pass


def rescore_seller(seller_id):
    from backend.routes.dashboard import get_seller_map, scores_drop, scores_put

    period = scoring.current_period()
    ref = db.collection('risk_scores').document(scoring.score_doc_id(seller_id, period))
    old = ref.get()
    old_rec = (old.to_dict() or {}) if old.exists else None
    res = Result(seller_id, before=(old_rec or {}).get('WeightedRiskScore'))

    feats = compute_seller_features(fetch_frames(db, seller_id))
    row = feats.loc[seller_id] if seller_id in feats.index else None
    if row is None or not row['total_orders'] > 0:
        if old_rec:                       # nothing left to judge: drop the stale score
            ref.delete()
            scoring.remove_from_meta(db, old_rec)
            scores_drop(seller_id)
            res.removed = True
        return res

    score, category = predict_one(row[FEATURE_COLS])
    if score is None:
        res.model_ready = False
        return res
    res.rec = scoring.write_single_score(db, seller_id, score, category, model_version())
    scores_put(res.rec)
    _remember(seller_id, res.before, res.rec.get('WeightedRiskScore'))
    if category == 'High' and (old_rec or {}).get('RiskCategory') != 'High':
        db.collection('alert_log').document().set(
            scoring.high_alert(seller_id, get_seller_map().get(seller_id), res.rec, 1))
    return res
