import io
import urllib.request
from datetime import date
import streamlit as st
import pandas as pd
from sqlalchemy import text
import plotly.express as px
import plotly.graph_objects as go
import json
import bcrypt
from comparador_regional import render_comparador

# --- CONFIGURAÇÃO DE CONEXÃO ---
# Uma única cadeia de resolução da credencial, em database.py: DATABASE_URL do
# .env no desenvolvimento, secrets.toml no deploy. Antes a mesma lógica existia
# duplicada aqui, e o app abria dois engines para o mesmo banco.
from database import engine, COMPETENCIA, ULTIMO_ANO_COMPLETO

# --- FUNÇÕES DE SEGURANÇA E BANCO ---
def init_db():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                senha TEXT NOT NULL
            );
        """))
        conn.commit()
    # Migração idempotente: ignora erro se a coluna já existir
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE usuarios "
                "ADD COLUMN filtros_favoritos TEXT NOT NULL DEFAULT '[]'"
            ))
            conn.commit()
    except Exception:
        pass

def gerar_hash_senha(senha):
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(senha.encode('utf-8'), salt).decode('utf-8')

def verificar_login(email, password):
    try:
        with engine.connect() as conn:
            query = text("SELECT * FROM usuarios WHERE email = :email")
            df = pd.read_sql(query, conn, params={"email": email})
            if not df.empty:
                hash_banco = df.iloc[0]['senha']
                if bcrypt.checkpw(password.encode('utf-8'), hash_banco.encode('utf-8')):
                    return df
        return pd.DataFrame()
    except: return pd.DataFrame()

def cadastrar_usuario(nome, email, password):
    try:
        hash_seguro = gerar_hash_senha(password)
        with engine.connect() as conn:
            query = text("INSERT INTO usuarios (nome, email, senha) VALUES (:nome, :email, :senha)")
            conn.execute(query, {"nome": nome, "email": email, "senha": hash_seguro})
            conn.commit()
        return True
    except: return False

def excluir_conta_db(email):
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM usuarios WHERE email = :email"), {"email": email})
            conn.commit()
        return True
    except: return False

def carregar_favoritos(email: str) -> list:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT filtros_favoritos FROM usuarios WHERE email = :e"),
                {"e": email},
            ).fetchone()
        return json.loads(row[0]) if row and row[0] else []
    except:
        return []

def salvar_favoritos(email: str, favoritos: list) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE usuarios SET filtros_favoritos = :f WHERE email = :e"),
                {"f": json.dumps(favoritos, ensure_ascii=False), "e": email},
            )
            conn.commit()
        return True
    except:
        return False

# --- CACHE DE FILTROS (ESSENCIAL PARA 10M DE LINHAS) ---
@st.cache_data(ttl=3600)
def carregar_opcoes_filtros():
    """Busca as opções de filtros direto das tabelas de referência para ser rápido"""
    try:
        with engine.connect() as conn:
            cnaes = pd.read_sql("SELECT DISTINCT descricao FROM cnaes_referencia ORDER BY 1", conn)
            cidades = pd.read_sql("SELECT DISTINCT descricao FROM municipios_referencia ORDER BY 1", conn)
            return cnaes['descricao'].tolist(), cidades['descricao'].tolist()
    except:
        return [], []

@st.cache_data(ttl=3600)
def carregar_sobrevivencia():
    """Tempo que empresas BAIXADAS permaneceram ativas, nos 20 maiores setores.

    Lê da MV mv_sobrevivencia_setor (ver mv_sobrevivencia_setor.sql), que mede
    data_situacao - data_abertura apenas para empresas com situação 'baixada'.

    ORDER BY total DESC — os 20 setores com mais baixadas, isto é, os que mais
    pesam no Brasil. A versão anterior ordenava por mediana DESC, o que trazia
    os 20 setores mais LONGEVOS da base: uma seleção pelo próprio valor que o
    gráfico ia exibir. O box plot mostrava caudas de 30+ anos e sugeria que
    empresa brasileira dura décadas.
    """
    sql = text("""
        SELECT setor, total, media, mediana, p05, q1, q3, p95
        FROM mv_sobrevivencia_setor
        ORDER BY total DESC
        LIMIT 20
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=600)
def mvs_disponiveis() -> set:
    """Views materializadas que existem no banco.

    As análises novas dependem de MVs criadas por scripts distintos, e algumas
    exigem uma recarga da Gold. Em vez de estourar uma exceção genérica quando
    uma delas falta, o dashboard checa antes e mostra o comando que resolve.
    """
    sql = text("SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'")
    try:
        with engine.connect() as conn:
            return set(pd.read_sql(sql, conn)["matviewname"])
    except Exception:
        return set()


@st.cache_data(ttl=3600)
def carregar_faixas_sobrevivencia(n_setores: int = 15) -> pd.DataFrame:
    """Distribuição do tempo de vida em faixas, nos setores com mais baixadas."""
    sql = text("""
        WITH maiores AS (
            SELECT setor
            FROM mv_sobrevivencia_faixas
            GROUP BY setor
            ORDER BY max(total_setor) DESC
            LIMIT :n
        )
        SELECT f.setor, f.faixa_ordem, f.faixa, f.qtd, f.total_setor, f.pct
        FROM mv_sobrevivencia_faixas f
        JOIN maiores m ON m.setor = f.setor
        ORDER BY f.setor, f.faixa_ordem
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params={"n": n_setores})


@st.cache_data(ttl=3600)
def carregar_coortes() -> pd.DataFrame:
    """Histograma (safra x ano da baixa) — matéria-prima da curva de sobrevivência."""
    sql = text("""
        SELECT coorte, ano_baixa, qtd
        FROM mv_coorte_sobrevivencia
        WHERE coorte BETWEEN 1996 AND :ano_fim
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params={"ano_fim": ULTIMO_ANO_COMPLETO})


def curva_de_sobrevivencia(df_coortes: pd.DataFrame, coorte: int) -> pd.DataFrame:
    """% da safra ainda não baixada a cada aniversário.

    ano_baixa = -1 é empresa não baixada até a competência: ela sobrevive a
    todos os pontos da curva. As demais somem a partir do aniversário em que
    a baixa ocorreu.

    A curva para na janela que a safra permite OBSERVAR. Uma safra de 2020,
    numa base de 2026, não tem como informar sobrevivência aos 10 anos — e
    estender a linha até lá desenharia um platô de 100% que é só ausência de
    tempo decorrido, não resiliência.
    """
    d = df_coortes[df_coortes["coorte"] == coorte]
    total = int(d["qtd"].sum())
    if total == 0:
        return pd.DataFrame(columns=["anos", "pct", "vivas"])

    janela = ULTIMO_ANO_COMPLETO - coorte
    mortas = d[d["ano_baixa"] >= 0]

    linhas = []
    for t in range(janela + 1):
        acumuladas = int(mortas.loc[mortas["ano_baixa"] < t, "qtd"].sum())
        vivas = total - acumuladas
        linhas.append({"anos": t, "vivas": vivas, "pct": 100.0 * vivas / total})
    return pd.DataFrame(linhas)


@st.cache_data(ttl=3600)
def carregar_natalidade(ufs: tuple[str, ...] = ()) -> pd.DataFrame:
    """Aberturas, baixas e saldo por ano — Brasil ou um recorte de UFs."""
    if ufs:
        sql = text("""
            SELECT ano, SUM(aberturas) AS aberturas, SUM(baixas) AS baixas,
                   SUM(saldo) AS saldo
            FROM mv_natalidade_mortalidade
            WHERE uf = ANY(:ufs) AND ano BETWEEN 1990 AND :ano_fim
            GROUP BY ano ORDER BY ano
        """)
        params = {"ufs": list(ufs), "ano_fim": ULTIMO_ANO_COMPLETO}
    else:
        sql = text("""
            SELECT ano, SUM(aberturas) AS aberturas, SUM(baixas) AS baixas,
                   SUM(saldo) AS saldo
            FROM mv_natalidade_mortalidade
            WHERE ano BETWEEN 1990 AND :ano_fim
            GROUP BY ano ORDER BY ano
        """)
        params = {"ano_fim": ULTIMO_ANO_COMPLETO}
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params=params)


@st.cache_data(ttl=3600)
def carregar_ufs_natalidade() -> list:
    sql = text("SELECT DISTINCT uf FROM mv_natalidade_mortalidade ORDER BY uf")
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)["uf"].tolist()


@st.cache_data(ttl=3600)
def carregar_capital_municipio(n_cidades: int = 20) -> pd.DataFrame:
    """Capital social mediano por ano, nos municípios com mais aberturas.

    O corte é por VOLUME de empresas — os municípios que realmente pesam — e
    não pela ordem alfabética, que era o default do gráfico de bolhas antigo.
    """
    sql = text("""
        WITH maiores AS (
            SELECT cidade
            FROM mv_capital_ano_municipio
            GROUP BY cidade
            ORDER BY SUM(empresas) DESC
            LIMIT :n
        )
        SELECT c.ano, c.cidade, c.empresas, c.capital_mediano
        FROM mv_capital_ano_municipio c
        JOIN maiores m ON m.cidade = c.cidade
        WHERE c.ano <= :ano_fim
        ORDER BY c.cidade, c.ano
    """)
    with engine.connect() as conn:
        return pd.read_sql(
            sql, conn, params={"n": n_cidades, "ano_fim": ULTIMO_ANO_COMPLETO}
        )


@st.cache_data(ttl=3600)
def carregar_regime() -> pd.DataFrame:
    sql = text("""
        SELECT regime, total_empresas, ativas, pct_ativas, baixadas_medidas,
               media, mediana, q1, q3, pct_menos_5_anos
        FROM mv_sobrevivencia_regime
        ORDER BY total_empresas DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=3600)
def carregar_motivo_baixa() -> pd.DataFrame:
    sql = text("""
        SELECT motivo, descricao, qtd, pct, mediana_anos
        FROM mv_motivo_baixa
        ORDER BY qtd DESC
        LIMIT 12
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=3600)
def carregar_sobrevivencia_geral():
    """Mediana e média nacionais de sobrevivência — linha única.

    Os KPIs no topo desta seção vinham de uma média ponderada das medianas dos
    20 setores exibidos no box plot. Média de medianas não é mediana, e o
    recorte ainda era enviesado — por isso o painel dizia 20,9 anos enquanto
    metricas_post.py apurava 3,3 sobre a base inteira.

    percentile_cont sobre o conjunto todo é a única forma de obter a mediana
    nacional. Fica em mv_sobrevivencia_geral (mv_correcoes_painel.sql).
    """
    sql = text("""
        SELECT total, media, mediana, q1, q3, p90
        FROM mv_sobrevivencia_geral
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn).iloc[0]


@st.cache_data(ttl=3600)
def carregar_coorte_regime() -> pd.DataFrame:
    """Histograma (safra x regime x ano da baixa) — base da curva por regime."""
    sql = text("""
        SELECT coorte, regime, ano_baixa, qtd
        FROM mv_coorte_regime
        WHERE regime <> 'Não informado'
          AND coorte BETWEEN 2009 AND :ano_fim
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params={"ano_fim": ULTIMO_ANO_COMPLETO})


def curva_por_regime(df: pd.DataFrame, coorte: int, regime: str) -> pd.DataFrame:
    """Mesma matemática de curva_de_sobrevivencia(), recortada por regime."""
    d = df[(df["coorte"] == coorte) & (df["regime"] == regime)]
    total = int(d["qtd"].sum())
    if total == 0:
        return pd.DataFrame(columns=["anos", "pct", "vivas"])

    janela = ULTIMO_ANO_COMPLETO - coorte
    mortas = d[d["ano_baixa"] >= 0]
    linhas = []
    for t in range(janela + 1):
        acumuladas = int(mortas.loc[mortas["ano_baixa"] < t, "qtd"].sum())
        vivas = total - acumuladas
        linhas.append({"anos": t, "vivas": vivas, "pct": 100.0 * vivas / total})
    return pd.DataFrame(linhas)

# ---------------------------------------------------------------------------
# PAINEL PRINCIPAL — agregação no PostgreSQL
#
# A versão anterior fazia `SELECT ... FROM empresas_gold ... LIMIT 30000` e
# agregava no pandas. LIMIT sem ORDER BY não devolve uma amostra: devolve as
# primeiras linhas que o plano de execução produzir — ordem física da tabela,
# ou o topo de um índice, conforme o que o planner escolher naquele dia.
#
# O resultado no painel era um gráfico de linha reto com um pico único e uma
# pizza de capital dominada por PERDIGÃO e NOVA MUTUM. Distribuição assim não
# sai de amostra aleatória: aquelas 30 mil linhas eram um recorte enviesado
# para o topo do capital social. (diagnostico_dashboard.py imprime o EXPLAIN
# da consulta antiga, se quiser ver qual plano era.)
#
# Agora COUNT/SUM/AVG/GROUP BY rodam no banco e só o resultado agregado sobe.
# É o princípio Query-First do projeto, que o LIMIT 30000 tinha furado.
# ---------------------------------------------------------------------------

# Os fragmentos abaixo são texto fixo escolhido por if — nenhum valor digitado
# pelo usuário entra na string. Setor e cidade viajam como bind params.
_JOINS_PAINEL = """
    FROM empresas_gold e
    LEFT JOIN cnaes_referencia      c ON e.cnae_fiscal   = c.codigo
    LEFT JOIN municipios_referencia m ON e.cod_municipio = m.codigo
    WHERE e.capital_social > 0
"""


def _filtros_painel(setor_sel: str, cidade_sel: str) -> tuple[str, dict]:
    """Devolve (fragmento SQL, params) para os filtros da sidebar."""
    fragmento, params = "", {}
    if setor_sel != "Todos":
        fragmento += " AND c.descricao = :setor"
        params["setor"] = setor_sel
    if cidade_sel != "Todas":
        fragmento += " AND m.descricao = :cidade"
        params["cidade"] = cidade_sel
    return fragmento, params


@st.cache_data(ttl=3600)
def carregar_kpis_painel(setor_sel: str, cidade_sel: str) -> pd.Series:
    """Contagem, capital total e capital médio do recorte — calculados no banco."""
    if setor_sel == "Todos" and cidade_sel == "Todas":
        # Caminho sem filtro: lê da MV, responde em milissegundos.
        sql = text("""
            SELECT SUM(empresas)                        AS empresas,
                   SUM(capital_total)                   AS capital_total,
                   SUM(capital_total) / NULLIF(SUM(empresas), 0) AS capital_medio
            FROM mv_painel_ano
        """)
        params = {}
    else:
        fragmento, params = _filtros_painel(setor_sel, cidade_sel)
        sql = text(f"""
            SELECT COUNT(*)                       AS empresas,
                   COALESCE(SUM(e.capital_social), 0) AS capital_total,
                   AVG(e.capital_social)          AS capital_medio
            {_JOINS_PAINEL} {fragmento}
        """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params=params).iloc[0]


@st.cache_data(ttl=3600)
def carregar_evolucao_painel(setor_sel: str, cidade_sel: str) -> pd.DataFrame:
    """Aberturas por ano no recorte, até o último ano completo da competência."""
    if setor_sel == "Todos" and cidade_sel == "Todas":
        sql = text("""
            SELECT ano, empresas AS qtd
            FROM mv_painel_ano
            WHERE ano BETWEEN 1900 AND :ano_fim
            ORDER BY ano
        """)
        params = {"ano_fim": ULTIMO_ANO_COMPLETO}
    else:
        fragmento, params = _filtros_painel(setor_sel, cidade_sel)
        params["ano_fim"] = ULTIMO_ANO_COMPLETO
        sql = text(f"""
            SELECT EXTRACT(YEAR FROM e.data_abertura)::int AS ano,
                   COUNT(*)                                AS qtd
            {_JOINS_PAINEL} {fragmento}
              AND e.data_abertura IS NOT NULL
              AND EXTRACT(YEAR FROM e.data_abertura)::int <= :ano_fim
            GROUP BY 1
            ORDER BY 1
        """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params=params)


@st.cache_data(ttl=3600)
def carregar_cidades_painel(setor_sel: str, cidade_sel: str) -> pd.DataFrame:
    """Top 10 cidades por capital social somado, dentro do recorte."""
    if setor_sel == "Todos" and cidade_sel == "Todas":
        sql = text("""
            SELECT cidade, capital_total
            FROM mv_painel_cidade
            ORDER BY capital_total DESC
            LIMIT 10
        """)
        params = {}
    else:
        fragmento, params = _filtros_painel(setor_sel, cidade_sel)
        sql = text(f"""
            SELECT COALESCE(m.descricao, e.cod_municipio::text) AS cidade,
                   SUM(e.capital_social)                        AS capital_total
            {_JOINS_PAINEL} {fragmento}
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 10
        """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params=params)


@st.cache_data(ttl=3600)
def carregar_crescimento_uf():
    # Corta o ano da competência: com dois meses de registros ele derruba a
    # linha de qualquer município e parece colapso de aberturas.
    sql = text("""
        SELECT ano, cod_municipio, nome_municipio, total
        FROM mv_crescimento_municipio
        WHERE ano <= :ano_fim
        ORDER BY nome_municipio, ano
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params={"ano_fim": ULTIMO_ANO_COMPLETO})

@st.cache_data(ttl=3600)
def carregar_data_quality():
    # Uma única passagem na tabela: LEFT JOIN detecta CNAEs não mapeados
    # sem trazer registros individuais para o Python.
    #
    # O teto de data é a COMPETÊNCIA da extração, não CURRENT_DATE. A carga é a
    # foto de fev/2026: uma abertura registrada em jul/2026 é impossível, mas
    # passava despercebida enquanto o limite fosse "hoje".
    sql = text("""
        SELECT
            COUNT(*)                                                               AS total,
            COUNT(*) FILTER (WHERE e.capital_social IS NULL
                                   OR e.capital_social <= 0)                      AS capital_invalido,
            COUNT(*) FILTER (WHERE e.data_abertura IS NULL
                                   OR e.data_abertura < '1800-01-01'
                                   OR e.data_abertura > :competencia)             AS data_invalida,
            COUNT(*) FILTER (WHERE c.codigo IS NULL)                              AS cnae_nao_mapeado
        FROM empresas_gold e
        LEFT JOIN cnaes_referencia c ON e.cnae_fiscal = c.codigo
    """)
    with engine.connect() as conn:
        return pd.read_sql(
            sql, conn, params={"competencia": COMPETENCIA}
        ).iloc[0]  # retorna Series com 1 linha

@st.cache_data(ttl=86400)  # GeoJSON do IBGE muda raramente — cache de 24 h
def carregar_geojson_estados():
    url = (
        "https://raw.githubusercontent.com/codeforamerica/"
        "click_that_hood/master/public/data/brazil-states.geojson"
    )
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())

# Removidos daqui:
#   carregar_contagem_uf()    — lia mv_densidade_uf, que não existe no banco.
#                               Nunca foi chamada; ficaria quebrada se fosse.
#   carregar_bolhas_ano_uf()  — alimentava o gráfico de bolhas, substituído
#                               pela matriz de capital (mv_capital_ano_municipio).
# A mv_bolhas_ano_municipio continua no banco e no refresh_views.py: o ETL a
# recria a cada carga. Se quiser mesmo aposentá-la, derrube antes de rodar o
# etl_cnpj.py — ele copia do catálogo o que encontrar.


@st.cache_data(ttl=3600)
def carregar_treemap_setores():
    sql = text("""
        SELECT divisao_cnae, total_empresas, capital_total
        FROM mv_treemap_setores
        ORDER BY total_empresas DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    page_title="Demografia Empresarial Brasil",
    page_icon="🏢",
    layout="wide",
)
init_db()

if 'logado' not in st.session_state:
    st.session_state['logado'] = False

# --- INTERFACE DE ACESSO ---
if not st.session_state['logado']:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("🔐 Demografia Empresarial Brasil")
        st.caption(
            "Sobrevivência, natalidade e mortalidade de empresas a partir da "
            "base pública de CNPJ da Receita Federal."
        )
        t_log, t_cad = st.tabs(["Login", "Criar Conta"])
        
        with t_log:
            e_l = st.text_input("E-mail", key="l_email")
            p_l = st.text_input("Senha", type="password", key="l_pass")
            if st.button("Entrar"):
                u = verificar_login(e_l, p_l)
                if not u.empty:
                    st.session_state['logado'] = True
                    st.session_state['user_nome'] = u.iloc[0]['nome']
                    st.session_state['user_email'] = u.iloc[0]['email']
                    st.rerun()
                else: st.error("Acesso Negado.")
        
        with t_cad:
            n_n = st.text_input("Nome")
            n_e = st.text_input("E-mail")
            n_p = st.text_input("Senha (min 8 carac.)", type="password")
            if st.button("Cadastrar"):
                if len(n_p) >= 8 and cadastrar_usuario(n_n, n_e, n_p):
                    st.success("Cadastrado! Faça o login.")
                else: st.error("Erro no cadastro ou senha curta.")

# --- DASHBOARD LOGADO ---
else:
    # --- SIDEBAR ---
    with st.sidebar:
        st.title(f"👋 Olá, {st.session_state['user_nome']}")
        if st.button("Sair do Sistema", width="stretch"):
            st.session_state.clear()
            st.rerun()
        
        st.markdown("---")
        st.subheader("🎯 Filtros Rápidos")

        # Carregando filtros via Cache
        lista_cnaes, lista_cidades = carregar_opcoes_filtros()

        # --- Favoritos ---
        favoritos = carregar_favoritos(st.session_state["user_email"])
        if favoritos:
            st.caption("⭐ Favoritos")
            for i, fav in enumerate(favoritos):
                col_btn, col_del = st.columns([4, 1])
                with col_btn:
                    if st.button(
                        fav["nome"], key=f"fav_apply_{i}", width="stretch"
                    ):
                        st.session_state["setor_sel"] = fav["setor"]
                        st.session_state["cidade_sel"] = fav["cidade"]
                        st.rerun()
                with col_del:
                    if st.button(
                        "✕", key=f"fav_del_{i}", help=f"Remover '{fav['nome']}'"
                    ):
                        favoritos.pop(i)
                        salvar_favoritos(st.session_state["user_email"], favoritos)
                        st.rerun()

        setor_sel = st.selectbox(
            "Setor (CNAE)", ["Todos"] + lista_cnaes, key="setor_sel"
        )
        cidade_sel = st.selectbox(
            "Cidade", ["Todas"] + lista_cidades, key="cidade_sel"
        )

        with st.expander("💾 Salvar como favorito"):
            nome_fav = st.text_input(
                "Nome do favorito",
                key="nome_fav",
                placeholder="ex.: Tech São Paulo",
            )
            if st.button("Salvar", key="btn_salvar_fav", width="stretch"):
                if nome_fav.strip():
                    favoritos.append(
                        {"nome": nome_fav.strip(), "setor": setor_sel, "cidade": cidade_sel}
                    )
                    if salvar_favoritos(st.session_state["user_email"], favoritos):
                        st.session_state["nome_fav"] = ""
                        st.success("✅ Favorito salvo!")
                        st.rerun()
                    else:
                        st.error("Erro ao salvar o favorito.")
                else:
                    st.warning("Digite um nome para o favorito.")

        st.markdown("---")
        with st.expander("⚙️ Gerenciar Conta"):
            confirma = st.checkbox("Confirmar exclusão")
            if st.button("EXCLUIR MINHA CONTA", type="primary"):
                if confirma and excluir_conta_db(st.session_state['user_email']):
                    st.session_state.clear()
                    st.rerun()

    # --- CORPO DO DASHBOARD ---
    st.markdown("""
<style>
[data-testid="stMetric"] {
    background: linear-gradient(135deg,
        rgba(28, 131, 225, 0.09),
        rgba(28, 131, 225, 0.02));
    border: 1px solid rgba(28, 131, 225, 0.28);
    border-radius: 12px;
    padding: 1rem 1.25rem;
}
[data-testid="stMetricLabel"] > div {
    font-size: 0.78rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    opacity: 0.75;
}
[data-testid="stMetricValue"] > div {
    font-size: 1.7rem;
    font-weight: 700;
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] hr {
    border-color: rgba(255, 255, 255, 0.1);
}
</style>
""", unsafe_allow_html=True)
    st.title("📊 Análise Estratégica")

    try:
        # Três consultas agregadas no banco — nenhuma linha crua sobe para cá.
        kpis = carregar_kpis_painel(setor_sel, cidade_sel)
        df_evol = carregar_evolucao_painel(setor_sel, cidade_sel)
        df_top_cidades = carregar_cidades_painel(setor_sel, cidade_sel)

        total_empresas = int(kpis["empresas"] or 0)

        if total_empresas:
            capital_total = float(kpis["capital_total"] or 0)
            capital_medio = float(kpis["capital_medio"] or 0)

            # KPIs Superiores
            with st.container(border=True):
                k1, k2, k3 = st.columns(3)
                k1.metric("🏭 Empresas Identificadas", f"{total_empresas:,}".replace(",", "."))
                k2.metric("💰 Capital Total", f"R$ {capital_total:,.2f}")
                k3.metric("📐 Média de Capital", f"R$ {capital_medio:,.2f}")
            st.caption(
                "Recorte inteiro da base, com capital social maior que zero — "
                "não uma amostra."
            )

            st.divider()

            # GRÁFICOS
            col_esq, col_dir = st.columns([2, 1])
            with col_esq:
                st.subheader("📈 Evolução de Abertura por Ano")
                fig_l = px.line(
                    df_evol, x="ano", y="qtd", markers=True, template="plotly_dark",
                    labels={"ano": "Ano de abertura", "qtd": "Empresas abertas"},
                )
                st.plotly_chart(fig_l, width="stretch")
                st.caption(
                    f"Série encerra em {ULTIMO_ANO_COMPLETO}. A competência da base é "
                    f"{COMPETENCIA:%m/%Y}, então {COMPETENCIA.year} tem apenas "
                    "os primeiros meses do ano e cairia como se fosse queda real."
                )

            with col_dir:
                st.subheader("🏙️ Capital por Cidade (Top 10)")
                fig_p = px.pie(
                    df_top_cidades, names="cidade", values="capital_total", hole=0.4
                )
                st.plotly_chart(fig_p, width="stretch")

            st.divider()

            # TABELA RANKING
            st.subheader("🏆 Maiores Empresas do Recorte")
            n_rank = st.select_slider(
                "Registros exibidos",
                options=[10, 25, 50, 100],
                value=25,
            )

            sql_rank = """
                SELECT e.razao_social, e.capital_social, e.data_abertura,
                       c.descricao AS setor, m.descricao AS cidade
                FROM empresas_gold e
                LEFT JOIN cnaes_referencia      c ON e.cnae_fiscal   = c.codigo
                LEFT JOIN municipios_referencia m ON e.cod_municipio = m.codigo
                WHERE e.capital_social > 0
            """
            params_rank: dict = {}
            if setor_sel != "Todos":
                sql_rank += " AND c.descricao = :setor"
                params_rank["setor"] = setor_sel
            if cidade_sel != "Todas":
                sql_rank += " AND m.descricao = :cidade"
                params_rank["cidade"] = cidade_sel
            sql_rank += " ORDER BY e.capital_social DESC LIMIT :n"
            params_rank["n"] = n_rank

            with engine.connect() as conn_rank:
                df_rank = pd.read_sql(text(sql_rank), conn_rank, params=params_rank)

            st.dataframe(
                df_rank,
                width="stretch", hide_index=True,
                column_config={
                    "capital_social": st.column_config.NumberColumn("Capital (R$)", format="R$ %.2f"),
                    "data_abertura": st.column_config.DateColumn("Abertura")
                }
            )

            today = date.today().strftime("%Y%m%d")

            # A exportação leva o ranking exibido, não as 30 mil linhas que a
            # versão anterior trazia. Aquele arquivo dizia "empresas_AAAAMMDD"
            # mas continha uma fatia arbitrária da base — quem abrisse acharia
            # que tinha o recorte inteiro nas mãos.
            st.caption(
                f"A exportação leva as {n_rank} empresas do ranking acima. "
                "Para o recorte completo, use a exportação do Comparador Regional "
                "ou consulte a base direto."
            )
            col_csv, col_xlsx = st.columns(2)

            with col_csv:
                st.download_button(
                    label="⬇️ Exportar CSV",
                    data=df_rank.to_csv(index=False, sep=";").encode("utf-8-sig"),
                    file_name=f"ranking_empresas_{today}.csv",
                    mime="text/csv",
                    width="stretch",
                )

            with col_xlsx:
                _buf = io.BytesIO()
                df_rank.to_excel(_buf, index=False, engine="openpyxl")
                st.download_button(
                    label="⬇️ Exportar Excel",
                    data=_buf.getvalue(),
                    file_name=f"ranking_empresas_{today}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                )

        else:
            st.warning("⚠️ Nenhum dado encontrado. Tente ajustar os filtros.")

    except Exception as e:
        st.error(f"❌ Erro de Processamento: {e}")

    # --- DATA QUALITY ---
    with st.expander("🔍 Qualidade dos Dados — empresas_gold"):
        try:
            dq = carregar_data_quality()
            total = int(dq["total"])

            def _badge(pct: float) -> str:
                if pct < 1:
                    return "🟢 OK"
                if pct < 5:
                    return "🟡 Atenção"
                return "🔴 Crítico"

            col_dq1, col_dq2, col_dq3 = st.columns(3)
            items = [
                (col_dq1, "capital_invalido", "💰 Capital nulo/zero"),
                (col_dq2, "data_invalida",    "📅 Data de abertura inválida"),
                (col_dq3, "cnae_nao_mapeado", "🏭 CNAE não mapeado"),
            ]
            for col, key, label in items:
                n = int(dq[key])
                pct = n / total * 100 if total else 0.0
                col.metric(label, f"{pct:.2f}%", delta=f"{n:,} registros", delta_color="off")
                col.caption(_badge(pct))

            st.caption(
                f"Universo: {total:,} registros em empresas_gold · "
                "Thresholds: 🟢 < 1 %  ·  🟡 1–5 %  ·  🔴 > 5 %"
            )
        except Exception as e_dq:
            st.error(f"❌ Erro ao carregar métricas de qualidade: {e_dq}")

    st.divider()

    # --- TREEMAP DE SETORES ECONÔMICOS ---
    st.title("🌳 Distribuição por Setor Econômico")

    try:
        df_tree = carregar_treemap_setores()

        if not df_tree.empty:
            fig_tree = px.treemap(
                df_tree,
                path=[px.Constant("Brasil"), "divisao_cnae"],
                values="total_empresas",
                color="capital_total",
                color_continuous_scale="Blues",
                hover_data={"capital_total": ":,.0f", "total_empresas": ":,"},
                labels={
                    "total_empresas": "Empresas",
                    "capital_total": "Capital Total (R$)",
                    "divisao_cnae": "Divisão CNAE",
                },
                title="Empresas por Divisão de CNAE",
            )
            fig_tree.update_traces(
                texttemplate="<b>%{label}</b><br>%{value:,} empresas",
                hovertemplate=(
                    "<b>%{label}</b><br>"
                    "Empresas: %{value:,}<br>"
                    "Capital Total: R$ %{customdata[0]:,.0f}<extra></extra>"
                ),
            )
            fig_tree.update_layout(margin=dict(t=50, l=10, r=10, b=10))
            st.plotly_chart(fig_tree, width="stretch")
        else:
            st.info("Nenhum setor encontrado para o treemap.")
    except Exception as e_tree:
        st.error(f"❌ Erro ao carregar treemap de setores: {e_tree}")

    st.divider()

    # --- ANÁLISE DE SOBREVIVÊNCIA EMPRESARIAL ---
    st.title("📉 Sobrevivência Empresarial")

    try:
        df_sobrev = carregar_sobrevivencia()

        if not df_sobrev.empty:
            # KPIs nacionais vêm da MV que roda percentile_cont sobre TODAS as
            # baixadas — não da média ponderada das medianas dos 20 setores
            # exibidos abaixo, que é o que produzia os 20,9 anos.
            geral = carregar_sobrevivencia_geral()

            col_a, col_b, col_c = st.columns(3)
            col_a.metric(
                "🏢 Empresas baixadas analisadas",
                f"{int(geral['total']):,}".replace(",", "."),
            )
            col_b.metric("⏱️ Média de sobrevivência", f"{float(geral['media']):.1f} anos")
            col_c.metric("📊 Mediana de sobrevivência", f"{float(geral['mediana']):.1f} anos")

            st.caption(
                "A média é puxada por uma cauda de empresas longevas; a mediana "
                "descreve melhor o caso típico. Por isso as duas aparecem juntas. "
                f"Metade das empresas baixadas durou até {float(geral['mediana']):.1f} anos; "
                f"um quarto passou de {float(geral['q3']):.1f}. "
                "Confere com o IBGE, que aponta ~60% de mortalidade antes dos 5 anos."
            )

            st.subheader("⏳ Quanto tempo as empresas duraram, por setor")

            # Substitui o box plot. Box plot exige que o leitor saiba ler
            # quartis, e o Plotly ainda empilha sete rótulos de hover por
            # caixa — a pergunta que importa ("quantas morrem cedo aqui?")
            # sumia no meio disso.
            #
            # Barras 100% empilhadas respondem essa pergunta na horizontal e
            # preservam a distribuição inteira, que uma barra de mediana
            # jogaria fora: dois setores com mediana 3 podem ter caudas
            # completamente diferentes.
            df_faixas = (
                carregar_faixas_sobrevivencia(15)
                if "mv_sobrevivencia_faixas" in mvs_disponiveis()
                else pd.DataFrame()
            )

            if "mv_sobrevivencia_faixas" not in mvs_disponiveis():
                st.info(
                    "Para este gráfico, crie a view com: "
                    "`python aplicar_indices.py mv_analises_sobrevivencia.sql`"
                )
            elif df_faixas.empty:
                st.info(
                    "Nenhum setor atingiu as 1.000 baixadas exigidas para o "
                    "recorte por faixas."
                )
            else:
                # Cores em rampa: vermelho = morreu cedo, azul = sobreviveu.
                # Rampa divergente: vermelho = morreu cedo, cinza neutro no
                # meio, azul = sobreviveu. É a leitura que o gráfico quer
                # mesmo — "ruim de um lado, bom do outro".
                #
                # A primeira versão vermelha que fiz tinha um defeito real,
                # medido contra o fundo #0e1117 do Streamlit: as faixas
                # "3 a 5 anos" (#9c9c9c) e "5 a 10 anos" (#67a9cf) ficavam a
                # ΔE 8,8 uma da outra em visão normal — o piso é 15 — e a 7,4
                # sob protanopia. Ou seja: duas fatias vizinhas que boa parte
                # das pessoas não conseguia separar, daltônica ou não.
                #
                # Estes tons mantêm o vermelho e afastam esse par: pior par
                # adjacente a ΔE 16,2 em visão normal e 14,8 sob protanopia,
                # ambos folgados.
                #
                # Duas faixas ficam abaixo de 3:1 de contraste com o fundo
                # (#a32b2b e #1c4f87). Isso é aceitável só com um caminho
                # alternativo de leitura — por isso a tabela logo abaixo do
                # gráfico não é enfeite, é o que torna esses tons utilizáveis.
                CORES_FAIXA = {
                    "Menos de 1 ano":  "#e66767",
                    "1 a 3 anos":      "#a32b2b",
                    "3 a 5 anos":      "#8a8a8a",
                    "5 a 10 anos":     "#3987e5",
                    "Mais de 10 anos": "#1c4f87",
                }
                ORDEM_FAIXAS = list(CORES_FAIXA.keys())

                # Ordena os setores pela mortalidade precoce (até 3 anos).
                # Como o Plotly desenha a primeira categoria embaixo, a ordem
                # crescente coloca o setor mais letal no topo.
                precoce = (
                    df_faixas[df_faixas["faixa_ordem"] <= 2]
                    .groupby("setor")["pct"].sum()
                    .sort_values()
                )
                df_faixas["setor_curto"] = df_faixas["setor"].str.slice(0, 42)
                ordem_setores = [s[:42] for s in precoce.index]

                fig_faixas = px.bar(
                    df_faixas,
                    x="pct",
                    y="setor_curto",
                    color="faixa",
                    orientation="h",
                    template="plotly_dark",
                    color_discrete_map=CORES_FAIXA,
                    category_orders={
                        "faixa": ORDEM_FAIXAS,
                        "setor_curto": ordem_setores,
                    },
                    labels={"pct": "% das empresas baixadas do setor",
                            "setor_curto": "", "faixa": "Durou"},
                    custom_data=["faixa", "qtd", "setor"],
                )
                fig_faixas.update_traces(
                    hovertemplate=(
                        "<b>%{customdata[2]}</b><br>"
                        "Durou %{customdata[0]}<br>"
                        "%{x:.1f}% — %{customdata[1]:,} empresas<extra></extra>"
                    ),
                    # 2 px da cor do fundo separando as fatias. É um respiro,
                    # não uma borda: contorno de cor contrastante empilharia
                    # mais uma linha sobre um gráfico que já é denso.
                    marker_line_width=2,
                    marker_line_color="#0e1117",
                )
                fig_faixas.update_layout(
                    height=620,
                    barmode="stack",
                    xaxis=dict(ticksuffix="%", range=[0, 100]),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                xanchor="left", x=0, title=""),
                    margin=dict(l=10, r=10, t=60, b=40),
                )
                st.plotly_chart(fig_faixas, width="stretch")

                pior = precoce.index[-1]
                st.caption(
                    "Cada barra é 100% das empresas **já baixadas** daquele setor, "
                    "repartidas pelo tempo que ficaram abertas. Quanto mais vermelho "
                    "à esquerda, mais o setor mata cedo. Setores com pelo menos 1.000 "
                    "baixadas, ordenados pela fatia que não chegou aos 3 anos — no "
                    f"topo, **{pior}**, com {precoce.iloc[-1]:.0f}% fechando antes disso."
                )

                with st.expander("📋 Ver os números deste gráfico"):
                    tabela_faixas = (
                        df_faixas.pivot(index="setor", columns="faixa", values="pct")
                        .reindex(columns=ORDEM_FAIXAS)
                        .reindex(precoce.index[::-1])
                    )
                    st.dataframe(
                        tabela_faixas.style.format("{:.1f}%", na_rep="—"),
                        width="stretch",
                    )
                    st.caption(
                        "Percentual das baixadas de cada setor por faixa de duração. "
                        "As linhas seguem a ordem do gráfico."
                    )

        else:
            st.warning(
                "⚠️ Sem dados de sobrevivência. Crie a view materializada com: "
                "`python aplicar_indices.py mv_sobrevivencia_setor.sql`"
            )

    except Exception as e:
        st.error(f"❌ Erro na análise de sobrevivência: {e}")

    st.divider()

    # --- CURVA DE SOBREVIVÊNCIA POR SAFRA ---
    st.title("📈 Curva de Sobrevivência por Safra de Abertura")
    st.caption(
        "De cada 100 empresas abertas num ano, quantas ainda não foram baixadas "
        "a cada aniversário. É o recorte que o IBGE usa na Demografia das Empresas, "
        "o que permite comparar direto com a estatística oficial."
    )

    if "mv_coorte_sobrevivencia" not in mvs_disponiveis():
        st.info(
            "Para esta seção, crie a view com: "
            "`python aplicar_indices.py mv_analises_sobrevivencia.sql`"
        )
    else:
        try:
            df_coortes = carregar_coortes()
            safras_disponiveis = sorted(df_coortes["coorte"].unique().tolist())

            padrao = [a for a in (2010, 2015, 2020) if a in safras_disponiveis]
            safras_sel = st.multiselect(
                "Safras de abertura",
                options=safras_disponiveis,
                default=padrao or safras_disponiveis[-3:],
                help="Cada safra é acompanhada só pelo tempo que a base permite observar.",
            )

            if safras_sel:
                fig_curva = go.Figure()
                for safra in sorted(safras_sel):
                    curva = curva_de_sobrevivencia(df_coortes, safra)
                    if curva.empty:
                        continue
                    fig_curva.add_trace(go.Scatter(
                        x=curva["anos"], y=curva["pct"],
                        mode="lines+markers", name=str(safra),
                        customdata=curva["vivas"],
                        hovertemplate=(
                            f"<b>Safra {safra}</b><br>"
                            "Aos %{x} anos: %{y:.1f}% ativas<br>"
                            "%{customdata:,} empresas<extra></extra>"
                        ),
                    ))

                # Régua dos 5 anos: é o marco que o IBGE publica.
                fig_curva.add_vline(
                    x=5, line_dash="dot", line_color="rgba(255,255,255,0.35)",
                    annotation_text="5 anos", annotation_position="top",
                )
                fig_curva.update_layout(
                    template="plotly_dark",
                    height=460,
                    xaxis_title="Anos desde a abertura",
                    yaxis_title="% ainda não baixadas",
                    yaxis=dict(ticksuffix="%", range=[0, 101]),
                    hovermode="x unified",
                    legend_title="Safra",
                )
                st.plotly_chart(fig_curva, width="stretch")

                # Taxa aos 5 anos por safra — só safras com 5 anos observáveis.
                pontos = []
                for safra in safras_disponiveis:
                    if ULTIMO_ANO_COMPLETO - safra < 5:
                        continue
                    curva = curva_de_sobrevivencia(df_coortes, safra)
                    linha_5 = curva[curva["anos"] == 5]
                    if not linha_5.empty:
                        pontos.append({"safra": safra,
                                       "pct": float(linha_5["pct"].iloc[0])})

                if pontos:
                    df_5 = pd.DataFrame(pontos)

                    # Uma cor só. Antes as barras eram coloridas pelo próprio
                    # valor — o comprimento já diz isso, e o degradê gastava o
                    # canal de cor repetindo a informação, ainda por cima
                    # deixando as barras mais altas quase brancas.
                    fig_5 = px.bar(
                        df_5, x="safra", y="pct", template="plotly_dark",
                        labels={"safra": "Safra de abertura",
                                "pct": "% ativas aos 5 anos"},
                    )
                    fig_5.update_traces(
                        marker_color="#3987e5",
                        # %{y} cru imprimia 90.6228669847254 no tooltip.
                        hovertemplate=(
                            "<b>Safra %{x}</b><br>"
                            "%{y:.1f}% ainda ativas aos 5 anos<extra></extra>"
                        ),
                    )
                    fig_5.update_layout(
                        height=360,
                        yaxis=dict(ticksuffix="%", range=[0, 100]),
                        xaxis=dict(tickmode="linear", dtick=2, tickangle=0),
                        bargap=0.25,
                        title="Sobrevivência aos 5 anos, safra a safra",
                        margin=dict(t=50, b=40),
                    )
                    st.plotly_chart(fig_5, width="stretch")
                    st.caption(
                        "Só aparecem as safras que já tiveram 5 anos para serem "
                        f"observadas (até {ULTIMO_ANO_COMPLETO - 5}). Incluir safras mais "
                        "novas produziria taxas altíssimas que refletem falta de "
                        "tempo decorrido, não solidez.\n\n"
                        "**Leia com cuidado antes de comparar com o IBGE.** Aqui "
                        "'morreu' significa **baixa formal** no cadastro da Receita. "
                        "Empresa que parou de operar mas nunca deu baixa segue contada "
                        "como viva — e é por isso que estes números são bem mais "
                        "otimistas que a mortalidade do IBGE, que mede cessação de "
                        "atividade. São duas perguntas diferentes, não uma divergência."
                    )

                    with st.expander("📋 Ver os números por safra"):
                        st.dataframe(
                            df_5.rename(columns={"safra": "Safra",
                                                 "pct": "% ativas aos 5 anos"})
                                .style.format({"% ativas aos 5 anos": "{:.1f}%"}),
                            width="stretch", hide_index=True,
                        )
            else:
                st.info("Selecione ao menos uma safra.")

        except Exception as e_curva:
            st.error(f"❌ Erro na curva de sobrevivência: {e_curva}")

    st.divider()

    # --- NATALIDADE x MORTALIDADE ---
    st.title("⚖️ Natalidade x Mortalidade de Empresas")

    if "mv_natalidade_mortalidade" not in mvs_disponiveis():
        st.info(
            "Para esta seção, crie a view com: "
            "`python aplicar_indices.py mv_analises_sobrevivencia.sql`"
        )
    else:
        try:
            ufs_nat = st.multiselect(
                "Filtrar por UF (vazio = Brasil inteiro)",
                options=carregar_ufs_natalidade(),
                default=[],
                key="ufs_natalidade",
            )
            df_nat = carregar_natalidade(tuple(ufs_nat))
            df_nat = df_nat[df_nat["ano"] >= 2000]

            fig_nat = go.Figure()
            fig_nat.add_trace(go.Bar(
                x=df_nat["ano"], y=df_nat["aberturas"], name="Aberturas",
                marker_color="#2166ac",
                hovertemplate="%{x}<br>Aberturas: %{y:,}<extra></extra>",
            ))
            fig_nat.add_trace(go.Bar(
                x=df_nat["ano"], y=-df_nat["baixas"], name="Baixas",
                marker_color="#b2182b", customdata=df_nat["baixas"],
                hovertemplate="%{x}<br>Baixas: %{customdata:,}<extra></extra>",
            ))
            fig_nat.add_trace(go.Scatter(
                x=df_nat["ano"], y=df_nat["saldo"], name="Saldo líquido",
                mode="lines+markers", line=dict(color="#f7f7f7", width=2.5),
                hovertemplate="%{x}<br>Saldo: %{y:,}<extra></extra>",
            ))
            fig_nat.add_hline(y=0, line_color="rgba(255,255,255,0.4)")
            fig_nat.update_layout(
                template="plotly_dark", height=460, barmode="relative",
                xaxis_title="Ano", yaxis_title="Empresas",
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02,
                            xanchor="right", x=1),
            )
            st.plotly_chart(fig_nat, width="stretch")

            negativos = df_nat[df_nat["saldo"] < 0]["ano"].tolist()
            recorte = ", ".join(ufs_nat) if ufs_nat else "Brasil"
            st.caption(
                f"{recorte}: aberturas para cima, baixas para baixo, saldo líquido "
                "na linha branca. "
                + (f"Anos em que fecharam mais empresas do que abriram: "
                   f"{', '.join(map(str, negativos))}."
                   if negativos else "Nenhum ano com saldo negativo no período.")
                + " Limite metodológico: isto é uma foto da base atual, não um "
                "registro histórico de eventos. Empresas que a Receita já removeu "
                "do cadastro não aparecem em ano nenhum, e o efeito é maior quanto "
                "mais antigo o ano."
            )

        except Exception as e_nat:
            st.error(f"❌ Erro em natalidade x mortalidade: {e_nat}")

    st.divider()

    # --- REGIME TRIBUTÁRIO E MOTIVO DA BAIXA ---
    # Dependem de colunas que só existem após reconstruir a Gold com o ETL novo.
    st.title("🧾 Regime Tributário e Motivo da Baixa")

    mvs = mvs_disponiveis()
    if "mv_sobrevivencia_regime" not in mvs and "mv_motivo_baixa" not in mvs:
        st.info(
            "Estas duas análises dependem de colunas novas na `empresas_gold` "
            "(`opcao_simples`, `opcao_mei`, `motivo_situacao`). Para habilitá-las:\n\n"
            "```\n"
            "python carregar_referencias.py\n"
            "set CNPJ_SKIP_BRONZE=1\n"
            "python etl_cnpj.py\n"
            "python aplicar_indices.py mv_analises_avancadas.sql\n"
            "```\n\n"
            "O ETL vai carregar só o `Simples.zip` (~285 MB) e refazer a Gold — "
            "as bronze de Empresas e Estabelecimentos são reaproveitadas."
        )
    else:
        col_reg, col_mot = st.columns(2)

        with col_reg:
            st.subheader("Sobrevivência por regime")
            if "mv_sobrevivencia_regime" not in mvs:
                st.info("Rode `python aplicar_indices.py mv_analises_avancadas.sql`")
            else:
                try:
                    df_reg = carregar_regime()
                    df_reg = df_reg[df_reg["regime"] != "Não informado"].copy()
                    if df_reg.empty:
                        raise ValueError(
                            "mv_sobrevivencia_regime só tem 'Não informado' — "
                            "o join com bronze_simples não trouxe nada."
                        )

                    df_reg = df_reg.sort_values("mediana")
                    df_reg["rotulo"] = df_reg["mediana"].map(lambda v: f"{v:.1f} anos")

                    fig_reg = px.bar(
                        df_reg,
                        x="mediana", y="regime", orientation="h",
                        template="plotly_dark",
                        text="rotulo",
                        labels={"mediana": "Mediana de sobrevivência (anos)",
                                "regime": ""},
                        color="mediana", color_continuous_scale="Blues",
                    )
                    fig_reg.update_traces(textposition="outside")
                    fig_reg.update_layout(height=300, coloraxis_showscale=False,
                                          margin=dict(t=20))
                    st.plotly_chart(fig_reg, width="stretch")

                    st.dataframe(
                        df_reg[["regime", "total_empresas", "pct_ativas",
                                "mediana", "pct_menos_5_anos"]],
                        width="stretch", hide_index=True,
                        column_config={
                            "regime": "Regime",
                            "total_empresas": st.column_config.NumberColumn(
                                "Empresas", format="%d"),
                            "pct_ativas": st.column_config.NumberColumn(
                                "% ativas", format="%.1f%%"),
                            "mediana": st.column_config.NumberColumn(
                                "Mediana (anos)", format="%.1f"),
                            "pct_menos_5_anos": st.column_config.NumberColumn(
                                "% < 5 anos", format="%.1f%%"),
                        },
                    )
                    st.caption(
                        "MEI é lido antes de Simples: todo MEI é optante do Simples, "
                        "então sem essa precedência o recorte MEI desapareceria "
                        "dentro do Simples. 'Não informado' (empresa ausente do "
                        "Simples.zip) fica de fora do gráfico."
                    )
                except Exception as e_reg:
                    st.error(f"❌ Erro no regime tributário: {e_reg}")

        with col_mot:
            st.subheader("Por que fecharam")
            if "mv_motivo_baixa" not in mvs:
                st.info("Rode `python aplicar_indices.py mv_analises_avancadas.sql`")
            else:
                try:
                    df_mot = carregar_motivo_baixa()
                    df_mot["rotulo"] = df_mot["descricao"].str.slice(0, 45)

                    fig_mot = px.bar(
                        df_mot.sort_values("qtd"),
                        x="qtd", y="rotulo", orientation="h",
                        template="plotly_dark",
                        labels={"qtd": "Empresas baixadas", "rotulo": ""},
                        color="qtd", color_continuous_scale="Reds",
                        custom_data=["pct", "mediana_anos"],
                    )
                    fig_mot.update_traces(
                        hovertemplate=(
                            "<b>%{y}</b><br>%{x:,} empresas "
                            "(%{customdata[0]:.1f}% das baixas)<br>"
                            "Mediana de vida: %{customdata[1]} anos<extra></extra>"
                        )
                    )
                    fig_mot.update_layout(height=460, coloraxis_showscale=False,
                                          margin=dict(t=20))
                    st.plotly_chart(fig_mot, width="stretch")
                    st.caption(
                        "'Empresa fechou' esconde eventos muito diferentes: "
                        "encerramento voluntário é decisão do dono; baixa por "
                        "omissão de declarações é a Receita cancelando um CNPJ "
                        "abandonado. Somar os dois distorce qualquer leitura de "
                        "mortalidade empresarial."
                    )
                except Exception as e_mot:
                    st.error(f"❌ Erro no motivo da baixa: {e_mot}")

        # --- MEI x regime normal, DENTRO da mesma safra ---
        st.subheader("MEI sobrevive menos? — comparação dentro da mesma safra")

        if "mv_coorte_regime" not in mvs:
            st.info("Rode `python aplicar_indices.py mv_analises_avancadas.sql`")
        else:
            try:
                df_cr = carregar_coorte_regime()
                safras_cr = sorted(df_cr["coorte"].unique().tolist())
                # Safras com pelo menos 5 anos observáveis, senão a curva não
                # chega ao marco que interessa.
                safras_cr = [s for s in safras_cr if ULTIMO_ANO_COMPLETO - s >= 5]

                if not safras_cr:
                    st.info("Nenhuma safra tem 5 anos observáveis ainda.")
                else:
                    padrao_cr = 2015 if 2015 in safras_cr else safras_cr[len(safras_cr) // 2]
                    safra_cr = st.select_slider(
                        "Safra de abertura",
                        options=safras_cr,
                        value=padrao_cr,
                        key="safra_regime",
                    )

                    # Slots 1, 2 e 3 da paleta categórica, na ordem fixa.
                    CORES_REGIME = {
                        "MEI": "#3987e5",
                        "Simples Nacional": "#d95926",
                        "Regime normal": "#199e70",
                    }

                    fig_cr = go.Figure()
                    for regime, cor in CORES_REGIME.items():
                        curva = curva_por_regime(df_cr, safra_cr, regime)
                        if curva.empty:
                            continue
                        fig_cr.add_trace(go.Scatter(
                            x=curva["anos"], y=curva["pct"],
                            name=regime, mode="lines+markers",
                            line=dict(color=cor, width=2),
                            marker=dict(size=6),
                            customdata=curva["vivas"],
                            hovertemplate=(
                                f"<b>{regime}</b><br>"
                                "Aos %{x} anos: %{y:.1f}% ativas<br>"
                                "%{customdata:,} empresas<extra></extra>"
                            ),
                        ))

                    fig_cr.add_vline(
                        x=5, line_dash="dot", line_color="rgba(255,255,255,0.35)",
                        annotation_text="5 anos", annotation_position="top",
                    )
                    fig_cr.update_layout(
                        template="plotly_dark", height=420,
                        xaxis_title="Anos desde a abertura",
                        yaxis_title="% ainda não baixadas",
                        yaxis=dict(ticksuffix="%", range=[0, 101]),
                        hovermode="x unified",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                    xanchor="left", x=0, title=""),
                        margin=dict(t=60, b=40),
                    )
                    st.plotly_chart(fig_cr, width="stretch")

                    aos_5 = {}
                    for regime in CORES_REGIME:
                        c = curva_por_regime(df_cr, safra_cr, regime)
                        linha_5 = c[c["anos"] == 5]
                        if not linha_5.empty:
                            aos_5[regime] = float(linha_5["pct"].iloc[0])

                    if aos_5:
                        resumo = " · ".join(
                            f"**{r}** {p:.1f}%" for r, p in aos_5.items()
                        )
                        st.caption(
                            f"Safra de {safra_cr}, sobrevivência aos 5 anos: {resumo}.\n\n"
                            "Comparar regimes **dentro da mesma safra** é o que torna a "
                            "conta honesta. O MEI existe desde 2008: numa comparação "
                            "solta, ele apareceria com sobrevivência pior só por ser "
                            "composto de empresas jovens, enquanto o regime normal "
                            "carrega companhias que tiveram décadas para se consolidar. "
                            "Fixando a safra, as três curvas partem do mesmo ponto no "
                            "tempo e a diferença que sobra é do regime, não da idade."
                        )
            except Exception as e_cr:
                st.error(f"❌ Erro na comparação por regime: {e_cr}")

    st.divider()

    # --- RANKING DE MUNICÍPIOS POR DENSIDADE ---
    st.title("📍 Densidade de Empresas por Município")

    try:
        # engine.connect() solto vazava uma conexão do pool a cada rerun do
        # Streamlit — em poucas interações o pool esgotava. Agora fecha sozinho.
        with engine.connect() as conn_rank_mun:
            df_ranking = pd.read_sql(
                text("""
                    SELECT nome_municipio, SUM(total) AS total_empresas
                    FROM mv_crescimento_municipio
                    GROUP BY nome_municipio
                    ORDER BY total_empresas DESC
                    LIMIT 30
                """),
                conn_rank_mun,
            )

        if not df_ranking.empty:
            fig_rank = px.bar(
                df_ranking.sort_values("total_empresas"),
                x="total_empresas",
                y="nome_municipio",
                orientation="h",
                template="plotly_dark",
                labels={"total_empresas": "Empresas Abertas (1990–hoje)", "nome_municipio": "Município"},
                color="total_empresas",
                color_continuous_scale="Blues",
            )
            fig_rank.update_layout(
                height=700,
                showlegend=False,
                coloraxis_showscale=False,
                yaxis=dict(tickfont=dict(size=11)),
            )
            st.plotly_chart(fig_rank, width="stretch")
            st.caption(
                f"Top 30 municípios por volume de empresas abertas desde 1990. "
                f"Total na base: {df_ranking['total_empresas'].sum():,} empresas."
            )
        else:
            st.warning("⚠️ Sem dados de município disponíveis.")

    except Exception as e:
        st.error(f"❌ Erro ao carregar ranking de municípios: {e}")

    st.divider()

    # --- CRESCIMENTO POR MUNICÍPIO ---
    st.title("🗺️ Crescimento por Município")

    try:
        df_uf = carregar_crescimento_uf()

        if not df_uf.empty:
            top5_nomes = (
                df_uf.groupby("nome_municipio")["total"]
                .sum()
                .nlargest(5)
                .index.tolist()
            )
            todos_nomes_mun = sorted(df_uf["nome_municipio"].unique().tolist())

            mun_sel = st.multiselect(
                "Selecione os Municípios",
                options=todos_nomes_mun,
                default=top5_nomes,
            )

            if mun_sel:
                df_plot = df_uf[df_uf["nome_municipio"].isin(mun_sel)]
                fig_uf = px.line(
                    df_plot,
                    x="ano",
                    y="total",
                    color="nome_municipio",
                    markers=True,
                    template="plotly_dark",
                    labels={"ano": "Ano", "total": "Empresas Abertas", "nome_municipio": "Município"},
                )
                fig_uf.update_layout(height=450)
                st.plotly_chart(fig_uf, width="stretch")
            else:
                st.info("Selecione ao menos um município para exibir o gráfico.")

        else:
            st.warning("⚠️ Sem dados de crescimento por município.")

    except Exception as e:
        st.error(f"❌ Erro no gráfico por município: {e}")

    st.divider()

    # --- CAPITAL SOCIAL: ANO × MUNICÍPIO ---
    #
    # Era um gráfico de bolhas, ilegível por três motivos somados:
    #   a) o filtro nascia com os 20 primeiros municípios em ordem ALFABÉTICA
    #      (ABADIA DE GOIAS, ABADIANIA, ABAETE…) — cidades escolhidas pelo
    #      alfabeto, não pela relevância;
    #   b) 20 séries de capital parecido viravam um borrão de bolhas
    #      sobrepostas, uma pilha por ano, com uma legenda gigante ao lado;
    #   c) o eixo Y era capital social MÉDIO — em capital social isso é quase
    #      uma medida de outlier. Umas poucas holdings bilionárias esticavam a
    #      escala até R$ 6 mi e achatavam todo o resto na linha do zero.
    #
    # Duas dimensões categóricas (ano, município) e uma magnitude (capital)
    # pedem matriz, não dispersão: numa matriz não existe sobreposição. E a
    # métrica passa a ser a MEDIANA, que descreve a empresa típica do lugar.
    st.title("🏙️ Capital Social Típico por Ano e Município")

    if "mv_capital_ano_municipio" not in mvs_disponiveis():
        st.info(
            "Para esta seção, crie a view com: "
            "`python aplicar_indices.py mv_analises_sobrevivencia.sql`"
        )
    else:
        try:
            df_cap = carregar_capital_municipio(20)

            if df_cap.empty:
                st.info("Sem dados suficientes para o gráfico de capital.")
            else:
                # A matriz que estava aqui não funcionou: 20 municípios x 21
                # anos = 420 células quase todas do mesmo azul, porque a
                # variação real é pequena perto da escala. Uma parede de cor
                # parecida não é leitura — é ruído com aparência de dado.
                #
                # A história que existe nesses números é a TRAJETÓRIA no tempo,
                # e trajetória se lê em linha. Menos municípios, eixo com valor
                # explícito: dá para responder "subiu ou caiu, e quanto" sem
                # decifrar tom de azul.
                volume = df_cap.groupby("cidade")["empresas"].sum().nlargest(5)
                df_linhas = df_cap[df_cap["cidade"].isin(volume.index)]

                # Slots 1 a 5 da paleta categórica, na ordem fixa. A ordem é o
                # próprio mecanismo de segurança para daltonismo — não é para
                # ser embaralhada nem escolhida por gosto.
                CORES_CIDADE = ["#3987e5", "#d95926", "#199e70",
                                "#c98500", "#d55181"]
                cores = dict(zip(volume.index, CORES_CIDADE))

                fig_cap = go.Figure()
                for cidade in volume.index:
                    d = df_linhas[df_linhas["cidade"] == cidade].sort_values("ano")
                    fig_cap.add_trace(go.Scatter(
                        x=d["ano"], y=d["capital_mediano"],
                        name=cidade.title(),
                        mode="lines+markers",
                        line=dict(color=cores[cidade], width=2),
                        marker=dict(size=6),
                        customdata=d["empresas"],
                        hovertemplate=(
                            f"<b>{cidade.title()}</b> — %{{x}}<br>"
                            "Capital mediano: R$ %{y:,.0f}<br>"
                            "%{customdata:,} empresas abertas<extra></extra>"
                        ),
                    ))

                fig_cap.update_layout(
                    template="plotly_dark",
                    height=460,
                    xaxis_title="Ano de abertura",
                    yaxis_title="Capital social mediano (R$)",
                    yaxis=dict(tickprefix="R$ ", tickformat=",.0f",
                               gridcolor="rgba(255,255,255,0.08)"),
                    xaxis=dict(showgrid=False, tickmode="linear", dtick=2),
                    hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                xanchor="left", x=0, title=""),
                    margin=dict(l=10, r=10, t=60, b=40),
                )
                st.plotly_chart(fig_cap, width="stretch")

                ultimo = int(df_cap["ano"].max())
                st.caption(
                    f"Os 5 municípios com mais empresas abertas, de 2005 a {ultimo}. "
                    "A linha é a **mediana** do capital social declarado na abertura — "
                    "a empresa típica daquele município naquele ano. Média não serviria: "
                    "meia dúzia de holdings bilionárias definiria a escala inteira. "
                    "Anos com menos de 50 aberturas no município ficam de fora, porque "
                    "ali a mediana oscila por acaso."
                )

                with st.expander("📋 Ver os números — inclui os 20 municípios"):
                    matriz = df_cap.pivot(
                        index="cidade", columns="ano", values="capital_mediano"
                    ).sort_values(ultimo, ascending=False)
                    st.dataframe(
                        matriz.style.format("R$ {:,.0f}", na_rep="—"),
                        width="stretch",
                    )
                    st.caption(
                        "O gráfico mostra 5 municípios para continuar legível; "
                        "a tabela traz os 20 que a view materializa."
                    )

        except Exception as e_cap:
            st.error(f"❌ Erro no gráfico de capital: {e_cap}")

    st.divider()
    render_comparador()

    # --- RODAPÉ ---
    st.markdown("---")
    st.markdown(
        f"""
        <div style="
            text-align: center;
            color: #6b7280;
            font-size: 0.78rem;
            padding: 1rem 0 0.25rem;
            line-height: 1.8;
        ">
            📂 Fonte: <b>Receita Federal do Brasil</b>
            &nbsp;·&nbsp; Base CNPJ pública (dados abertos)
            &nbsp;·&nbsp; Competência: <b>{COMPETENCIA:%m/%Y}</b>
            &nbsp;·&nbsp; Séries temporais encerram em <b>{ULTIMO_ANO_COMPLETO}</b>
            (último ano completo)
        </div>
        """,
        unsafe_allow_html=True,
    )