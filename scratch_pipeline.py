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
        po_lines_docs = list(po_lines_ref.where(filter=firestore.FieldFilter('sellerId', '==', seller_id)).stream(timeout=3600))
    else:
        po_lines_docs = list(po_lines_ref.stream(timeout=3600))
        
    po_lines_data = []
    po_ids = set()
    for doc in po_lines_docs:
        d = doc.to_dict() or {}
        po_id = d.get('poId')
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
