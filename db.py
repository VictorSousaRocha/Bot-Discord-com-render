import os
import psycopg

def conectar():
    """
    Usa DATABASE_URL (postgres://... sslmode=require).
    Se não existir, tenta variáveis separadas: PGHOST/PGDATABASE/PGUSER/PGPASSWORD/PGPORT.
    Força SSL por padrão (recomendado em Render/Neon).
    """
    url = os.getenv("DATABASE_URL")
    if url:
        # psycopg3 aceita string de conexão; sslmode=require já vem na URL do Render
        return psycopg.connect(url)

    host = os.getenv("PGHOST", "localhost")
    db   = os.getenv("PGDATABASE", "postgres")
    user = os.getenv("PGUSER", "postgres")
    pwd  = os.getenv("PGPASSWORD", "")
    port = int(os.getenv("PGPORT", "5432"))

    return psycopg.connect(
        host=host,
        dbname=db,
        user=user,
        password=pwd,
        port=port,
        sslmode="require",
    )

