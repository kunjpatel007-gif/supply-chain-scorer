import os
import sys

import oracledb
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from project_env import load_env

load_env()


def get_connection():
    return oracledb.connect(
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        dsn=os.getenv('DB_DSN'),
    )


def main():
    try:
        conn = get_connection()
        print("Connected to Oracle Database.")
    except Exception as e:
        print(f"Failed to connect to DB: {e}")
        return

    # Feature SQL aligned with pipeline/risk_scoring_pipeline.py and desktop_app/app.py
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
        s.SellerID as seller_id,
        NVL(ss.total_orders, 0) as total_orders,
        NVL(ss.total_items, 0) as total_items,
        NVL(ss.unique_products, 0) as unique_products,
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
        s.SellerState as seller_state
    FROM SELLER s
    LEFT JOIN seller_stats ss ON s.SellerID = ss.SellerID
    LEFT JOIN delivery_stats ds ON s.SellerID = ds.SellerID
    LEFT JOIN quality_stats qs ON s.SellerID = qs.SellerID
    LEFT JOIN price_stats ps ON s.SellerID = ps.SellerID
    """

    print("Executing query to extract seller features...")
    df = pd.read_sql(query, con=conn)
    conn.close()

    output_path = os.path.join(PROJECT_ROOT, 'etl', 'seller_features.csv')
    df.to_csv(output_path, index=False)

    print("\nExtraction Summary:")
    print(f"Number of sellers: {len(df)}")
    print(f"Columns: {', '.join(df.columns)}")
    print("\nFirst 5 rows:")
    print(df.head())
    print(f"\nSaved to {output_path}")


if __name__ == '__main__':
    main()
