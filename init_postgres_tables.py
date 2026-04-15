import psycopg2

def init_db():
    try:
        # Connexion à PostgreSQL (utilisateur par défaut 'postgres')
        # On suppose que vous avez créé la base 'blockchain_dw' comme indiqué dans le guide
        conn = psycopg2.connect(
            dbname="blockchain_dw",
            user="postgres",
            password="password",
            host="localhost",
            port="5433"
        )
        cursor = conn.cursor()

        print("🚀 Initialisation des tables PostgreSQL...")

        # 1. Table des Temps (Dimension)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dim_time (
                time_id SERIAL PRIMARY KEY,
                full_timestamp TIMESTAMP UNIQUE,
                hour INT,
                day INT,
                month INT,
                year INT,
                day_of_week INT
            );
        """)

        # 2. Table des Blocs (Dimension)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dim_block (
                block_id INT PRIMARY KEY,
                block_time TIMESTAMP
            );
        """)

        # 3. Table des Adresses (Dimension)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dim_address (
                address_id SERIAL PRIMARY KEY,
                address TEXT UNIQUE
            );
        """)

        # 4. Table des Transactions (FAITS)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fact_transactions (
                txid TEXT PRIMARY KEY,
                block_id INT REFERENCES dim_block(block_id),
                time_id INT REFERENCES dim_time(time_id),
                num_inputs INT,
                num_outputs INT,
                total_input BIGINT,
                total_output BIGINT,
                fee BIGINT,
                vsize FLOAT,
                weight INT,
                fee_rate FLOAT,
                largest_output BIGINT,
                smallest_output BIGINT,
                input_std FLOAT,
                output_std FLOAT,
                input_output_ratio FLOAT,
                fee_to_input_ratio FLOAT,
                output_dominance FLOAT,
                tx_density INT,
                log_total_input FLOAT,
                log_fee FLOAT
            );
        """)

        conn.commit()
        print("✅ Toutes les tables ont été créées avec succès dans 'blockchain_dw'.")

    except Exception as e:
        print(f"❌ Erreur lors de l'initialisation : {e}")
    finally:
        if 'conn' in locals():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    init_db()
