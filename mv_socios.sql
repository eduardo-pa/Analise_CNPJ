-- Sociedade e sobrevivência.
--
-- Depende das colunas qtd_socios / qtd_socios_pj / qtd_socios_pf, que só
-- existem depois de reconstruir a Gold com o Socios.zip carregado:
--
--     python etl_cnpj.py
--     python aplicar_indices.py mv_socios.sql
--
-- PRIVACIDADE: nada aqui identifica sócio. O arquivo Socios.zip da Receita
-- contém nome e CPF parcialmente mascarado de pessoas físicas; esses campos
-- ficam na tabela bronze e nunca chegam à Gold, que recebe apenas contagens.
-- Estas views agregam contagens de contagens.


-- ---------------------------------------------------------------------------
-- 1. Sobrevivência por faixa de número de sócios.
--
-- A faixa "Sem sócio registrado" não é dado faltante: empresário individual e
-- MEI legitimamente não têm registro de sócio no arquivo da Receita. É uma
-- categoria de verdade, e provavelmente a mais numerosa da base.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_sobrevivencia_socios;

CREATE MATERIALIZED VIEW mv_sobrevivencia_socios AS
WITH classificada AS (
    SELECT
        CASE WHEN qtd_socios = 0 THEN 0
             WHEN qtd_socios = 1 THEN 1
             WHEN qtd_socios = 2 THEN 2
             WHEN qtd_socios <= 5 THEN 3
             ELSE                     4 END AS faixa_ordem,
        situacao_cadastral,
        CASE WHEN situacao_cadastral = 8
                  AND data_situacao IS NOT NULL
                  AND data_situacao >= data_abertura
             THEN (data_situacao - data_abertura) / 365.25
        END AS anos
    FROM empresas_gold
)
SELECT
    faixa_ordem,
    CASE faixa_ordem WHEN 0 THEN 'Sem sócio registrado'
                     WHEN 1 THEN '1 sócio'
                     WHEN 2 THEN '2 sócios'
                     WHEN 3 THEN '3 a 5 sócios'
                     ELSE        '6 ou mais sócios' END               AS faixa,
    count(*)                                                          AS total_empresas,
    round(100.0 * count(*) FILTER (WHERE situacao_cadastral = 2)
          / count(*), 2)                                              AS pct_ativas,
    count(anos)                                                       AS baixadas_medidas,
    round(avg(anos)::numeric, 2)                                      AS media,
    round(percentile_cont(0.50) WITHIN GROUP (ORDER BY anos)::numeric, 2) AS mediana,
    round(100.0 * count(*) FILTER (WHERE anos < 5)
          / NULLIF(count(anos), 0), 2)                                AS pct_menos_5_anos
FROM classificada
GROUP BY faixa_ordem;

CREATE UNIQUE INDEX uidx_mv_sobrevivencia_socios
    ON mv_sobrevivencia_socios (faixa_ordem);


-- ---------------------------------------------------------------------------
-- 2. Curva de sobrevivência por safra E faixa de sócios.
--
-- Mesmo cuidado da comparação por regime tributário: sem fixar a safra, a
-- comparação mede idade, não sociedade. Sociedades com muitos sócios tendem a
-- ser mais antigas; olhar todas juntas faria o número de sócios parecer causa
-- de longevidade quando é só correlação com a época de abertura.
--
-- ano_baixa = -1 marca empresa não baixada até a competência.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_coorte_socios;

CREATE MATERIALIZED VIEW mv_coorte_socios AS
SELECT
    EXTRACT(YEAR FROM data_abertura)::int AS coorte,
    CASE WHEN qtd_socios = 0 THEN 0
         WHEN qtd_socios = 1 THEN 1
         WHEN qtd_socios = 2 THEN 2
         WHEN qtd_socios <= 5 THEN 3
         ELSE                     4 END   AS faixa_ordem,
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
WHERE data_abertura >= DATE '1996-01-01'
GROUP BY 1, 2, 3;

CREATE UNIQUE INDEX uidx_mv_coorte_socios
    ON mv_coorte_socios (coorte, faixa_ordem, ano_baixa);
