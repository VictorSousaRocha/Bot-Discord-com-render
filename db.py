# db.py (Postgres no Render)
import os
import psycopg2

def conectar():
    url = os.getenv("DATABASE_URL")
    if not url:
        # opcionalmente, você pode dar raise aqui:
        # raise RuntimeError("DATABASE_URL não definida")
        # mas deixo um fallback com variáveis separadas:
        host = os.getenv("PGHOST", "localhost")
        db   = os.getenv("PGDATABASE", "postgres")
        user = os.getenv("PGUSER", "postgres")
        pwd  = os.getenv("PGPASSWORD", "")
        port = int(os.getenv("PGPORT", "5432"))
        return psycopg2.connect(
            host=host, dbname=db, user=user, password=pwd, port=port, sslmode="require"
        )
    # Render/Neon costumam exigir SSL
    return psycopg2.connect(url, sslmode="require")
