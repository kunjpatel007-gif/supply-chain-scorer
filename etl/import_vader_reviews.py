import os
import pandas as pd
import oracledb
from dotenv import load_dotenv

# Load Environment
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

def get_connection():
    return oracledb.connect(
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        dsn=os.getenv('DB_DSN')
    )

def main():
    csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'nlp', 'vader_classified_reviews.csv')
    if not os.path.exists(csv_path):
        print(f"ERROR: Could not find {csv_path}")
        return

    print(f"Reading {csv_path}...")
    df = pd.read_csv(csv_path)
    
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Add VaderSeverityWeight column if it doesn't exist
    try:
        cursor.execute("SELECT VaderSeverityWeight FROM QUALITY_INSPECTION FETCH FIRST 1 ROW ONLY")
        print("VaderSeverityWeight column already exists.")
    except oracledb.DatabaseError as e:
        error_obj, = e.args
        if error_obj.code == 904: # ORA-00904: invalid identifier
            print("Adding VaderSeverityWeight column to QUALITY_INSPECTION...")
            cursor.execute("ALTER TABLE QUALITY_INSPECTION ADD VaderSeverityWeight NUMBER(5,2)")
            conn.commit()
        else:
            raise

    # 2. Seed new 7-tier Taxonomy safely (to avoid FK constraint errors)
    taxonomy = {
        'Not a Defect': (10, 0.0),
        'Service Defect': (11, 2.0),
        'Quality Issue': (12, 2.5),
        'Logistics Issue': (13, 3.0),
        'Fulfillment Error': (14, 3.5),
        'Major Product Issue': (15, 4.0),
        'Critical Failure': (16, 5.0)
    }
    
    print("Seeding new taxonomy into DEFECT_TYPE...")
    for cat, (tid, sev) in taxonomy.items():
        try:
            cursor.execute("""
                INSERT INTO DEFECT_TYPE (DefectTypeID, DefectCategory, SeverityWeight) 
                VALUES (:1, :2, :3)
            """, [tid, cat, sev])
        except oracledb.IntegrityError:
            # Already exists
            pass
    conn.commit()

    # 3. Batch Update QUALITY_INSPECTION
    # The CSV has: InspectionID, DefectCategory, SeverityWeight, TranslatedText, VaderCompound
    print("Preparing batch updates...")
    update_data = []
    
    # Pre-map category names to IDs
    cat_to_id = {k: v[0] for k, v in taxonomy.items()}
    
    for _, row in df.iterrows():
        insp_id = int(row['InspectionID'])
        cat_name = str(row['DefectCategory'])
        fluid_sev = float(row['SeverityWeight'])
        eng_text = str(row['TranslatedText']) if pd.notna(row['TranslatedText']) else ""
        
        type_id = cat_to_id.get(cat_name, 10) # Default to Not a Defect if unknown
        
        # [eng_text, type_id, fluid_sev, insp_id]
        update_data.append((eng_text, type_id, fluid_sev, insp_id))

    print(f"Executing batch update for {len(update_data)} rows. This may take ~10-20 seconds...")
    
    # We use executemany for high performance bulk updates
    cursor.executemany("""
        UPDATE QUALITY_INSPECTION 
        SET ReviewCommentMessage = :1, 
            DefectTypeID = :2, 
            VaderSeverityWeight = :3 
        WHERE InspectionID = :4
    """, update_data)
    
    conn.commit()
    conn.close()
    
    print("SUCCESS: VADER analysis fully integrated into Oracle Database!")

if __name__ == "__main__":
    main()
