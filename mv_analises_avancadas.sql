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
-- em 100,0% aos 5 anos contra 48% do regime normal. Na base real, dos
-- 16.479.119 CNPJs marcados como MEI hoje, 229 constam como baixados.
--
-- foi_mei vem de data_opcao_mei, que a Receita preserva depois da exclusão e
-- depois da baixa. É o único dos dois que descreve o passado.
--
--
-- A TERCEIRA CATEGORIA É A AUSÊNCIA NO ARQUIVO, e isso não era óbvio.
--
-- O Simples.zip tem 47.184.414 linhas para uma base de 66,7 milhões de
-- empresas, e TODAS as 47 milhões trazem data_opcao_simples preenchida — zero
-- exceções. O arquivo não é um cadastro de "situação perante o Simples": é a
-- lista de quem aderiu ao Simples alguma vez. Quem nunca aderiu não está lá.
--
-- Duas consequências:
--
--   a) `foi_simples IS FALSE` é um conjunto VAZIO. A empresa ou está no
--      arquivo com data de adesão, ou não está no arquivo.
--   b) o grupo de comparação — empresa de lucro presumido ou real, que nunca
--      passou pelo Simples — é justamente o que a versão anterior descartava
--      como "Não informado". Eram 19,5 milhões de empresas jogadas fora por
--      um rótulo errado.
--
-- Por isso a ausência vira categoria própria, "Fora do Simples", em vez de
-- buraco. O informativo do etl_cnpj.py conta quantas linhas caem no ramo (a)
-- — se deixar de ser zero, a fonte mudou e este comentário está desatualizado.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_sobrevivencia_regime;

CREATE MATERIALIZED VIEW mv_sobrevivencia_regime AS
WITH classificada AS (
    SELECT
        CASE WHEN foi_mei     THEN 'MEI'
             WHEN foi_simples THEN 'Simples Nacional'
             ELSE                  'Fora do Simples' END AS regime,
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
    -- Mesmas três categorias da view 1, pelos mesmos motivos. Se alterar uma,
    -- altere a outra: test_coorte_regime_usa_as_mesmas_colunas falha se as
    -- duas discordarem sobre quem é MEI.
    CASE WHEN foi_mei     THEN 'MEI'
         WHEN foi_simples THEN 'Simples Nacional'
         ELSE                  'Fora do Simples' END AS regime,
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
