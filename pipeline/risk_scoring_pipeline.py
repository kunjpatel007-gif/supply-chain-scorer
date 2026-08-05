import oracledb
import os
import json
import time
import datetime
import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.preprocessing import MinMaxScaler
from dotenv import load_dotenv

def get_connection():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    load_dotenv(env_path)
    return oracledb.connect(
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        dsn=os.getenv('DB_DSN')
    )

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

def get_seller_features(conn):
    query = """
    WITH seller_stats AS (
        SELECT 
            pl.SellerID,
            COUNT(DISTINCT pl.PO_ID) as total_orders,
            COUNT(pl.LineNo) as total_items,
            COUNT(DISTINCT pl.ProductID) as unique_products,
            AVG(pl.UnitPriceAtOrder) as avg_price,
            AVG(pl.FreightValue) as avg_freight
        FROM PO_LINE pl
        GROUP BY pl.SellerID
    ),
    delivery_stats AS (
        SELECT
            pl.SellerID,
            AVG(d.DelayDays) as avg_delay_days,
            MAX(d.DelayDays) as max_delay_days,
            SUM(CASE WHEN d.DelayDays > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(d.DeliveryID), 0) as pct_late_deliveries
        FROM PO_LINE pl
        JOIN DELIVERY d ON pl.PO_ID = d.PO_ID
        GROUP BY pl.SellerID
    ),
    quality_stats AS (
        SELECT
            pl.SellerID,
            AVG(qi.ReviewScore) as avg_review_score,
            SUM(CASE WHEN qi.RejectionFlag = 'Negative' THEN 1 ELSE 0 END) / NULLIF(COUNT(qi.InspectionID), 0) as rejection_rate,
            SUM(CASE WHEN qi.ReviewScore <= 3 THEN 1 ELSE 0 END) as negative_review_count,
            NVL(SUM(NVL(qi.VaderSeverityWeight, dt.SeverityWeight)), 0) as defect_penalty
        FROM PO_LINE pl
        JOIN QUALITY_INSPECTION qi ON pl.PO_ID = qi.PO_ID
        LEFT JOIN DEFECT_TYPE dt ON qi.DefectTypeID = dt.DefectTypeID
        GROUP BY pl.SellerID
    ),
    price_stats AS (
        SELECT 
            SellerID,
            STDDEV(UnitPrice) as price_volatility
        FROM PRICE_HISTORY
        GROUP BY SellerID
    )
    SELECT 
        s.SellerID,
        s.SellerName,
        NVL(ds.avg_delay_days, 0) as avg_delay_days,
        NVL(ds.max_delay_days, 0) as max_delay_days,
        NVL(ds.pct_late_deliveries, 0) as pct_late_deliveries,
        NVL(qs.avg_review_score, 5) as avg_review_score,
        NVL(qs.rejection_rate, 0) as rejection_rate,
        NVL(qs.negative_review_count, 0) as negative_review_count,
        NVL(qs.defect_penalty, 0) as defect_penalty,
        NVL(ss.avg_price, 0) as avg_price,
        NVL(ps.price_volatility, 0) as price_volatility,
        NVL(ss.avg_freight, 0) as avg_freight,
        NVL(ss.total_orders, 0) as total_orders,
        NVL(ss.total_items, 0) as total_items,
        NVL(ss.unique_products, 0) as unique_products
    FROM SELLER s
    LEFT JOIN seller_stats ss ON s.SellerID = ss.SellerID
    LEFT JOIN delivery_stats ds ON s.SellerID = ds.SellerID
    LEFT JOIN quality_stats qs ON s.SellerID = qs.SellerID
    LEFT JOIN price_stats ps ON s.SellerID = ps.SellerID
    """
    print("Executing query to extract seller features...")
    df = pd.read_sql(query, conn)
    return df

def compute_subscores(df):
    print("Computing sub-scores using MinMaxScaler...")
    scaler = MinMaxScaler()
    
    # Fill any remaining NaNs just in case
    df = df.fillna(0)
    
    delay_raw = (df['AVG_DELAY_DAYS'] + df['PCT_LATE_DELIVERIES']).values.reshape(-1, 1)
    df['DelaySubScore'] = scaler.fit_transform(delay_raw) * 100
    
    # Quality raw now mathematically penalizes sellers based on the total severity of AI defects
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
        conn = get_connection()
    except Exception as e:
        print(f"Database connection failed: {e}")
        return
        
    cursor = conn.cursor()
    try:
        df = get_seller_features(conn)
        
        if df.empty:
            print("No sellers found to score.")
            return
            
        print(f"Fetched {len(df)} sellers.")
        
        feature_cols = [
            'total_orders', 'total_items', 'unique_products',
            'avg_delay_days', 'max_delay_days', 'pct_late_deliveries',
            'avg_review_score', 'rejection_rate', 'negative_review_count',
            'avg_price', 'price_volatility', 'avg_freight'
        ]
        
        # Ensure column names map correctly depending on oracle pd.read_sql output case
        orig_cols = {c.lower(): c for c in df.columns}
        df.columns = [c.lower() for c in df.columns]
        
        print("Running XGBoost inference...")
        dmatrix = xgb.DMatrix(df[feature_cols])
        preds = booster.predict(dmatrix)
        
        if len(preds.shape) > 1 and preds.shape[1] > 1:
            pred_classes = np.argmax(preds, axis=1)
        else:
            pred_classes = np.round(preds).astype(int)
            
        # label_classes is a list like ['High', 'Low', 'Medium'] from LabelEncoder
        df['riskcategory'] = [label_classes[int(p)] if int(p) < len(label_classes) else 'Unknown' for p in pred_classes]
        
        # Restore upper case to interact with subscore function consistently
        df.columns = [c.upper() for c in df.columns]
        df = compute_subscores(df)
        
        print("Fetching RISK_WEIGHT_CONFIG...")
        cursor.execute("SELECT DelayWeight, QualityWeight, PriceWeight FROM RISK_WEIGHT_CONFIG WHERE ProductCategoryLevel = 'DEFAULT'")
        weights = cursor.fetchone()
        if weights:
            delay_w, qual_w, price_w = [float(w) for w in weights]
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
        prev_scores_query = """
            SELECT SellerID, WeightedRiskScore 
            FROM RISK_SCORE
            WHERE EvaluationPeriod = (
                SELECT MAX(EvaluationPeriod) 
                FROM RISK_SCORE rs2 
                WHERE rs2.SellerID = RISK_SCORE.SellerID AND EvaluationPeriod < :period
            )
        """
        prev_df = pd.read_sql(prev_scores_query, conn, params={"period": period})
        prev_map = dict(zip(prev_df['SELLERID'], prev_df['WEIGHTEDRISKSCORE']))
        
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
        
        print(f"Merging {len(df)} records into RISK_SCORE...")
        merge_sql = """
            MERGE INTO RISK_SCORE trg
            USING (SELECT :1 as SellerID, :2 as EvaluationPeriod, :3 as DelaySubScore, :4 as QualitySubScore, :5 as PriceVolatilitySubScore, :6 as WeightedRiskScore, :7 as RiskTrend, :8 as RiskCategory FROM dual) src
            ON (trg.SellerID = src.SellerID AND trg.EvaluationPeriod = src.EvaluationPeriod)
            WHEN MATCHED THEN
                UPDATE SET 
                    DelaySubScore = src.DelaySubScore,
                    QualitySubScore = src.QualitySubScore,
                    PriceVolatilitySubScore = src.PriceVolatilitySubScore,
                    WeightedRiskScore = src.WeightedRiskScore,
                    RiskTrend = src.RiskTrend,
                    RiskCategory = src.RiskCategory
            WHEN NOT MATCHED THEN
                INSERT (SellerID, EvaluationPeriod, DelaySubScore, QualitySubScore, PriceVolatilitySubScore, WeightedRiskScore, RiskTrend, RiskCategory)
                VALUES (src.SellerID, src.EvaluationPeriod, src.DelaySubScore, src.QualitySubScore, src.PriceVolatilitySubScore, src.WeightedRiskScore, src.RiskTrend, src.RiskCategory)
        """
        
        insert_data = []
        for _, row in df.iterrows():
            insert_data.append((
                row['SELLERID'], row['EvaluationPeriod'], 
                float(row['DelaySubScore']), float(row['QualitySubScore']), 
                float(row['PriceVolatilitySubScore']), float(row['WeightedRiskScore']), 
                row['RiskTrend'], row['RISKCATEGORY']
            ))
            
        cursor.executemany(merge_sql, insert_data)
        conn.commit()
        
        # Only generate alerts for sellers whose XGBoost prediction is actually 'High'
        high_risk_df = df[df['RISKCATEGORY'] == 'High']
        
        print(f"Generating alerts for {len(high_risk_df)} high-risk sellers...")
        alert_sql = """
            INSERT INTO ALERT_LOG (AlertID, SellerID, EvaluationPeriod, AlertDate, ThresholdCrossedCategory, AlertMessage, AlertStatus, EscalationLevel)
            VALUES (ALERT_SEQ.NEXTVAL, :1, :2, CURRENT_TIMESTAMP, :3, :4, 'Open', :5)
        """
        
        alert_prev_query = """
            SELECT DISTINCT SellerID 
            FROM ALERT_LOG 
            WHERE ThresholdCrossedCategory = 'High' AND EvaluationPeriod = (
                SELECT MAX(EvaluationPeriod) 
                FROM ALERT_LOG a2 
                WHERE a2.SellerID = ALERT_LOG.SellerID AND EvaluationPeriod < :period
            )
        """
        prev_high_sellers = [r[0] for r in cursor.execute(alert_prev_query, period=period).fetchall()]
        
        alert_data = []
        for _, row in high_risk_df.iterrows():
            sid = row['SELLERID']
            sname = row['SELLERNAME']
            score = row['WeightedRiskScore']
            category = row['RISKCATEGORY']
            msg = f"Seller {sname if pd.notna(sname) else sid} scored {score:.2f} ({category} Risk) for period {period}"
            esc = 2 if sid in prev_high_sellers else 1
            
            alert_data.append((
                sid, period, category, msg, esc
            ))
            
        if alert_data:
            cursor.executemany(alert_sql, alert_data)
            conn.commit()
            
        end_time = time.time()
        dist = df['RISKCATEGORY'].value_counts().to_dict()
        
        print("-" * 50)
        print("PIPELINE SUMMARY")
        print("-" * 50)
        print(f"Total sellers scored: {len(df)}")
        print(f"Distribution: {dist.get('Low', 0)} Low, {dist.get('Medium', 0)} Medium, {dist.get('High', 0)} High")
        print(f"New alerts generated: {len(alert_data)}")
        
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
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    run_pipeline()
