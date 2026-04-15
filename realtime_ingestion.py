"""
realtime_ingestion.py — Ingestion en Temps Réel des Transactions Bitcoin
=========================================================================
Ce script tourne en boucle infinie et :
  1. Récupère les dernières transactions du Mempool (mempool.space API)
  2. Les stocke dans MongoDB (bitcoin_anomaly_db.transactions)
  3. Déclenche l'ETL pour les transférer dans PostgreSQL
  4. Attend un intervalle configurable avant de recommencer

Lancement :
    python realtime_ingestion.py
    python realtime_ingestion.py --interval 60   # toutes les 60 secondes
    python realtime_ingestion.py --interval 30 --mode blocks  # par blocs
"""

import time
import argparse
import signal
import sys
import os
from datetime import datetime

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

# ── Modules locaux ─────────────────────────────────────────────────────────────
import mempool_apis
import orchestrator

# ── Configuration ──────────────────────────────────────────────────────────────
MONGO_URI   = "mongodb://localhost:27017/"
DB_NAME     = "bitcoin_anomaly_db"
COLLECTION  = "transactions"

DEFAULT_INTERVAL = 60   # secondes entre chaque cycle
DEFAULT_MODE     = "mempool"   # "mempool" ou "blocks"

# ── Statistiques globales ───────────────────────────────────────────────────────
stats = {
    "cycles":       0,
    "total_new":    0,
    "total_skipped": 0,
    "errors":       0,
    "start_time":   datetime.now(),
}

# ── Arrêt propre sur Ctrl+C ─────────────────────────────────────────────────────
_running = True

def _handle_sigint(sig, frame):
    global _running
    elapsed = (datetime.now() - stats["start_time"]).seconds
    print("\n")
    print("=" * 60)
    print("  🛑  Arrêt demandé par l'utilisateur (Ctrl+C)")
    print("=" * 60)
    print(f"  ⏱  Durée totale   : {elapsed // 60}m {elapsed % 60}s")
    print(f"  🔄  Cycles réalisés: {stats['cycles']}")
    print(f"  ✅  Nouvelles tx   : {stats['total_new']}")
    print(f"  ⏭  Doublons ignorés: {stats['total_skipped']}")
    print(f"  ❌  Erreurs        : {stats['errors']}")
    print("=" * 60)
    _running = False
    sys.exit(0)

signal.signal(signal.SIGINT, _handle_sigint)


# ── Connexion MongoDB ────────────────────────────────────────────────────────────
def get_collection():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return client[DB_NAME][COLLECTION]


# ── Ingestion depuis le Mempool ─────────────────────────────────────────────────
def ingest_mempool(col) -> tuple[int, int]:
    """
    Récupère les ~100 dernières transactions du Mempool
    et insère les nouvelles dans MongoDB.
    Retourne (nouvelles, ignorées).
    """
    txs = mempool_apis.get_mempool_recent()
    if not txs:
        return 0, 0

    new_count = 0
    skip_count = 0

    for tx in txs:
        if col.find_one({"txid": tx["txid"]}, {"_id": 1}):
            skip_count += 1
            continue

        if "status" not in tx:
            tx["status"] = {
                "confirmed":    False,
                "block_height": 0,
                "block_time":   int(time.time()),
            }
        tx["processed_in_pg"] = False
        tx["ingested_at"]     = datetime.utcnow().isoformat()

        col.insert_one(tx)
        new_count += 1

    return new_count, skip_count


# ── Ingestion par Blocs ─────────────────────────────────────────────────────────
def ingest_latest_block(col) -> tuple[int, int]:
    """
    Récupère toutes les transactions du dernier bloc confirmé
    et insère les nouvelles dans MongoDB.
    """
    try:
        height    = mempool_apis.get_last_height()
        blk_hash  = mempool_apis.get_block_hash(height)
        txs       = mempool_apis.get_block_transactions(blk_hash)
        blk_info  = mempool_apis.get_block_details(blk_hash)
    except Exception as e:
        print(f"  ⚠️  Erreur API blocs : {e}")
        return 0, 0

    if not txs:
        return 0, 0

    new_count  = 0
    skip_count = 0

    for tx in txs:
        if col.find_one({"txid": tx["txid"]}, {"_id": 1}):
            skip_count += 1
            continue

        tx["status"] = {
            "confirmed":    True,
            "block_height": height,
            "block_time":   blk_info.get("timestamp", int(time.time())),
            "block_hash":   blk_hash,
        }
        tx["processed_in_pg"] = False
        tx["ingested_at"]     = datetime.utcnow().isoformat()

        col.insert_one(tx)
        new_count += 1

    return new_count, skip_count


# ── Affichage formaté ────────────────────────────────────────────────────────────
def _banner():
    print("=" * 60)
    print("  ₿  REALTIME INGESTION — Bitcoin Anomaly Detection")
    print("=" * 60)

def _cycle_log(cycle, mode, new, skip, etl_ok, elapsed_s):
    ts  = datetime.now().strftime("%H:%M:%S")
    etl = "✅ ETL OK" if etl_ok else "⚠️  ETL skipped"
    print(
        f"[{ts}] Cycle #{cycle:04d} | Mode:{mode.upper()} | "
        f"Nouvelles:{new:>4} | Doublons:{skip:>4} | {etl} | ⏱{elapsed_s:.1f}s"
    )


# ── Boucle Principale ────────────────────────────────────────────────────────────
def run(interval: int = DEFAULT_INTERVAL, mode: str = DEFAULT_MODE):
    global _running

    _banner()
    print(f"  ⏱  Intervalle     : {interval}s")
    print(f"  📡  Mode           : {mode}")
    print(f"  🗄️  MongoDB        : {MONGO_URI}{DB_NAME}")
    print(f"  🔄  Démarré à      : {stats['start_time'].strftime('%H:%M:%S')}")
    print("  ↩  Ctrl+C pour arrêter proprement")
    print("-" * 60)

    try:
        col = get_collection()
    except Exception as e:
        print(f"❌ Impossible de se connecter à MongoDB : {e}")
        sys.exit(1)

    while _running:
        cycle_start = time.time()
        stats["cycles"] += 1

        # ── 1. Ingestion ────────────────────────────────────────────────
        try:
            if mode == "blocks":
                new, skip = ingest_latest_block(col)
            else:
                new, skip = ingest_mempool(col)

            stats["total_new"]     += new
            stats["total_skipped"] += skip
        except Exception as e:
            print(f"  ❌ Erreur ingestion : {e}")
            stats["errors"] += 1
            new, skip = 0, 0

        # ── 2. ETL vers PostgreSQL (uniquement si nouvelles transactions) ─
        etl_ok = False
        if new > 0:
            try:
                orchestrator.run_etl()
                etl_ok = True
            except Exception as e:
                print(f"  ⚠️  Erreur ETL : {e}")
                stats["errors"] += 1

        # ── 3. Log + attente ────────────────────────────────────────────
        elapsed = time.time() - cycle_start
        _cycle_log(stats["cycles"], mode, new, skip, etl_ok, elapsed)

        # Attendre le reste de l'intervalle (minimum 0)
        wait = max(0, interval - elapsed)
        if _running and wait > 0:
            time.sleep(wait)


# ── Point d'entrée ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingestion en temps réel des transactions Bitcoin → MongoDB → PostgreSQL"
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL,
        help=f"Intervalle en secondes entre chaque cycle (défaut: {DEFAULT_INTERVAL}s)"
    )
    parser.add_argument(
        "--mode", choices=["mempool", "blocks"], default=DEFAULT_MODE,
        help=(
            "mempool : ~100 tx récentes du mempool (rapide, léger) | "
            "blocks  : toutes les tx du dernier bloc confirmé (complet, plus lent)"
        )
    )
    args = parser.parse_args()
    run(interval=args.interval, mode=args.mode)
