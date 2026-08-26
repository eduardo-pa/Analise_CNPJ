-- Base de demonstração — dados SINTÉTICOS.
--
-- Por que isto existe: o pipeline real depende de ~7 GB de arquivos da Receita
-- Federal e de ~75 GB de tabelas intermediárias. Ninguém avalia um projeto
-- esperando meia hora de download e uma hora de carga.
--
-- Este script cria uma `empresas_gold` com o MESMO ESQUEMA da real, populada
-- com 300 mil empresas geradas. Todas as views materializadas, todas as
-- consultas do dashboard e toda a suíte de testes rodam em cima dela sem
-- modificação — é o mesmo código exercitando dados de mentira.
--
-- OS NÚMEROS NÃO SIGNIFICAM NADA. Não cite nenhuma conclusão tirada daqui.
-- Para os números reais, rode o pipeline completo (veja o README).
--
-- Uso:  python demo/criar_demo.py

DROP TABLE IF EXISTS empresas_gold CASCADE;
DROP TABLE IF EXISTS cnaes_referencia CASCADE;
DROP TABLE IF EXISTS municipios_referencia CASCADE;
DROP TABLE IF EXISTS motivos_referencia CASCADE;

CREATE TABLE cnaes_referencia      (codigo bigint PRIMARY KEY, descricao text);
CREATE TABLE municipios_referencia (codigo bigint PRIMARY KEY, descricao text);
CREATE TABLE motivos_referencia    (codigo bigint PRIMARY KEY, descricao text);

-- CNAEs reais, com as descrições oficiais abreviadas.
INSERT INTO cnaes_referencia VALUES
 (4712100,'Comércio varejista de mercadorias em geral'),
 (5611201,'Restaurantes e similares'),
 (5611203,'Lanchonetes, casas de chá e de sucos'),
 (9602501,'Cabeleireiros, manicure e pedicure'),
 (4781400,'Comércio varejista de artigos do vestuário'),
 (4399103,'Obras de alvenaria'),
 (4930202,'Transporte rodoviário de carga'),
 (6201501,'Desenvolvimento de programas de computador'),
 (7319002,'Promoção de vendas'),
 (8599604,'Treinamento em desenvolvimento profissional'),
 (4771701,'Comércio varejista de produtos farmacêuticos'),
 (4530703,'Comércio a varejo de peças para veículos'),
 (9430800,'Atividades de associações de defesa de direitos'),
 (6920601,'Atividades de contabilidade'),
 (4120400,'Construção de edifícios');

-- Códigos no padrão da RFB (NÃO IBGE) — 7107 = São Paulo, 6001 = Rio.
INSERT INTO municipios_referencia VALUES
 (7107,'SAO PAULO'), (6001,'RIO DE JANEIRO'), (4123,'CURITIBA'),
 (8105,'PORTO ALEGRE'), (4557,'BELO HORIZONTE'), (2927,'SALVADOR'),
 (1301,'FORTALEZA'), (9701,'BRASILIA'), (7133,'CAMPINAS'),
 (2611,'RECIFE'), (5107,'GOIANIA'), (1100,'MANAUS');

INSERT INTO motivos_referencia VALUES
 (0,'SEM MOTIVO'),
 (1,'EXTINCAO POR ENCERRAMENTO LIQUIDACAO VOLUNTARIA'),
 (63,'OMISSAO DE DECLARACOES'),
 (71,'INAPTIDAO'),
 (73,'BAIXA A PEDIDO');

-- ---------------------------------------------------------------------------
-- A geração
--
-- Os parâmetros foram escolhidos para que as MVs produzam distribuições com
-- forma plausível — aberturas crescendo ao longo do tempo, sobrevivência com
-- cauda longa, capital social muito assimétrico. Nada aqui é uma estimativa
-- do Brasil real.
-- ---------------------------------------------------------------------------
CREATE TABLE empresas_gold AS
WITH base AS (
    SELECT
        i,
        -- Aberturas concentradas nas últimas décadas, com cauda até 1970.
        (DATE '2026-02-28' - (random() ^ 2.2 * 20400)::int) AS abertura,
        random() AS r_situacao,
        random() AS r_regime,
        random() AS r_vida,
        random() AS r_socios,
        random() AS r_capital,
        (ARRAY[7107,6001,4123,8105,4557,2927,1301,9701,7133,2611,5107,1100]
        )[1 + (random() * 11)::int] AS mun,
        (ARRAY[4712100,5611201,5611203,9602501,4781400,4399103,4930202,
               6201501,7319002,8599604,4771701,4530703,9430800,6920601,4120400]
        )[1 + (random() * 14)::int] AS cnae
    FROM generate_series(1, 300000) i
),
-- O capital sai num CTE próprio para que a MARCA de sentinela possa ser
-- derivada do VALOR, e não do sorteio que o produziu — ver o comentário em
-- capital_sentinela lá embaixo.
--
-- Ele usa r_capital, que vem do `base`, um por linha. Um `random()` dentro de
-- subconsulta não correlacionada seria avaliado UMA vez e replicado nas 300
-- mil linhas: foi assim que a primeira versão da contagem de sócios saiu com
-- zero em toda a base.
com_capital AS (
    SELECT
        b.*,
        -- 26% com capital zero; cauda longuíssima no topo, como na base real.
        --
        -- O ramo dos 0,05% reproduz o VALOR-SENTINELA: na base da Receita há
        -- 169 empresas com capital acima de R$ 500 bilhões, boa parte com
        -- exatamente R$ 999.999.999.999,00 — doze noves, o teto do campo. Não
        -- é capital, é preenchimento, e essas 169 linhas sozinhas respondiam
        -- por 56,6% do capital declarado do país.
        --
        -- Está aqui de propósito. Uma base de demonstração que só contém dado
        -- limpo não exercita o código que existe para lidar com dado sujo: os
        -- testes de integração passariam sem provar nada.
        CASE WHEN b.r_capital >= 0.9995 THEN 999999999999::numeric
             WHEN b.r_capital <  0.26   THEN 0::numeric
             WHEN b.r_capital >  0.995  THEN (random() * 900000000)::numeric(14,2)
             ELSE (random() * 90000)::numeric(14,2) END AS capital
    FROM base b
)
SELECT
    lpad(i::text, 8, '0')                                        AS cnpj_basico,
    'EMPRESA DEMONSTRACAO ' || i                                 AS razao_social,
    (ARRAY[2062, 2135, 2305, 4014])[1 + (random() * 3)::int]::bigint
                                                                 AS natureza_juridica,
    -- Porte declarado. As microempresas recebem de propósito uma fatia dos
    -- capitais absurdos: é a combinação "Microempresa + centenas de bilhões"
    -- que a tabela de maiores capitais exibe para explicar por que o campo não
    -- é confiável, e sem ela o teste que a defende não exercitaria nada.
    CASE WHEN r_capital >= 0.9995 THEN 'Microempresa'
         WHEN r_socios  <  0.46   THEN 'Microempresa'
         WHEN r_socios  <  0.70   THEN 'Pequeno porte'
         WHEN r_socios  <  0.95   THEN 'Demais'
         ELSE                          'Não informado' END       AS porte,
    capital                                                      AS capital_social,
    -- O MESMO limiar do etl_cnpj.py (LIMIAR_CAPITAL_SENTINELA), aplicado ao
    -- valor, não ao sorteio que o gerou. Derivar a marca do sorteio deixaria o
    -- seed sempre certo por construção e a regra do ETL nunca seria exercitada
    -- — inclusive os dois erros que esse limiar já teve: R$ 1 trilhão, que
    -- passava por baixo de um sentinela de 999.999.999.999, e R$ 500 bilhões,
    -- que deixava passar uma faixa inteira de valores igualmente impossíveis.
    (capital >= 250000000000::numeric)                           AS capital_sentinela,
    abertura                                                     AS data_abertura,
    cnae::bigint                                                 AS cnae_fiscal,
    mun::bigint                                                  AS cod_municipio,
    CASE mun WHEN 7107 THEN 'SP' WHEN 7133 THEN 'SP'
             WHEN 6001 THEN 'RJ' WHEN 4123 THEN 'PR'
             WHEN 8105 THEN 'RS' WHEN 4557 THEN 'MG'
             WHEN 2927 THEN 'BA' WHEN 1301 THEN 'CE'
             WHEN 9701 THEN 'DF' WHEN 2611 THEN 'PE'
             WHEN 5107 THEN 'GO' ELSE 'AM' END                   AS uf,
    -- 2 = ativa, 8 = baixada, 4 = inapta
    CASE WHEN r_situacao < 0.466 THEN 8
         WHEN r_situacao < 0.869 THEN 2
         ELSE 4 END                                              AS situacao_cadastral,
    -- Tempo de vida com cauda longa: a maioria morre cedo, poucas duram décadas.
    CASE WHEN r_situacao < 0.466
         THEN LEAST(abertura + (r_vida ^ 3 * 14000)::int, DATE '2026-02-28')
         ELSE NULL END                                           AS data_situacao,
    CASE WHEN r_situacao < 0.466
         THEN (ARRAY[1, 63, 71, 73])[1 + (random() * 3)::int]
         ELSE NULL END                                           AS motivo_situacao,
    -- REGIME TRIBUTÁRIO — duas colunas que parecem a mesma coisa e não são.
    --
    -- foi_simples / foi_mei     = aderiu em ALGUM momento (data_opcao_* na origem)
    -- opcao_simples / opcao_mei = é optante HOJE          (opcao_* na origem)
    --
    -- O seed reproduz duas patologias da fonte, ambas de propósito.
    --
    -- (1) Quem fecha sai do registro do Simples. Daí opcao_* virar false para
    --     toda empresa baixada. Sem isso o seed seria mais bem-comportado que a
    --     realidade e test_regime_nao_e_classificado_por_status_atual passaria
    --     mesmo com o bug de volta.
    --
    -- (2) O Simples.zip só contém quem aderiu ao Simples alguma vez — 47,2 M de
    --     linhas para 66,7 M de empresas, todas com data de adesão preenchida.
    --     Quem nunca aderiu simplesmente não está no arquivo. Por isso
    --     foi_simples aqui é NULL (ausente) ou true (aderiu), e NUNCA false:
    --     "está no arquivo mas sem data de adesão" não acontece na base real.
    --
    --     Os ~29% de NULL espelham a proporção real de empresas fora do
    --     arquivo. Esse grupo é o CONTROLE da comparação por regime, não dado
    --     faltando — e uma versão anterior do dashboard o descartava sob o
    --     rótulo "Não informado".
    --
    -- Uma base de demonstração honesta precisa saber mentir do mesmo jeito que
    -- a base de verdade mente.
    CASE WHEN r_regime < 0.29    THEN NULL
         WHEN r_situacao >= 0.466 AND r_regime < 0.80 THEN true
         ELSE false END                                          AS opcao_simples,
    CASE WHEN r_regime < 0.29    THEN NULL
         WHEN r_situacao >= 0.466 AND r_regime < 0.66 THEN true
         ELSE false END                                          AS opcao_mei,
    -- O histórico, esse a Receita preserva — inclusive para as baixadas.
    CASE WHEN r_regime < 0.29 THEN NULL ELSE true END            AS foi_simples,
    CASE WHEN r_regime < 0.29 THEN NULL
         WHEN r_regime < 0.66 THEN true
         ELSE false END                                          AS foi_mei,
    -- Contagem de sócios. Na base real vem do Socios.zip, agregado ANTES do
    -- join; aqui é gerado direto. Muitas empresas com zero: empresário
    -- individual e MEI não têm registro de sócio.
    --
    -- O sorteio (r_socios) vem do CTE `base`, um por linha. A primeira versão
    -- usava CROSS JOIN LATERAL com uma subconsulta que não referenciava `base`
    -- — ou seja, não era lateral de verdade. O PostgreSQL avaliou aquilo UMA
    -- vez e replicou o resultado, e as 300 mil empresas saíram com zero sócios.
    CASE WHEN r_socios < 0.46 THEN 0
         WHEN r_socios < 0.70 THEN 1
         WHEN r_socios < 0.90 THEN 2
         WHEN r_socios < 0.98 THEN 3
         ELSE                      6 END                         AS qtd_socios,
    CASE WHEN r_socios >= 0.70 AND r_socios < 0.72 THEN 1 ELSE 0 END AS qtd_socios_pj,
    CASE WHEN r_socios < 0.46 THEN 0
         WHEN r_socios < 0.70 THEN 1
         WHEN r_socios < 0.72 THEN 1
         WHEN r_socios < 0.90 THEN 2
         WHEN r_socios < 0.98 THEN 3
         ELSE                      6 END                         AS qtd_socios_pf
FROM com_capital;

ALTER TABLE empresas_gold ADD PRIMARY KEY (cnpj_basico);

-- Os mesmos índices que o etl_cnpj.py cria na base real.
CREATE INDEX idx_eg_cnae      ON empresas_gold (cnae_fiscal);
CREATE INDEX idx_eg_municipio ON empresas_gold (cod_municipio);
CREATE INDEX idx_eg_uf        ON empresas_gold (uf);
CREATE INDEX idx_eg_situacao  ON empresas_gold (situacao_cadastral);
CREATE INDEX idx_eg_data_abertura ON empresas_gold
    USING brin (data_abertura) WITH (pages_per_range = 128);
CREATE INDEX idx_eg_capital_positivo ON empresas_gold
    (capital_social DESC, cnae_fiscal, cod_municipio)
    WHERE capital_social > 0 AND NOT capital_sentinela;
CREATE INDEX idx_eg_regime ON empresas_gold (foi_mei, foi_simples);

ANALYZE empresas_gold;
