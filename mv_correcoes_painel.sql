-- Views materializadas que corrigem os quatro bugs de apresentação do dashboard.
--
-- Aplicar com:
--     python aplicar_indices.py mv_correcoes_painel.sql
--
-- Pré-requisito: empresas_gold reconstruída pelo etl_cnpj.py (precisa das
-- colunas uf, situacao_cadastral e data_situacao). Se alguma view falhar com
-- "column uf does not exist", a Gold ainda é a antiga — rode o ETL primeiro.


-- ---------------------------------------------------------------------------
-- 1. Sobrevivência NACIONAL — uma linha, sobre TODAS as baixadas.
--
-- O dashboard vinha estimando a mediana nacional como média ponderada das
-- medianas dos 20 setores mais longevos (mv_sobrevivencia_setor ORDER BY
-- mediana DESC LIMIT 20). Dois erros somados:
--   a) viés de seleção — só os setores de maior sobrevivência entravam;
--   b) média de medianas não é mediana.
-- Resultado: 20,9 anos, contra os 3,3 anos reais apurados em metricas_post.py.
--
-- A mediana nacional só pode sair de um percentile_cont sobre o conjunto
-- inteiro. É o que esta view faz.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_sobrevivencia_geral;

CREATE MATERIALIZED VIEW mv_sobrevivencia_geral AS
WITH duracao AS (
    SELECT (data_situacao - data_abertura) / 365.25 AS anos
    FROM empresas_gold
    WHERE situacao_cadastral = 8              -- 8 = baixada
      AND data_situacao IS NOT NULL
      AND data_situacao >= data_abertura      -- descarta datas invertidas
)
SELECT
    count(*)                                                              AS total,
    round(avg(anos)::numeric, 2)                                          AS media,
    round(percentile_cont(0.50) WITHIN GROUP (ORDER BY anos)::numeric, 2) AS mediana,
    round(percentile_cont(0.25) WITHIN GROUP (ORDER BY anos)::numeric, 2) AS q1,
    round(percentile_cont(0.75) WITHIN GROUP (ORDER BY anos)::numeric, 2) AS q3,
    round(percentile_cont(0.90) WITHIN GROUP (ORDER BY anos)::numeric, 2) AS p90
FROM duracao;

CREATE UNIQUE INDEX uidx_mv_sobrevivencia_geral ON mv_sobrevivencia_geral (total);


-- ---------------------------------------------------------------------------
-- 2. KPIs por UF — usando a coluna uf de verdade.
--
-- comparador_regional.py derivava a UF de LEFT(cod_municipio::text, 2). Isso
-- está errado: o campo "municipio" da Receita NÃO é o código IBGE, é o código
-- interno da RFB (São Paulo = 7107, Rio = 6001). Por isso o comparador exibia
-- "Grupo 71" e "Grupo 60" em vez de SP e RJ — 71 e 60 não existem no mapa de
-- UFs do IBGE, então o .fillna() devolvia o número cru.
--
-- A empresas_gold reconstruída já traz st.uf, a sigla oficial. É ela que vale.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_kpis_uf;

CREATE MATERIALIZED VIEW mv_kpis_uf AS
SELECT
    uf,
    count(*)                                                             AS total_empresas,
    -- Média e mediana lado a lado, ambas sem os valores-sentinela. O
    -- comparador exibe a MEDIANA: a média de capital social é dominada pelas
    -- poucas empresas do topo e diz mais sobre onde estão as holdings do que
    -- sobre o porte típico do estado.
    round(avg(capital_social) FILTER
          (WHERE capital_social > 0 AND NOT capital_sentinela)::numeric,
          2)                                                             AS capital_medio,
    round(percentile_cont(0.50) WITHIN GROUP (ORDER BY capital_social) FILTER
          (WHERE capital_social > 0 AND NOT capital_sentinela)::numeric,
          2)                                                             AS capital_mediano,
    round(100.0 * count(*) FILTER (WHERE situacao_cadastral = 2)
          / count(*), 2)                                                 AS pct_ativas
FROM empresas_gold
WHERE uf IS NOT NULL AND uf <> ''
GROUP BY uf;

CREATE UNIQUE INDEX uidx_mv_kpis_uf ON mv_kpis_uf (uf);


-- ---------------------------------------------------------------------------
-- 3. Série de aberturas por UF e ano — também pela uf real.
-- Substitui o SUM sobre mv_crescimento_municipio agrupado por LEFT(cod,2).
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_crescimento_uf;

CREATE MATERIALIZED VIEW mv_crescimento_uf AS
SELECT
    uf,
    EXTRACT(YEAR FROM data_abertura)::int AS ano,
    count(*)                              AS aberturas
FROM empresas_gold
WHERE data_abertura IS NOT NULL
  AND uf IS NOT NULL AND uf <> ''
GROUP BY uf, EXTRACT(YEAR FROM data_abertura)::int;

CREATE UNIQUE INDEX uidx_mv_crescimento_uf ON mv_crescimento_uf (uf, ano);


-- ---------------------------------------------------------------------------
-- 4. Painel principal pré-agregado — mata o LIMIT 30000.
--
-- O bloco "Análise Estratégica" puxava 30 mil linhas cruas para o pandas e
-- agregava lá. Sem ORDER BY, essas 30 mil linhas são apenas as que o plano de
-- execução devolver primeiro — não um sorteio. O painel mostrava linha reta
-- com um pico único e pizza dominada por PERDIGÃO e NOVA MUTUM: recorte
-- enviesado para o topo do capital social, não o Brasil.
--
-- Estas duas views devolvem o mesmo painel sobre a base inteira, em ~50 ms.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_painel_ano;

-- `FILTER (WHERE NOT capital_sentinela)` exclui os R$ 999.999.999.999,00 do
-- arquivo da Receita — doze noves, campo preenchido até estourar.
-- 169 dessas linhas, em 66,7 milhões, respondiam por 56,6% do capital
-- declarado do país: davam 67% do capital nacional a uma única cidade no donut
-- e punham a média de capital na casa dos milhões.
--
-- A empresa continua contada em `empresas`. O que sai da conta é o valor.
CREATE MATERIALIZED VIEW mv_painel_ano AS
SELECT
    EXTRACT(YEAR FROM data_abertura)::int                    AS ano,
    count(*)                                                 AS empresas,
    COALESCE(sum(capital_social)
             FILTER (WHERE NOT capital_sentinela), 0)        AS capital_total,
    count(*) FILTER (WHERE NOT capital_sentinela)            AS empresas_com_capital,
    -- Mediana, não média. Capital social tem cauda pesadíssima: a média
    -- descreve o topo da distribuição, não o país. A mesma decisão já valia
    -- para mv_capital_ano_municipio; aqui ela vira a métrica do painel.
    round(percentile_cont(0.50) WITHIN GROUP (ORDER BY capital_social)
          FILTER (WHERE NOT capital_sentinela)::numeric, 2)  AS capital_mediano,
    count(*) FILTER (WHERE capital_sentinela)                AS sentinelas
FROM empresas_gold
WHERE capital_social > 0
  AND data_abertura IS NOT NULL
GROUP BY EXTRACT(YEAR FROM data_abertura)::int;

CREATE UNIQUE INDEX uidx_mv_painel_ano ON mv_painel_ano (ano);


DROP MATERIALIZED VIEW IF EXISTS mv_painel_cidade;

CREATE MATERIALIZED VIEW mv_painel_cidade AS
SELECT
    COALESCE(m.descricao, e.cod_municipio::text)             AS cidade,
    count(*)                                                 AS empresas,
    COALESCE(sum(e.capital_social)
             FILTER (WHERE NOT e.capital_sentinela), 0)      AS capital_total,
    count(*) FILTER (WHERE e.capital_sentinela)              AS sentinelas
FROM empresas_gold e
LEFT JOIN municipios_referencia m ON m.codigo = e.cod_municipio
WHERE e.capital_social > 0
GROUP BY COALESCE(m.descricao, e.cod_municipio::text);

CREATE UNIQUE INDEX uidx_mv_painel_cidade ON mv_painel_cidade (cidade);
