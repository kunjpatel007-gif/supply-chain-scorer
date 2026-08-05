import os
import oracledb
from dotenv import load_dotenv

load_dotenv('.env')

conn = oracledb.connect(
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    dsn=os.getenv('DB_DSN')
)
cursor = conn.cursor()

# Rename all legacy categories that were NOT processed by the VADER AI
# (because they had no text) into a single clean category.
cursor.execute("""
    UPDATE DEFECT_TYPE 
    SET DefectCategory = 'Unwritten Negative Review', SeverityWeight = 3.0
    WHERE DefectTypeID NOT IN (10, 11, 12, 13, 14, 15, 16)
""")

print(f"Consolidated {cursor.rowcount} legacy categories.")
conn.commit()
conn.close()
