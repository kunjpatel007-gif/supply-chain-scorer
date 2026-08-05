import oracledb
import os
import argparse
import requests
import json
import time
import csv
import logging
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)

OLLAMA_HOST = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
OLLAMA_API_URL = f"{OLLAMA_HOST}/api/generate"
MODEL_NAME = "qwen2.5:3b-instruct-q4_K_M"
CHECKPOINT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'classification_progress.csv')

# Defect Types Mapping
DEFECT_TYPES = {
    'Late Delivery': 1,
    'Damaged Goods': 2,
    'Wrong Item': 3,
    'Poor Quality': 4,
    'Other': 5
}

PROMPT_TEMPLATE = """You are a supply chain quality analyst. Classify the following customer review into exactly ONE of these categories:
- Late Delivery
- Damaged Goods  
- Wrong Item
- Poor Quality
- Other

Examples:
Review: "Produto chegou quebrado" → Damaged Goods
Review: "Entrega demorou muito além do prazo" → Late Delivery
Review: "Veio o produto errado" → Wrong Item
Review: "Produto de má qualidade, não como descrito" → Poor Quality
Review: "Não tenho nada a reclamar especificamente" → Other

Respond with ONLY the category name. No explanation, no punctuation, no extra text.

Review: "{review_text}"
"""

def get_connection():
    return oracledb.connect(
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        dsn=os.getenv('DB_DSN')
    )

def load_checkpoint():
    processed = set()
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                for row in reader:
                    if row and len(row) >= 1:
                        processed.add(int(row[0]))
        except Exception as e:
            logger.warning(f"Failed to read checkpoint file: {e}")
    return processed

def write_checkpoint_batch(batch):
    file_exists = os.path.exists(CHECKPOINT_FILE)
    try:
        with open(CHECKPOINT_FILE, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['InspectionID', 'DefectCategory', 'DefectTypeID'])
            for item in batch:
                writer.writerow([item['InspectionID'], item['DefectCategory'], item['DefectTypeID']])
    except Exception as e:
        logger.error(f"Failed to write checkpoint batch: {e}")

def classify_review_ollama(review_text):
    if not review_text or not review_text.strip():
        return 'Other'
        
    prompt = PROMPT_TEMPLATE.replace("{review_text}", review_text)
    
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False
    }
    
    for attempt in range(3):
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=30)
            response.raise_for_status()
            result_json = response.json()
            output = result_json.get("response", "").strip()
            
            # Match category
            output_lower = output.lower()
            for category in DEFECT_TYPES.keys():
                if category.lower() in output_lower:
                    return category
                    
            return 'Other'
            
        except Exception as e:
            if attempt < 2:
                logger.warning(f"Ollama API call failed (attempt {attempt+1}/3): {e}. Retrying in 2s...")
                time.sleep(2)
            else:
                logger.error(f"Ollama API call failed after 3 attempts: {e}")
                
    return 'Other'

def update_db_batch(conn, batch):
    try:
        cursor = conn.cursor()
        sql = "UPDATE QUALITY_INSPECTION SET DefectTypeID = :1 WHERE InspectionID = :2"
        data = [(item['DefectTypeID'], item['InspectionID']) for item in batch]
        cursor.executemany(sql, data)
        conn.commit()
        cursor.close()
    except Exception as e:
        logger.error(f"Failed to update database batch: {e}")
        conn.rollback()

def main():
    parser = argparse.ArgumentParser(description="Classify negative reviews using Ollama LLM")
    parser.add_argument('--sample', type=int, help="Limit to first N negative reviews")
    args = parser.parse_args()
    
    logger.info("Starting review classification process...")
    start_time = time.time()
    
    processed_ids = load_checkpoint()
    logger.info(f"Loaded checkpoint: {len(processed_ids)} reviews already classified.")
    
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Query for negative reviews
        sql = """
            SELECT InspectionID, PO_ID, ReviewCommentTitle, ReviewCommentMessage
            FROM QUALITY_INSPECTION
            WHERE RejectionFlag = 'Negative'
            AND (ReviewCommentMessage IS NOT NULL OR ReviewCommentTitle IS NOT NULL)
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        
        to_process = []
        for row in rows:
            inspection_id = row[0]
            if inspection_id not in processed_ids:
                to_process.append({
                    'InspectionID': row[0],
                    'PO_ID': row[1],
                    'ReviewCommentTitle': row[2],
                    'ReviewCommentMessage': row[3]
                })
                
        logger.info(f"Found {len(rows)} total negative reviews. {len(to_process)} remaining to process.")
        
        if args.sample:
            to_process = to_process[:args.sample]
            logger.info(f"Limiting to sample of {args.sample} reviews.")
            
        total_to_process = len(to_process)
        if total_to_process == 0:
            logger.info("No reviews to process. Exiting.")
            return
            
        classified_count = 0
        category_counts = {k: 0 for k in DEFECT_TYPES.keys()}
        current_batch = []
        
        for i, review in enumerate(to_process):
            title = review['ReviewCommentTitle'] or ''
            message = review['ReviewCommentMessage'] or ''
            
            if hasattr(message, 'read'): # Handle CLOB
                try:
                    message = message.read()
                except:
                    message = str(message)
                    
            combined_text = f"{title} {message}".strip()
            
            category = classify_review_ollama(combined_text)
            defect_type_id = DEFECT_TYPES.get(category, DEFECT_TYPES['Other'])
            
            category_counts[category] += 1
            classified_count += 1
            
            item = {
                'InspectionID': review['InspectionID'],
                'DefectCategory': category,
                'DefectTypeID': defect_type_id
            }
            current_batch.append(item)
            
            if len(current_batch) >= 100:
                write_checkpoint_batch(current_batch)
                update_db_batch(conn, current_batch)
                logger.info(f"Classified {classified_count}/{total_to_process} reviews...")
                current_batch = []
                
        # Process remaining
        if current_batch:
            write_checkpoint_batch(current_batch)
            update_db_batch(conn, current_batch)
            logger.info(f"Classified {classified_count}/{total_to_process} reviews...")
            
        elapsed_time = time.time() - start_time
        
        logger.info("\n--- Classification Summary ---")
        logger.info(f"Total reviews classified in this run: {classified_count}")
        for category, count in category_counts.items():
            logger.info(f"- {category}: {count}")
        logger.info(f"Elapsed time: {elapsed_time:.2f} seconds")
        
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    main()
