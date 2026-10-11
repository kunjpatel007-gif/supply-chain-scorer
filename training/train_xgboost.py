"""
train_xgboost.py
----------------
Trains the vendor-risk model on training/dataset.csv (make it with build_dataset.py).

    python training/train_xgboost.py

Runs on a normal CPU in well under a minute. No GPU needed.

Saves:
  training/vendor_risk_model.json   the model (trained on ALL windows after evaluation)
  training/model_card.json          features, band edges, test metrics, when it was trained
"""
import datetime
import json
import os
import sys

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from backend.features import FEATURE_COLS  # noqa: E402

DATA_PATH = os.path.join(PROJECT_ROOT, 'training', 'dataset.csv')
MODEL_PATH = os.path.join(PROJECT_ROOT, 'training', 'vendor_risk_model.json')
CARD_PATH = os.path.join(PROJECT_ROOT, 'training', 'model_card.json')

# Score = P(worst 25% next 4 months) x 100.  Category bands on that score:
BANDS = {'Medium': 40.0, 'High': 60.0}   # < 40 Low, 40-60 Medium, >= 60 High
MIN_TEST_AUC = 0.62

PARAMS = dict(
    n_estimators=300, max_depth=3, learning_rate=0.03,
    subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
    scale_pos_weight=3,             # ~1 positive per 3 negatives
    objective='binary:logistic', eval_metric='logloss',
    tree_method='hist', random_state=42,
)
# Inputs the model uses. Price spread is computed for the seller page but left out of the model:
# it has no steady link to risk, and it made a normal order at a seller's usual price move the score.
MODEL_FEATURES = [f for f in FEATURE_COLS if f != 'price_volatility']
# Common sense the model must respect (checked by an audit of 400 real sellers):
#   worse delivery, worse reviews, more defects -> risk can only go up;
#   a longer track record (more orders / items / reviews) -> risk can only go down.
MONOTONE = {'avg_delay_days': 1, 'max_delay_days': 1, 'pct_late_deliveries': 1,
            'avg_review_score': -1, 'negative_review_rate': 1, 'defect_rate': 1,
            'total_orders': -1, 'total_items': -1, 'n_reviews': -1}
PARAMS['monotone_constraints'] = '(' + ','.join(str(MONOTONE.get(f, 0)) for f in MODEL_FEATURES) + ')'


def band(score):
    return np.where(score >= BANDS['High'], 'High', np.where(score >= BANDS['Medium'], 'Medium', 'Low'))


def main():
    if not os.path.exists(DATA_PATH):
        sys.exit("training/dataset.csv not found. Run: python training/build_dataset.py --source csv")

    df = pd.read_csv(DATA_PATH)
    train, test = df[df.split == 'train'], df[df.split == 'test']
    print(f"Train rows: {len(train)}  Test rows: {len(test)}")

    # 1) Evaluate on the later, unseen period
    model = xgb.XGBClassifier(**PARAMS)
    model.fit(train[MODEL_FEATURES], train['y'])
    p = model.predict_proba(test[MODEL_FEATURES])[:, 1]
    auc = roc_auc_score(test['y'], p)
    pr_auc = average_precision_score(test['y'], p)
    base_neg = roc_auc_score(test['y'], test['negative_review_rate'].fillna(0))
    base_late = roc_auc_score(test['y'], test['pct_late_deliveries'].fillna(0))

    t = test.assign(score=p * 100)
    t['band'] = band(t['score'])
    table = (t.groupby('band')
               .agg(sellers=('y', 'size'), landed_in_worst_25pct=('y', 'mean'),
                    avg_future_bad_rate=('bad_rate', 'mean'))
               .reindex(['Low', 'Medium', 'High']).fillna(0))

    print("\n=== Test period (never seen in training) ===")
    print(f"AUC:     {auc:.3f}   (0.5 = coin flip)")
    print(f"PR-AUC:  {pr_auc:.3f}   (random = {test['y'].mean():.3f})")
    print(f"Baselines  past negative-review rate AUC {base_neg:.3f} | past late % AUC {base_late:.3f}")
    print("\nBands on the test period:")
    print(table.round(3).to_string())

    imp = pd.Series(model.feature_importances_, MODEL_FEATURES).sort_values(ascending=False)
    print("\nTop features:", ', '.join(imp.index[:5]))

    ok = auc >= MIN_TEST_AUC
    print(f"\nQuality check: AUC {auc:.3f} {'>=' if ok else '<'} {MIN_TEST_AUC} -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        sys.exit("Model NOT saved. Send training output to Claude before deploying.")

    # 2) Refit on every window (train + test) for the model the app will use
    final = xgb.XGBClassifier(**PARAMS)
    final.fit(df[MODEL_FEATURES], df['y'])
    final.save_model(MODEL_PATH)

    # Average absolute push of each feature on the score (exact SHAP values from XGBoost),
    # shown on the Model page. Shares sum to 100%.
    contrib = final.get_booster().predict(
        xgb.DMatrix(df[MODEL_FEATURES], feature_names=MODEL_FEATURES), pred_contribs=True)[:, :-1]
    mean_abs = np.abs(contrib).mean(axis=0)
    impact = {f: round(float(v / mean_abs.sum()), 4) for f, v in zip(MODEL_FEATURES, mean_abs)}

    card = {
        'model_version': datetime.datetime.now().strftime('%Y%m%d-%H%M%S'),
        'trained_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'question': 'Risk that a seller lands in the worst 25% over the next 4 months, '
                    'where an order is bad if late or reviewed \u2264 2 stars',
        'features': MODEL_FEATURES,
        'bands': BANDS,
        'params': PARAMS,
        'train_rows': int(len(train)),
        'test_rows': int(len(test)),
        'cutoffs': sorted(df['cutoff'].unique().tolist()),
        'test_metrics': {
            'auc': round(float(auc), 4),
            'pr_auc': round(float(pr_auc), 4),
            'positive_rate': round(float(test['y'].mean()), 4),
            'baseline_auc_negative_review_rate': round(float(base_neg), 4),
            'baseline_auc_pct_late': round(float(base_late), 4),
        },
        'test_bands': {b: {k: round(float(v), 4) for k, v in row.items()} for b, row in table.iterrows()},
        'top_features': imp.index[:5].tolist(),
        'feature_impact': dict(sorted(impact.items(), key=lambda kv: -kv[1])),
    }
    with open(CARD_PATH, 'w', encoding='utf-8') as f:
        json.dump(card, f, indent=2)

    print(f"\nSaved {MODEL_PATH}")
    print(f"Saved {CARD_PATH}")


if __name__ == '__main__':
    main()
