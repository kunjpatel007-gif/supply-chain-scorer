import os
import json
import time
import datetime
import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.preprocessing import MinMaxScaler
import sys
from tqdm import tqdm

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from project_env import load_env

from backend.firebase_client import db

def load_models(base_dir):
    model_path = os.path.join(base_dir, '..', 'training', 'vendor_risk_model.json')
    labels_path = os.path.join(base_dir, '..', 'training', 'label_classes.json')
    
    print(f"Loading XGBoost model from {model_path}...")
    booster = xgb.Booster()
    booster.load_model(model_path)
    
    print(f"Loading label classes from {labels_path}...")
    with open(labels_path, 'r') as f:
        label_classes = json.load(f)
        
    return booster, label_classes

def get_seller_features(db, seller_id=None):
    import pandas as pd
    import numpy as np

    print(f"Fetching data from Firestore for {'Seller: ' + seller_id if seller_id else 'ALL Sellers'}...")

    # 1. SELLERS
    sellers_ref = db.collection('sellers')
    if seller_id:
        doc = sellers_ref.document(seller_id).get()
        sellers_docs = [doc] if doc.exists else []
    else:
        sellers_docs = list(sellers_ref.stream(timeout=3600))
        
    sellers_data = []
    for doc in sellers_docs:
        d = doc.to_dict() or {}
        sellers_data.append({
            'SellerID': d.get('sellerId') or doc.id,
            'SellerName': d.get('sellerName'),
        })
    sellers_df = pd.DataFrame(sellers_data)
    if sellers_df.empty:
        return pd.DataFrame()

    # 2. PO_LINES
    po_lines_ref = db.collection('po_lines')
    if seller_id:
        from google.cloud import firestore
        po_lines_docs = list(po_lines_ref.where(filter=firestore.FieldFilter('sellerId', '==', seller_id)).stream(timeout=3600))
    else:
        po_lines_docs = list(po_lines_ref.stream(timeout=3600))
        
    po_lines_data = []
    po_ids = set()
    for doc in po_lines_docs:
        d = doc.to_dict() or {}
        po_id = d.get('poId')
        if po_id:
            po_ids.add(po_id)
        po_lines_data.append({
            'SellerID': d.get('sellerId'),
            'PO_ID': po_id,
            'LineNo': d.get('lineNo'),
            'ProductID': d.get('productId'),
            'UnitPriceAtOrder': d.get('unitPriceAtOrder', 0.0),
            'FreightValue': d.get('freightValue', 0.0),
        })
    po_lines_df = pd.DataFrame(po_lines_data)

    # Helper function to fetch in batches of 30 (Firestore IN limit)
    def fetch_by_po_ids(collection_name, po_ids_list):
        from google.cloud import firestore
        docs = []
        for i in range(0, len(po_ids_list), 30):
            batch_ids = po_ids_list[i:i+30]
            if batch_ids:
                docs.extend(db.collection(collection_name).where(filter=firestore.FieldFilter('poId', 'in', batch_ids)).stream(timeout=3600))
        return docs

    po_ids_list = list(po_ids)

    # 3. DELIVERIES
    if seller_id and po_ids_list:
        del_docs = fetch_by_po_ids('deliveries', po_ids_list)
    elif seller_id:
        del_docs = []
    else:
        del_docs = list(db.collection('deliveries').stream(timeout=3600))
        
    del_data = []
    for doc in del_docs:
        d = doc.to_dict() or {}
        del_data.append({
            'PO_ID': d.get('poId'),
            'DeliveryID': d.get('deliveryId') or doc.id,
            'DelayDays': d.get('delayDays', 0),
        })
    del_df = pd.DataFrame(del_data)

    # 4. QUALITY_INSPECTIONS
    if seller_id and po_ids_list:
        qi_docs = fetch_by_po_ids('quality_inspections', po_ids_list)
    elif seller_id:
        qi_docs = []
    else:
        qi_docs = list(db.collection('quality_inspections').stream(timeout=3600))
        
    qi_data = []
    for doc in qi_docs:
        d = doc.to_dict() or {}
        qi_data.append({
            'PO_ID': d.get('poId'),
            'InspectionID': d.get('inspectionId') or doc.id,
            'ReviewScore': d.get('reviewScore', 5),
            'RejectionFlag': d.get('rejectionFlag', ''),
            'DefectTypeID': d.get('defectTypeId'),
            'VaderSeverityWeight': d.get('vaderSeverityWeight'),
        })
    qi_df = pd.DataFrame(qi_data)

    # 5. DEFECT_TYPES
    dt_docs = list(db.collection('defect_types').stream(timeout=3600))
    dt_data = []
    for doc in dt_docs:
        d = doc.to_dict() or {}
        dt_data.append({
            'DefectTypeID': d.get('defectTypeId') or doc.id,
            'SeverityWeight': d.get('severity', 0),
        })
    dt_df = pd.DataFrame(dt_data)

    # 6. PRICE_HISTORY
    ph_ref = db.collection('price_history')
    if seller_id:
        from google.cloud import firestore
        ph_docs = list(ph_ref.where(filter=firestore.FieldFilter('sellerId', '==', seller_id)).stream(timeout=3600))
    else:
        ph_docs = list(ph_ref.stream(timeout=3600))
    ph_data = []
    for doc in ph_docs:
        d = doc.to_dict() or {}
        ph_data.append({
            'SellerID': d.get('sellerId'),
            'UnitPrice': d.get('unitPrice', 0.0),
        })
    ph_df = pd.DataFrame(ph_data)

    print("Computing features via Pandas...")
    # SELLER STATS
    if not po_lines_df.empty:
        seller_stats = po_lines_df.groupby('SellerID').agg(
            total_orders=('PO_ID', 'nunique'),
            total_items=('LineNo', 'count'),
            unique_products=('ProductID', 'nunique'),
            avg_price=('UnitPriceAtOrder', 'mean'),
            avg_freight=('FreightValue', 'mean')
        ).reset_index()
    else:
        seller_stats = pd.DataFrame(columns=['SellerID', 'total_orders', 'total_items', 'unique_products', 'avg_price', 'avg_freight'])

    # DELIVERY STATS
    if not po_lines_df.empty and not del_df.empty:
        po_del = pd.merge(po_lines_df[['SellerID', 'PO_ID']].drop_duplicates(), del_df, on='PO_ID', how='inner')
        if not po_del.empty:
            po_del['is_late'] = (po_del['DelayDays'] > 0).astype(int)
            del_stats = po_del.groupby('SellerID').agg(
                avg_delay_days=('DelayDays', 'mean'),
                max_delay_days=('DelayDays', 'max'),
                late_count=('is_late', 'sum'),
                del_count=('DeliveryID', 'count')
            ).reset_index()
            del_stats['pct_late_deliveries'] = del_stats['late_count'] / del_stats['del_count'].replace(0, np.nan)
        else:
            del_stats = pd.DataFrame(columns=['SellerID', 'avg_delay_days', 'max_delay_days', 'pct_late_deliveries'])
    else:
        del_stats = pd.DataFrame(columns=['SellerID', 'avg_delay_days', 'max_delay_days', 'pct_late_deliveries'])

    # QUALITY STATS
    if not po_lines_df.empty and not qi_df.empty:
        po_qi = pd.merge(po_lines_df[['SellerID', 'PO_ID']].drop_duplicates(), qi_df, on='PO_ID', how='inner')
        if not po_qi.empty and not dt_df.empty:
            po_qi['DefectTypeID'] = po_qi['DefectTypeID'].astype(str)
            dt_df['DefectTypeID'] = dt_df['DefectTypeID'].astype(str)
            po_qi = pd.merge(po_qi, dt_df, on='DefectTypeID', how='left')
        elif not po_qi.empty:
            po_qi['SeverityWeight'] = np.nan
            
        if not po_qi.empty:
            po_qi['is_negative_reject'] = (po_qi['RejectionFlag'] == 'Negative').astype(int)
            po_qi['is_negative_review'] = (po_qi['ReviewScore'] <= 3).astype(int)
            po_qi['vader_or_severity'] = po_qi['VaderSeverityWeight'].fillna(po_qi['SeverityWeight'] if 'SeverityWeight' in po_qi else 0).fillna(0)
            
            qi_stats = po_qi.groupby('SellerID').agg(
                avg_review_score=('ReviewScore', 'mean'),
                neg_reject_count=('is_negative_reject', 'sum'),
                insp_count=('InspectionID', 'count'),
                negative_review_count=('is_negative_review', 'sum'),
                defect_penalty=('vader_or_severity', 'sum')
            ).reset_index()
            qi_stats['rejection_rate'] = qi_stats['neg_reject_count'] / qi_stats['insp_count'].replace(0, np.nan)
        else:
            qi_stats = pd.DataFrame(columns=['SellerID', 'avg_review_score', 'rejection_rate', 'negative_review_count', 'defect_penalty'])
    else:
        qi_stats = pd.DataFrame(columns=['SellerID', 'avg_review_score', 'rejection_rate', 'negative_review_count', 'defect_penalty'])

    # PRICE STATS
    if not ph_df.empty:
        ph_stats = ph_df.groupby('SellerID').agg(
            price_volatility=('UnitPrice', 'std')
        ).reset_index()
    else:
        ph_stats = pd.DataFrame(columns=['SellerID', 'price_volatility'])

    # MERGE ALL
    final_df = sellers_df[['SellerID', 'SellerName']].copy()
    
    final_df = pd.merge(final_df, seller_stats, on='SellerID', how='left')
    final_df = pd.merge(final_df, del_stats[['SellerID', 'avg_delay_days', 'max_delay_days', 'pct_late_deliveries']], on='SellerID', how='left')
    final_df = pd.merge(final_df, qi_stats[['SellerID', 'avg_review_score', 'rejection_rate', 'negative_review_count', 'defect_penalty']], on='SellerID', how='left')
    final_df = pd.merge(final_df, ph_stats[['SellerID', 'price_volatility']], on='SellerID', how='left')
    
    final_df['avg_delay_days'] = final_df['avg_delay_days'].fillna(0)
    final_df['max_delay_days'] = final_df['max_delay_days'].fillna(0)
    final_df['pct_late_deliveries'] = final_df['pct_late_deliveries'].fillna(0)
    final_df['avg_review_score'] = final_df['avg_review_score'].fillna(5)
    final_df['rejection_rate'] = final_df['rejection_rate'].fillna(0)
    final_df['negative_review_count'] = final_df['negative_review_count'].fillna(0)
    final_df['defect_penalty'] = final_df['defect_penalty'].fillna(0)
    final_df['avg_price'] = final_df['avg_price'].fillna(0)
    final_df['price_volatility'] = final_df['price_volatility'].fillna(0)
    final_df['avg_freight'] = final_df['avg_freight'].fillna(0)
    final_df['total_orders'] = final_df['total_orders'].fillna(0)
    final_df['total_items'] = final_df['total_items'].fillna(0)
    final_df['unique_products'] = final_df['unique_products'].fillna(0)
    
    final_df.columns = [c.upper() for c in final_df.columns]
    return final_df

def compute_subscores(df):
    print("Computing sub-scores using MinMaxScaler...")
    scaler = MinMaxScaler()
    
    df = df.fillna(0)
    
    delay_raw = (df['AVG_DELAY_DAYS'] + df['PCT_LATE_DELIVERIES']).values.reshape(-1, 1)
    df['DelaySubScore'] = scaler.fit_transform(delay_raw) * 100
    
    quality_raw = (df['REJECTION_RATE'] + (5 - df['AVG_REVIEW_SCORE']) + (df['DEFECT_PENALTY'] * 0.1)).values.reshape(-1, 1)
    df['QualitySubScore'] = scaler.fit_transform(quality_raw) * 100
    
    price_vol_raw = df['PRICE_VOLATILITY'].values.reshape(-1, 1)
    df['PriceVolatilitySubScore'] = scaler.fit_transform(price_vol_raw) * 100
    
    return df

def run_pipeline():
    start_time = time.time()
    print("Starting Risk Scoring Pipeline...")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        booster, label_classes = load_models(base_dir)
    except Exception as e:
        print(f"Error loading models: {e}")
        return
        
    try:
        df = get_seller_features(db)
        
        if df.empty:
            print("No sellers found to score.")
            return
            
        print(f"Fetched {len(df)} sellers.")
        
        feature_cols = [
            'total_orders', 'total_items', 'unique_products',
            'avg_delay_days', 'max_delay_days', 'pct_late_deliveries',
            'avg_review_score', 'rejection_rate', 'negative_review_count',
            'avg_price', 'price_volatility', 'avg_freight', 'defect_penalty',
        ]
        
        # Lowercase for XGBoost
        df.columns = [c.lower() for c in df.columns]
        
        print("Running XGBoost inference...")
        dmatrix = xgb.DMatrix(df[feature_cols])
        preds = booster.predict(dmatrix)
        
        if len(preds.shape) > 1 and preds.shape[1] > 1:
            pred_classes = np.argmax(preds, axis=1)
        else:
            pred_classes = np.round(preds).astype(int)
            
        df['riskcategory'] = [label_classes[int(p)] if int(p) < len(label_classes) else 'Unknown' for p in pred_classes]
        
        # Restore upper case to interact with subscore function consistently
        df.columns = [c.upper() for c in df.columns]
        df = compute_subscores(df)
        
        print("Fetching RISK_WEIGHT_CONFIG...")
        weight_docs = list(db.collection('risk_weight_config').where('productCategoryLevel', '==', 'DEFAULT').stream())
        if weight_docs:
            w = weight_docs[0].to_dict()
            delay_w = float(w.get('delayWeight', 0.40))
            qual_w = float(w.get('qualityWeight', 0.35))
            price_w = float(w.get('priceWeight', 0.25))
        else:
            print("DEFAULT weight config not found. Using fallbacks.")
            delay_w, qual_w, price_w = 0.40, 0.35, 0.25
            
        df['WeightedRiskScore'] = (
            df['DelaySubScore'] * delay_w +
            df['QualitySubScore'] * qual_w +
            df['PriceVolatilitySubScore'] * price_w
        )
        
        period = datetime.datetime.now().strftime('%Y-%m')
        
        print("Fetching previous risk scores...")
        # Since we cannot easily do a subquery in firestore, fetch all and filter locally or fetch per seller
        # A simpler way is to query risk_scores where EvaluationPeriod < period
        rs_docs = db.collection('risk_scores').where('EvaluationPeriod', '<', period).stream()
        prev_scores = {}
        for doc in rs_docs:
            d = doc.to_dict()
            sid = d.get('SellerID')
            ep = d.get('EvaluationPeriod')
            sc = d.get('WeightedRiskScore', 0)
            if sid not in prev_scores or ep > prev_scores[sid]['period']:
                prev_scores[sid] = {'period': ep, 'score': sc}
                
        prev_map = {sid: data['score'] for sid, data in prev_scores.items()}
        
        trends = []
        for index, row in df.iterrows():
            sid = row['SELLERID']
            current_score = row['WeightedRiskScore']
            prev_score = prev_map.get(sid)
            
            if prev_score is None:
                trends.append('NEW')
            else:
                pct_change = (current_score - prev_score) / (prev_score if prev_score != 0 else 1) * 100
                if pct_change > 5:
                    trends.append('UP')
                elif pct_change < -5:
                    trends.append('DOWN')
                else:
                    trends.append('STABLE')
                    
        df['RiskTrend'] = trends
        df['EvaluationPeriod'] = period
        
        print(f"Merging {len(df)} records into RISK_SCORE using Firestore batches...")
        batch = db.batch()
        batch_count = 0
        total_writes = 0
        
        for _, row in tqdm(df.iterrows(), total=len(df), desc="Writing Risk Scores"):
            doc_id = f"{row['SELLERID']}_{period}"
            doc_ref = db.collection('risk_scores').document(doc_id)
            
            data = {
                'SellerID': row['SELLERID'],
                'EvaluationPeriod': period,
                'DelaySubScore': float(row['DelaySubScore']),
                'QualitySubScore': float(row['QualitySubScore']),
                'PriceVolatilitySubScore': float(row['PriceVolatilitySubScore']),
                'WeightedRiskScore': float(row['WeightedRiskScore']),
                'RiskTrend': row['RiskTrend'],
                'RiskCategory': row['RISKCATEGORY']
            }
            batch.set(doc_ref, data, merge=True)
            batch_count += 1
            
            if batch_count >= 400:
                batch.commit()
                batch = db.batch()
                batch_count = 0
                
        if batch_count > 0:
            batch.commit()
            
        high_risk_df = df[df['RISKCATEGORY'] == 'High']
        
        print(f"Generating alerts for {len(high_risk_df)} high-risk sellers...")
        alert_docs = db.collection('alert_log').where('ThresholdCrossedCategory', '==', 'High').where('EvaluationPeriod', '<', period).stream()
        prev_high_sellers = set()
        for doc in alert_docs:
            d = doc.to_dict()
            prev_high_sellers.add(d.get('SellerID'))
            
        # Delete existing alerts for this period
        existing_alerts = db.collection('alert_log').where('EvaluationPeriod', '==', period).where('ThresholdCrossedCategory', '==', 'High').stream()
        del_batch = db.batch()
        del_count = 0
        for doc in existing_alerts:
            del_batch.delete(doc.reference)
            del_count += 1
            if del_count >= 400:
                del_batch.commit()
                del_batch = db.batch()
                del_count = 0
        if del_count > 0:
            del_batch.commit()
        
        alert_batch = db.batch()
        alert_count = 0
        
        alert_data_len = len(high_risk_df)
        for _, row in tqdm(high_risk_df.iterrows(), total=alert_data_len, desc="Writing Alerts"):
            sid = row['SELLERID']
            sname = row['SELLERNAME']
            score = row['WeightedRiskScore']
            category = row['RISKCATEGORY']
            msg = f"Seller {sname if pd.notna(sname) else sid} scored {score:.2f} ({category} Risk) for period {period}"
            esc = 2 if sid in prev_high_sellers else 1
            
            doc_ref = db.collection('alert_log').document()
            alert_data = {
                'SellerID': sid,
                'EvaluationPeriod': period,
                'AlertDate': firestore.SERVER_TIMESTAMP,
                'ThresholdCrossedCategory': category,
                'AlertMessage': msg,
                'AlertStatus': 'Open',
                'EscalationLevel': esc
            }
            alert_batch.set(doc_ref, alert_data)
            alert_count += 1
            if alert_count >= 400:
                alert_batch.commit()
                alert_batch = db.batch()
                alert_count = 0
                
        if alert_count > 0:
            alert_batch.commit()
            
        end_time = time.time()
        dist = df['RISKCATEGORY'].value_counts().to_dict()
        
        print("-" * 50)
        print("PIPELINE SUMMARY")
        print("-" * 50)
        print(f"Total sellers scored: {len(df)}")
        print(f"Distribution: {dist.get('Low', 0)} Low, {dist.get('Medium', 0)} Medium, {dist.get('High', 0)} High")
        print(f"New alerts generated: {alert_data_len}")
        
        print("\nTop 5 Highest Risk Sellers:")
        top_5 = df.nlargest(5, 'WeightedRiskScore')
        for i, (_, row) in enumerate(top_5.iterrows(), 1):
            print(f"{i}. {row['SELLERID']} - Score: {row['WeightedRiskScore']:.2f} ({row['RISKCATEGORY']}, Trend: {row['RiskTrend']})")
            
        print(f"\nElapsed time: {end_time - start_time:.2f} seconds")
        print("-" * 50)

    except Exception as e:
        print(f"Pipeline error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    run_pipeline()
