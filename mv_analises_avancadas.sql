-- Análises que dependem das colunas NOVAS da Gold (opcao_simples, opcao_mei,
-- motivo_situacao). Só rode DEPOIS de reconstruir a base:
--
--     python carregar_referencias.py          (traz motivos_referencia)
--     set CNPJ_SKIP_BRONZE=1
--     python etl_cnpj.py                      (carrega o Simples e refaz a Gold)
--     python aplicar_indices.py mv_analises_avancadas.sql
--
-- Se rodar antes, cada view falha com "column opcao_simples does not exist" —
-- sem estragar nada do que já existe.


-- ---------------------------------------------------------------------------
-- 1. Sobrevivência por regime tributário.
--
-- A hipótese que dá para testar aqui: MEI é a porta de entrada mais barata do
-- país para formalizar um negócio. Barreira de entrada baixa costuma significar
-- também mortalidade mais alta — abre-se muito, e boa parte não vinga.
--
-- Regimes são mutuamente exclusivos na leitura: MEI é um recorte dentro do
-- Simples, então a ordem do CASE importa (MEI primeiro, senão todo MEI cairia
-- em "Simples").
--
-- A CLASSIFICAÇÃO USA foi_mei / foi_simples, NÃO opcao_mei / opcao_simples.
--
-- Esta linha custou um gráfico inteiro. `opcao_mei` é o status de hoje no
-- registro do Simples, e quem fecha sai do registro — então o grupo "MEI"
-- definido por ele contém apenas empresas vivas, e a sobrevivência dá 100% por
-- construção. Foi exatamente o que o dashboard exibiu: MEI e Simples parados
-- em 100,0% aos 5 anos contra 48% do regime normal.
--
-- foi_mei vem de data_opcao_mei, que a Receita preserva depois da exclusão e
-- depois da baixa. É o único dos dois que descreve o passado.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_sobrevivencia_regime;

CREATE MATERIALIZED VIEW mv_sobrevivencia_regime AS
WITH classificada AS (
    SELECT
        CASE WHEN foi_mei                THEN 'MEI'
             WHEN foi_simples            THEN 'Simples Nacional'
             WHEN foi_simples IS FALSE   THEN 'Regime normal'
             ELSE                             'Não informado' END AS regime,
        situacao_cadastral,
        CASE WHEN situacao_cadastral = 8
                  AND data_situacao IS NOT NULL
                  AND data_situacao >= data_abertura
             THEN (data_situacao - data_abertura) / 365.25
        END AS anos
    FROM empresas_gold
)
SELECT
    regime,
    count(*)                                                     AS total_empresas,
    count(*) FILTER (WHERE situacao_cadastral = 2)               AS ativas,
    round(100.0 * count(*) FILTER (WHERE situacao_cadastral = 2)
          / count(*), 2)                                         AS pct_ativas,
    count(anos)                                                  AS baixadas_medidas,
    round(avg(anos)::numeric, 2)                                 AS media,
    round(percentile_cont(0.50) WITHIN GROUP (ORDER BY anos)::numeric, 2) AS mediana,
    round(percentile_cont(0.25) WITHIN GROUP (ORDER BY anos)::numeric, 2) AS q1,
    round(percentile_cont(0.75) WITHIN GROUP (ORDER BY anos)::numeric, 2) AS q3,
    round(100.0 * count(*) FILTER (WHERE anos < 5)
          / NULLIF(count(anos), 0), 2)                           AS pct_menos_5_anos
FROM classificada
GROUP BY regime;

CREATE UNIQUE INDEX uidx_mv_sobrevivencia_regime
    ON mv_sobrevivencia_regime (regime);


-- ---------------------------------------------------------------------------
-- 2. Motivo da baixa.
--
-- "Empresa fechou" esconde coisas muito diferentes: encerramento voluntário é
-- decisão do dono; baixa por omissão de declarações é a Receita cancelando um
-- CNPJ abandonado. Tratar os dois como o mesmo evento distorce qualquer
-- conclusão sobre mortalidade empresarial.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_motivo_baixa;

CREATE MATERIALIZED VIEW mv_motivo_baixa AS
WITH baixadas AS (
    SELECT
        COALESCE(motivo_situacao, -1) AS motivo,
        CASE WHEN data_situacao IS NOT NULL AND data_situacao >= data_abertura
             THEN (data_situacao - data_abertura) / 365.25
        END AS anos
    FROM empresas_gold
    WHERE situacao_cadastral = 8
)
SELECT
    b.motivo,
    COALESCE(m.descricao, 'Motivo ' || b.motivo::text)            AS descricao,
    count(*)                                                      AS qtd,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)            AS pct,
    round(percentile_cont(0.50) WITHIN GROUP (ORDER BY b.anos)::numeric, 2) AS mediana_anos
FROM baixadas b
LEFT JOIN motivos_referencia m ON m.codigo = b.motivo
GROUP BY b.motivo, m.descricao;

CREATE UNIQUE INDEX uidx_mv_motivo_baixa ON mv_motivo_baixa (motivo);


-- ---------------------------------------------------------------------------
-- 3. Curva de sobrevivência por coorte E regime — a junção das duas análises.
--
-- Permite a pergunta que o TCC pode defender: dentro da MESMA safra de
-- abertura, o MEI sobrevive menos que o regime normal? Controlar pela safra
-- elimina o viés de o MEI ser um regime recente (criado em 2008): comparar MEI
-- e regime normal sem esse controle compararia empresas jovens com empresas
-- que tiveram décadas para se consolidar.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_coorte_regime;

CREATE MATERIALIZED VIEW mv_coorte_regime AS
SELECT
    EXTRACT(YEAR FROM data_abertura)::int AS coorte,
    -- foi_mei / foi_simples, pelo mesmo motivo da view 1 acima.
    CASE WHEN foi_mei              THEN 'MEI'
         WHEN foi_simples          THEN 'Simples Nacional'
         WHEN foi_simples IS FALSE THEN 'Regime normal'
         ELSE                           'Não informado' END AS regime,
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
WHERE data_abertura >= DATE '2009-01-01'   -- MEI existe desde 2008
GROUP BY 1, 2, 3;

CREATE UNIQUE INDEX uidx_mv_coorte_regime
    ON mv_coorte_regime (coorte, regime, ano_baixa);
