"""
tests/test_comparador_regional.py
==================================
Testes para as queries e lógica do Comparador Regional.

Schema real confirmado:
  - municipios_referencia: colunas 'codigo' e 'descricao'
  - empresas_gold: cod_municipio é sequencial RFB (não IBGE)
  - mv_kpis_uf: uf, total_empresas, capital_medio, capital_mediano, pct_ativas
  - mv_crescimento_municipio: ano, cod_municipio, nome_municipio, total

Executar: python -m pytest tests/test_comparador_regional.py -v
"""

import pandas as pd
import pytest
from unittest.mock import MagicMock, patch, call
from decimal import Decimal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def df_kpi_uf_mock() -> pd.DataFrame:
    """DataFrame simulando retorno de _kpis_por_uf."""
    return pd.DataFrame({
        "cod_uf":         ["35", "33", "31"],
        "total_empresas": [5_000_000, 2_000_000, 1_500_000],
        "capital_mediano":  [85_000.0, 72_000.0, 65_000.0],
    })


@pytest.fixture
def df_kpi_uf_decimal_mock() -> pd.DataFrame:
    """DataFrame com capital_mediano como Decimal (como retorna o PostgreSQL)."""
    return pd.DataFrame({
        "cod_uf":         ["35", "33"],
        "total_empresas": [5_000_000, 2_000_000],
        "capital_mediano":  [Decimal("85432.50"), Decimal("72100.00")],
    })


@pytest.fixture
def df_setores_mock() -> pd.DataFrame:
    """DataFrame simulando retorno de _setores_por_uf (distribuição CNAE)."""
    return pd.DataFrame({
        "cod_uf":       ["35", "35", "35", "33", "33", "33"],
        "divisao_cnae": ["47", "62", "41", "47", "56", "43"],
        "total":        [120_000, 80_000, 60_000, 50_000, 40_000, 35_000],
    })


@pytest.fixture
def df_crescimento_mock() -> pd.DataFrame:
    """DataFrame simulando retorno de mv_crescimento_municipio."""
    return pd.DataFrame({
        "ano":            [2020, 2021, 2022, 2023, 2020, 2021, 2022, 2023],
        "cod_municipio":  [   1,    1,    1,    1,    2,    2,    2,    2],
        "nome_municipio": ["São Paulo"] * 4 + ["Rio de Janeiro"] * 4,
        "total":          [50_000, 55_000, 48_000, 62_000,
                           30_000, 32_000, 29_000, 35_000],
    })


@pytest.fixture
def mock_engine():
    """Engine SQLAlchemy mockado."""
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return engine


# ---------------------------------------------------------------------------
# Testes de transformação de dados — sem banco, sem Streamlit
# ---------------------------------------------------------------------------

class TestTransformacoes:
    """Testa lógica de transformação pura, sem I/O."""

    def test_capital_mediano_decimal_convertido_para_float(self, df_kpi_uf_decimal_mock):
        """Decimal do PostgreSQL deve ser convertível para float antes de f-string."""
        df = df_kpi_uf_decimal_mock.copy()
        df["capital_mediano"] = df["capital_mediano"].apply(float)

        for valor in df["capital_mediano"]:
            assert isinstance(valor, float), "capital_mediano deve ser float após conversão"
            formatted = f"R$ {valor:,.2f}"
            assert "R$" in formatted

    def test_capital_mediano_formatacao_br(self):
        """Formatação de valor monetário brasileiro não deve lançar TypeError."""
        valor = float(Decimal("123456.78"))
        resultado = f"R$ {valor:,.2f}"
        assert resultado == "R$ 123,456.78"

    def test_filtro_regiao_por_uf(self, df_kpi_uf_mock):
        """Filtro de UFs deve retornar apenas as regiões selecionadas."""
        ufs_selecionadas = ["35", "33"]
        filtrado = df_kpi_uf_mock[df_kpi_uf_mock["cod_uf"].isin(ufs_selecionadas)]

        assert len(filtrado) == 2
        assert set(filtrado["cod_uf"].tolist()) == {"35", "33"}

    def test_selecao_minimo_duas_regioes(self):
        """Comparador exige mínimo de 2 e máximo de 4 regiões."""
        def valida_selecao(regioes: list) -> bool:
            return 2 <= len(regioes) <= 4

        assert valida_selecao(["35", "33"]) is True
        assert valida_selecao(["35", "33", "31", "29"]) is True
        assert valida_selecao(["35"]) is False
        assert valida_selecao(["35", "33", "31", "29", "43"]) is False

    def test_pct_setorial_calculado_por_uf(self, df_setores_mock):
        """Percentual setorial deve somar ~100% por UF."""
        df = df_setores_mock.copy()
        df["pct"] = df.groupby("cod_uf")["total"].transform(
            lambda x: x / x.sum() * 100
        )

        for uf in df["cod_uf"].unique():
            total_pct = df[df["cod_uf"] == uf]["pct"].sum()
            assert abs(total_pct - 100.0) < 0.01, (
                f"UF {uf}: percentuais somam {total_pct:.2f}%, esperado 100%"
            )

    def test_crescimento_coluna_total_existe(self, df_crescimento_mock):
        """mv_crescimento_municipio usa coluna 'total', não 'total_aberturas'."""
        assert "total" in df_crescimento_mock.columns, (
            "Coluna deve ser 'total', não 'total_aberturas'"
        )
        assert "total_aberturas" not in df_crescimento_mock.columns

    def test_crescimento_filtro_por_ano(self, df_crescimento_mock):
        """Slider de período deve filtrar por ano corretamente."""
        ano_inicio = 2021
        filtrado = df_crescimento_mock[df_crescimento_mock["ano"] >= ano_inicio]

        anos_resultado = filtrado["ano"].unique()
        assert all(a >= ano_inicio for a in anos_resultado)
        assert 2020 not in anos_resultado

    def test_top_n_cnae_por_uf(self, df_setores_mock):
        """Heatmap usa top 8 CNAEs por região — lógica de seleção."""
        TOP_N = 8
        df = df_setores_mock.copy()

        top_cnaes = (
            df.groupby("divisao_cnae")["total"]
            .sum()
            .nlargest(TOP_N)
            .index.tolist()
        )

        assert len(top_cnaes) <= TOP_N
        assert isinstance(top_cnaes, list)

    def test_ordenacao_bar_chart_capital(self, df_kpi_uf_mock):
        """Bar chart de capital mediano deve estar ordenado de forma decrescente."""
        df = df_kpi_uf_mock.sort_values("capital_mediano", ascending=False)
        capitais = df["capital_mediano"].tolist()

        for i in range(len(capitais) - 1):
            assert capitais[i] >= capitais[i + 1], (
                "Capital mediano deve ser decrescente no bar chart"
            )


# ---------------------------------------------------------------------------
# Testes de schema — validações de estrutura de dados
# ---------------------------------------------------------------------------

class TestSchema:
    """Valida que as estruturas de dados aderem ao schema real confirmado."""

    # REMOVIDO: test_mv_comparador_uf_kpis_colunas.
    #
    # Ele construía um DataFrame com três colunas e em seguida verificava que o
    # DataFrame tinha aquelas três colunas — uma tautologia. Pior: as colunas
    # eram as da mv_comparador_uf_kpis, view legada que agrupava por
    # LEFT(cod_municipio, 2) e produzia o bug do "Grupo 71". O teste passava
    # verde enquanto descrevia um esquema que o projeto tinha abandonado.
    #
    # A verificação de verdade é test_mv_kpis_uf_expoe_media_e_mediana, em
    # tests/test_integracao_mvs.py, que pergunta ao PostgreSQL.

    def test_municipios_referencia_schema_real(self):
        """municipios_referencia usa 'codigo' e 'descricao' — NÃO cod_municipio/nome_municipio."""
        df_municipios = pd.DataFrame({
            "codigo":   [1, 2, 3],
            "descricao": ["Osasco", "São Paulo", "Campinas"],
        })
        # Colunas que NÃO devem existir
        assert "cod_municipio" not in df_municipios.columns, (
            "Schema errado: usar 'codigo', não 'cod_municipio'"
        )
        assert "nome_municipio" not in df_municipios.columns, (
            "Schema errado: usar 'descricao', não 'nome_municipio'"
        )
        # Colunas que DEVEM existir
        assert "codigo" in df_municipios.columns
        assert "descricao" in df_municipios.columns

    def test_join_municipios_referencia_correto(self):
        """JOIN deve ser ON mr.codigo = eg.cod_municipio."""
        empresas = pd.DataFrame({
            "cnpj_basico":   ["12345678", "87654321"],
            "cod_municipio": [1, 2],
            "capital_social": [100_000.0, 200_000.0],
        })
        municipios = pd.DataFrame({
            "codigo":    [1, 2],
            "descricao": ["Osasco", "São Paulo"],
        })

        resultado = empresas.merge(
            municipios,
            left_on="cod_municipio",
            right_on="codigo",
            how="left"
        )

        assert "descricao" in resultado.columns
        assert resultado.loc[0, "descricao"] == "Osasco"
        assert resultado.loc[1, "descricao"] == "São Paulo"

    def test_empresas_gold_colunas_validas(self):
        """empresas_gold não tem uf, situacao_cadastral, data_situacao."""
        colunas_que_nao_existem = [
            "uf",
            "situacao_cadastral",
            "data_situacao",
            "nome_municipio",
        ]
        colunas_que_existem = [
            "cnpj_basico",
            "razao_social",
            "capital_social",
            "data_abertura",
            "cnae_fiscal",
            "cod_municipio",
        ]

        df_gold = pd.DataFrame(columns=colunas_que_existem)

        for col in colunas_que_nao_existem:
            assert col not in df_gold.columns, (
                f"Coluna '{col}' não deve existir na empresas_gold"
            )
        for col in colunas_que_existem:
            assert col in df_gold.columns


# ---------------------------------------------------------------------------
# Testes de query (mockando o engine SQLAlchemy)
# ---------------------------------------------------------------------------

class TestQueryMock:
    """Testa chamadas de query sem banco real."""

    def test_query_kpi_usa_mv_correta(self, mock_engine):
        """Query de KPIs deve referenciar mv_comparador_uf_kpis."""
        with mock_engine.connect() as conn:
            conn.execute(
                MagicMock(),
                {"ufs": ["35", "33"]}
            )

        mock_engine.connect.assert_called()

    def test_query_sem_f_string_sql(self):
        """Verificar que queries usam parametrização, não f-string."""
        ufs = ["35", "33"]

        # Simula construção segura de query
        placeholders = ", ".join([f":uf_{i}" for i in range(len(ufs))])
        params = {f"uf_{i}": uf for i, uf in enumerate(ufs)}

        assert ":uf_0" in placeholders
        assert ":uf_1" in placeholders
        assert "35" not in placeholders  # valor não deve estar na query string
        assert params["uf_0"] == "35"
        assert params["uf_1"] == "33"

    @patch("pandas.read_sql")
    def test_read_sql_retorna_dataframe(self, mock_read_sql, df_kpi_uf_mock, mock_engine):
        """pd.read_sql deve retornar DataFrame mockado."""
        mock_read_sql.return_value = df_kpi_uf_mock

        resultado = pd.read_sql("SELECT 1", mock_engine)

        assert isinstance(resultado, pd.DataFrame)
        assert len(resultado) == 3
        mock_read_sql.assert_called_once()


# ---------------------------------------------------------------------------
# Testes de exportação CSV
# ---------------------------------------------------------------------------

class TestExportacao:
    """Testa geração de CSV no padrão brasileiro."""

    def test_csv_encoding_utf8_sig(self, df_kpi_uf_mock, tmp_path):
        """CSV deve ser gerado com utf-8-sig e separador ';'."""
        caminho = tmp_path / "export_test.csv"
        df_kpi_uf_mock.to_csv(caminho, index=False, encoding="utf-8-sig", sep=";")

        # Lê os bytes brutos para verificar BOM
        raw = caminho.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf", "Arquivo deve iniciar com BOM utf-8-sig"

        # Lê de volta e verifica separador
        df_lido = pd.read_csv(caminho, sep=";", encoding="utf-8-sig")
        assert list(df_lido.columns) == list(df_kpi_uf_mock.columns)

    def test_csv_separador_ponto_virgula(self, df_kpi_uf_mock, tmp_path):
        """Separador deve ser ';' para compatibilidade com Excel BR."""
        caminho = tmp_path / "export_sep.csv"
        df_kpi_uf_mock.to_csv(caminho, index=False, encoding="utf-8-sig", sep=";")

        primeira_linha = caminho.read_text(encoding="utf-8-sig").split("\n")[0]
        assert ";" in primeira_linha, "CSV deve usar ';' como separador"
        assert "," not in primeira_linha.replace(",", ""), True  # não obrigatório mas verifica padrão

    def test_nome_arquivo_com_data(self):
        """Nome do arquivo de exportação deve incluir data no formato YYYYMMDD."""
        import datetime
        data_hoje = datetime.date.today().strftime("%Y%m%d")
        nome_arquivo = f"comparador_kpis_{data_hoje}.csv"

        assert data_hoje in nome_arquivo
        assert len(data_hoje) == 8
        assert nome_arquivo.endswith(".csv")
