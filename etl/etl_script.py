from datetime import datetime
import numpy as np
from pymongo import MongoClient
import psycopg2

# ---------------------------
# MongoDB
# ---------------------------
mongo_client = MongoClient("mongodb://localhost:27017/")
mongo_db = mongo_client["bitcoin_anomaly_db"]
raw_collection = mongo_db["transactions"]

# SPEED OPTIMIZATION: Indexing for sub-second lookups
raw_collection.create_index([("processed_in_pg", 1)])
raw_collection.create_index([("txid", 1)])

# ---------------------------
# PostgreSQL
# ---------------------------
pg_conn = psycopg2.connect(
    dbname="blockchain_dw",
    user="postgres",
    password="password",
    host="localhost",
    port="5433"
)

pg_cursor = pg_conn.cursor()

# ---------------------------
# DIM INSERT HELPERS
# ---------------------------

def get_or_create_time(block_time):

    pg_cursor.execute("""
        INSERT INTO dim_time (full_timestamp, hour, day, month, year, day_of_week)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (full_timestamp) DO NOTHING
        RETURNING time_id;
    """, (
        block_time,
        block_time.hour,
        block_time.day,
        block_time.month,
        block_time.year,
        block_time.weekday()
    ))

    result = pg_cursor.fetchone()

    if result:
        return result[0]

    # already exists → fetch id
    pg_cursor.execute(
        "SELECT time_id FROM dim_time WHERE full_timestamp = %s",
        (block_time,)
    )
    return pg_cursor.fetchone()[0]


def get_or_create_block(block_height, block_time):

    pg_cursor.execute("""
        INSERT INTO dim_block (block_id, block_time)
        VALUES (%s, %s)
        ON CONFLICT (block_id) DO NOTHING;
    """, (block_height, block_time))

    return block_height


def get_or_create_address(address):

    if not address:
        return None

    pg_cursor.execute("""
        INSERT INTO dim_address (address)
        VALUES (%s)
        ON CONFLICT (address) DO NOTHING
        RETURNING address_id;
    """, (address,))

    result = pg_cursor.fetchone()

    if result:
        return result[0]

    pg_cursor.execute(
        "SELECT address_id FROM dim_address WHERE address = %s",
        (address,)
    )
    return pg_cursor.fetchone()[0]


# ---------------------------
# TRANSFORMATION
# ---------------------------

def transform_features(tx):

    try:
        if "vin" not in tx or "vout" not in tx:
            # Handle MINIFIED mempool txs (only have txid, fee, vsize, value)
            if "fee" in tx and "vsize" in tx:
                return {
                    "txid": tx["txid"],
                    "block_height": tx["status"]["block_height"],
                    "block_time": datetime.fromtimestamp(tx["status"]["block_time"]),
                    "num_inputs": 0, "num_outputs": 0,
                    "total_input": tx.get("value", 0) + tx["fee"],
                    "total_output": tx.get("value", 0),
                    "fee": tx["fee"], "vsize": tx["vsize"], "weight": tx["vsize"] * 4,
                    "fee_rate": tx["fee"] / tx["vsize"],
                    "largest_output": tx.get("value", 0), "smallest_output": tx.get("value", 0),
                    "input_std": 0, "output_std": 0,
                    "input_output_ratio": 1.0, "fee_to_input_ratio": 0, "output_dominance": 1.0,
                    "tx_density": 0, "log_total_input": 0, "log_fee": 0
                }
            return None

        inputs = [
            vin["prevout"]["value"]
            for vin in tx["vin"]
            if not vin.get("is_coinbase", False)
        ]

        outputs = [vout["value"] for vout in tx["vout"]]

        if not inputs or not outputs:
            return None

        block_height = tx["status"]["block_height"]
        block_time = datetime.fromtimestamp(tx["status"]["block_time"])

        fee = tx["fee"]
        weight = tx["weight"]

        # Basic
        num_inputs = len(inputs)
        num_outputs = len(outputs)

        total_input = sum(inputs)
        total_output = sum(outputs)

        vsize = weight / 4 if weight > 0 else 0
        fee_rate = fee / vsize if vsize > 0 else 0

        # Stats
        largest_output = max(outputs)
        smallest_output = min(outputs)

        input_std = float(np.std(inputs))
        output_std = float(np.std(outputs))

        # Derived
        input_output_ratio = total_input / total_output if total_output > 0 else 0
        fee_to_input_ratio = fee / total_input if total_input > 0 else 0
        output_dominance = largest_output / total_output if total_output > 0 else 0

        tx_density = num_inputs + num_outputs

        log_total_input = float(np.log(total_input + 1))
        log_fee = float(np.log(fee + 1))

        return {
            "txid": tx["txid"],
            "block_height": block_height,
            "block_time": block_time,

            "num_inputs": num_inputs,
            "num_outputs": num_outputs,

            "total_input": total_input,
            "total_output": total_output,

            "fee": fee,
            "vsize": vsize,
            "weight": weight,
            "fee_rate": fee_rate,

            "largest_output": largest_output,
            "smallest_output": smallest_output,

            "input_std": input_std,
            "output_std": output_std,

            "input_output_ratio": input_output_ratio,
            "fee_to_input_ratio": fee_to_input_ratio,
            "output_dominance": output_dominance,

            "tx_density": tx_density,

            "log_total_input": log_total_input,
            "log_fee": log_fee
        }

    except Exception as e:
        print("Error:", tx.get("txid"), e)
        return None


# ---------------------------
# LOAD
# ---------------------------

def load_data():

    print("Loading ONLY NEW transactions into STAR SCHEMA (Incremental)...")

    count = 0
    # SPEED OPTIMIZATION: Only fetch unprocessed transactions
    new_txs = list(raw_collection.find({"processed_in_pg": {"$ne": True}}))
    
    if not new_txs:
        print("No new transactions to process.")
        return

    for tx in new_txs:

        data = transform_features(tx)
        if not data:
            # Mark as processed even if failed to avoid re-retrying broken data
            raw_collection.update_one({"_id": tx["_id"]}, {"$set": {"processed_in_pg": True}})
            continue

        # DIMENSIONS
        time_id = get_or_create_time(data["block_time"])
        block_id = get_or_create_block(data["block_height"], data["block_time"])

        # ADDRESSES (optional usage)
        for vin in tx.get("vin", []):
            if "prevout" in vin:
                get_or_create_address(vin["prevout"].get("scriptpubkey_address"))

        for vout in tx.get("vout", []):
            get_or_create_address(vout.get("scriptpubkey_address"))

        # FACT INSERT
        try:
            pg_cursor.execute("""
                INSERT INTO fact_transactions (
                    txid, block_id, time_id,
                    num_inputs, num_outputs,
                    total_input, total_output,
                    fee, vsize, weight, fee_rate,
                    largest_output, smallest_output,
                    input_std, output_std,
                    input_output_ratio, fee_to_input_ratio, output_dominance,
                    tx_density,
                    log_total_input, log_fee
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (txid) DO NOTHING;
            """, (
                data["txid"],
                block_id,
                time_id,

                data["num_inputs"],
                data["num_outputs"],

                data["total_input"],
                data["total_output"],

                data["fee"],
                data["vsize"],
                data["weight"],
                data["fee_rate"],

                data["largest_output"],
                data["smallest_output"],

                data["input_std"],
                data["output_std"],

                data["input_output_ratio"],
                data["fee_to_input_ratio"],
                data["output_dominance"],

                data["tx_density"],

                data["log_total_input"],
                data["log_fee"]
            ))

            # Mark as processed in MongoDB
            raw_collection.update_one({"_id": tx["_id"]}, {"$set": {"processed_in_pg": True}})
            count += 1

        except Exception as e:
            print("Insert error:", e)

    pg_conn.commit()
    print(f"--- SUCCESS: Processed {count} transactions. ---")


if __name__ == "__main__":
    load_data()

    pg_cursor.close()
    pg_conn.close()