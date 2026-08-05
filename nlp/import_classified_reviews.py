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
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'classified_reviews.csv')
    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} not found.")
        return
        
    print(f"Loading {csv_path}...")
    df = pd.read_csv(csv_path)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Get existing categories
    cursor.execute("SELECT DefectCategory, DefectTypeID FROM DEFECT_TYPE")
    existing_cats = {row[0].lower(): row[1] for row in cursor.fetchall()}
    
    # Track new categories to insert
    new_cats_to_insert = {}
    
    # 2. Identify new categories and their severities
    for _, row in df.iterrows():
        cat = str(row['DefectCategory']).strip()
        cat_lower = cat.lower()
        if cat_lower not in existing_cats and cat_lower not in [c.lower() for c in new_cats_to_insert.keys()]:
            # Use the AI's severity weight, clamped between 1.0 and 5.0
            try:
                sev = float(row['SeverityWeight'])
                sev = max(1.0, min(5.0, sev))
            except:
                sev = 2.5
            new_cats_to_insert[cat] = sev
            
    # 3. Insert new categories
    if new_cats_to_insert:
        print(f"Found {len(new_cats_to_insert)} dynamic categories to add!")
        cursor.execute("SELECT NVL(MAX(DefectTypeID), 0) FROM DEFECT_TYPE")
        next_id = cursor.fetchone()[0] + 1
        
        insert_data = []
        for cat, sev in new_cats_to_insert.items():
            insert_data.append((next_id, cat[:50], sev)) # limit to 50 chars for schema safety
            existing_cats[cat.lower()] = next_id
            next_id += 1
            
        cursor.executemany("INSERT INTO DEFECT_TYPE (DefectTypeID, DefectCategory, SeverityWeight) VALUES (:1, :2, :3)", insert_data)
        conn.commit()
        print(f"Inserted {len(insert_data)} new categories into DEFECT_TYPE.")
        
    # 4. Prepare updates for QUALITY_INSPECTION
    print("Preparing QUALITY_INSPECTION updates...")
    updates = []
    for _, row in df.iterrows():
        cat_lower = str(row['DefectCategory']).strip().lower()
        type_id = existing_cats.get(cat_lower)
        if type_id:
            updates.append((type_id, row['InspectionID']))
            
    # 5. Batch update
    print(f"Updating {len(updates)} reviews in database...")
    batch_size = 5000
    for i in range(0, len(updates), batch_size):
        batch = updates[i:i+batch_size]
        cursor.executemany("UPDATE QUALITY_INSPECTION SET DefectTypeID = :1 WHERE InspectionID = :2", batch)
        conn.commit()
        print(f"  - Updated batch {i//batch_size + 1}")
        
    print("Done! Database updated with dynamic categories and severities.")
    conn.close()

if __name__ == '__main__':
    main()
