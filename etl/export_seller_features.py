import pandas as pd
import oracledb
import os
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

def get_connection():
    return oracledb.connect(
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        dsn=os.getenv('DB_DSN')
    )

def main():
    try:
        conn = get_connection()
        print("Connected to Oracle Database.")
    except Exception as e:
        print(f"Failed to connect to DB: {e}")
        return

    query = """
    SELECT 
        s.SellerID as seller_id,
        COUNT(DISTINCT po.PO_ID) as total_orders,
        COUNT(pl.LineNo) as total_items,
        COUNT(DISTINCT pl.ProductID) as unique_products,
        AVG(d.DelayDays) as avg_delay_days,
        MAX(d.DelayDays) as max_delay_days,
        SUM(CASE WHEN d.DelayDays > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(d.DeliveryID), 0) * 100 as pct_late_deliveries,
        AVG(qi.ReviewScore) as avg_review_score,
        SUM(CASE WHEN qi.RejectionFlag = 'Negative' THEN 1 ELSE 0 END) / NULLIF(COUNT(qi.InspectionID), 0) * 100 as rejection_rate,
        SUM(CASE WHEN qi.RejectionFlag = 'Negative' THEN 1 ELSE 0 END) as negative_review_count,
        AVG(pl.UnitPriceAtOrder) as avg_price,
        STDDEV(pl.UnitPriceAtOrder) as price_volatility,
        AVG(pl.FreightValue) as avg_freight,
        MAX(s.SellerState) as seller_state
    FROM SELLER s
    JOIN PO_LINE pl ON s.SellerID = pl.SellerID
    JOIN PURCHASE_ORDER po ON pl.PO_ID = po.PO_ID
    LEFT JOIN DELIVERY d ON po.PO_ID = d.PO_ID
    LEFT JOIN QUALITY_INSPECTION qi ON po.PO_ID = qi.PO_ID
    GROUP BY s.SellerID
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
