import os
import json
import xgboost as xgb
import pandas as pd
import numpy as np
import oracledb
from flask import Flask, render_template, request, redirect, url_for
from dotenv import load_dotenv

app = Flask(__name__)

# Load Environment
def get_base_path():
    import sys
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

env_path = os.path.join(get_base_path(), '.env')
load_dotenv(env_path)

# Load Models on Startup
import sys
if getattr(sys, 'frozen', False):
    model_path = os.path.join(get_base_path(), 'training', 'vendor_risk_model.json')
    labels_path = os.path.join(get_base_path(), 'training', 'label_classes.json')
else:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model_path = os.path.join(base_dir, 'training', 'vendor_risk_model.json')
    labels_path = os.path.join(base_dir, 'training', 'label_classes.json')

print("Loading XGBoost Model...")
booster = xgb.Booster()
booster.load_model(model_path)
with open(labels_path, 'r') as f:
    label_classes = json.load(f)

def get_db():
    return oracledb.connect(
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        dsn=os.getenv('DB_DSN')
    )

@app.route('/')
def index():
    conn = get_db()
    cursor = conn.cursor()
    
    # Dashboard Stats
    cursor.execute("SELECT COUNT(*) FROM SELLER")
    total_sellers = cursor.fetchone()[0]
    
    # We grab the latest period scores safely
    cursor.execute("""
        SELECT RiskCategory, COUNT(*) 
        FROM RISK_SCORE 
        WHERE EvaluationPeriod = (SELECT MAX(EvaluationPeriod) FROM RISK_SCORE)
        GROUP BY RiskCategory
    """)
    category_counts = {r[0]: r[1] for r in cursor.fetchall()}
    
    # Top 10 Riskiest
    cursor.execute("""
        SELECT r.SellerID, s.SellerName, r.WeightedRiskScore, r.RiskCategory, r.RiskTrend 
        FROM RISK_SCORE r JOIN SELLER s ON r.SellerID = s.SellerID
        WHERE r.EvaluationPeriod = (SELECT MAX(EvaluationPeriod) FROM RISK_SCORE)
        ORDER BY r.WeightedRiskScore DESC 
        FETCH FIRST 10 ROWS ONLY
    """)
    top_risky = cursor.fetchall()
    
    # Latest Alerts
    cursor.execute("""
        SELECT a.SellerID, s.SellerName, a.AlertMessage, a.AlertDate 
        FROM ALERT_LOG a JOIN SELLER s ON a.SellerID = s.SellerID
        ORDER BY a.AlertDate DESC 
        FETCH FIRST 5 ROWS ONLY
    """)
    recent_alerts = cursor.fetchall()
    
    # Fetch all sellers for the search autocomplete dropdown
    cursor.execute("SELECT SellerName, SellerID FROM SELLER ORDER BY SellerName")
    all_sellers = cursor.fetchall()
    
    conn.close()
    
    return render_template('index.html', 
                           total_sellers=total_sellers,
                           category_counts=category_counts,
                           top_risky=top_risky,
                           recent_alerts=recent_alerts,
                           all_sellers=all_sellers)

@app.route('/search', methods=['POST'])
def search():
    query = request.form.get('seller_id', '').strip()
    if not query:
        return redirect(url_for('index'))
        
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if exactly 32 hex chars
    if len(query) == 32 and all(c in '0123456789abcdefABCDEF' for c in query):
        cursor.execute("SELECT SellerID FROM SELLER WHERE SellerID = :1", [query])
        if cursor.fetchone():
            conn.close()
            return redirect(url_for('seller', seller_id=query))
            
    # Otherwise treat as name or partial ID search
    cursor.execute("""
        SELECT SellerID, SellerName, SellerCity, SellerState 
        FROM SELLER 
        WHERE LOWER(SellerName) LIKE LOWER(:1) OR LOWER(SellerID) = LOWER(:2)
    """, [f'%{query}%', query])
    matches = cursor.fetchall()
    conn.close()
    
    if len(matches) == 1:
        return redirect(url_for('seller', seller_id=matches[0][0]))
    
    # If 0 or >1, show the results page
    return render_template('search_results.html', query=query, results=matches)

@app.route('/seller/<seller_id>')
def seller(seller_id):
    conn = get_db()
    cursor = conn.cursor()
    
    # Basic Info
    cursor.execute("SELECT SellerCity, SellerState, SellerName FROM SELLER WHERE SellerID = :1", [seller_id])
    seller_info = cursor.fetchone()
    
    if not seller_info:
        conn.close()
        return "Seller not found", 404
        
    # Live ML Inference: Fetch Features
    feature_query = """
    WITH seller_stats AS (
        SELECT pl.SellerID, COUNT(DISTINCT pl.PO_ID) as total_orders, COUNT(pl.LineNo) as total_items, 
               COUNT(DISTINCT pl.ProductID) as unique_products, AVG(pl.UnitPriceAtOrder) as avg_price, AVG(pl.FreightValue) as avg_freight
        FROM PO_LINE pl WHERE pl.SellerID = :1 GROUP BY pl.SellerID
    ),
    delivery_stats AS (
        SELECT pl.SellerID, AVG(d.DelayDays) as avg_delay_days, MAX(d.DelayDays) as max_delay_days,
               SUM(CASE WHEN d.DelayDays > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(d.DeliveryID), 0) as pct_late_deliveries
        FROM PO_LINE pl JOIN DELIVERY d ON pl.PO_ID = d.PO_ID WHERE pl.SellerID = :1 GROUP BY pl.SellerID
    ),
    quality_stats AS (
        SELECT pl.SellerID, AVG(qi.ReviewScore) as avg_review_score,
               SUM(CASE WHEN qi.RejectionFlag = 'Negative' THEN 1 ELSE 0 END) / NULLIF(COUNT(qi.InspectionID), 0) as rejection_rate,
               SUM(CASE WHEN qi.ReviewScore <= 3 THEN 1 ELSE 0 END) as negative_review_count,
               NVL(SUM(dt.SeverityWeight), 0) as defect_penalty
        FROM PO_LINE pl JOIN QUALITY_INSPECTION qi ON pl.PO_ID = qi.PO_ID
        LEFT JOIN DEFECT_TYPE dt ON qi.DefectTypeID = dt.DefectTypeID
        WHERE pl.SellerID = :1 GROUP BY pl.SellerID
    ),
    price_stats AS (
        SELECT SellerID, STDDEV(UnitPrice) as price_volatility FROM PRICE_HISTORY WHERE SellerID = :1 GROUP BY SellerID
    )
    SELECT 
        NVL(ss.total_orders, 0) as total_orders, NVL(ss.total_items, 0) as total_items, NVL(ss.unique_products, 0) as unique_products,
        NVL(ds.avg_delay_days, 0) as avg_delay_days, NVL(ds.max_delay_days, 0) as max_delay_days, NVL(ds.pct_late_deliveries, 0) as pct_late_deliveries,
        NVL(qs.avg_review_score, 5) as avg_review_score, NVL(qs.rejection_rate, 0) as rejection_rate, NVL(qs.negative_review_count, 0) as negative_review_count,
        NVL(ss.avg_price, 0) as avg_price, NVL(ps.price_volatility, 0) as price_volatility, NVL(ss.avg_freight, 0) as avg_freight,
        NVL(qs.defect_penalty, 0) as defect_penalty
    FROM SELLER s
    LEFT JOIN seller_stats ss ON s.SellerID = ss.SellerID
    LEFT JOIN delivery_stats ds ON s.SellerID = ds.SellerID
    LEFT JOIN quality_stats qs ON s.SellerID = qs.SellerID
    LEFT JOIN price_stats ps ON s.SellerID = ps.SellerID
    WHERE s.SellerID = :1
    """
    df = pd.read_sql(feature_query, conn, params=[seller_id, seller_id, seller_id, seller_id, seller_id])
    df.columns = [c.lower() for c in df.columns]
    
    # Predict Live!
    feature_cols = [
        'total_orders', 'total_items', 'unique_products',
        'avg_delay_days', 'max_delay_days', 'pct_late_deliveries',
        'avg_review_score', 'rejection_rate', 'negative_review_count',
        'avg_price', 'price_volatility', 'avg_freight'
    ]
    
    # Fill NAs to 0 just in case
    df[feature_cols] = df[feature_cols].fillna(0)
    
    dmatrix = xgb.DMatrix(df[feature_cols])
    preds = booster.predict(dmatrix)
    if len(preds.shape) > 1 and preds.shape[1] > 1:
        pred_class = np.argmax(preds[0])
    else:
        pred_class = int(np.round(preds[0]))
    live_ml_category = label_classes[pred_class] if pred_class < len(label_classes) else 'Unknown'
    
    # Get NLP Defects
    cursor.execute("""
        SELECT dt.DefectCategory, COUNT(*), NVL(MAX(qi.VaderSeverityWeight), MAX(dt.SeverityWeight))
        FROM PO_LINE pl
        JOIN QUALITY_INSPECTION qi ON pl.PO_ID = qi.PO_ID
        JOIN DEFECT_TYPE dt ON qi.DefectTypeID = dt.DefectTypeID
        WHERE pl.SellerID = :1 AND qi.RejectionFlag = 'Negative'
        GROUP BY dt.DefectCategory
        ORDER BY NVL(MAX(qi.VaderSeverityWeight), MAX(dt.SeverityWeight)) DESC, COUNT(*) DESC
    """, [seller_id])
    defects = cursor.fetchall()
    
    # Fetch historical risk scores
    cursor.execute("SELECT EvaluationPeriod, WeightedRiskScore, RiskCategory, RiskTrend FROM RISK_SCORE WHERE SellerID = :1 ORDER BY EvaluationPeriod DESC", [seller_id])
    history = cursor.fetchall()
    
    # Fetch open alerts
    cursor.execute("SELECT AlertDate, AlertMessage, EscalationLevel FROM ALERT_LOG WHERE SellerID = :1 AND AlertStatus = 'Open' ORDER BY AlertDate DESC", [seller_id])
    alerts = cursor.fetchall()
    
    conn.close()
    
    return render_template('seller.html', 
                           seller_id=seller_id,
                           seller_info=seller_info,
                           live_ml_category=live_ml_category,
                           features=df.iloc[0].to_dict(),
                           defects=defects,
                           history=history,
                           alerts=alerts)

@app.route('/category/<risk_level>')
def category_list(risk_level):
    risk_level_title = risk_level.capitalize()
    if risk_level_title not in ['High', 'Medium', 'Low']:
        return redirect(url_for('index'))
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT r.SellerID, s.SellerName, r.WeightedRiskScore, r.RiskCategory, r.RiskTrend 
        FROM RISK_SCORE r JOIN SELLER s ON r.SellerID = s.SellerID
        WHERE r.EvaluationPeriod = (SELECT MAX(EvaluationPeriod) FROM RISK_SCORE)
        AND r.RiskCategory = :cat
        ORDER BY r.WeightedRiskScore DESC
    """, [risk_level_title])
    
    results = cursor.fetchall()
    conn.close()
    
    return render_template('category_list.html', risk_level=risk_level_title, results=results)

if __name__ == '__main__':
    # Run on all interfaces, port 5000
    app.run(host='0.0.0.0', port=5000, debug=True)
