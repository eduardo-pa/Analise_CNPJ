"""
Atualiza as Materialized Views do banco tcc_cnpj.
Execute diariamente via agendador (Task Scheduler / cron).

Uso:
    python refresh_views.py
"""

import sys

from sqlalchemy import text

from database import engine

VIEWS = [
    "mv_crescimento_municipio",
    "mv_bolhas_ano_municipio",
    "mv_treemap_setores",
    "mv_comparador_uf_kpis",   # legado — agrupa por LEFT(cod_municipio,2),
                               # que não é UF. Substituída por mv_kpis_uf.
    "mv_sobrevivencia_setor",
    # Criadas por mv_correcoes_painel.sql
    "mv_sobrevivencia_geral",
    "mv_kpis_uf",
    "mv_crescimento_uf",
    "mv_painel_ano",
    "mv_painel_cidade",
    # Criadas por mv_analises_sobrevivencia.sql
    "mv_sobrevivencia_faixas",
    "mv_coorte_sobrevivencia",
    "mv_natalidade_mortalidade",
    # Criadas por mv_analises_avancadas.sql — só existem depois de recarregar a
    # Gold com o ETL novo. Ausentes, o refresh apenas registra [ERRO] e segue.
    "mv_sobrevivencia_regime",
    "mv_motivo_baixa",
    "mv_coorte_regime",
    # Criadas por mv_socios.sql
    "mv_sobrevivencia_socios",
    "mv_coorte_socios",
]


def refresh_views() -> bool:
    ok = True
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for view in VIEWS:
            try:
                conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}"))
                print(f"[OK] {view}")
            except Exception as exc:
                print(f"[ERRO] {view}: {exc}")
                ok = False
    return ok


if __name__ == "__main__":
    success = refresh_views()
    sys.exit(0 if success else 1)
