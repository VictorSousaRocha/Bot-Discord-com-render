# db.py
import os
import psycopg  # psycopg v3 (adicione 'psycopg[binary]' no requirements)

def conectar():
    """
    Conecta no Postgres priorizando:
      1) DATABASE_URL (ex.: postgres://user:pass@host:5432/db?sslmode=require)
      2) Variáveis separadas: PGHOST, PGUSER, PGPASSWORD, PGDATABASE, PGPORT (opcional), PGSSLMODE (opcional)
    """
    # 1) Se houver DATABASE_URL, usa direto
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        # 2) Monta a partir das PG* vars (nomes exatamente como no seu Render)
        host = os.getenv("PGHOST")
        user = os.getenv("PGUSER")
        password = os.getenv("PGPASSWORD")
        dbname = os.getenv("PGDATABASE")
        port = os.getenv("PGPORT", "5432")           # default Postgres
        sslmode = os.getenv("PGSSLMODE", "require")  # no Render geralmente 'require'

        # validação básica pra evitar conexão vazia
        missing = [k for k, v in {
            "PGHOST": host,
            "PGUSER": user,
            "PGPASSWORD": password,
            "PGDATABASE": dbname
        }.items() if not v]
        if missing:
            raise RuntimeError(
                "Variáveis de conexão ausentes: " + ", ".join(missing) +
                ". Defina DATABASE_URL OU as variáveis PG*."
            )

        dsn = f"postgres://{user}:{password}@{host}:{port}/{dbname}?sslmode={sslmode}"

    # autocommit False por padrão; chame conn.commit() em INSERT/UPDATE/DELETE
    return psycopg.connect(dsn, autocommit=False)


# (Opcional) teste rápido local: python db.py
if __name__ == "__main__":
    try:
