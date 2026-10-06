"""
ml_service.py
-------------
All XGBoost model loading and risk category prediction logic lives here.
Routes import predict_risk_category() — they never touch xgboost directly.
"""
import json
import os
import sys

import numpy as np
import xgboost as xgb

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FEATURE_COLS = [
    'total_orders', 'total_items', 'unique_products',
    'avg_delay_days', 'max_delay_days', 'pct_late_deliveries',
    'avg_review_score', 'rejection_rate', 'negative_review_count',
    'avg_price', 'price_volatility', 'avg_freight', 'defect_penalty',
]

_booster = None
_label_classes = None


def _model_paths():
    return (
        os.path.join(PROJECT_ROOT, 'training', 'vendor_risk_model.json'),
        os.path.join(PROJECT_ROOT, 'training', 'label_classes.json'),
    )


def get_model():
    """Lazy-load the XGBoost model once and cache it in memory."""
    global _booster, _label_classes
    if _booster is None:
        model_path, labels_path = _model_paths()
        _booster = xgb.Booster()
        _booster.load_model(model_path)
        with open(labels_path, 'r', encoding='utf-8') as f:
            _label_classes = json.load(f)
    return _booster, _label_classes


def predict_risk_category(df):
    """
    Given a single-row DataFrame of seller features,
    return the predicted risk category label (e.g. 'High', 'Low').
    """
    booster, label_classes = get_model()
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    for col in FEATURE_COLS:
        if col not in df.columns:
            df[col] = 0
    df[FEATURE_COLS] = df[FEATURE_COLS].fillna(0)
    dmatrix = xgb.DMatrix(df[FEATURE_COLS])
    preds = booster.predict(dmatrix)
    if len(preds.shape) > 1 and preds.shape[1] > 1:
        pred_class = int(np.argmax(preds[0]))
    else:
        pred_class = int(np.round(preds[0]))
    if pred_class < len(label_classes):
        return label_classes[pred_class]
    return 'Unknown'
