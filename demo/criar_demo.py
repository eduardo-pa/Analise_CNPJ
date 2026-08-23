"""
Monta a base de demonstração do zero — dados SINTÉTICOS.

Cria a `empresas_gold` com o mesmo esquema da real, popula com 300 mil
empresas geradas e constrói todas as views materializadas do projeto. Ao final,
o dashboard sobe e funciona por inteiro.

    docker compose up -d
    python demo/criar_demo.py
    streamlit run app.py

OS NÚMEROS NÃO SIGNIFICAM NADA — servem para exercitar o código, não para
tirar conclusões sobre empresas brasileiras. O pipeline real está no
etl_cnpj.py e exige os arquivos da Receita Federal.
"""

import os
import re
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

RAIZ = Path(__file__).resolve().parent.parent

# Ordem importa: as MVs de análise dependem da empresas_gold criada pelo seed.
#
# Esta lista precisa cobrir TODAS as views que o dashboard consulta. Quando
# faltou o mv_base_dashboard.sql aqui, o app subia com três blocos em erro —
# e só o CI pegou, porque na máquina de desenvolvimento aquelas views já
# existiam de execuções anteriores do ETL.
SCRIPTS = [
    RAIZ / "demo" / "seed_demo.sql",
    RAIZ / "mv_base_dashboard.sql",
    RAIZ / "mv_sobrevivencia_setor.sql",
    RAIZ / "mv_correcoes_painel.sql",
    RAIZ / "mv_analises_sobrevivencia.sql",
    RAIZ / "mv_analises_avancadas.sql",
    RAIZ / "mv_socios.sql",
]


def comandos(sql: str) -> list[str]:
    """Remove comentários de linha e separa os comandos."""
    return [c.strip() for c in re.sub(r"--[^\n]*", "", sql).split(";") if c.strip()]


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit(
            "DATABASE_URL ausente.\n"
            "Copie .env.example para .env — o padrão já aponta para o Postgres "
            "do docker compose."
        )

    faltando = [p.name for p in SCRIPTS if not p.exists()]
    if faltando:
        sys.exit(f"Arquivos não encontrados: {', '.join(faltando)}")

    inicio = time.time()
    conn = psycopg2.connect(url)
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            for script in SCRIPTS:
                print(f"\n[{script.name}]")
                cmds = comandos(script.read_text(encoding="utf-8"))
                for i, cmd in enumerate(cmds, 1):
                    rotulo = " ".join(cmd.split())[:58]
                    print(f"  [{i}/{len(cmds)}] {rotulo}...", end=" ", flush=True)
                    t0 = time.time()
                    try:
                        cur.execute(cmd)
                        print(f"OK ({time.time() - t0:.1f}s)")
                    except psycopg2.Error as e:
                        print(f"FALHOU\n      {str(e).strip().splitlines()[0]}")

            # Confirma que o resultado é utilizável antes de dizer que deu certo.
            cur.execute("SELECT count(*) FROM empresas_gold")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM pg_matviews WHERE schemaname = 'public'"
            )
            mvs = cur.fetchone()[0]
    finally:
        conn.close()

    print(f"\n{'=' * 66}")
    print(f"Base de demonstração pronta em {time.time() - inicio:.0f}s")
    print(f"  empresas_gold:        {total:,} linhas".replace(",", "."))
    print(f"  views materializadas: {mvs}")
    print("\n  Os dados são SINTÉTICOS. Nenhum número aqui descreve o Brasil.")
    print("\n  Agora rode:  streamlit run app.py")
    print(f"{'=' * 66}\n")


if __name__ == "__main__":
    main()
