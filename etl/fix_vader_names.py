import os
import oracledb
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from project_env import load_env

load_env()

conn = oracledb.connect(
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    dsn=os.getenv('DB_DSN')
)
cursor = conn.cursor()

taxonomy = {
    10: ('Not a Defect', 0.0),
    11: ('Service Defect', 2.0),
    12: ('Quality Issue', 2.5),
    13: ('Logistics Issue', 3.0),
    14: ('Fulfillment Error', 3.5),
    15: ('Major Product Issue', 4.0),
    16: ('Critical Failure', 5.0)
}

for tid, (cat, sev) in taxonomy.items():
    cursor.execute("""
        UPDATE DEFECT_TYPE
        SET DefectCategory = :1, SeverityWeight = :2
        WHERE DefectTypeID = :3
    """, [cat, sev, tid])

conn.commit()
conn.close()
print("Fixed taxonomy names!")
