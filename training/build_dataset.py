"""
build_dataset.py
----------------
Builds the training table: "given a seller's history up to a cutoff date,
was the seller among the worst 25% over the next 4 months?"

    python training/build_dataset.py --source csv         # Olist CSVs in data/raw (free, default)
    python training/build_dataset.py --source firestore   # live Firestore data (~500k reads)

Output: training/dataset.csv  (one row per seller per cutoff, with split = train/test)
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from backend.features import FEATURE_COLS, compute_seller_features  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, 'training', 'dataset.csv')

# Monthly cutoffs used for training, and one later cutoff held out for testing.
# Olist orders run Sep 2016 - Oct 2018; each cutoff needs 4 months of future data after it.
TRAIN_CUTOFFS = ['2017-05-01', '2017-06-01', '2017-07-01', '2017-08-01',
                 '2017-09-01', '2017-10-01', '2017-11-01']
TEST_CUTOFF = '2018-03-01'
HORIZON_MONTHS = 4
MIN_FUTURE_ORDERS = 5      # fewer than this in the window = too few to judge
WORST_SHARE = 0.25         # label = worst 25% of sellers in that window
BAD_REVIEW = 2             # an order is "bad" if late OR reviewed <= 2 stars


def order_outcomes(frames):
    """One row per order: was it late, and its worst review score."""
    d = frames['deliveries'][['poId', 'delayDays']].copy()
    d['delayDays'] = pd.to_numeric(d['delayDays'], errors='coerce')
    late = d.groupby('poId')['delayDays'].max().gt(0)

    q = frames['quality_inspections'][['poId', 'reviewScore']].copy()
    q['reviewScore'] = pd.to_numeric(q['reviewScore'], errors='coerce')
    worst = q.groupby('poId')['reviewScore'].min()

    out = pd.DataFrame({'late': late}).join(worst.rename('worst_review'), how='outer')
    out['late'] = out['late'].fillna(False).astype(bool)
    out['bad'] = out['late'] | (out['worst_review'] <= BAD_REVIEW)
    return out[['bad']]


def future_target(frames, outcomes, start, end):
    orders = frames['purchase_orders'][['poId', 'orderDate']].copy()
    orders['orderDate'] = pd.to_datetime(orders['orderDate'], utc=True, errors='coerce').dt.tz_localize(None)
    in_window = orders[(orders['orderDate'] >= start) & (orders['orderDate'] < end)]['poId']

    lines = frames['po_lines'][['sellerId', 'poId']].drop_duplicates()
    lines = lines[lines['poId'].isin(set(in_window))]
    lines = lines.merge(outcomes, left_on='poId', right_index=True, how='left')
    lines['bad'] = lines['bad'].fillna(False).astype(float)
    return lines.groupby('sellerId').agg(future_orders=('poId', 'nunique'), bad_rate=('bad', 'mean'))


def build_window(frames, outcomes, cutoff):
    cutoff = pd.Timestamp(cutoff)
    end = cutoff + pd.DateOffset(months=HORIZON_MONTHS)
    feats = compute_seller_features(frames, end=cutoff)
    target = future_target(frames, outcomes, cutoff, end)
    df = feats.join(target, how='inner')
    df = df[df['future_orders'] >= MIN_FUTURE_ORDERS].copy()
    threshold = df['bad_rate'].quantile(1 - WORST_SHARE)
    df['y'] = (df['bad_rate'] > threshold).astype(int)
    df['cutoff'] = cutoff.date().isoformat()
    return df


def load_frames(source):
    if source == 'csv':
        from etl.olist_frames import find_raw_dir, load_olist_frames
        raw = find_raw_dir(PROJECT_ROOT)
        print(f"Reading Olist CSVs from {raw}")
        return load_olist_frames(raw)
    from backend.firebase_client import db
    from backend.features import fetch_frames
    print("Reading Firestore (this downloads every order; run it rarely)...")
    return fetch_frames(db, include_orders=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', choices=['csv', 'firestore'], default='csv')
    args = ap.parse_args()

    frames = load_frames(args.source)
    outcomes = order_outcomes(frames)

    parts = []
    for c in TRAIN_CUTOFFS:
        w = build_window(frames, outcomes, c)
        w['split'] = 'train'
        parts.append(w)
        print(f"  cutoff {c}: {len(w)} sellers, {w['y'].mean():.2f} positive")
    t = build_window(frames, outcomes, TEST_CUTOFF)
    t['split'] = 'test'
    parts.append(t)
    print(f"  cutoff {TEST_CUTOFF} (test): {len(t)} sellers, {t['y'].mean():.2f} positive")

    data = pd.concat(parts).reset_index()
    cols = ['sellerId', 'cutoff', 'split'] + FEATURE_COLS + ['future_orders', 'bad_rate', 'y']
    data[cols].to_csv(OUT_PATH, index=False)

    tr, te = data[data.split == 'train'], data[data.split == 'test']
    print(f"\nTrain rows: {len(tr)}  (positive rate {tr.y.mean():.3f})")
    print(f"Test rows:  {len(te)}  (positive rate {te.y.mean():.3f})")
    print(f"Saved {OUT_PATH}")


if __name__ == '__main__':
    main()
