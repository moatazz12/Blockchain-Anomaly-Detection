import subprocess
import os
import sys

def _get_paths():
    python_exe = sys.executable
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ingestion_script = os.path.join(base_dir, "mongodb_ingestion.py")
    etl_script = os.path.join(base_dir, "etl", "etl_script.py")
    return python_exe, ingestion_script, etl_script

def run_ingestion():
    """Step 1: Extract from mempool.space and load into MongoDB."""
    python_exe, ingestion_script, _ = _get_paths()
    print("--- Running Ingestion ---")
    subprocess.run([python_exe, ingestion_script], check=True)
    print("--- Ingestion Done ---")

def run_etl():
    """Step 2: Transform MongoDB data and load into PostgreSQL."""
    python_exe, _, etl_script = _get_paths()
    print("--- Running ETL ---")
    subprocess.run([python_exe, etl_script], check=True)
    print("--- ETL Done ---")

def run_pipeline():
    """Full pipeline: Ingestion + ETL."""
    print("--- Starting Pipeline ---")
    run_ingestion()
    run_etl()
    print("--- Pipeline Completed Successfully ---")

if __name__ == "__main__":
    run_pipeline()
