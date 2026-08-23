"""
Testes de integração das views materializadas — exigem PostgreSQL de verdade.

Os testes com mock provam que o Python sabe ler um DataFrame; estes provam que
o SQL faz o que diz. São executados contra a base de demonstração:

    docker compose up -d
    python demo/criar_demo.py
    CNPJ_TEST_DSN=postgresql://cnpj:cnpj_local@localhost:5432/cnpj \\
        python -m pytest tests/test_integracao_mvs.py -v

Sem CNPJ_TEST_DSN definida, o módulo inteiro é pulado — a suíte de unidade
continua rodando em qualquer máquina, sem banco.

Vários testes aqui são regressões de bugs que chegaram a ir ao ar. Estão
marcados como tal.
"""

import os

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DSN = os.environ.get("CNPJ_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="CNPJ_TEST_DSN não definida — testes de integração pulados"
)

# As 27 unidades da federação. Nada além disto é UF.
UFS_VALIDAS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    with conn.cursor() as c:
        yield c
    conn.close()


def _existe(cur, nome):
    cur.execute("SELECT to_regclass(%s)", (nome,))
    return cur.fetchone()[0] is not None


MVS_ESPERADAS = [
    "mv_sobrevivencia_setor", "mv_sobrevivencia_geral", "mv_sobrevivencia_faixas",
    "mv_coorte_sobrevivencia", "mv_natalidade_mortalidade", "mv_kpis_uf",
    "mv_crescimento_uf", "mv_painel_ano", "mv_painel_cidade",
    "mv_capital_ano_municipio", "mv_sobrevivencia_regime", "mv_motivo_baixa",
    "mv_coorte_regime",
    "mv_sobrevivencia_socios", "mv_coorte_socios",
]


@pytest.mark.parametrize("mv", MVS_ESPERADAS)
def test_mv_existe_e_tem_linhas(cur, mv):
    assert _existe(cur, mv), f"{mv} não existe — rode demo/criar_demo.py"
    cur.execute(f"SELECT count(*) FROM {mv}")
    assert cur.fetchone()[0] > 0, f"{mv} está vazia"


def test_uf_e_sempre_sigla_valida(cur):
    """REGRESSÃO: o comparador derivava a UF de LEFT(cod_municipio::text, 2).

    O campo `municipio` da Receita é código interno da RFB, não IBGE — São
    Paulo é 7107, Rio é 6001. Aquele LEFT produzia '71', '60', '81', que a
    interface exibia como 'Grupo 71'. Este teste falha no instante em que
    alguém reintroduzir um agrupamento que não seja a coluna uf.
    """
    cur.execute("SELECT DISTINCT uf FROM mv_kpis_uf")
    encontradas = {r[0] for r in cur.fetchall()}
    invalidas = encontradas - UFS_VALIDAS
    assert not invalidas, f"valores que não são UF: {sorted(invalidas)}"


def test_faixas_somam_cem_por_setor(cur):
    """Cada barra do gráfico empilhado tem que fechar em 100%."""
    cur.execute("""
        SELECT setor, round(sum(pct), 1) AS total
        FROM mv_sobrevivencia_faixas
        GROUP BY setor
        HAVING round(sum(pct), 1) NOT BETWEEN 99.5 AND 100.5
    """)
    assert cur.fetchall() == []


def test_coorte_preserva_o_total_da_safra(cur):
    """A soma do histograma tem que bater com a contagem na tabela-fonte.

    Se bater diferente, alguma empresa foi perdida ou contada duas vezes na
    agregação — e a curva de sobrevivência sairia sobre um denominador errado.
    """
    cur.execute("""
        SELECT c.coorte, c.total_mv, g.total_gold
        FROM (SELECT coorte, sum(qtd) AS total_mv
              FROM mv_coorte_sobrevivencia GROUP BY coorte) c
        JOIN (SELECT EXTRACT(YEAR FROM data_abertura)::int AS coorte,
                     count(*) AS total_gold
              FROM empresas_gold
              WHERE data_abertura >= DATE '1990-01-01'
              GROUP BY 1) g USING (coorte)
        WHERE c.total_mv <> g.total_gold
    """)
    assert cur.fetchall() == []


def test_saldo_e_aberturas_menos_baixas(cur):
    cur.execute("""
        SELECT count(*) FROM mv_natalidade_mortalidade
        WHERE saldo <> aberturas - baixas
    """)
    assert cur.fetchone()[0] == 0


def test_mediana_nacional_nao_e_media_das_medianas(cur):
    """REGRESSÃO do número de 20,9 anos.

    A mediana nacional tem que vir de mv_sobrevivencia_geral, que roda
    percentile_cont sobre todas as baixadas. Aqui só verificamos que ela é
    coerente com os próprios quartis — se alguém trocar a fonte por uma média
    de medianas setoriais, a relação q1 <= mediana <= q3 continua valendo,
    mas o valor sai da faixa dos dados.
    """
    cur.execute("SELECT total, media, mediana, q1, q3 FROM mv_sobrevivencia_geral")
    total, media, mediana, q1, q3 = cur.fetchone()

    assert total > 0
    assert q1 <= mediana <= q3, "quartis fora de ordem"

    # A mediana precisa estar dentro da amplitude observada de fato.
    cur.execute("""
        SELECT min((data_situacao - data_abertura) / 365.25),
               max((data_situacao - data_abertura) / 365.25)
        FROM empresas_gold
        WHERE situacao_cadastral = 8
          AND data_situacao IS NOT NULL
          AND data_situacao >= data_abertura
    """)
    menor, maior = cur.fetchone()
    assert menor <= mediana <= maior


def test_sobrevivencia_so_conta_baixadas(cur):
    """Sobrevivência mede empresa que FECHOU. Contar as ativas transformaria
    a métrica em 'idade desde a fundação' — que foi o bug original do
    projeto, o que produziu os 44,5 anos."""
    cur.execute("""
        SELECT count(*) FROM empresas_gold
        WHERE situacao_cadastral = 8 AND data_situacao IS NOT NULL
          AND data_situacao >= data_abertura
    """)
    baixadas = cur.fetchone()[0]
    cur.execute("SELECT total FROM mv_sobrevivencia_geral")
    assert cur.fetchone()[0] == baixadas


def test_nenhuma_data_futura(cur):
    """Nenhuma abertura pode ser posterior à competência da extração."""
    competencia = os.environ.get("CNPJ_COMPETENCIA", "2026-02-28")
    cur.execute(
        "SELECT count(*) FROM empresas_gold WHERE data_abertura > %s",
        (competencia,),
    )
    assert cur.fetchone()[0] == 0


def test_nenhuma_decada_concentra_a_base(cur):
    """REGRESSÃO do bug de shard.

    Quando o ETL cruzava Empresas0 com Estabelecimentos0, a interseção caía
    para ~16% da base e 81% dela se empilhava nos anos 2020. Nenhuma década
    deve passar de 60%.
    """
    cur.execute("""
        SELECT round(100.0 * max(c) / sum(c), 1) FROM (
            SELECT count(*) AS c FROM empresas_gold
            GROUP BY (EXTRACT(YEAR FROM data_abertura)::int / 10)
        ) t
    """)
    assert cur.fetchone()[0] < 60


def test_contagem_de_socios_e_coerente(cur):
    """PJ + PF nunca pode passar do total.

    A Gold recebe as três contagens de uma agregação só; se alguém trocar a
    ordem do FILTER ou duplicar o join com bronze_socios, esta soma estoura.
    O terceiro identificador (3 = sócio estrangeiro) explica a folga quando
    PJ + PF é menor que o total.
    """
    cur.execute("""
        SELECT count(*) FROM empresas_gold
        WHERE qtd_socios_pj + qtd_socios_pf > qtd_socios
    """)
    assert cur.fetchone()[0] == 0

    cur.execute("SELECT count(*) FROM empresas_gold WHERE qtd_socios < 0")
    assert cur.fetchone()[0] == 0


def test_socios_nao_vazam_identificacao(cur):
    """PRIVACIDADE: a Gold não pode ter nome nem CPF de sócio.

    O Socios.zip é o único arquivo do conjunto com dado de pessoa física. A
    regra do projeto é que ele fica na bronze; a Gold recebe só contagens.
    Este teste falha se alguém propagar uma coluna de identificação.
    """
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'empresas_gold'
          AND (column_name ILIKE '%%nome_socio%%'
               OR column_name ILIKE '%%cpf%%'
               OR column_name ILIKE '%%representante%%')
    """)
    vazamentos = [r[0] for r in cur.fetchall()]
    assert not vazamentos, f"identificação de sócio na Gold: {vazamentos}"


def test_faixas_de_socios_cobrem_a_base(cur):
    """A soma das faixas tem que ser a base inteira — nenhuma empresa fora."""
    cur.execute("SELECT sum(total_empresas) FROM mv_sobrevivencia_socios")
    das_faixas = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM empresas_gold")
    assert das_faixas == cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Regime tributário — o bug da curva de 100%
# ---------------------------------------------------------------------------

def test_nenhum_regime_tem_sobrevivencia_perfeita(cur):
    """REGRESSÃO da curva do MEI parada em 100,0%.

    O dashboard exibiu MEI e Simples Nacional com 100,0% de sobrevivência aos
    5 anos contra 48% do regime normal. Parecia um achado extraordinário e era
    tautologia: as views classificavam o regime por `opcao_mei`, que é o status
    ATUAL no registro do Simples. Quem fecha sai do registro, então o grupo
    "MEI" continha apenas empresas vivas e a sobrevivência dava 100% por
    construção.

    Todo regime relevante tem que conter empresas que fecharam. Se algum vier
    com quase nenhuma baixada, alguém trocou foi_mei por opcao_mei de novo.
    """
    cur.execute("""
        SELECT regime, total_empresas, baixadas_medidas
        FROM mv_sobrevivencia_regime
        WHERE total_empresas > 1000
          AND 100.0 * baixadas_medidas / total_empresas < 1
    """)
    assert cur.fetchall() == [], (
        "regime sem baixadas — a classificação está usando status atual"
    )


def test_regime_nao_e_classificado_por_status_atual(cur):
    """O contrapositivo do teste acima, direto na Gold.

    Prova que `opcao_mei` de fato correlaciona com estar vivo — ou seja, que a
    armadilha existe nesta base — e que `foi_mei` não correlaciona. Se um dia a
    Receita passar a manter os baixados no registro, este teste avisa que a
    premissa mudou em vez de deixar a distinção virar folclore no código.
    """
    cur.execute("""
        SELECT
            round(100.0 * count(*) FILTER
                  (WHERE opcao_mei AND situacao_cadastral = 8)
                  / NULLIF(count(*) FILTER (WHERE opcao_mei), 0), 2) AS pct_atual,
            round(100.0 * count(*) FILTER
                  (WHERE foi_mei AND situacao_cadastral = 8)
                  / NULLIF(count(*) FILTER (WHERE foi_mei), 0), 2)   AS pct_historico
        FROM empresas_gold
    """)
    pct_atual, pct_historico = cur.fetchone()

    assert pct_historico > 5, (
        f"apenas {pct_historico}% de quem já foi MEI aparece como baixada — "
        "o histórico não está sendo capturado"
    )
    assert pct_historico > pct_atual, (
        "status atual e histórico dão o mesmo resultado; se a fonte mudou, "
        "atualize o comentário em etl_cnpj.py antes de relaxar este teste"
    )


def test_coorte_regime_usa_as_mesmas_colunas(cur):
    """As duas views de regime têm que concordar sobre quem é MEI.

    mv_sobrevivencia_regime e mv_coorte_regime repetem o mesmo CASE. Repetição
    assim é onde uma correção chega pela metade: arruma-se uma view, esquece-se
    a outra, e o dashboard passa a exibir dois números diferentes para a mesma
    pergunta em telas vizinhas.
    """
    cur.execute("""
        SELECT sum(qtd) FROM mv_coorte_regime
        WHERE regime = 'MEI' AND coorte >= 2009
    """)
    da_coorte = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT count(*) FROM empresas_gold
        WHERE foi_mei AND data_abertura >= DATE '2009-01-01'
    """)
    da_gold = cur.fetchone()[0]

    assert da_coorte == da_gold, (
        f"mv_coorte_regime conta {da_coorte:,} MEIs; a Gold tem {da_gold:,}"
    )


# ---------------------------------------------------------------------------
# Capital social — o bug dos doze noves
# ---------------------------------------------------------------------------

def test_limiar_de_sentinela_nao_pega_empresa_real(cur):
    """O corte de capital não pode estar apagando empresa de verdade.

    Os valores-sentinela são um punhado de linhas. Se o limiar descer e passar
    a marcar 1% da base, ele deixou de remover preenchimento e começou a
    remover capital legítimo — que é pior que o problema original.
    """
    cur.execute("""
        SELECT round(100.0 * count(*) FILTER (WHERE capital_sentinela)
               / NULLIF(count(*), 0), 4) FROM empresas_gold
    """)
    assert cur.fetchone()[0] < 1.0


def test_nenhuma_mv_de_capital_soma_sentinela(cur):
    """REGRESSÃO do ranking de holdings com R$ 999.999.999.999,00.

    Um único FILTER esquecido em qualquer view de capital devolve o donut com
    67% para uma cidade só. Aqui a checagem é indireta e robusta: o maior
    capital total por cidade não pode encostar na ordem de grandeza que só um
    sentinela alcança.
    """
    cur.execute("SELECT max(capital_total) FROM mv_painel_cidade")
    maior_cidade = float(cur.fetchone()[0] or 0)

    cur.execute("""
        SELECT COALESCE(sum(capital_social), 0) FROM empresas_gold
        WHERE capital_social > 0 AND NOT capital_sentinela
    """)
    capital_real = float(cur.fetchone()[0] or 0)

    assert maior_cidade <= capital_real, (
        "uma cidade sozinha soma mais capital do que existe sem os sentinelas"
    )

    cur.execute("SELECT max(capital_total) FROM mv_treemap_setores")
    assert float(cur.fetchone()[0] or 0) <= capital_real


def test_mv_kpis_uf_expoe_media_e_mediana(cur):
    """O comparador lê capital_mediano de mv_kpis_uf.

    Substitui um teste que montava um DataFrame e conferia as colunas dele
    mesmo. Este pergunta ao PostgreSQL, e quebra se a MV mudar de forma.
    """
    # information_schema.columns NÃO enxerga view materializada — ela lista
    # tabelas e views comuns. Uma consulta ali devolveria conjunto vazio e o
    # teste falharia sem que nada estivesse errado. As colunas de uma MV estão
    # no catálogo interno.
    cur.execute("""
        SELECT attname FROM pg_attribute
        WHERE attrelid = 'mv_kpis_uf'::regclass AND attnum > 0 AND NOT attisdropped
    """)
    colunas = {r[0] for r in cur.fetchall()}
    assert {"uf", "total_empresas", "capital_medio",
            "capital_mediano", "pct_ativas"} <= colunas

    cur.execute("SELECT count(*) FROM mv_kpis_uf WHERE capital_mediano > capital_medio")
    assert cur.fetchone()[0] == 0, (
        "mediana acima da média em alguma UF — capital social é assimétrico "
        "à direita, então isso indica colunas trocadas"
    )


def test_painel_ano_reporta_a_mediana(cur):
    """A métrica de tendência do painel é mediana, não média.

    Capital social é cauda pesada: a média nacional dava R$ 5,7 milhões, valor
    que não descreve empresa brasileira nenhuma. A mediana tem que ser
    materialmente menor — se as duas convergirem, ou a coluna virou média de
    novo, ou a distribuição mudou de natureza.
    """
    cur.execute("""
        SELECT max(capital_mediano),
               max(capital_total / NULLIF(empresas_com_capital, 0))
        FROM mv_painel_ano
    """)
    mediana, media = cur.fetchone()
    assert mediana is not None and float(mediana) > 0
    assert float(mediana) < float(media)
