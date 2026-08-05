import oracledb
import os
import pandas as pd
from dotenv import load_dotenv

def get_connection():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    load_dotenv(env_path)
    return oracledb.connect(
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        dsn=os.getenv('DB_DSN')
    )

def main():
    print("Connecting to database...")
    conn = get_connection()
    
    query = """
        SELECT InspectionID, NVL(ReviewCommentTitle, '') || ' ' || NVL(ReviewCommentMessage, '') as ReviewText
        FROM QUALITY_INSPECTION
        WHERE RejectionFlag = 'Negative'
        AND (ReviewCommentTitle IS NOT NULL OR ReviewCommentMessage IS NOT NULL)
        AND DefectTypeID IS NULL
    """
    
    print("Extracting unclassified negative reviews...")
    # oracledb + pandas: handling CLOBs properly
    cursor = conn.cursor()
    cursor.execute(query)
    
    rows = []
    for row in cursor.fetchall():
        insp_id = row[0]
        text = row[1]
        if hasattr(text, 'read'):
            text = text.read()
        rows.append({'InspectionID': insp_id, 'ReviewText': text.strip()})
        
    df = pd.DataFrame(rows)
    conn.close()
    
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reviews_to_classify.csv')
    df.to_csv(out_path, index=False)
    
    print(f"Exported {len(df)} reviews to {out_path}")
    print("Upload this file to Google Colab for NLP classification!")

if __name__ == '__main__':
    main()
