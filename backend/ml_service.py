"""
ml_service.py
-------------
Loads the XGBoost model + model_card.json once and turns features into
(score 0-100, category). Routes and the pipeline never touch xgboost directly.

Score and category come from the SAME number, so a "High" seller always has a
higher score than a "Low" one.
"""
import json
import logging
import os
import threading

import numpy as np
import pandas as pd

from backend.features import FEATURE_COLS

log = logging.getLogger(__name__)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.getenv('RISK_MODEL_DIR', os.path.join(PROJECT_ROOT, 'training'))
MODEL_PATH = os.path.join(MODEL_DIR, 'vendor_risk_model.json')
CARD_PATH = os.path.join(MODEL_DIR, 'model_card.json')

_model = None
_card = None
_lock = threading.Lock()


class ModelNotReady(RuntimeError):
    """Raised when the new model hasn't been trained yet (no model_card.json)."""


def get_model():
    global _model, _card
    with _lock:
        if _model is None:
            if not os.path.exists(CARD_PATH):
                raise ModelNotReady(
                    "training/model_card.json missing - run build_dataset.py and train_xgboost.py")
            import xgboost as xgb
            with open(CARD_PATH, 'r', encoding='utf-8') as f:
                card = json.load(f)
            if not card.get('features') or not set(card['features']) <= set(FEATURE_COLS):
                raise ModelNotReady("Model was trained on different features - retrain it")
            booster = xgb.Booster()
            booster.load_model(MODEL_PATH)
            _model, _card = booster, card
        return _model, _card


def model_version():
    try:
        return get_model()[1]['model_version']
    except ModelNotReady:
        return None


def categorize(scores, bands):
    s = np.asarray(scores, dtype=float)
    return np.where(s >= bands['High'], 'High', np.where(s >= bands['Medium'], 'Medium', 'Low'))


def score_frame(features_df):
    """features_df: rows of FEATURE_COLS -> DataFrame with RiskScore (0-100) and RiskCategory."""
    import xgboost as xgb
    booster, card = get_model()
    cols = card['features']                 # the inputs this model was trained on (a subset of FEATURE_COLS)
    X = features_df.reindex(columns=cols).astype(float)
    prob = booster.predict(xgb.DMatrix(X, feature_names=cols))
    scores = np.round(prob * 100, 2)
    return pd.DataFrame({'RiskScore': scores, 'RiskCategory': categorize(scores, card['bands'])},
                        index=features_df.index)


def predict_one(features_row):
    """features_row: Series/dict of FEATURE_COLS -> (score, category), or (None, None) if no model yet."""
    try:
        out = score_frame(pd.DataFrame([dict(features_row)]))
    except ModelNotReady as exc:
        log.warning("ML model not ready: %s", exc)
        return None, None
    return float(out['RiskScore'].iloc[0]), str(out['RiskCategory'].iloc[0])


def model_card():
    """The trained model's card (metrics, bands, features), or None before retraining."""
    try:
        return get_model()[1]
    except ModelNotReady:
        return None


FEATURE_LABELS = {
    'total_orders': 'Orders', 'total_items': 'Items sold', 'unique_products': 'Products',
    'avg_price': 'Avg price', 'avg_freight': 'Avg freight', 'price_volatility': 'Price spread',
    'avg_delay_days': 'Avg delivery vs promise', 'max_delay_days': 'Worst delivery',
    'pct_late_deliveries': 'Late deliveries', 'avg_review_score': 'Avg review',
    'negative_review_rate': 'Reviews \u2264 3 stars', 'n_reviews': 'Reviews counted',
    'defect_rate': 'Defect severity / order',
}


def explain_one(features_row, top=5):
    """
    Why did this seller get its score? Uses XGBoost's exact per-feature contributions (SHAP).
    Returns [(feature, label, value, contribution)] sorted by absolute push, biggest first.
    Positive contribution = pushes risk up. Empty list if the model isn't trained yet.
    """
    import xgboost as xgb
    try:
        booster, _ = get_model()
    except ModelNotReady:
        return []
    cols = get_model()[1]['features']
    X = pd.DataFrame([dict(features_row)]).reindex(columns=cols).astype(float)
    contrib = booster.predict(xgb.DMatrix(X, feature_names=cols), pred_contribs=True)[0][:-1]
    rows = []
    for f, c in zip(cols, contrib):
        v = X[f].iloc[0]
        rows.append((f, FEATURE_LABELS.get(f, f), None if v != v else float(v), float(c)))
    rows.sort(key=lambda r: abs(r[3]), reverse=True)
    return rows[:top]
