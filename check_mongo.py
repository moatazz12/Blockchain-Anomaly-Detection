from pymongo import MongoClient
import os

client = MongoClient("mongodb://localhost:27017/")
db = client["bitcoin_anomaly_db"]
col = db["transactions"]

total = col.count_documents({})
unprocessed = col.count_documents({"processed_in_pg": {"$ne": True}})

print(f"TOTAL_TX: {total}")
print(f"UNPROCESSED_TX: {unprocessed}")
