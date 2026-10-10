"""
olist_frames.py
---------------
Turns the raw Olist CSVs into DataFrames with the SAME field names Firestore uses.
Shared by etl/load_data_firebase.py (upload) and training/build_dataset.py (training),
so training on the CSVs is identical to training on what's in Firestore.
"""
import os

import numpy as np
import pandas as pd

# 7-category NLP taxonomy (IDs 10-16). IDs 1-5 are the old prompt categories,
# kept only so older documents still resolve.
DEFECT_TYPES = [
    {'defectTypeId': 1, 'category': 'Late Delivery', 'severity': 3.0},
    {'defectTypeId': 2, 'category': 'Damaged Goods', 'severity': 4.0},
    {'defectTypeId': 3, 'category': 'Wrong Item', 'severity': 5.0},
    {'defectTypeId': 4, 'category': 'Poor Quality', 'severity': 3.5},
    {'defectTypeId': 5, 'category': 'Other', 'severity': 1.0},
    {'defectTypeId': 10, 'category': 'Not a Defect', 'severity': 0.0},
    {'defectTypeId': 11, 'category': 'Service Defect', 'severity': 2.0},
    {'defectTypeId': 12, 'category': 'Quality Issue', 'severity': 2.5},
    {'defectTypeId': 13, 'category': 'Logistics Issue', 'severity': 3.0},
    {'defectTypeId': 14, 'category': 'Fulfillment Error', 'severity': 3.5},
    {'defectTypeId': 15, 'category': 'Major Product Issue', 'severity': 4.0},
    {'defectTypeId': 16, 'category': 'Critical Failure', 'severity': 5.0},
]
DEFECT_MAP = {d['category']: d['defectTypeId'] for d in DEFECT_TYPES if d['defectTypeId'] >= 10}


def find_raw_dir(project_root):
    """data/raw is the new home of the CSVs; fall back to backup/ and the root."""
    for cand in ('data/raw', 'backup', '.'):
        path = os.path.join(project_root, cand)
        if os.path.exists(os.path.join(path, 'olist_orders_dataset.csv')):
            return path
    raise FileNotFoundError(
        "Olist CSVs not found. Put olist_*.csv in data/raw/ (see the retraining steps).")


def load_olist_frames(raw_dir, vader_path=None):
    rd = lambda n: pd.read_csv(os.path.join(raw_dir, n))
    sellers = rd('olist_sellers_dataset.csv')
    orders = rd('olist_orders_dataset.csv')
    items = rd('olist_order_items_dataset.csv')
    reviews = rd('olist_order_reviews_dataset.csv')

    for col in ('order_purchase_timestamp', 'order_estimated_delivery_date',
                'order_delivered_carrier_date', 'order_delivered_customer_date'):
        orders[col] = pd.to_datetime(orders[col])

    frames = {}
    frames['sellers'] = pd.DataFrame({
        'sellerId': sellers['seller_id'],
        'sellerName': sellers['seller_city'].fillna('Unknown').astype(str) + ', '
                      + sellers['seller_state'].fillna('??').astype(str),
        'sellerCity': sellers['seller_city'],
        'sellerState': sellers['seller_state'],
    })
    frames['purchase_orders'] = pd.DataFrame({
        'poId': orders['order_id'],
        'orderDate': orders['order_purchase_timestamp'],
    })
    frames['po_lines'] = pd.DataFrame({
        'poId': items['order_id'],
        'lineNo': items['order_item_id'],
        'productId': items['product_id'],
        'sellerId': items['seller_id'],
        'unitPriceAtOrder': items['price'],
        'freightValue': items['freight_value'],
    })

    delivered = orders[orders['order_delivered_customer_date'].notna()]
    frames['deliveries'] = pd.DataFrame({
        'poId': delivered['order_id'],
        'delayDays': (delivered['order_delivered_customer_date']
                      - delivered['order_estimated_delivery_date']).dt.days,
    })

    qi = reviews.reset_index(drop=True)
    qi['inspectionId'] = qi.index + 1  # same numbering as load_data_firebase.py
    if vader_path is None:
        vader_path = os.path.join(raw_dir, 'vader_classified_reviews.csv')
    if os.path.exists(vader_path):
        v = pd.read_csv(vader_path)
        v = pd.DataFrame({
            'inspectionId': v['InspectionID'],
            'defectTypeId': v['DefectCategory'].map(DEFECT_MAP).astype('Int64'),
            'vaderSeverityWeight': v['SeverityWeight'],
        })
        qi = qi.merge(v, on='inspectionId', how='left')
    else:
        print(f"  (no NLP file at {vader_path}; defect_rate will be 0)")
        qi['defectTypeId'] = pd.array([pd.NA] * len(qi), dtype='Int64')
        qi['vaderSeverityWeight'] = np.nan
    frames['quality_inspections'] = pd.DataFrame({
        'inspectionId': qi['inspectionId'],
        'poId': qi['order_id'],
        'reviewScore': qi['review_score'],
        'defectTypeId': qi['defectTypeId'],
        'vaderSeverityWeight': qi['vaderSeverityWeight'],
    })
    frames['defect_types'] = pd.DataFrame(DEFECT_TYPES)
    return frames
