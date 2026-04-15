from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import mempool_apis

client = MongoClient("mongodb://localhost:27017/")
db = client["bitcoin_anomaly_db"]
transactions_collection = db["transactions"]



def ingest_transactions():
    print("Fetching RECENT MEMPOOL transactions (Ultra-Fast Mode)...")
    
    # SINGLE CALL: Get latest 100 txs
    txs = mempool_apis.get_mempool_recent()
    
    if not txs:
        print("No recent transactions found.")
        return

    count = 0
    for tx in txs:
        # Check if already exists by txid to avoid duplicates
        if transactions_collection.find_one({"txid": tx["txid"]}):
            continue

        # In mempool, we don't have block status yet
        # We manually add a 'fake' pending status so ETL doesn't crash
        if "status" not in tx:
            tx["status"] = {"confirmed": False, "block_height": 0, "block_time": int(time.time())}
        
        tx["processed_in_pg"] = False
        transactions_collection.insert_one(tx)
        count += 1
        
    print(f"Ingestion completed. Added {count} new transactions.")

if __name__ == "__main__":
    import time
    ingest_transactions()


