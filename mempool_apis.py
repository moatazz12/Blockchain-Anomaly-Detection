import requests
import json
import time


API_URL = "https://mempool.space/api"

def get_last_height():
    url = f"{API_URL}/blocks/tip/height"
    return requests.get(url).json()

def get_block_hash(height):
    url = f"{API_URL}/block-height/{height}"
    return requests.get(url).text

def get_block_details(block_hash):
    url = f"{API_URL}/block/{block_hash}"
    return requests.get(url).json()

def get_block_transactions(block_hash):
    all_txs = []
    start_index = 0


    while True:
        url = f"{API_URL}/block/{block_hash}/txs/{start_index}"
        response = requests.get(url)

        if response.status_code != 200:
            break

        txs = response.json()

        if not txs:
            break

        all_txs.extend(txs)
        start_index += 25

        time.sleep(0.2)
    
    return all_txs

def get_mempool_recent():
    """Fetches the latest ~100 transactions from the mempool (single call)"""
    url = f"{API_URL}/mempool/recent"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    return []