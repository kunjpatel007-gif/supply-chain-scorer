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
            if card.get('features') != FEATURE_COLS:
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
    X = features_df.reindex(columns=FEATURE_COLS).astype(float)
    prob = booster.predict(xgb.DMatrix(X, feature_names=FEATURE_COLS))
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
