import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))


def conectar():
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=5,
    )


if __name__ == "__main__":
    with conectar() as conexao:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM public.doador")
            total_doadores = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM public.doacao")
            total_doacoes = cursor.fetchone()[0]

    print("Conexão realizada com sucesso!")
    print(f"Doadores cadastrados: {total_doadores}")
    print(f"Doações cadastradas: {total_doacoes}")