import argparse
import pandas as pd
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
import os
from datetime import datetime
from tqdm import tqdm

import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from etl.olist_frames import DEFECT_MAP, DEFECT_TYPES, find_raw_dir  # noqa: E402

DATA_DIR = find_raw_dir(PROJECT_ROOT)

def init_firebase():
    if not firebase_admin._apps:
        if os.getenv("FIRESTORE_EMULATOR_HOST"):
            cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred, {"projectId": "demo-olist"})
        elif os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            cred = credentials.Certificate(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
            firebase_admin.initialize_app(cred)
        else:
            cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred, {"projectId": "dbms-d424e"})
    return firestore.client()

def clean_dict(d):
    cleaned = {}
    for k, v in d.items():
        if pd.isna(v):
            cleaned[k] = None
        elif isinstance(v, pd.Timestamp):
            cleaned[k] = v.to_pydatetime()
        elif isinstance(v, np.integer):
            cleaned[k] = int(v)
        elif isinstance(v, np.floating):
            cleaned[k] = float(v)
        else:
            cleaned[k] = v
    return cleaned

def batch_write(db, collection_name, records, doc_id_field=None):
    print(f"Loading {collection_name}...")
    batch = db.batch()
    count = 0
    total = len(records)
    
    for record in tqdm(records, desc=collection_name, unit="doc"):
        cleaned_record = clean_dict(record)
        
        if doc_id_field and cleaned_record.get(doc_id_field) is not None:
            doc_ref = db.collection(collection_name).document(str(cleaned_record[doc_id_field]))
        else:
            doc_ref = db.collection(collection_name).document()
            
        batch.set(doc_ref, cleaned_record)
        count += 1
        
        if count % 500 == 0:
            batch.commit()
            batch = db.batch()
            
    if count % 500 != 0:
        batch.commit()

def delete_collection(coll_ref, batch_size=500):
    docs = list(coll_ref.limit(batch_size).stream())
    while docs:
        batch = coll_ref.firestore.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        docs = list(coll_ref.limit(batch_size).stream())

def main():
    parser = argparse.ArgumentParser(description='Load Olist CSV data into Firestore.')
    parser.add_argument('--replace', action='store_true', help='Delete existing documents before insert.')
    parser.add_argument('--collection', type=str, help='Specific collection to load.')
    args = parser.parse_args()

    db = init_firebase()

    COLLECTIONS = [
        'sellers', 'products', 'purchase_orders', 'po_lines', 'deliveries', 
        'quality_inspections', 'price_history', 'defect_types', 'risk_weight_config',
        'risk_scores', 'alert_log'
    ]

    if args.replace:
        colls_to_clear = [args.collection] if args.collection else COLLECTIONS
        print("Clearing existing data (--replace)...")
        for coll in colls_to_clear:
            print(f"Deleting {coll}...")
            delete_collection(db.collection(coll))

    target_collections = [args.collection] if args.collection else COLLECTIONS

    print("Loading CSV files...")
    sellers_df = pd.read_csv(os.path.join(DATA_DIR, 'olist_sellers_dataset.csv'))
    products_df = pd.read_csv(os.path.join(DATA_DIR, 'olist_products_dataset.csv'))
    orders_df = pd.read_csv(os.path.join(DATA_DIR, 'olist_orders_dataset.csv'))
    order_items_df = pd.read_csv(os.path.join(DATA_DIR, 'olist_order_items_dataset.csv'))
    reviews_df = pd.read_csv(os.path.join(DATA_DIR, 'olist_order_reviews_dataset.csv'))
    
    nlp_path = os.path.join(DATA_DIR, 'vader_classified_reviews.csv')
    if not os.path.exists(nlp_path):
        nlp_path = os.path.join(PROJECT_ROOT, 'nlp', 'vader_classified_reviews.csv')
    if os.path.exists(nlp_path):
        vader_df = pd.read_csv(nlp_path)
    else:
        vader_df = pd.DataFrame(columns=['InspectionID', 'DefectCategory', 'SeverityWeight'])

    # 1. sellers
    if 'sellers' in target_collections:
        seller_data = sellers_df.copy()
        seller_data['sellerName'] = seller_data['seller_city'].fillna('Unknown').astype(str) + ', ' + seller_data['seller_state'].fillna('??').astype(str)
        seller_data = seller_data.rename(columns={
            'seller_id': 'sellerId',
            'seller_zip_code_prefix': 'sellerZipCodePrefix',
            'seller_city': 'sellerCity',
            'seller_state': 'sellerState'
        })[['sellerId', 'sellerName', 'sellerZipCodePrefix', 'sellerCity', 'sellerState']]
        batch_write(db, 'sellers', seller_data.to_dict('records'), 'sellerId')

    # 2. products
    if 'products' in target_collections:
        product_data = products_df.copy().rename(columns={
            'product_id': 'productId',
            'product_category_name': 'productCategoryName'
        })[['productId', 'productCategoryName']]
        batch_write(db, 'products', product_data.to_dict('records'), 'productId')

    # 3. purchase_orders
    if 'purchase_orders' in target_collections:
        po_data = orders_df.copy()
        po_data['order_purchase_timestamp'] = pd.to_datetime(po_data['order_purchase_timestamp'])
        po_data['order_estimated_delivery_date'] = pd.to_datetime(po_data['order_estimated_delivery_date'])
        po_data = po_data.rename(columns={
            'order_id': 'poId',
            'order_purchase_timestamp': 'orderDate',
            'order_estimated_delivery_date': 'expectedDeliveryDate',
            'order_status': 'orderStatus'
        })[['poId', 'orderDate', 'expectedDeliveryDate', 'orderStatus']]
        batch_write(db, 'purchase_orders', po_data.to_dict('records'), 'poId')

    # 4. po_lines
    if 'po_lines' in target_collections:
        line_data = order_items_df.copy()
        line_data['shipping_limit_date'] = pd.to_datetime(line_data['shipping_limit_date'])
        line_data = line_data.rename(columns={
            'order_id': 'poId',
            'order_item_id': 'lineNo',
            'product_id': 'productId',
            'seller_id': 'sellerId',
            'price': 'unitPriceAtOrder',
            'freight_value': 'freightValue',
            'shipping_limit_date': 'shippingLimitDate'
        })[['poId', 'lineNo', 'productId', 'sellerId', 'unitPriceAtOrder', 'freightValue', 'shippingLimitDate']]
        batch_write(db, 'po_lines', line_data.to_dict('records'))

    # 5. deliveries
    if 'deliveries' in target_collections or 'price_history' in target_collections:
        orders_df['order_delivered_carrier_date'] = pd.to_datetime(orders_df['order_delivered_carrier_date'])
        orders_df['order_delivered_customer_date'] = pd.to_datetime(orders_df['order_delivered_customer_date'])
        orders_df['order_estimated_delivery_date'] = pd.to_datetime(orders_df['order_estimated_delivery_date'])
        order_items_df['shipping_limit_date'] = pd.to_datetime(order_items_df['shipping_limit_date'])
        merged_orders = pd.merge(orders_df, order_items_df, on='order_id', how='inner')

    if 'deliveries' in target_collections:
        min_shipping = merged_orders.groupby('order_id')['shipping_limit_date'].min().reset_index()
        del_data = pd.merge(orders_df, min_shipping, on='order_id', how='inner')
        del_data = del_data[del_data['order_delivered_customer_date'].notnull()].copy()
        del_data['sellerDispatchDelayDays'] = (del_data['order_delivered_carrier_date'] - del_data['shipping_limit_date']).dt.days
        del_data['carrierTransitDelayDays'] = (del_data['order_delivered_customer_date'] - del_data['order_delivered_carrier_date']).dt.days
        del_data['delayDays'] = (del_data['order_delivered_customer_date'] - del_data['order_estimated_delivery_date']).dt.days
        del_data.reset_index(drop=True, inplace=True)
        del_data['deliveryId'] = del_data.index + 1
        del_data = del_data.rename(columns={
            'order_id': 'poId',
            'order_delivered_carrier_date': 'deliveredCarrierDate',
            'order_delivered_customer_date': 'actualDeliveryDate'
        })[['deliveryId', 'poId', 'deliveredCarrierDate', 'actualDeliveryDate', 'sellerDispatchDelayDays', 'carrierTransitDelayDays', 'delayDays']]
        batch_write(db, 'deliveries', del_data.to_dict('records'))

    # 6. quality_inspections
    if 'quality_inspections' in target_collections:
        qi_data = reviews_df.copy()
        qi_data['review_creation_date'] = pd.to_datetime(qi_data['review_creation_date'])
        qi_data['review_answer_timestamp'] = pd.to_datetime(qi_data['review_answer_timestamp'])
        qi_data['rejectionFlag'] = np.where(qi_data['review_score'] <= 3, 'Negative', 'Positive')
        qi_data.reset_index(drop=True, inplace=True)
        qi_data['inspectionId'] = qi_data.index + 1
        
        if not vader_df.empty:
            vader_df['defectTypeId'] = vader_df['DefectCategory'].map(DEFECT_MAP).astype('Int64')
            vader_df = vader_df.rename(columns={'SeverityWeight': 'vaderSeverityWeight', 'InspectionID': 'inspectionId'})
            qi_data = pd.merge(qi_data, vader_df[['inspectionId', 'defectTypeId', 'vaderSeverityWeight']], on='inspectionId', how='left')
        else:
            qi_data['defectTypeId'] = None
            qi_data['vaderSeverityWeight'] = None

        qi_data = qi_data.rename(columns={
            'order_id': 'poId',
            'review_creation_date': 'inspectionDate',
            'review_score': 'reviewScore',
            'review_answer_timestamp': 'reviewAnswerTimestamp',
            'review_comment_title': 'reviewCommentTitle',
            'review_comment_message': 'reviewCommentMessage'
        })[['inspectionId', 'poId', 'inspectionDate', 'reviewScore', 'rejectionFlag', 'reviewAnswerTimestamp', 'reviewCommentTitle', 'reviewCommentMessage', 'defectTypeId', 'vaderSeverityWeight']]
        batch_write(db, 'quality_inspections', qi_data.to_dict('records'))

    # 7. price_history
    if 'price_history' in target_collections:
        price_hist = merged_orders[['seller_id', 'product_id', 'order_purchase_timestamp', 'price']].copy()
        price_hist.sort_values(by=['seller_id', 'product_id', 'order_purchase_timestamp'], inplace=True)
        price_hist['priceChangePercent'] = price_hist.groupby(['seller_id', 'product_id'])['price'].pct_change() * 100
        price_hist.reset_index(drop=True, inplace=True)
        price_hist['priceRecordId'] = price_hist.index + 1
        price_hist = price_hist.rename(columns={
            'seller_id': 'sellerId',
            'product_id': 'productId',
            'order_purchase_timestamp': 'priceDate',
            'price': 'unitPrice'
        })[['priceRecordId', 'sellerId', 'productId', 'priceDate', 'unitPrice', 'priceChangePercent']]
        batch_write(db, 'price_history', price_hist.to_dict('records'))

    # 8. defect_types
    if 'defect_types' in target_collections:
        defect_types = DEFECT_TYPES
        batch_write(db, 'defect_types', defect_types, 'defectTypeId')

    # 9. risk_weight_config
    if 'risk_weight_config' in target_collections:
        config = [{
            'weightConfigId': '1',
            'productCategoryLevel': 'DEFAULT',
            'delayWeight': 0.40,
            'qualityWeight': 0.35,
            'priceWeight': 0.25,
            'effectiveFromDate': datetime.now() if 'datetime' in globals() else pd.Timestamp.now().to_pydatetime()
        }]
        batch_write(db, 'risk_weight_config', config, 'weightConfigId')

    # 10 & 11 are empty
    if 'risk_scores' in target_collections:
        pass
    if 'alert_log' in target_collections:
        pass

    print("All data loaded successfully.")

if __name__ == '__main__':
    main()
