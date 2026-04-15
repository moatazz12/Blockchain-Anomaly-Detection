"""
ml_api.py — FastAPI Inference Service for Bitcoin Transaction Anomaly Detection
================================================================================
Expose un endpoint /predict qui utilise un modèle IsolationForest
entraîné sur les données PostgreSQL pour classifier les nouvelles transactions.

Lancement : uvicorn ml_api:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import joblib
import numpy as np
import pandas as pd
import psycopg2

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────
MODEL_PATH  = os.path.join(os.path.dirname(__file__), "machine_learning", "isolation_forest.pkl")
SCALER_PATH = os.path.join(os.path.dirname(__file__), "machine_learning", "scaler.pkl")

DB_CONFIG = dict(
    dbname="blockchain_dw",
    user="postgres",
    password="password",
    host="localhost",
    port="5433"
)

# Features utilisées (identiques au notebook 02_model_training.ipynb)
FEATURES = ["log_total_input", "fee_rate", "hour"]

# ──────────────────────────────────────────────────────────────────────────────
# App FastAPI
# ──────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Blockchain Anomaly Detection API",
    description="API ML temps réel — Isolation Forest sur transactions Bitcoin",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────────────────────────────
# Schémas Pydantic
# ──────────────────────────────────────────────────────────────────────────────
class Transaction(BaseModel):
    log_total_input: float
    fee_rate:        float
    hour:            int  # 0-23

class PredictRequest(BaseModel):
    transactions: List[Transaction]

class PredictResult(BaseModel):
    index:        int
    is_anomaly:   bool
    score:        float   # anomaly score brut (plus négatif = plus suspect)
    label:        str     # "Anomalie" | "Normal"

class PredictResponse(BaseModel):
    model_version:  str
    n_transactions: int
    n_anomalies:    int
    results:        List[PredictResult]

class TrainResponse(BaseModel):
    status:          str
    n_samples_train: int
    n_anomalies_detected: int
    contamination:   float
    timestamp:       str

class HealthResponse(BaseModel):
    status:        str
    model_loaded:  bool
    model_version: str

# ──────────────────────────────────────────────────────────────────────────────
# État global — modèle en mémoire
# ──────────────────────────────────────────────────────────────────────────────
_model_state = {
    "model":   None,
    "scaler":  None,
    "version": "untrained"
}


def _get_pg_connection():
    return psycopg2.connect(**DB_CONFIG)


def _load_training_data() -> pd.DataFrame:
    """Charge les données de fact_transactions + dim_time depuis PostgreSQL."""
    sql = """
        SELECT
            f.total_input,
            f.fee_rate,
            t.full_timestamp
        FROM fact_transactions f
        JOIN dim_time t ON f.time_id = t.time_id
        ORDER BY t.full_timestamp DESC
        LIMIT 50000
    """
    conn = _get_pg_connection()
    df = pd.read_sql(sql, conn)
    conn.close()
    return df


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Applique le même feature-engineering que le notebook 01."""
    df = df.copy()
    df["full_timestamp"] = pd.to_datetime(df["full_timestamp"])
    df["hour"] = df["full_timestamp"].dt.hour
    df["log_total_input"] = np.log1p(df["total_input"].clip(lower=0))
    df["fee_rate"] = df["fee_rate"].fillna(0)
    return df[FEATURES].fillna(0)


def _train_and_save():
    """Entraîne l'IsolationForest et persiste modèle + scaler."""
    df_raw = _load_training_data()
    if df_raw.empty:
        raise RuntimeError("Aucune donnée disponible dans PostgreSQL pour l'entraînement.")

    X = _build_features(df_raw)

    X_train, _ = train_test_split(X, test_size=0.2, random_state=42)

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    model = IsolationForest(contamination=0.01, random_state=42, n_estimators=200)
    model.fit(X_train_scaled)

    # Statistiques rapides
    y_pred = model.predict(scaler.transform(X))
    n_anom = int((y_pred == -1).sum())

    # Sauvegarde
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    joblib.dump(scaler, SCALER_PATH)

    return len(X_train), n_anom


def _ensure_model():
    """Charge le modèle depuis le disque s'il n'est pas en mémoire."""
    if _model_state["model"] is not None:
        return

    if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
        _model_state["model"]   = joblib.load(MODEL_PATH)
        _model_state["scaler"]  = joblib.load(SCALER_PATH)
        mtime = os.path.getmtime(MODEL_PATH)
        _model_state["version"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    else:
        raise HTTPException(
            status_code=503,
            detail="Modèle non encore entraîné. Appelez d'abord POST /train."
        )


# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["Système"])
def health():
    """Vérifie l'état de l'API et si un modèle est chargé."""
    loaded = os.path.exists(MODEL_PATH)
    ver    = "non chargé"
    if loaded:
        mtime = os.path.getmtime(MODEL_PATH)
        ver   = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    return HealthResponse(
        status="ok",
        model_loaded=loaded,
        model_version=ver
    )


@app.post("/train", response_model=TrainResponse, tags=["Entraînement"])
def train():
    """
    Entraîne l'IsolationForest sur les dernières données PostgreSQL
    et sauvegarde le modèle sur le disque.
    """
    try:
        n_train, n_anom = _train_and_save()
        # Recharge en mémoire
        _model_state["model"]   = joblib.load(MODEL_PATH)
        _model_state["scaler"]  = joblib.load(SCALER_PATH)
        _model_state["version"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        return TrainResponse(
            status="Modèle entraîné et sauvegardé avec succès",
            n_samples_train=n_train,
            n_anomalies_detected=n_anom,
            contamination=0.01,
            timestamp=_model_state["version"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/predict", response_model=PredictResponse, tags=["Inférence"])
def predict(request: PredictRequest):
    """
    Classifie une liste de transactions comme normales ou anomalies.

    Chaque transaction doit fournir :
    - `log_total_input` : log(1 + total_input) en satoshis
    - `fee_rate`        : frais en sat/vByte
    - `hour`            : heure de la transaction (0-23)
    """
    _ensure_model()

    model  = _model_state["model"]
    scaler = _model_state["scaler"]

    rows = [
        [t.log_total_input, t.fee_rate, t.hour]
        for t in request.transactions
    ]
    X = np.array(rows, dtype=float)
    X_scaled = scaler.transform(X)

    preds  = model.predict(X_scaled)           # 1 = normal, -1 = anomalie
    scores = model.score_samples(X_scaled)     # plus négatif = plus suspect

    results = []
    for i, (pred, score) in enumerate(zip(preds, scores)):
        is_anom = bool(pred == -1)
        results.append(PredictResult(
            index=i,
            is_anomaly=is_anom,
            score=round(float(score), 6),
            label="Anomalie" if is_anom else "Normal"
        ))

    n_anom = sum(1 for r in results if r.is_anomaly)

    return PredictResponse(
        model_version=_model_state["version"],
        n_transactions=len(results),
        n_anomalies=n_anom,
        results=results
    )


@app.get("/predict/batch_from_db", tags=["Inférence"])
def predict_batch_from_db(limit: int = 1000):
    """
    Récupère les `limit` dernières transactions de PostgreSQL,
    les classe et retourne les résultats enrichis.
    """
    _ensure_model()

    model  = _model_state["model"]
    scaler = _model_state["scaler"]

    try:
        conn = _get_pg_connection()
        sql = f"""
            SELECT
                f.txid,
                f.total_input,
                f.fee_rate,
                f.fee,
                f.vsize,
                f.num_inputs,
                f.num_outputs,
                f.output_dominance,
                f.tx_density,
                t.full_timestamp
            FROM fact_transactions f
            JOIN dim_time t ON f.time_id = t.time_id
            ORDER BY t.full_timestamp DESC
            LIMIT {int(limit)}
        """
        df = pd.read_sql(sql, conn)
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur PostgreSQL: {str(e)}")

    if df.empty:
        return {"n_transactions": 0, "n_anomalies": 0, "results": []}

    X = _build_features(df)
    X_scaled = scaler.transform(X)

    df["if_pred"]      = model.predict(X_scaled)
    df["if_score"]     = model.score_samples(X_scaled)
    df["is_anomaly"]   = df["if_pred"] == -1
    df["anomaly_label"]= df["is_anomaly"].map({True: "Anomalie", False: "Normal"})

    # Statistiques
    n_anom = int(df["is_anomaly"].sum())
    anom_pct = round(n_anom / len(df) * 100, 2)

    return {
        "model_version":  _model_state["version"],
        "n_transactions": len(df),
        "n_anomalies":    n_anom,
        "anomaly_rate_pct": anom_pct,
        "results": df[[
            "txid", "fee_rate", "total_input", "full_timestamp",
            "is_anomaly", "anomaly_label", "if_score"
        ]].to_dict(orient="records")
    }


@app.get("/model/info", tags=["Modèle"])
def model_info():
    """Retourne les métadonnées du modèle chargé."""
    _ensure_model()
    model = _model_state["model"]
    return {
        "version":       _model_state["version"],
        "type":          "IsolationForest",
        "n_estimators":  model.n_estimators,
        "contamination": model.contamination,
        "features":      FEATURES,
        "model_path":    MODEL_PATH
    }
