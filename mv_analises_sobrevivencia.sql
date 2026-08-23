-- Análises novas que rodam sobre a empresas_gold ATUAL — não precisa recarregar
-- nada. Aplicar com:
--
--     python aplicar_indices.py mv_analises_sobrevivencia.sql
--
-- Conteúdo:
--   1. mv_sobrevivencia_faixas      → substitui o box plot por faixas legíveis
--   2. mv_coorte_sobrevivencia      → curva de sobrevivência por safra de abertura
--   3. mv_natalidade_mortalidade    → aberturas x baixas x saldo, por ano e UF
--   4. mv_capital_ano_municipio     → substitui o gráfico de bolhas por um heatmap
--
-- É idempotente (DROP + CREATE em cada view), então pode rodar de novo com
-- segurança para pegar a view 4 se você já tinha aplicado as três primeiras.


-- ---------------------------------------------------------------------------
-- 1. Distribuição do tempo de vida por setor, em faixas.
--
-- O box plot pedia que o leitor soubesse ler quartis e ainda empilhava sete
-- rótulos de hover por caixa. A pergunta que interessa — "quantas empresas
-- deste setor morrem antes de completar 3 anos?" — some no meio disso.
-- Faixas respondem essa pergunta direto, e ainda preservam a distribuição
-- inteira, que uma barra de mediana jogaria fora.
--
-- Corte em 1.000 baixadas por setor: abaixo disso os percentuais oscilam
-- demais para comparação.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_sobrevivencia_faixas;

CREATE MATERIALIZED VIEW mv_sobrevivencia_faixas AS
WITH duracao AS (
    SELECT
        e.cnae_fiscal,
        (e.data_situacao - e.data_abertura) / 365.25 AS anos
    FROM empresas_gold e
    WHERE e.situacao_cadastral = 8              -- 8 = baixada
      AND e.data_situacao IS NOT NULL
      AND e.data_situacao >= e.data_abertura
),
classificada AS (
    SELECT
        COALESCE(c.descricao, 'CNAE ' || d.cnae_fiscal::text) AS setor,
        CASE WHEN d.anos <  1 THEN 1
             WHEN d.anos <  3 THEN 2
             WHEN d.anos <  5 THEN 3
             WHEN d.anos < 10 THEN 4
             ELSE                  5 END                     AS faixa_ordem
    FROM duracao d
    LEFT JOIN cnaes_referencia c ON d.cnae_fiscal = c.codigo
),
agregada AS (
    SELECT setor, faixa_ordem, count(*) AS qtd
    FROM classificada
    GROUP BY setor, faixa_ordem
),
totais AS (
    SELECT setor, sum(qtd) AS total_setor
    FROM agregada
    GROUP BY setor
    HAVING sum(qtd) >= 1000
)
SELECT
    a.setor,
    a.faixa_ordem,
    CASE a.faixa_ordem WHEN 1 THEN 'Menos de 1 ano'
                       WHEN 2 THEN '1 a 3 anos'
                       WHEN 3 THEN '3 a 5 anos'
                       WHEN 4 THEN '5 a 10 anos'
                       ELSE         'Mais de 10 anos' END    AS faixa,
    a.qtd,
    t.total_setor,
    round(100.0 * a.qtd / t.total_setor, 2)                  AS pct
FROM agregada a
JOIN totais   t ON t.setor = a.setor;

CREATE UNIQUE INDEX uidx_mv_sobrevivencia_faixas
    ON mv_sobrevivencia_faixas (setor, faixa_ordem);


-- ---------------------------------------------------------------------------
-- 2. Curva de sobrevivência por coorte (safra de abertura).
--
-- É o recorte que o IBGE usa na Demografia das Empresas: pega TODAS as
-- empresas abertas num ano e acompanha quantas seguem vivas 1, 2, 5, 10 anos
-- depois. Diferente da distribuição de tempo de vida das baixadas, aqui as
-- empresas ainda ativas entram na conta — são justamente as sobreviventes.
--
-- Guardar a curva pronta custaria caro. O que fica materializado é o histograma
-- (coorte x ano em que a baixa ocorreu); a curva acumulada sai disso com uma
-- soma cumulativa barata no dashboard.
--
-- ano_baixa = -1 marca empresa NÃO baixada até a competência. Usar -1 em vez de
-- NULL permite índice único e REFRESH CONCURRENTLY.
--
-- ATENÇÃO à janela de observação: a coorte de 2020 só pode ser acompanhada por
-- 5 anos numa base de 2026. Ler "sobrevivência aos 10 anos" dela daria 100%
-- por falta de tempo decorrido, não por mérito. O dashboard corta cada curva
-- na janela que a safra realmente permite observar.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_coorte_sobrevivencia;

CREATE MATERIALIZED VIEW mv_coorte_sobrevivencia AS
SELECT
    EXTRACT(YEAR FROM data_abertura)::int AS coorte,
    COALESCE(
        CASE WHEN situacao_cadastral = 8
                  AND data_situacao IS NOT NULL
                  AND data_situacao >= data_abertura
             THEN floor((data_situacao - data_abertura) / 365.25)::int
        END,
        -1
    )                                     AS ano_baixa,
    count(*)                              AS qtd
FROM empresas_gold
WHERE data_abertura >= DATE '1990-01-01'
GROUP BY 1, 2;

CREATE UNIQUE INDEX uidx_mv_coorte_sobrevivencia
    ON mv_coorte_sobrevivencia (coorte, ano_baixa);


-- ---------------------------------------------------------------------------
-- 3. Natalidade x mortalidade por ano e UF.
--
-- Aberturas contadas por data_abertura; baixas por data_situacao das empresas
-- com situação 'baixada'. O saldo é a diferença.
--
-- Limite metodológico que precisa ir na legenda: isto é uma FOTO da base atual,
-- não um registro histórico de eventos. Empresas que a Receita já removeu do
-- cadastro não aparecem em ano nenhum, e o efeito é maior nos anos antigos.
-- A série é confiável para tendência recente; para as décadas de 1990 e 2000
-- ela subestima tanto aberturas quanto baixas.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_natalidade_mortalidade;

CREATE MATERIALIZED VIEW mv_natalidade_mortalidade AS
WITH aberturas AS (
    SELECT EXTRACT(YEAR FROM data_abertura)::int AS ano, uf, count(*) AS qtd
    FROM empresas_gold
    WHERE data_abertura IS NOT NULL
      AND uf IS NOT NULL AND uf <> ''
    GROUP BY 1, 2
),
baixas AS (
    SELECT EXTRACT(YEAR FROM data_situacao)::int AS ano, uf, count(*) AS qtd
    FROM empresas_gold
    WHERE situacao_cadastral = 8
      AND data_situacao IS NOT NULL
      AND uf IS NOT NULL AND uf <> ''
    GROUP BY 1, 2
)
SELECT
    COALESCE(a.ano, b.ano)                    AS ano,
    COALESCE(a.uf,  b.uf)                     AS uf,
    COALESCE(a.qtd, 0)                        AS aberturas,
    COALESCE(b.qtd, 0)                        AS baixas,
    COALESCE(a.qtd, 0) - COALESCE(b.qtd, 0)   AS saldo
FROM aberturas a
FULL OUTER JOIN baixas b ON a.ano = b.ano AND a.uf = b.uf
WHERE COALESCE(a.ano, b.ano) BETWEEN 1990 AND 2100;

CREATE UNIQUE INDEX uidx_mv_natalidade_mortalidade
    ON mv_natalidade_mortalidade (ano, uf);


-- ---------------------------------------------------------------------------
-- 4. Capital social por ano de abertura e município.
--
-- Substitui o gráfico de bolhas, que era ilegível por três motivos somados:
--
--   a) o filtro vinha com os 20 PRIMEIROS municípios em ordem alfabética
--      (ABADIA DE GOIAS, ABADIANIA, ABAETE…) — cidades minúsculas escolhidas
--      pelo alfabeto, não pela relevância;
--   b) 20 séries com capital parecido viravam um borrão de bolhas empilhadas
--      no mesmo ponto, uma por ano;
--   c) o eixo Y usava capital social MÉDIO, que em capital social é quase uma
--      medida de outlier: um punhado de holdings bilionárias define a escala
--      inteira e joga todo o resto para a linha do zero.
--
-- Aqui a métrica é a MEDIANA, que descreve a empresa típica do município, e a
-- forma é uma matriz ano x município — sem sobreposição possível.
--
-- HAVING >= 50 corta células onde a mediana oscilaria por acaso.
--
-- Custo: percentile_cont exige ordenar dentro de cada grupo. É a view mais
-- pesada do projeto (alguns minutos numa base de 66 M). Roda uma vez só.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_capital_ano_municipio;

CREATE MATERIALIZED VIEW mv_capital_ano_municipio AS
SELECT
    EXTRACT(YEAR FROM e.data_abertura)::int             AS ano,
    COALESCE(m.descricao, e.cod_municipio::text)        AS cidade,
    count(*)                                            AS empresas,
    round(percentile_cont(0.50) WITHIN GROUP (ORDER BY e.capital_social)::numeric,
          2)                                            AS capital_mediano,
    round(avg(e.capital_social)::numeric, 2)            AS capital_medio
FROM empresas_gold e
LEFT JOIN municipios_referencia m ON m.codigo = e.cod_municipio
WHERE e.capital_social > 0
  -- Fora os R$ 999.999.999.999,00 de preenchimento do arquivo da Receita. A
  -- mediana já era quase imune a eles; a média não era nem um pouco.
  AND NOT e.capital_sentinela
  AND e.cod_municipio IS NOT NULL
  AND e.data_abertura >= DATE '2005-01-01'
GROUP BY 1, 2
HAVING count(*) >= 50;

CREATE UNIQUE INDEX uidx_mv_capital_ano_municipio
    ON mv_capital_ano_municipio (ano, cidade);
