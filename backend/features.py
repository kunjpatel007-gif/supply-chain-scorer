"""
features.py
-----------
ONE place that turns raw order data into per-seller features.

Used by:
  * pipeline/risk_scoring_pipeline.py  (all sellers, from Firestore)
  * backend/routes/sellers.py           (one seller, from Firestore)
  * backend/routes/simulator.py         (one seller, from Firestore)
  * training/build_dataset.py           (all sellers, from CSV or Firestore, in date windows)

Because training and serving share this code, a seller always gets the same
numbers in both places.

All frames use the Firestore field names written by etl/load_data_firebase.py.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

FEATURE_COLS = [
    'total_orders', 'total_items', 'unique_products',
    'avg_price', 'avg_freight', 'price_volatility',
    'avg_delay_days', 'max_delay_days', 'pct_late_deliveries',
    'avg_review_score', 'negative_review_rate', 'n_reviews', 'defect_rate',
]

# Columns each frame needs (missing columns are added as NaN).
FRAME_COLUMNS = {
    'sellers': ['sellerId', 'sellerName', 'sellerCity', 'sellerState'],
    'po_lines': ['poId', 'sellerId', 'lineNo', 'productId', 'unitPriceAtOrder', 'freightValue'],
    'purchase_orders': ['poId', 'orderDate'],
    'deliveries': ['poId', 'delayDays'],
    'quality_inspections': ['poId', 'reviewScore', 'vaderSeverityWeight', 'defectTypeId'],
    'defect_types': ['defectTypeId', 'category', 'severity'],
}


def _frame(frames, name):
    df = frames.get(name)
    if df is None:
        df = pd.DataFrame()
    df = df.copy()
    for col in FRAME_COLUMNS[name]:
        if col not in df.columns:
            df[col] = np.nan
    return df


def _int_id(series):
    """Defect type IDs arrive as 10, 10.0 or '10' depending on the source."""
    return pd.to_numeric(series, errors='coerce').astype('Int64')


def compute_seller_features(frames, start=None, end=None):
    """
    frames : dict of DataFrames keyed by collection name (see FRAME_COLUMNS).
    start, end : optional dates. Only orders placed in [start, end) count.
                 Needs frames['purchase_orders'] when either is given.
    Returns a DataFrame indexed by sellerId with FEATURE_COLS (+ sellerName).
    Missing values stay NaN on purpose: XGBoost handles them natively, and
    training and serving then treat gaps exactly the same way.
    """
    sellers = _frame(frames, 'sellers')
    lines = _frame(frames, 'po_lines')
    deliveries = _frame(frames, 'deliveries')
    qi = _frame(frames, 'quality_inspections')
    dtypes = _frame(frames, 'defect_types')

    lines = lines[lines['sellerId'].notna() & lines['poId'].notna()]

    if start is not None or end is not None:
        orders = _frame(frames, 'purchase_orders')
        orders['orderDate'] = pd.to_datetime(orders['orderDate'], utc=True, errors='coerce').dt.tz_localize(None)
        mask = orders['orderDate'].notna()
        if start is not None:
            mask &= orders['orderDate'] >= pd.Timestamp(start)
        if end is not None:
            mask &= orders['orderDate'] < pd.Timestamp(end)
        lines = lines[lines['poId'].isin(set(orders.loc[mask, 'poId']))]

    for col in ('unitPriceAtOrder', 'freightValue'):
        lines[col] = pd.to_numeric(lines[col], errors='coerce')

    # --- volume & price -------------------------------------------------
    vol = lines.groupby('sellerId').agg(
        total_orders=('poId', 'nunique'),
        total_items=('poId', 'size'),
        unique_products=('productId', 'nunique'),
        avg_price=('unitPriceAtOrder', 'mean'),
        avg_freight=('freightValue', 'mean'),
        price_volatility=('unitPriceAtOrder', 'std'),
    )

    seller_po = lines[['sellerId', 'poId']].drop_duplicates()

    # --- deliveries -----------------------------------------------------
    deliveries['delayDays'] = pd.to_numeric(deliveries['delayDays'], errors='coerce')
    d = seller_po.merge(deliveries[['poId', 'delayDays']].dropna(), on='poId', how='inner')
    d['late'] = (d['delayDays'] > 0).astype(float)
    dstats = d.groupby('sellerId').agg(
        avg_delay_days=('delayDays', 'mean'),
        max_delay_days=('delayDays', 'max'),
        pct_late_deliveries=('late', 'mean'),
    )

    # --- reviews & defects ----------------------------------------------
    qi['reviewScore'] = pd.to_numeric(qi['reviewScore'], errors='coerce')
    qi['vaderSeverityWeight'] = pd.to_numeric(qi['vaderSeverityWeight'], errors='coerce')
    qi['defectTypeId'] = _int_id(qi['defectTypeId'])
    dtypes['defectTypeId'] = _int_id(dtypes['defectTypeId'])
    dtypes['severity'] = pd.to_numeric(dtypes['severity'], errors='coerce')
    qi = qi.merge(dtypes[['defectTypeId', 'severity']].dropna(subset=['defectTypeId']),
                  on='defectTypeId', how='left')
    qi['sev'] = qi['vaderSeverityWeight'].fillna(qi['severity']).fillna(0.0)

    q = seller_po.merge(qi[['poId', 'reviewScore', 'sev']], on='poId', how='inner')
    q = q[q['reviewScore'].notna()]
    q['neg'] = (q['reviewScore'] <= 3).astype(float)
    qstats = q.groupby('sellerId').agg(
        avg_review_score=('reviewScore', 'mean'),
        negative_review_rate=('neg', 'mean'),
        n_reviews=('neg', 'size'),
        defect_sum=('sev', 'sum'),
    )

    # --- assemble -------------------------------------------------------
    if start is not None or end is not None:
        ids = pd.Index(vol.index.astype(str))          # windowed: sellers active in the window
    else:
        ids = pd.Index(sellers['sellerId'].dropna().astype(str).unique()).union(vol.index.astype(str))
    ids.name = 'sellerId'

    out = pd.DataFrame(index=ids)
    out = out.join(vol).join(dstats).join(qstats)
    for col in ('total_orders', 'total_items', 'unique_products', 'n_reviews'):
        out[col] = out[col].fillna(0)
    out['defect_rate'] = out['defect_sum'] / out['total_orders'].replace(0, np.nan)
    out = out.drop(columns=['defect_sum'])

    names = sellers.dropna(subset=['sellerId']).drop_duplicates('sellerId').set_index('sellerId')['sellerName']
    out['sellerName'] = names.reindex(out.index)
    out.index.name = 'sellerId'
    return out[FEATURE_COLS + ['sellerName']].astype({c: float for c in FEATURE_COLS})


# ----------------------------------------------------------------------
# Firestore loaders
# ----------------------------------------------------------------------
_DEFECT_TYPES = None
_DEFECT_LOCK = threading.Lock()


def _docs_to_df(docs, id_field=None):
    rows = []
    for doc in docs:
        d = doc.to_dict() or {}
        if id_field and not d.get(id_field):
            d[id_field] = doc.id
        rows.append(d)
    return pd.DataFrame(rows)


def fetch_all(db, coll_name, page=15000):
    """Download a whole collection in pages (avoids 503 timeouts on big reads)."""
    docs, last = [], None
    while True:
        query = db.collection(coll_name).order_by('__name__').limit(page)
        if last is not None:
            query = query.start_after(last)
        try:
            batch = list(query.stream(timeout=300))
        except Exception as exc:  # one retry on a transient error
            print(f"  {coll_name}: {exc}; retrying once...")
            batch = list(query.stream(timeout=300))
        docs.extend(batch)
        if len(batch) < page:
            break
        last = batch[-1]
    print(f"  {coll_name}: {len(docs)} docs")
    return docs


def get_defect_types(db):
    """defect_types is tiny and static, so read it once per process."""
    global _DEFECT_TYPES
    with _DEFECT_LOCK:
        if _DEFECT_TYPES is None:
            _DEFECT_TYPES = _docs_to_df(db.collection('defect_types').stream(), 'defectTypeId')
        return _DEFECT_TYPES


def _field_filter(field, op, value):
    from google.cloud.firestore_v1.base_query import FieldFilter
    return FieldFilter(field, op, value)


def fetch_frames(db, seller_id=None, include_orders=False):
    """
    Read the raw collections needed by compute_seller_features.
    seller_id=None -> every seller (pipeline); otherwise just that seller.
    """
    frames = {'defect_types': get_defect_types(db)}

    if seller_id is None:
        frames['sellers'] = _docs_to_df(fetch_all(db, 'sellers'), 'sellerId')
        frames['po_lines'] = _docs_to_df(fetch_all(db, 'po_lines'))
        frames['deliveries'] = _docs_to_df(fetch_all(db, 'deliveries'))
        frames['quality_inspections'] = _docs_to_df(fetch_all(db, 'quality_inspections'))
        if include_orders:
            frames['purchase_orders'] = _docs_to_df(fetch_all(db, 'purchase_orders'), 'poId')
        return frames

    doc = db.collection('sellers').document(seller_id).get()
    seller = (doc.to_dict() or {}) if doc.exists else None
    frames['sellers'] = pd.DataFrame([{**seller, 'sellerId': seller_id}]) if seller is not None else pd.DataFrame()

    lines = list(db.collection('po_lines')
                 .where(filter=_field_filter('sellerId', '==', seller_id)).stream())
    frames['po_lines'] = _docs_to_df(lines)
    po_ids = sorted({(d.to_dict() or {}).get('poId') for d in lines} - {None})

    def by_po(coll):
        chunks = [po_ids[i:i + 30] for i in range(0, len(po_ids), 30)]  # 'in' takes max 30 values

        def run(chunk):
            return list(db.collection(coll).where(filter=_field_filter('poId', 'in', chunk)).stream())

        # Big sellers need many lookups; run them side by side instead of one after another.
        with ThreadPoolExecutor(max_workers=8) as pool:
            docs = [d for part in pool.map(run, chunks) for d in part]
        return _docs_to_df(docs)

    frames['deliveries'] = by_po('deliveries')
    frames['quality_inspections'] = by_po('quality_inspections')
    return frames


def defect_breakdown(frames, seller_id):
    """[(category, count, severity)] for one seller, most frequent first."""
    lines = _frame(frames, 'po_lines')
    qi = _frame(frames, 'quality_inspections')
    dtypes = _frame(frames, 'defect_types')
    po = set(lines.loc[lines['sellerId'] == seller_id, 'poId'])
    qi = qi[qi['poId'].isin(po)].copy()
    qi['defectTypeId'] = _int_id(qi['defectTypeId'])
    qi = qi[qi['defectTypeId'].notna()]
    if qi.empty:
        return []
    dtypes['defectTypeId'] = _int_id(dtypes['defectTypeId'])
    qi = qi.merge(dtypes[['defectTypeId', 'category', 'severity']], on='defectTypeId', how='left')
    g = qi.groupby('category').agg(n=('poId', 'size'), sev=('severity', 'first')).sort_values('n', ascending=False)
    return [(cat, int(r.n), float(r.sev) if pd.notna(r.sev) else 0.0) for cat, r in g.iterrows()]
