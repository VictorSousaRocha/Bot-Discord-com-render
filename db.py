# db.py
import os
import psycopg  # use psycopg[binary] no requirements

def conectar():
    """
    Conecta no Postgres priorizando:
      1) DATABASE_URL (ex.: postgres://user:pass@host:5432/db?sslmode=require)
      2) Variáveis separadas: PGHOST, PGUSER, PGPASSWORD, PGDATABASE, PGPORT (opcional), PGSSLMODE (opcional)
    Nada de credencial hard-coded aqui.
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        host = os.getenv("PGHOST")
        user = os.getenv("PGUSER")
        password = os.getenv("PGPASSWORD")
        dbname = os.getenv("PGDATABASE")
        port = os.getenv("PGPORT", "5432")
        sslmode = os.getenv("PGSSLMODE", "require")

        # validação básica
        missing = [k for k, v in {
            "PGHOST": host,
            "PGUSER": user,
            "PGPASSWORD": password,
            "PGDATABASE": dbname
        }.items() if not v]
        if missing:
            raise RuntimeError(
                "Variáveis de conexão ausentes: " + ", ".join(missing) +
                ". Defina DATABASE_URL ou as variáveis PG*."
            )

        dsn = f"postgres://{user}:{password}@{host}:{port}/{dbname}?sslmode={sslmode}"

    # autocommit=False; faça conn.commit() em INSERT/UPDATE/DELETE
    return psycopg.connect(dsn, autocommit=False)
