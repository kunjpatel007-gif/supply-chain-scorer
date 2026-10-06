import os
filepath = r'C:\Projects\DBMS PROJECT\pipeline\risk_scoring_pipeline.py'
with open(filepath, 'r') as f:
    content = f.read()

helper = '''
    def _fetch_all_chunked(coll_name):
        print(f"Chunking {coll_name} download to prevent 503 Timeouts...")
        docs = []
        query = db.collection(coll_name).order_by('__name__').limit(15000)
        while True:
            try:
                batch = list(query.stream(timeout=120))
            except Exception as e:
                print(f"Stream exception {e}, retrying...")
                import time; time.sleep(2)
                batch = list(query.stream(timeout=300))
                
            docs.extend(batch)
            if len(batch) < 15000:
                break
            query = db.collection(coll_name).order_by('__name__').start_after(batch[-1]).limit(15000)
        print(f"Successfully downloaded {len(docs)} {coll_name} records.")
        return docs
'''

content = content.replace('    if sellers_df.empty:\n        return pd.DataFrame()\n', 
                          '    if sellers_df.empty:\n        return pd.DataFrame()\n' + helper + '\n')

content = content.replace('po_lines_docs = list(po_lines_ref.stream(timeout=3600))', 'po_lines_docs = _fetch_all_chunked(\'po_lines\')')
content = content.replace('del_docs = list(db.collection(\'deliveries\').stream(timeout=3600))', 'del_docs = _fetch_all_chunked(\'deliveries\')')
content = content.replace('qi_docs = list(db.collection(\'quality_inspections\').stream(timeout=3600))', 'qi_docs = _fetch_all_chunked(\'quality_inspections\')')
content = content.replace('ph_docs = list(ph_ref.stream(timeout=3600))', 'ph_docs = _fetch_all_chunked(\'price_history\')')

with open(filepath, 'w') as f:
    f.write(content)
