import pandas as pd
import numpy as np
import oracledb
import os
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = PROJECT_ROOT
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
        # Ensure we set cursor.executemany batch size
        cursor = conn.cursor()
        print("Connected to Oracle Database.")
    except Exception as e:
        print(f"Failed to connect to DB: {e}")
        return

    # Load CSVs
    print("Loading CSV files...")
    sellers_df = pd.read_csv(os.path.join(DATA_DIR, 'olist_sellers_dataset.csv'))
    products_df = pd.read_csv(os.path.join(DATA_DIR, 'olist_products_dataset.csv'))
    orders_df = pd.read_csv(os.path.join(DATA_DIR, 'olist_orders_dataset.csv'))
    order_items_df = pd.read_csv(os.path.join(DATA_DIR, 'olist_order_items_dataset.csv'))
    reviews_df = pd.read_csv(os.path.join(DATA_DIR, 'olist_order_reviews_dataset.csv'))
    
    # Process Data
    # 1. SELLER
    seller_data = sellers_df[['seller_id', 'seller_zip_code_prefix', 'seller_city', 'seller_state']].copy()
    seller_data = seller_data.astype(object).where(pd.notnull(seller_data), None)
    seller_records = [tuple(x) for x in seller_data.to_numpy()]

    # 2. PRODUCT
    product_data = products_df[['product_id', 'product_category_name']].copy()
    product_data = product_data.astype(object).where(pd.notnull(product_data), None)
    product_records = [tuple(x) for x in product_data.to_numpy()]
    
    # 3. PURCHASE_ORDER
    orders_df['order_purchase_timestamp'] = pd.to_datetime(orders_df['order_purchase_timestamp'])
    orders_df['order_estimated_delivery_date'] = pd.to_datetime(orders_df['order_estimated_delivery_date'])
    orders_df['order_delivered_carrier_date'] = pd.to_datetime(orders_df['order_delivered_carrier_date'])
    orders_df['order_delivered_customer_date'] = pd.to_datetime(orders_df['order_delivered_customer_date'])

    po_data = orders_df[['order_id', 'order_purchase_timestamp', 'order_estimated_delivery_date', 'order_status']].copy()
    po_data = po_data.astype(object).where(pd.notnull(po_data), None)
    po_records = [tuple(x) for x in po_data.to_numpy()]
    
    # 4. PO_LINE
    order_items_df['shipping_limit_date'] = pd.to_datetime(order_items_df['shipping_limit_date'])
    po_line_data = order_items_df[['order_id', 'order_item_id', 'product_id', 'seller_id', 'price', 'freight_value', 'shipping_limit_date']].copy()
    po_line_data = po_line_data.astype(object).where(pd.notnull(po_line_data), None)
    po_line_records = [tuple(x) for x in po_line_data.to_numpy()]

    # 5. DELIVERY
    merged_orders = pd.merge(orders_df, order_items_df, on='order_id', how='inner')
    min_shipping_limit = merged_orders.groupby('order_id')['shipping_limit_date'].min().reset_index()
    delivery_data = pd.merge(orders_df, min_shipping_limit, on='order_id', how='inner')
    
    # Filter delivered orders
    delivery_data = delivery_data[delivery_data['order_delivered_customer_date'].notnull()].copy()
    
    delivery_data['SellerDispatchDelayDays'] = (delivery_data['order_delivered_carrier_date'] - delivery_data['shipping_limit_date']).dt.days
    delivery_data['CarrierTransitDelayDays'] = (delivery_data['order_delivered_customer_date'] - delivery_data['order_delivered_carrier_date']).dt.days
    delivery_data['DelayDays'] = (delivery_data['order_delivered_customer_date'] - delivery_data['order_estimated_delivery_date']).dt.days
    
    delivery_data.reset_index(drop=True, inplace=True)
    delivery_data['DeliveryID'] = delivery_data.index + 1
    
    del_insert_data = delivery_data[['DeliveryID', 'order_id', 'order_delivered_carrier_date', 'order_delivered_customer_date',
                                      'SellerDispatchDelayDays', 'CarrierTransitDelayDays', 'DelayDays']].copy()
    del_insert_data = del_insert_data.astype(object).where(pd.notnull(del_insert_data), None)
    delivery_records = [tuple(x) for x in del_insert_data.to_numpy()]

    # 6. QUALITY_INSPECTION
    reviews_df['review_creation_date'] = pd.to_datetime(reviews_df['review_creation_date'])
    reviews_df['review_answer_timestamp'] = pd.to_datetime(reviews_df['review_answer_timestamp'])
    
    qi_data = reviews_df[['order_id', 'review_creation_date', 'review_score', 'review_answer_timestamp', 'review_comment_title', 'review_comment_message']].copy()
    qi_data['RejectionFlag'] = np.where(qi_data['review_score'] <= 3, 'Negative', 'Positive')
    qi_data['DefectTypeID'] = None
    
    qi_data.reset_index(drop=True, inplace=True)
    qi_data['InspectionID'] = qi_data.index + 1
    
    qi_insert_data = qi_data[['InspectionID', 'order_id', 'review_creation_date', 'review_score', 'RejectionFlag', 
                              'review_answer_timestamp', 'review_comment_title', 'review_comment_message', 'DefectTypeID']].copy()
    qi_insert_data = qi_insert_data.astype(object).where(pd.notnull(qi_insert_data), None)
    qi_records = [tuple(x) for x in qi_insert_data.to_numpy()]

    # 7. PRICE_HISTORY
    price_hist = merged_orders[['seller_id', 'product_id', 'order_purchase_timestamp', 'price']].copy()
    price_hist.sort_values(by=['seller_id', 'product_id', 'order_purchase_timestamp'], inplace=True)
    price_hist['PriceChangePercent'] = price_hist.groupby(['seller_id', 'product_id'])['price'].pct_change() * 100
    
    price_hist.reset_index(drop=True, inplace=True)
    price_hist['PriceRecordID'] = price_hist.index + 1
    
    ph_insert_data = price_hist[['PriceRecordID', 'seller_id', 'product_id', 'order_purchase_timestamp', 'price', 'PriceChangePercent']].copy()
    ph_insert_data = ph_insert_data.astype(object).where(pd.notnull(ph_insert_data), None)
    ph_records = [tuple(x) for x in ph_insert_data.to_numpy()]

    # Load to DB
    def insert_records(table_name, sql, records, batch_size=5000):
        print(f"Loading {table_name}...")
        try:
            # use batch inserts
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                cursor.executemany(sql, batch)
            print(f"Loading {table_name}... done ({len(records)} rows)")
        except Exception as e:
            print(f"Error loading {table_name}: {e}")
            raise
    
    try:
        # DB Insert commands
        insert_records('SELLER', 'INSERT INTO SELLER (SellerID, SellerZipCodePrefix, SellerCity, SellerState) VALUES (:1, :2, :3, :4)', seller_records)
        insert_records('PRODUCT', 'INSERT INTO PRODUCT (ProductID, ProductCategoryName) VALUES (:1, :2)', product_records)
        insert_records('PURCHASE_ORDER', 'INSERT INTO PURCHASE_ORDER (PO_ID, OrderDate, ExpectedDeliveryDate, OrderStatus) VALUES (:1, :2, :3, :4)', po_records)
        insert_records('PO_LINE', 'INSERT INTO PO_LINE (PO_ID, LineNo, ProductID, SellerID, UnitPriceAtOrder, FreightValue, ShippingLimitDate) VALUES (:1, :2, :3, :4, :5, :6, :7)', po_line_records)
        insert_records('DELIVERY', 'INSERT INTO DELIVERY (DeliveryID, PO_ID, DeliveredCarrierDate, ActualDeliveryDate, SellerDispatchDelayDays, CarrierTransitDelayDays, DelayDays) VALUES (:1, :2, :3, :4, :5, :6, :7)', delivery_records)
        insert_records('QUALITY_INSPECTION', 'INSERT INTO QUALITY_INSPECTION (InspectionID, PO_ID, InspectionDate, ReviewScore, RejectionFlag, ReviewAnswerTimestamp, ReviewCommentTitle, ReviewCommentMessage, DefectTypeID) VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9)', qi_records)
        insert_records('PRICE_HISTORY', 'INSERT INTO PRICE_HISTORY (PriceRecordID, SellerID, ProductID, PriceDate, UnitPrice, PriceChangePercent) VALUES (:1, :2, :3, :4, :5, :6)', ph_records)

        conn.commit()
        print("\nFinal Summary:")
        print(f"SELLER: {len(seller_records)}")
        print(f"PRODUCT: {len(product_records)}")
        print(f"PURCHASE_ORDER: {len(po_records)}")
        print(f"PO_LINE: {len(po_line_records)}")
        print(f"DELIVERY: {len(delivery_records)}")
        print(f"QUALITY_INSPECTION: {len(qi_records)}")
        print(f"PRICE_HISTORY: {len(ph_records)}")
        print("All data loaded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Transaction rolled back due to error: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    main()
