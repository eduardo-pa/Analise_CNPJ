"""
comparador_regional.py
======================
Funcionalidade: Comparador Regional de UFs/Municípios
Página do dashboard — Demografia Empresarial Brasil

Lógica:
- Usuário seleciona 2 a 4 regiões (UF ou Município)
- Dashboard gera painel comparativo lado a lado com 4 blocos:
  1. KPI Cards   → total empresas, capital médio, % ativas
  2. Bar Chart   → capital médio comparativo
  3. Heatmap     → distribuição percentual por setor (top 8 CNAEs)
  4. Line Chart  → crescimento de aberturas ao longo do tempo

Regras críticas seguidas:
- NUNCA f-string em SQL → sempre text() + params={}
- NUNCA SELECT * sem LIMIT
- Agregação no PostgreSQL, não no Pandas
- UF vem da coluna 'uf' de empresas_gold (sigla oficial, carregada pelo ETL)
- Séries temporais param em ULTIMO_ANO_COMPLETO — o ano da competência é parcial

CORREÇÃO — era o bug do "Grupo 60" / "Grupo 71":
    A versão anterior derivava a UF de LEFT(cod_municipio::text, 2), assumindo
    que cod_municipio fosse código IBGE. Não é. O campo "municipio" do arquivo
    de Estabelecimentos traz o código INTERNO da Receita Federal — São Paulo é
    7107 e Rio de Janeiro é 6001, não 3550308 e 3304557 como no IBGE.
    Assim LEFT(...,2) produzia "71", "60", "81"…, que não existem no mapa de
    UFs do IBGE; o .fillna() deixava o número cru passar e a tela exibia
    "Grupo 71". E o agrupamento por faixa de código da RFB não corresponde a
    estado: misturava municípios de UFs diferentes e rotulava aquilo de região.

    A empresas_gold reconstruída carrega st.uf, a sigla de duas letras. É a
    única fonte correta de UF neste projeto.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import text

# Streamlit Cloud injeta st.secrets; local usa DATABASE_URL do .env.
from database import engine, COMPETENCIA, ULTIMO_ANO_COMPLETO
                              # resolvedor neutro: evita import circular
                              # (app.py importa este modulo)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Sigla → nome por extenso. Serve só para rotular a interface; o agrupamento
# é feito pela própria sigla vinda do banco.
UF_NOME: dict[str, str] = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas",
    "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
    "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão",
    "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco",
    "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima",
    "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe",
    "TO": "Tocantins",
}

# Paleta fixa para consistência entre gráficos (máx 4 regiões)
PALETA: list[str] = ["#2196F3", "#FF5722", "#4CAF50", "#9C27B0"]

# Setores CNAE — divisão (2 primeiros dígitos) → label legível
DIVISAO_CNAE_MAP: dict[str, str] = {
    "01": "Agricultura", "10": "Alimentos", "13": "Têxtil",
    "20": "Química", "23": "Minerais não-metálicos", "25": "Metal",
    "26": "Eletrônicos", "28": "Máquinas", "33": "Manutenção",
    "35": "Eletricidade", "41": "Construção Civil", "45": "Automotivo",
    "46": "Comércio Atacadista", "47": "Comércio Varejista",
    "49": "Transporte Terrestre", "52": "Armazenagem", "55": "Hospedagem",
    "56": "Alimentação", "58": "Publicações", "61": "Telecom",
    "62": "TI/Software", "63": "Dados/TI", "64": "Finanças",
    "65": "Seguros", "68": "Imobiliário", "69": "Jurídico",
    "70": "Gestão", "71": "Engenharia", "72": "P&D",
    "73": "Publicidade", "74": "Design/Foto", "75": "Veterinária",
    "77": "Locação", "78": "RH/Recrutamento", "79": "Turismo",
    "80": "Vigilância", "81": "Facilities", "82": "Administração",
    "84": "Administração Pública", "85": "Educação", "86": "Saúde",
    "87": "Assistência Social", "90": "Arte/Cultura", "93": "Esporte",
    "94": "Associações", "96": "Serviços Pessoais",
}

# ---------------------------------------------------------------------------
# Queries — todas parametrizadas via text() + params
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def _carregar_ufs_disponiveis() -> pd.DataFrame:
    """Lista de UFs para o seletor — siglas reais, lidas de mv_kpis_uf."""
    sql = text("""
        SELECT uf, total_empresas
        FROM mv_kpis_uf
        ORDER BY total_empresas DESC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn)
    df["label"] = (
        df["uf"] + " — " + df["uf"].map(UF_NOME).fillna("?") +
        " (" + df["total_empresas"].map("{:,}".format).str.replace(",", ".") + ")"
    )
    return df


@st.cache_data(ttl=3600)
def _carregar_municipios_top(limite: int = 100) -> pd.DataFrame:
    """Retorna os N municípios com mais empresas para popular o multiselect."""
    sql = text("""
        SELECT
            eg.cod_municipio,
            COALESCE(mr.descricao, eg.cod_municipio::text) AS nome_municipio,
            COUNT(*)                                            AS total_empresas
        FROM empresas_gold eg
        LEFT JOIN municipios_referencia mr
               ON mr.codigo = eg.cod_municipio
        GROUP BY eg.cod_municipio, mr.descricao
        ORDER BY 3 DESC
        LIMIT :limite
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"limite": limite})
    df["label"] = df["nome_municipio"] + " (" + df["total_empresas"].map("{:,}".format) + ")"
    return df


@st.cache_data(ttl=1800)
def _kpis_por_uf(ufs: tuple[str, ...]) -> pd.DataFrame:
    """
    KPIs agregados por UF: total_empresas | capital_medio | pct_ativas

    Lê de mv_kpis_uf, pré-agregada sobre a coluna uf real. A % de ativas só
    existe agora porque situacao_cadastral entrou na Gold nesta reconstrução —
    a docstring do módulo prometia esse KPI desde o início e ele nunca aparecia.
    """
    sql = text("""
        SELECT uf AS sigla_uf, total_empresas, capital_medio, pct_ativas
        FROM mv_kpis_uf
        WHERE uf = ANY(:ufs)
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params={"ufs": list(ufs)})


@st.cache_data(ttl=1800)
def _kpis_por_municipio(cod_municipios: tuple[int, ...]) -> pd.DataFrame:
    """KPIs agregados por município."""
    sql = text("""
        SELECT
            eg.cod_municipio,
            COALESCE(mr.descricao, eg.cod_municipio::text) AS nome_municipio,
            COUNT(*)                                            AS total_empresas,
            ROUND(
                AVG(eg.capital_social) FILTER (WHERE eg.capital_social > 0)::numeric,
                2
            )                                                   AS capital_medio
        FROM empresas_gold eg
        LEFT JOIN municipios_referencia mr ON mr.codigo = eg.cod_municipio
        WHERE eg.cod_municipio = ANY(:municipios)
        GROUP BY eg.cod_municipio, mr.descricao
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"municipios": list(cod_municipios)})
    return df


@st.cache_data(ttl=1800)
def _setores_por_uf(ufs: tuple[str, ...], top_n: int = 8) -> pd.DataFrame:
    """
    Distribuição percentual dos top N setores (divisão CNAE 2 dígitos) por UF.
    Retorna tabela longa: sigla_uf | divisao | pct
    """
    sql = text("""
        WITH base AS (
            SELECT
                uf                             AS sigla_uf,
                LEFT(cnae_fiscal::text, 2)     AS divisao,
                COUNT(*)                       AS qtd
            FROM empresas_gold
            WHERE uf = ANY(:ufs)
              AND cnae_fiscal IS NOT NULL
            GROUP BY 1, 2
        ),
        totais AS (
            SELECT sigla_uf, SUM(qtd) AS total FROM base GROUP BY sigla_uf
        ),
        top_divisoes AS (
            SELECT divisao
            FROM base
            GROUP BY divisao
            ORDER BY SUM(qtd) DESC
            LIMIT :top_n
        )
        SELECT
            b.sigla_uf,
            b.divisao,
            ROUND(b.qtd * 100.0 / t.total, 2) AS pct
        FROM base b
        JOIN totais       t  ON t.sigla_uf = b.sigla_uf
        JOIN top_divisoes td ON td.divisao = b.divisao
        ORDER BY b.sigla_uf, pct DESC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"ufs": list(ufs), "top_n": top_n})
    df["setor"] = df["divisao"].map(DIVISAO_CNAE_MAP).fillna("Divisão " + df["divisao"])
    return df


@st.cache_data(ttl=1800)
def _setores_por_municipio(cod_municipios: tuple[int, ...], top_n: int = 8) -> pd.DataFrame:
    """Distribuição percentual dos top N setores por município."""
    sql = text("""
        WITH base AS (
            SELECT
                eg.cod_municipio,
                COALESCE(mr.descricao, eg.cod_municipio::text) AS nome_municipio,
                LEFT(eg.cnae_fiscal::text, 2)                       AS divisao,
                COUNT(*)                                            AS qtd
            FROM empresas_gold eg
            LEFT JOIN municipios_referencia mr ON mr.codigo = eg.cod_municipio
            WHERE eg.cod_municipio = ANY(:municipios)
              AND eg.cnae_fiscal IS NOT NULL
            GROUP BY eg.cod_municipio, mr.descricao, LEFT(eg.cnae_fiscal::text, 2)
        ),
        totais AS (
            SELECT cod_municipio, SUM(qtd) AS total FROM base GROUP BY cod_municipio
        ),
        top_divisoes AS (
            SELECT divisao
            FROM base
            GROUP BY divisao
            ORDER BY SUM(qtd) DESC
            LIMIT :top_n
        )
        SELECT
            b.cod_municipio,
            b.nome_municipio,
            b.divisao,
            ROUND(b.qtd * 100.0 / t.total, 2) AS pct
        FROM base b
        JOIN totais       t  ON t.cod_municipio = b.cod_municipio
        JOIN top_divisoes td ON td.divisao       = b.divisao
        ORDER BY b.cod_municipio, pct DESC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"municipios": list(cod_municipios), "top_n": top_n})
    df["setor"] = df["divisao"].map(DIVISAO_CNAE_MAP).fillna("Divisão " + df["divisao"])
    return df


@st.cache_data(ttl=1800)
def _crescimento_por_uf(
    ufs: tuple[str, ...],
    ano_inicio: int = 2000,
) -> pd.DataFrame:
    """
    Série temporal de aberturas por UF e ano — de mv_crescimento_uf.

    O teto :ano_fim corta o ano da competência. Com dois meses de registros ele
    aparecia como despencada de ~600 mil para ~90 mil aberturas: parecia colapso
    da atividade empresarial e era só a base terminando ali.
    """
    sql = text("""
        SELECT uf AS sigla_uf, ano, aberturas
        FROM mv_crescimento_uf
        WHERE uf = ANY(:ufs)
          AND ano BETWEEN :ano_inicio AND :ano_fim
        ORDER BY uf, ano
    """)
    with engine.connect() as conn:
        return pd.read_sql(
            sql, conn,
            params={
                "ufs": list(ufs),
                "ano_inicio": ano_inicio,
                "ano_fim": ULTIMO_ANO_COMPLETO,
            },
        )


@st.cache_data(ttl=1800)
def _crescimento_por_municipio(
    cod_municipios: tuple[int, ...],
    ano_inicio: int = 2000,
) -> pd.DataFrame:
    """Série temporal de aberturas por município e ano — usa mv_crescimento_municipio."""
    sql = text("""
        SELECT
            mcm.cod_municipio,
            COALESCE(mr.descricao, mcm.cod_municipio::text) AS nome_municipio,
            mcm.ano,
            mcm.total                                            AS aberturas
        FROM mv_crescimento_municipio mcm
        LEFT JOIN municipios_referencia mr ON mr.codigo = mcm.cod_municipio
        WHERE mcm.cod_municipio = ANY(:municipios)
          AND mcm.ano BETWEEN :ano_inicio AND :ano_fim
        ORDER BY mcm.cod_municipio, mcm.ano
    """)
    with engine.connect() as conn:
        return pd.read_sql(
            sql, conn,
            params={
                "municipios": list(cod_municipios),
                "ano_inicio": ano_inicio,
                "ano_fim": ULTIMO_ANO_COMPLETO,
            },
        )


# ---------------------------------------------------------------------------
# Componentes de UI
# ---------------------------------------------------------------------------

def _render_kpi_cards(df_kpi: pd.DataFrame, col_regiao: str, cores: dict) -> None:
    cols = st.columns(len(df_kpi))
    for col, row in zip(cols, df_kpi.itertuples()):
        regiao = getattr(row, col_regiao)
        cor = cores.get(regiao, PALETA[0])

        try:
            total = f"{int(row.total_empresas):,}".replace(",", ".")
        except Exception:
            total = "—"

        try:
            capital_val = float(row.capital_medio) if pd.notna(row.capital_medio) else None
            capital = (
                f"R$ {capital_val:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
                if capital_val is not None else "—"
            )
        except Exception:
            capital = "—"

        with col:
            st.markdown(
                f"<div style='border-left: 4px solid {cor}; padding-left: 12px; margin-bottom: 8px;'>"
                f"<span style='font-size:1.2rem; font-weight:700; color:{cor}'>{regiao}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.metric(label="🏢 Total de empresas", value=total)
            st.metric(label="💰 Capital médio", value=capital)
            # pct_ativas só existe no modo UF (vem de mv_kpis_uf).
            pct = getattr(row, "pct_ativas", None)
            if pct is not None and pd.notna(pct):
                st.metric(label="✅ Empresas ativas", value=f"{float(pct):.1f}%")
            st.divider()


def _render_bar_capital(df_kpi: pd.DataFrame, col_regiao: str, cores: dict[str, str]) -> None:
    """Bar chart horizontal comparando capital médio entre regiões."""
    df_plot = df_kpi[[col_regiao, "capital_medio"]].dropna(subset=["capital_medio"])
    df_plot = df_plot.sort_values("capital_medio", ascending=True)

    fig = go.Figure(go.Bar(
        x=df_plot["capital_medio"],
        y=df_plot[col_regiao],
        orientation="h",
        marker_color=[cores.get(r, PALETA[0]) for r in df_plot[col_regiao]],
        text=df_plot["capital_medio"].map(lambda v: f"R$ {v:,.0f}"),
        textposition="outside",
    ))
    fig.update_layout(
        title="Capital Social Médio (R$)",
        xaxis_title="",
        yaxis_title="",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        height=280,
        margin=dict(l=0, r=80, t=40, b=20),
        font=dict(size=13),
    )
    fig.update_xaxes(showticklabels=False, showgrid=False)
    st.plotly_chart(fig, width="stretch")


def _render_heatmap_setores(df_setores: pd.DataFrame, col_regiao: str) -> None:
    """
    Heatmap de distribuição percentual por setor.
    Linhas = setores, Colunas = regiões.
    """
    pivot = df_setores.pivot_table(
        index="setor",
        columns=col_regiao,
        values="pct",
        aggfunc="sum",
        fill_value=0,
    )
    # Ordena setores pelo total entre regiões (mais relevantes no topo)
    pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]

    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=pivot.columns.tolist(),
        y=pivot.index.tolist(),
        colorscale="Blues",
        text=[[f"{v:.1f}%" for v in row] for row in pivot.values],
        texttemplate="%{text}",
        showscale=True,
        colorbar=dict(title="% do total"),
    ))
    fig.update_layout(
        title="Distribuição Setorial (% do total de empresas da região)",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=380,
        margin=dict(l=0, r=0, t=40, b=20),
        font=dict(size=12),
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, width="stretch")


def _render_linha_crescimento(
    df_cresc: pd.DataFrame,
    col_regiao: str,
    cores: dict[str, str],
) -> None:
    """Line chart de aberturas de empresas ao longo do tempo por região."""
    fig = px.line(
        df_cresc,
        x="ano",
        y="aberturas",
        color=col_regiao,
        color_discrete_map=cores,
        markers=True,
        title="Aberturas de Empresas por Ano",
        labels={"ano": "Ano", "aberturas": "Novas aberturas", col_regiao: "Região"},
    )
    fig.update_traces(line_width=2.5, marker_size=5)
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=350,
        margin=dict(l=0, r=0, t=40, b=20),
        font=dict(size=13),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
    st.plotly_chart(fig, width="stretch")


def _is_dark() -> bool:
    """Verifica tema atual do session_state (compatível com toggle do dashboard)."""
    return st.session_state.get("tema_escuro", False)


# ---------------------------------------------------------------------------
# Página principal — ponto de entrada
# ---------------------------------------------------------------------------

def render_comparador() -> None:
    """
    Renderiza a página completa do Comparador Regional.
    Chame esta função em app.py na navegação de páginas.
    """
    st.title("🗺️ Comparador Regional")
    st.caption(
        "Compare perfis empresariais entre estados (UF) ou municípios — "
        "capital médio, taxa de atividade, setores dominantes e crescimento histórico."
    )
    st.divider()

    # ------------------------------------------------------------------
    # Controles
    # ------------------------------------------------------------------
    col_modo, col_ano = st.columns([2, 1])

    with col_modo:
        modo = st.radio(
            "Comparar por:",
            options=["UF (Estado)", "Município"],
            horizontal=True,
            key="comparador_modo",
        )

    with col_ano:
        # O teto acompanha a competência: estava fixo em 2023 e ficava
        # defasado a cada recarga da base.
        ano_inicio = st.slider(
            "Período (crescimento)",
            min_value=1990,
            max_value=ULTIMO_ANO_COMPLETO,
            value=min(2005, ULTIMO_ANO_COMPLETO),
            key="comparador_ano_inicio",
        )

    # ------------------------------------------------------------------
    # Seleção de regiões
    # ------------------------------------------------------------------
    if modo == "UF (Estado)":
        df_ufs = _carregar_ufs_disponiveis()
        opcoes = dict(zip(df_ufs["label"], df_ufs["uf"]))

        selecao_labels = st.multiselect(
            "Selecione 2 a 4 estados para comparar:",
            options=list(opcoes.keys()),
            default=list(opcoes.keys())[:2],
            max_selections=4,
            key="comparador_ufs",
            help="Ordenados por volume de empresas.",
        )

        if len(selecao_labels) < 2:
            st.info("📌 Selecione pelo menos 2 estados para ativar a comparação.")
            return

        siglas = tuple(opcoes[l] for l in selecao_labels)
        cores = dict(zip(siglas, PALETA[: len(siglas)]))

        # Queries
        with st.spinner("Consultando PostgreSQL..."):
            df_kpi = _kpis_por_uf(siglas)
            df_setores = _setores_por_uf(siglas)
            df_cresc = _crescimento_por_uf(siglas, ano_inicio)

        col_regiao = "sigla_uf"
        col_kpi_regiao = "sigla_uf"

    else:  # Município
        df_muns = _carregar_municipios_top(100)
        opcoes_mun = dict(zip(df_muns["label"], df_muns["cod_municipio"]))

        selecao_labels = st.multiselect(
            "Selecione 2 a 4 municípios para comparar (top 100 por volume):",
            options=list(opcoes_mun.keys()),
            default=list(opcoes_mun.keys())[:2],
            max_selections=4,
            key="comparador_municipios",
        )

        if len(selecao_labels) < 2:
            st.info("📌 Selecione pelo menos 2 municípios para ativar a comparação.")
            return

        cod_selecionados = tuple(int(opcoes_mun[l]) for l in selecao_labels)

        with st.spinner("Consultando PostgreSQL..."):
            df_kpi = _kpis_por_municipio(cod_selecionados)
            df_setores = _setores_por_municipio(cod_selecionados)
            df_cresc = _crescimento_por_municipio(cod_selecionados, ano_inicio)

        col_regiao = "nome_municipio"
        col_kpi_regiao = "nome_municipio"
        nomes = df_kpi[col_kpi_regiao].tolist()
        cores = dict(zip(nomes, PALETA[: len(nomes)]))

    # ------------------------------------------------------------------
    # Validação de dados
    # ------------------------------------------------------------------
    if df_kpi.empty:
        st.warning("Nenhum dado encontrado para a seleção. Tente outras regiões.")
        return

    # ------------------------------------------------------------------
    # Bloco 1 — KPI Cards
    # ------------------------------------------------------------------
    st.subheader("📊 Visão Geral")
    _render_kpi_cards(df_kpi, col_kpi_regiao, cores)

    st.divider()

    # ------------------------------------------------------------------
    # Bloco 2 — Capital Médio + Heatmap Setores (lado a lado)
    # ------------------------------------------------------------------
    col_bar, col_heat = st.columns([1, 2])

    with col_bar:
        st.subheader("💰 Capital Médio")
        _render_bar_capital(df_kpi, col_kpi_regiao, cores)

    with col_heat:
        st.subheader("🏭 Perfil Setorial")
        if df_setores.empty:
            st.info("Dados de CNAE não disponíveis para a seleção.")
        else:
            _render_heatmap_setores(df_setores, col_regiao)

    st.divider()

    # ------------------------------------------------------------------
    # Bloco 3 — Crescimento temporal
    # ------------------------------------------------------------------
    st.subheader("📈 Evolução de Aberturas")
    if df_cresc.empty:
        st.info("Dados de crescimento não encontrados nas views de crescimento.")
    else:
        _render_linha_crescimento(df_cresc, col_regiao, cores)
        st.caption(
            f"Série de {ano_inicio} a {ULTIMO_ANO_COMPLETO}. A base é a competência "
            f"de {COMPETENCIA:%m/%Y}, então {COMPETENCIA.year} tem apenas os primeiros "
            "meses registrados — incluí-lo desenharia uma queda que não existe."
        )

    st.divider()

    # ------------------------------------------------------------------
    # Bloco 4 — Exportação
    # ------------------------------------------------------------------
    with st.expander("⬇️ Exportar dados desta comparação"):
        col_exp1, col_exp2 = st.columns(2)

        with col_exp1:
            csv_kpi = df_kpi.to_csv(index=False, encoding="utf-8-sig", sep=";")
            st.download_button(
                label="📥 KPIs — CSV",
                data=csv_kpi,
                file_name=f"comparador_kpis_{pd.Timestamp.now():%Y%m%d}.csv",
                mime="text/csv",
            )

        with col_exp2:
            csv_setores = df_setores.to_csv(index=False, encoding="utf-8-sig", sep=";")
            st.download_button(
                label="📥 Setores — CSV",
                data=csv_setores,
                file_name=f"comparador_setores_{pd.Timestamp.now():%Y%m%d}.csv",
                mime="text/csv",
            )
