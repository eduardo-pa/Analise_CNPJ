-- Views materializadas de base do dashboard.
--
--     python aplicar_indices.py mv_base_dashboard.sql
--
-- Estas duas nasceram no banco de desenvolvimento e só existiam no
-- `views_materializadas.sql` — arquivo que o etl_cnpj.py REGENERA a cada carga,
-- lendo as definições do catálogo do PostgreSQL. Isso funcionava enquanto o
-- único caminho para o banco era rodar o ETL completo.
--
-- Quebrou quando a base de demonstração entrou: ela cria a Gold direto, sem
-- passar pelo ETL, então nada regenerava essas views. O dashboard subia com
-- três blocos em erro — e isso passou despercebido na minha máquina, porque lá
-- eu tinha rodado o views_materializadas.sql à mão em algum momento. O CI, que
-- parte de um banco vazio de verdade, pegou na primeira execução.
--
-- Definição versionada aqui, então, em vez de depender de um artefato gerado.
-- O `views_materializadas.sql` continua existindo como retrato do catálogo,
-- útil para restaurar um banco, mas não é mais fonte de verdade de nada.


-- ---------------------------------------------------------------------------
-- Aberturas por ano e município — alimenta o ranking de densidade e o gráfico
-- de crescimento por município.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_crescimento_municipio;

CREATE MATERIALIZED VIEW mv_crescimento_municipio AS
SELECT
    EXTRACT(YEAR FROM e.data_abertura)::int          AS ano,
    e.cod_municipio,
    COALESCE(m.descricao, e.cod_municipio::text)     AS nome_municipio,
    count(*)                                         AS total
FROM empresas_gold e
LEFT JOIN municipios_referencia m ON m.codigo = e.cod_municipio
WHERE e.data_abertura IS NOT NULL
  AND e.cod_municipio IS NOT NULL
  AND e.data_abertura >= DATE '1990-01-01'
GROUP BY 1, 2, 3;

CREATE UNIQUE INDEX uidx_mv_crescimento_municipio
    ON mv_crescimento_municipio (ano, cod_municipio);
CREATE INDEX idx_mv_crescimento_municipio_ano
    ON mv_crescimento_municipio (ano);


-- ---------------------------------------------------------------------------
-- Empresas e capital por divisão de CNAE (2 primeiros dígitos) — alimenta o
-- treemap de setores econômicos.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_treemap_setores;

CREATE MATERIALIZED VIEW mv_treemap_setores AS
SELECT
    LEFT(cnae_fiscal::text, 2)  AS divisao_cnae,
    count(*)                    AS total_empresas,
    sum(capital_social)         AS capital_total
FROM empresas_gold
WHERE capital_social >= 0
  AND cnae_fiscal IS NOT NULL
GROUP BY 1;

CREATE UNIQUE INDEX uidx_mv_treemap_setores
    ON mv_treemap_setores (divisao_cnae);
