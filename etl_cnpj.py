"""
ETL CNPJ — Receita Federal
Substitui tratar_dados_final.py e tratar_dados.py.

Correções em relação à versão anterior:
  1. Lê os 10 shards de cada arquivo (antes: só o shard 0).
  2. Não assume que Empresas{N} corresponde a Estabelecimentos{N} — os arquivos
     são fatiados por critérios diferentes (Empresas por faixa de CNPJ,
     Estabelecimentos embaralhado). O join só é correto sobre a base completa.
  3. Chave normalizada com zfill(8), nunca lstrip('0').
  4. Uma linha por EMPRESA (filtra matriz), não por estabelecimento.
  5. Carrega situacao_cadastral e data_situacao — permite análise de sobrevivência.
  6. Portões de qualidade que abortam a carga em vez de gravar dado silenciosamente errado.
  7. Nenhuma data simulada. Nenhuma credencial em código.
  8. Carrega o Simples.zip (opção pelo Simples e pelo MEI) e o motivo da baixa —
     permite comparar sobrevivência por regime tributário e saber POR QUE as
     empresas fecham, não só quando.

Estratégia: Query-First. O join roda no Postgres, não em pandas — a base completa
não cabe em 16 GB de RAM. Os CSVs entram por COPY nas tabelas bronze.

Uso:
    As variáveis vêm do .env — não precisa exportar nada para a carga normal.

    python etl_cnpj.py

Para reaproveitar as bronze já carregadas e pular os ~30 min de COPY, defina
CNPJ_SKIP_BRONZE. A sintaxe MUDA conforme o terminal, e errar aqui não dá erro
nenhum: a variável simplesmente não chega ao Python e o ETL recarrega tudo.

    PowerShell (o prompt começa com "PS C:\\...")
        $env:CNPJ_SKIP_BRONZE = "1"
        python etl_cnpj.py

    Prompt de Comando (cmd.exe)
        set CNPJ_SKIP_BRONZE=1
        python etl_cnpj.py

No PowerShell, `set` é apelido de Set-Variable e cria uma variável do shell,
não do ambiente — `os.environ` não enxerga.

Para conferir antes de rodar:

    PowerShell:  $env:CNPJ_SKIP_BRONZE
    cmd.exe:     echo %CNPJ_SKIP_BRONZE%
"""

import os
import io
import sys
import glob
import time
import zipfile

import psycopg2
from dotenv import load_dotenv

# ----------------------------------------------------------------------------
# Configuração — nada hardcoded
# ----------------------------------------------------------------------------
load_dotenv()

try:
    DATABASE_URL = os.environ["DATABASE_URL"]
    CNPJ_DIR = os.environ["CNPJ_DIR"]
except KeyError as e:
    sys.exit(f"Variável de ambiente obrigatória ausente: {e}. Veja .env.example")

# Tablespace opcional para as tabelas bronze (~75 GB). Se o disco do Postgres
# não comportar, aponte CNPJ_TABLESPACE_DIR para um volume com espaço sobrando.
# A camada Gold (~9 GB) permanece no tablespace padrão.
TABLESPACE_DIR = os.environ.get("CNPJ_TABLESPACE_DIR")
TABLESPACE = "ts_cnpj_bronze" if TABLESPACE_DIR else None
CLAUSULA_TS = f" TABLESPACE {TABLESPACE}" if TABLESPACE else ""

# CNPJ_SKIP_BRONZE=1 reaproveita as bronze já carregadas. Útil ao iterar sobre
# a lógica da Gold sem repetir os ~30 min de COPY.
PULAR_BRONZE = os.environ.get("CNPJ_SKIP_BRONZE", "").strip() in ("1", "true", "True")

# Layout oficial da Receita Federal (posicional, sem cabeçalho)
DDL_EMPRESAS = """
CREATE UNLOGGED TABLE bronze_empresas (
    cnpj_basico                 text,
    razao_social                text,
    natureza_juridica           text,
    qualificacao_responsavel    text,
    capital_social              text,
    porte_empresa               text,
    ente_federativo_responsavel text
){ts};
"""

DDL_ESTABELECIMENTOS = """
CREATE UNLOGGED TABLE bronze_estabelecimentos (
    cnpj_basico                text, cnpj_ordem            text,
    cnpj_dv                    text, matriz_filial         text,
    nome_fantasia              text, situacao_cadastral    text,
    data_situacao              text, motivo_situacao       text,
    nome_cidade_exterior       text, pais                  text,
    data_inicio_atividade      text, cnae_fiscal           text,
    cnae_secundaria            text, tipo_logradouro       text,
    logradouro                 text, numero                text,
    complemento                text, bairro                text,
    cep                        text, uf                    text,
    municipio                  text, ddd_1                 text,
    telefone_1                 text, ddd_2                 text,
    telefone_2                 text, ddd_fax               text,
    fax                        text, correio_eletronico    text,
    situacao_especial          text, data_situacao_especial text
){ts};
"""

# Simples.zip — arquivo único (não é fatiado em 10 como Empresas/Estabelecimentos).
# Uma linha por cnpj_basico, com a adesão ao Simples Nacional e ao MEI.
DDL_SIMPLES = """
CREATE UNLOGGED TABLE bronze_simples (
    cnpj_basico            text,
    opcao_simples          text,   -- S / N
    data_opcao_simples     text,
    data_exclusao_simples  text,
    opcao_mei              text,   -- S / N
    data_opcao_mei         text,
    data_exclusao_mei      text
){ts};
"""

# Socios0..9.zip — uma linha por SÓCIO, várias por empresa.
#
# ATENÇÃO, e é o motivo de este comentário existir: este é o único arquivo do
# conjunto que contém dado de PESSOA FÍSICA — nome do sócio e CPF parcialmente
# mascarado. É público, mas isso não é licença para espalhá-lo.
#
# A regra deste projeto: nome e CPF ficam na bronze e não saem dali. A camada
# Gold recebe apenas CONTAGENS (quantos sócios, quantos são PJ, quantos são
# PF). Nenhuma view materializada, nenhum gráfico, nenhuma exportação e nenhuma
# instância publicada carrega identificação de sócio.
#
# As colunas de identificação são declaradas abaixo porque o COPY precisa
# consumir o CSV inteiro para posicionar os campos — não porque serão usadas.
# Depois que a Gold é construída, dá para descartar a bronze:
#     DROP TABLE bronze_socios;
DDL_SOCIOS = """
CREATE UNLOGGED TABLE bronze_socios (
    cnpj_basico                text,
    identificador_socio        text,   -- 1 = PJ, 2 = PF, 3 = estrangeiro
    nome_socio                 text,   -- pessoa física: não vai para a Gold
    cpf_cnpj_socio             text,   -- pessoa física: não vai para a Gold
    qualificacao_socio         text,
    data_entrada_sociedade     text,
    pais                       text,
    representante_legal        text,   -- CPF: não vai para a Gold
    nome_representante         text,   -- não vai para a Gold
    qualificacao_representante text,
    faixa_etaria               text
){ts};
"""


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def preparar_tablespace():
    """Cria o tablespace das bronze se CNPJ_TABLESPACE_DIR estiver definido.

    CREATE TABLESPACE não pode rodar dentro de bloco de transação, então usa
    uma conexão própria em autocommit, separada da transação principal.

    Antes de qualquer coisa, confere se a PASTA do tablespace está acessível.
    A versão anterior só olhava o catálogo: encontrava o tablespace registrado,
    logava "já existe" e seguia em frente. Se a pasta tivesse sumido — drive
    externo desconectado, letra de unidade trocada — o erro só aparecia lá na
    frente, no CREATE TABLE, e nesta forma:

        não foi possível criar o diretório "pg_tblspc/41458/PG_18_.../16388":
        No such file or directory

    Nada ali diz "conecte o drive". Este bloco diz.
    """
    if not TABLESPACE:
        log("Tablespace: padrão (bronze e gold no mesmo volume)")
        return

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_tablespace_location(oid) FROM pg_tablespace "
                "WHERE spcname = %s",
                (TABLESPACE,),
            )
            row = cur.fetchone()

            if row:
                registrado = row[0] or TABLESPACE_DIR
                # O caminho que vale é o gravado no catálogo: editar o .env não
                # move um tablespace já criado.
                if not os.path.isdir(registrado):
                    sys.exit(
                        f"\nO tablespace {TABLESPACE} existe no PostgreSQL e aponta para:\n"
                        f"    {registrado}\n"
                        f"...mas essa pasta não está acessível agora.\n\n"
                        f"Causa mais comum: o drive externo está desconectado, ou "
                        f"voltou com outra letra de unidade.\n\n"
                        f"Rode 'python diagnostico_tablespace.py' — ele confere as "
                        f"letras montadas, o caminho do catálogo e se as tabelas "
                        f"bronze ainda estão legíveis, e diz o que fazer em cada caso.\n"
                    )
                log(f"Tablespace {TABLESPACE} já existe em {registrado}")
                return

            if not os.path.isdir(TABLESPACE_DIR):
                sys.exit(
                    f"\nCNPJ_TABLESPACE_DIR aponta para uma pasta que não existe:\n"
                    f"    {TABLESPACE_DIR}\n\n"
                    f"Crie a pasta (VAZIA) e dê Controle Total à conta que roda o "
                    f"serviço do PostgreSQL, ou apague CNPJ_TABLESPACE_DIR do .env "
                    f"para deixar as bronze no volume padrão (~75 GB).\n"
                )

            caminho = TABLESPACE_DIR.replace("\\", "/")
            log(f"Criando tablespace {TABLESPACE} em {caminho}")
            try:
                cur.execute(f"CREATE TABLESPACE {TABLESPACE} LOCATION '{caminho}'")
            except psycopg2.Error as e:
                sys.exit(
                    f"Falha ao criar o tablespace: {e}\n"
                    f"Verifique se a pasta {TABLESPACE_DIR} existe, está VAZIA, e se a "
                    f"conta que roda o serviço do PostgreSQL tem Controle Total sobre ela."
                )
    finally:
        conn.close()


class SemBytesNulos(io.RawIOBase):
    """Remove bytes NUL (0x00) do stream durante o COPY.

    Os arquivos da Receita contêm NUL esparsos em campos de texto. O PostgreSQL
    rejeita 0x00 em colunas text sob qualquer encoding, então o COPY aborta.
    Filtrar aqui evita descompactar 30 GB em disco só para limpar.
    """

    TAMANHO_PADRAO = 1 << 20  # 1 MB

    def __init__(self, fh):
        self._fh = fh
        self.removidos = 0

    def readable(self):
        return True

    def read(self, size=-1):
        alvo = size if size and size > 0 else self.TAMANHO_PADRAO
        while True:
            bloco = self._fh.read(alvo)
            if not bloco:
                return b""              # EOF real
            if b"\x00" in bloco:
                self.removidos += bloco.count(b"\x00")
                bloco = bloco.replace(b"\x00", b"")
                if not bloco:
                    continue            # bloco era só NUL: lê o próximo
            return bloco


def copiar_shards(cur, padrao, tabela):
    """Streama cada ZIP direto para o Postgres via COPY. Sem pandas, sem RAM."""
    arquivos = sorted(glob.glob(os.path.join(CNPJ_DIR, padrao)))
    if not arquivos:
        sys.exit(f"Nenhum arquivo encontrado para o padrão {padrao} em {CNPJ_DIR}")

    log(f"{tabela}: {len(arquivos)} shards encontrados")
    total_nulos = 0
    for caminho in arquivos:
        nome = os.path.basename(caminho)
        with zipfile.ZipFile(caminho) as z:
            interno = z.namelist()[0]
            with z.open(interno) as bruto:
                fh = SemBytesNulos(bruto)
                cur.copy_expert(
                    f"COPY {tabela} FROM STDIN WITH "
                    "(FORMAT csv, DELIMITER ';', QUOTE '\"', ENCODING 'LATIN1')",
                    fh,
                )
                total_nulos += fh.removidos
        cur.execute(f"SELECT count(*) FROM {tabela}")
        log(f"  {nome} carregado — acumulado: {cur.fetchone()[0]:,}")

    if total_nulos:
        log(f"  {total_nulos:,} bytes NUL removidos na origem ({tabela})")


ARQUIVO_MVS = "views_materializadas.sql"


def bronze_pronta(cur):
    """Diz se as bronze já existem e estão populadas.

    bronze_simples e bronze_socios entram na checagem: quem já tinha as bronze
    carregadas de uma execução anterior NÃO tem essas tabelas. Sem isso,
    CNPJ_SKIP_BRONZE=1 pularia a carga e a Gold sairia sem Simples/MEI e sem a
    contagem de sócios — silenciosamente, porque o join é LEFT.
    """
    cur.execute("SELECT to_regclass('bronze_empresas'), "
                "to_regclass('bronze_estabelecimentos'), "
                "to_regclass('bronze_simples'), "
                "to_regclass('bronze_socios')")
    if any(t is None for t in cur.fetchone()):
        return False
    cur.execute("SELECT (SELECT count(*) FROM bronze_empresas) > 0 "
                "AND (SELECT count(*) FROM bronze_estabelecimentos) > 0 "
                "AND (SELECT count(*) FROM bronze_simples) > 0 "
                "AND (SELECT count(*) FROM bronze_socios) > 0")
    return cur.fetchone()[0]


def complementares_prontas(cur):
    """Só as bronze pequenas (Simples e Sócios).

    Permite carregar apenas elas quando Empresas e Estabelecimentos já estão no
    banco, poupando os ~30 min de COPY dos arquivos grandes.
    """
    for tabela in ("bronze_simples", "bronze_socios"):
        cur.execute("SELECT to_regclass(%s)", (tabela,))
        if cur.fetchone()[0] is None:
            return False
        cur.execute(f"SELECT count(*) > 0 FROM {tabela}")
        if not cur.fetchone()[0]:
            return False
    return True


def capturar_dependentes(cur):
    """Salva definição e índices das MVs que dependem de empresas_gold.

    As MVs precisam cair junto com a tabela (DROP CASCADE) e ser recriadas
    depois. Ler do catálogo garante que nada se perca, mesmo para views cuja
    definição não está versionada no repositório.
    """
    cur.execute("SELECT to_regclass('empresas_gold')")
    if cur.fetchone()[0] is None:
        return None

    cur.execute("""
        SELECT DISTINCT m.oid::regclass::text, pg_get_viewdef(m.oid, true)
        FROM pg_depend d
        JOIN pg_rewrite r ON r.oid = d.objid
        JOIN pg_class   m ON m.oid = r.ev_class
        WHERE d.refobjid = 'empresas_gold'::regclass
          AND m.relkind  = 'm'
          AND m.oid     <> 'empresas_gold'::regclass
        ORDER BY 1
    """)
    mvs = cur.fetchall()
    if not mvs:
        return None

    nomes = [n for n, _ in mvs]
    cur.execute("SELECT indexdef FROM pg_indexes WHERE tablename = ANY(%s)", (nomes,))
    indices = [r[0] for r in cur.fetchall()]

    log(f"Views materializadas dependentes: {', '.join(nomes)}")

    # Versiona as definições — três delas não tinham script no repositório.
    with open(ARQUIVO_MVS, "w", encoding="utf-8") as f:
        f.write("-- Gerado por etl_cnpj.py a partir do catálogo do PostgreSQL.\n")
        f.write("-- Recriado automaticamente após a reconstrução de empresas_gold.\n\n")
        for nome, definicao in mvs:
            f.write(f"CREATE MATERIALIZED VIEW {nome} AS\n{definicao}\n\n")
        for idx in indices:
            f.write(f"{idx};\n")
    log(f"Definições salvas em {ARQUIVO_MVS}")

    return mvs, indices


def recriar_dependentes(cur, capturado):
    if not capturado:
        return
    mvs, indices = capturado
    log("Recriando views materializadas")
    for nome, definicao in mvs:
        cur.execute(f"CREATE MATERIALIZED VIEW {nome} AS {definicao}")
        log(f"  {nome}")
    for idx in indices:
        cur.execute(idx)
    log(f"  {len(indices)} índices restaurados")


# Capital social acima deste valor não é capital: é campo preenchido.
#
# A base real traz milhares de empresas com exatamente R$ 999.999.999.999,00 —
# doze noves, o teto de um campo de 12 dígitos. Elas apareciam no topo de
# "Maiores Empresas", davam a uma única cidade 67% do capital do país e
# empurravam a média nacional de capital para a casa dos milhões.
#
# Para calibrar: a maior capitalização social legítima do Brasil está na ordem
# de R$ 200 bilhões (Petrobras). R$ 500 bilhões é mais que o dobro disso e
# ainda assim fica abaixo dos doze noves — o corte pega o preenchimento sem
# encostar em nenhuma empresa real.
#
# O primeiro valor escrito aqui foi R$ 1 trilhão, e ele não pegava nada:
# 999.999.999.999 é exatamente um centavo MENOR que um trilhão. Um limiar
# redondo escolhido "com folga" em cima de um sentinela que é o teto de um
# campo passa por baixo dele. A folga tem que ficar do lado de dentro.
#
# A linha NÃO é excluída da Gold — a empresa existe e conta em toda análise que
# não seja de capital. O que se marca é que o VALOR do capital é inutilizável.
LIMIAR_CAPITAL_SENTINELA = 500_000_000_000

SQL_GOLD = """
DROP TABLE IF EXISTS empresas_gold CASCADE;

CREATE TABLE empresas_gold AS
SELECT
    e.cnpj_basico,
    e.razao_social,
    NULLIF(e.natureza_juridica, '')::bigint                       AS natureza_juridica,
    COALESCE(replace(e.capital_social, ',', '.')::numeric, 0)     AS capital_social,
    -- Ver LIMIAR_CAPITAL_SENTINELA acima. Marca o valor como inutilizável sem
    -- apagá-lo e sem remover a empresa.
    (COALESCE(replace(e.capital_social, ',', '.')::numeric, 0)
        >= %LIMIAR%::numeric)                                     AS capital_sentinela,
    to_date(NULLIF(st.data_inicio_atividade, '0'), 'YYYYMMDD')    AS data_abertura,
    NULLIF(st.cnae_fiscal, '')::bigint                            AS cnae_fiscal,
    NULLIF(st.municipio, '')::bigint                              AS cod_municipio,
    st.uf,
    NULLIF(st.situacao_cadastral, '')::int                        AS situacao_cadastral,
    to_date(NULLIF(st.data_situacao, '0'), 'YYYYMMDD')            AS data_situacao,
    NULLIF(st.motivo_situacao, '')::int                           AS motivo_situacao,
    -- 'S'/'N' da Receita viram booleano; ausência no Simples.zip vira NULL
    -- (a empresa nunca foi avaliada), que é diferente de 'N' (avaliada e fora).
    --
    -- ATENÇÃO: estas duas colunas são o status de HOJE. Uma empresa que fechou
    -- deixa de ser optante, então `opcao_mei = true` seleciona, na prática,
    -- empresas vivas. Use-as para "quantos MEIs existem hoje" e NUNCA para
    -- segmentar sobrevivência — veja foi_simples/foi_mei logo abaixo.
    CASE si.opcao_simples WHEN 'S' THEN true WHEN 'N' THEN false END AS opcao_simples,
    CASE si.opcao_mei     WHEN 'S' THEN true WHEN 'N' THEN false END AS opcao_mei,
    -- Já foi optante ALGUM DIA. É o que a Receita guarda em data_opcao_*: a
    -- data de adesão continua preenchida depois da exclusão e depois da baixa.
    --
    -- Esta distinção não é preciosismo. O dashboard chegou a exibir uma curva
    -- de sobrevivência do MEI parada em 100,0% aos 5 anos, contra 48% do regime
    -- normal — o que parecia um achado espetacular e era só tautologia: o
    -- grupo tinha sido definido por uma condição que exige estar vivo.
    -- Segmentar o passado por um atributo do presente condiciona na
    -- sobrevivência. É o mesmo erro dos 44,5 anos, com outra roupa.
    --
    -- NULL quando a empresa não consta do arquivo do Simples: "nunca aderiu" e
    -- "não sabemos" são coisas diferentes e não devem virar o mesmo `false`.
    CASE WHEN si.cnpj_basico IS NULL THEN NULL
         ELSE si.data_opcao_simples IS NOT NULL END               AS foi_simples,
    CASE WHEN si.cnpj_basico IS NULL THEN NULL
         ELSE si.data_opcao_mei IS NOT NULL END                   AS foi_mei,
    -- Sócios entram só como CONTAGEM. Nome e CPF ficam na bronze.
    -- 0 não é ausência de dado: empresário individual e MEI legitimamente não
    -- têm registro de sócio no arquivo da Receita.
    COALESCE(so.qtd_socios, 0)    AS qtd_socios,
    COALESCE(so.qtd_socios_pj, 0) AS qtd_socios_pj,
    COALESCE(so.qtd_socios_pf, 0) AS qtd_socios_pf
FROM (
    SELECT lpad(cnpj_basico, 8, '0') AS cnpj_basico,
           razao_social, natureza_juridica, capital_social
    FROM bronze_empresas
) e
JOIN (
    -- matriz_filial = '1' garante uma linha por EMPRESA, não por estabelecimento
    SELECT lpad(cnpj_basico, 8, '0') AS cnpj_basico,
           data_inicio_atividade, cnae_fiscal, municipio, uf,
           situacao_cadastral, data_situacao, motivo_situacao
    FROM bronze_estabelecimentos
    WHERE matriz_filial = '1'
      -- Quarentena: a origem tem um punhado de datas impossíveis (ano 0,
      -- datas futuras). São descartadas aqui e contabilizadas no log, em vez
      -- de contaminarem médias e séries temporais na camada final.
      AND data_inicio_atividade ~ '^[0-9]{8}$'
      AND to_date(data_inicio_atividade, 'YYYYMMDD')
          BETWEEN DATE '1900-01-01' AND CURRENT_DATE
) st USING (cnpj_basico)
LEFT JOIN (
    -- GROUP BY colapsa eventuais duplicatas de cnpj_basico no Simples.zip.
    -- Sem isso, uma única linha repetida multiplicaria a empresa na Gold e
    -- derrubaria a PRIMARY KEY lá embaixo — falha barulhenta, mas depois de
    -- 40 minutos de join.
    --
    -- '0' e '' são as duas formas de "sem data" no arquivo da Receita; ambas
    -- viram NULL aqui para que `IS NOT NULL` lá em cima signifique mesmo
    -- "aderiu em algum momento".
    SELECT lpad(cnpj_basico, 8, '0')   AS cnpj_basico,
           max(NULLIF(opcao_simples, '')) AS opcao_simples,
           max(NULLIF(opcao_mei, ''))     AS opcao_mei,
           max(NULLIF(NULLIF(data_opcao_simples, ''), '0')) AS data_opcao_simples,
           max(NULLIF(NULLIF(data_opcao_mei, ''), '0'))     AS data_opcao_mei
    FROM bronze_simples
    GROUP BY 1
) si USING (cnpj_basico)
LEFT JOIN (
    -- A agregação acontece AQUI, antes do join. É o que garante uma linha por
    -- empresa: juntar bronze_socios diretamente multiplicaria a empresa pelo
    -- número de sócios e a PRIMARY KEY estouraria lá embaixo.
    --
    -- Só contagens saem desta subconsulta. Nome e CPF do sócio não são sequer
    -- referenciados — ficam na bronze e não entram na Gold.
    SELECT lpad(cnpj_basico, 8, '0')                                AS cnpj_basico,
           count(*)                                                 AS qtd_socios,
           count(*) FILTER (WHERE identificador_socio = '1')         AS qtd_socios_pj,
           count(*) FILTER (WHERE identificador_socio = '2')         AS qtd_socios_pf
    FROM bronze_socios
    GROUP BY 1
) so USING (cnpj_basico);

ALTER TABLE empresas_gold ADD PRIMARY KEY (cnpj_basico);
"""

# Portões de qualidade. Cada um retorna (rótulo, valor, condição_ok).
CHECKS = [
    ("Linhas na Gold",
     "SELECT count(*) FROM empresas_gold",
     lambda v, ctx: v > 40_000_000),

    ("Retenção Gold/Empresas",
     "SELECT round(100.0 * (SELECT count(*) FROM empresas_gold) "
     "/ NULLIF((SELECT count(*) FROM bronze_empresas), 0), 2)",
     lambda v, ctx: v is not None and v > 90),

    ("CNPJs duplicados",
     "SELECT count(*) - count(DISTINCT cnpj_basico) FROM empresas_gold",
     lambda v, ctx: v == 0),

    ("Datas de abertura nulas",
     "SELECT count(*) FROM empresas_gold WHERE data_abertura IS NULL",
     lambda v, ctx: v == 0),

    ("Datas fora do intervalo plausível",
     "SELECT count(*) FROM empresas_gold "
     "WHERE data_abertura < DATE '1900-01-01' OR data_abertura > now()",
     lambda v, ctx: v == 0),

    # Detecta o bug de shard: nenhuma década deve concentrar mais de 60% da base
    ("Maior concentração por década",
     """SELECT round(100.0 * max(c) / sum(c), 1) FROM (
            SELECT count(*) AS c FROM empresas_gold
            GROUP BY (EXTRACT(YEAR FROM data_abertura)::int / 10)
        ) t""",
     lambda v, ctx: v is not None and v < 60),

    # O join com o Simples é LEFT: se o lpad da chave divergir, ele não quebra,
    # só devolve NULL em tudo — e a análise por regime tributário sai vazia sem
    # avisar. Este portão transforma esse silêncio em falha.
    ("Cobertura do Simples/MEI",
     "SELECT round(100.0 * count(*) FILTER (WHERE opcao_simples IS NOT NULL) "
     "/ NULLIF(count(*), 0), 1) FROM empresas_gold",
     lambda v, ctx: v is not None and v > 20),

    ("Baixadas sem motivo informado",
     "SELECT round(100.0 * count(*) FILTER (WHERE motivo_situacao IS NULL) "
     "/ NULLIF(count(*), 0), 1) FROM empresas_gold WHERE situacao_cadastral = 8",
     lambda v, ctx: v is not None and v < 50),

    # Mesma lógica do portão do Simples: o join com sócios é LEFT, então uma
    # chave que não casa não quebra nada — devolve 0 em tudo e a análise de
    # sociedade sai dizendo que o Brasil inteiro é de sócio único.
    ("Empresas com ao menos um sócio",
     "SELECT round(100.0 * count(*) FILTER (WHERE qtd_socios > 0) "
     "/ NULLIF(count(*), 0), 1) FROM empresas_gold",
     lambda v, ctx: v is not None and v > 10),

    # Portão que teria pego a curva do MEI parada em 100%.
    #
    # Todo regime tributário relevante tem que conter empresas que fecharam. Se
    # um deles vier com quase nenhuma baixada, o recorte não está medindo
    # regime: está medindo estar vivo. Mede-se o PIOR regime — basta um cair
    # abaixo do piso para a carga parar.
    ("Menor taxa de baixadas entre os regimes",
     """SELECT min(pct) FROM (
            SELECT round(100.0 * count(*) FILTER (WHERE situacao_cadastral = 8)
                         / NULLIF(count(*), 0), 2) AS pct
            FROM empresas_gold
            WHERE foi_simples IS NOT NULL
            GROUP BY CASE WHEN foi_mei THEN 'MEI'
                          WHEN foi_simples THEN 'Simples'
                          ELSE 'Normal' END
            HAVING count(*) > 100000
        ) t""",
     lambda v, ctx: v is not None and v > 1),

    # Guarda o LIMIAR contra si mesmo.
    #
    # O risco aqui não é deixar sentinela passar — é o limiar descer e começar
    # a marcar empresa de verdade. Os valores-sentinela são um punhado de
    # linhas: se mais de 0,1% da base cair na marca, o corte está apagando
    # capital legítimo, e isso é pior que o problema original.
    #
    # (A FATIA DO CAPITAL que eles representam é outra coisa e é enorme —
    # ~67% do capital declarado do país. Esse número não é uma falha, é o
    # achado; ele aparece no log logo abaixo, sem portão.)
    ("Empresas marcadas como capital-sentinela (%)",
     "SELECT round(100.0 * count(*) FILTER (WHERE capital_sentinela) "
     "/ NULLIF(count(*), 0), 4) FROM empresas_gold",
     lambda v, ctx: v is not None and v < 0.1),
]


# Números que valem a pena imprimir, mas que não são critério de aprovação.
# Descrevem a base; não dizem se a carga está boa.
INFORMATIVOS = [
    ("Fatia do capital declarado em valores-sentinela (%)",
     "SELECT round(100.0 * COALESCE(sum(capital_social) "
     "FILTER (WHERE capital_sentinela), 0) / NULLIF(sum(capital_social), 0), 1) "
     "FROM empresas_gold"),

    ("Capital mediano nacional (R$)",
     "SELECT round(percentile_cont(0.50) WITHIN GROUP (ORDER BY capital_social)"
     "::numeric, 2) FROM empresas_gold "
     "WHERE capital_social > 0 AND NOT capital_sentinela"),

    ("Empresas que já foram MEI",
     "SELECT count(*) FROM empresas_gold WHERE foi_mei"),

    ("Delas, quantas já fecharam",
     "SELECT count(*) FROM empresas_gold "
     "WHERE foi_mei AND situacao_cadastral = 8"),
]


def rodar_checks(cur):
    log("Números da base (informativos)")
    for rotulo, sql in INFORMATIVOS:
        cur.execute(sql)
        valor = cur.fetchone()[0]
        log(f"  {rotulo}: {valor:,}" if isinstance(valor, int)
            else f"  {rotulo}: {valor}")

    log("Portões de qualidade")
    falhas = []
    for rotulo, sql, ok in CHECKS:
        cur.execute(sql)
        valor = cur.fetchone()[0]
        passou = ok(valor, None)
        marca = "OK  " if passou else "FALHA"
        log(f"  [{marca}] {rotulo}: {valor:,}" if isinstance(valor, int)
            else f"  [{marca}] {rotulo}: {valor}")
        if not passou:
            falhas.append(rotulo)
    if falhas:
        raise RuntimeError(
            "Carga abortada — portões reprovados: " + ", ".join(falhas)
        )
    log("Todos os portões passaram")


def main():
    inicio = time.time()

    # Registra a decisão logo no começo. CNPJ_SKIP_BRONZE definido com a
    # sintaxe errada do shell não gera erro algum — só não chega ao Python, e
    # o ETL recarrega 75 GB em silêncio. Esta linha entrega o engano em 1 s.
    log(
        f"CNPJ_SKIP_BRONZE={os.environ.get('CNPJ_SKIP_BRONZE') or '(não definida)'}"
        f" → bronze existente será {'reaproveitada' if PULAR_BRONZE else 'RECARREGADA'}"
    )

    # Fora da transação principal: CREATE TABLESPACE exige autocommit.
    preparar_tablespace()

    with psycopg2.connect(DATABASE_URL) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            # O join de ~66 M x ~55 M linhas derrama dezenas de GB em arquivos
            # temporários. Por padrão eles vão para o tablespace default; se
            # houver disco alternativo, mande os temporários para lá também,
            # senão o volume do Postgres enche no meio da execução.
            if TABLESPACE:
                cur.execute(f"SET temp_tablespaces = '{TABLESPACE}'")
                log(f"Arquivos temporários direcionados para {TABLESPACE}")

            # Mais memória para hash join e criação de índices nesta sessão.
            cur.execute("SET work_mem = '512MB'")
            cur.execute("SET maintenance_work_mem = '2GB'")
            cur.execute("SET synchronous_commit = off")

            if PULAR_BRONZE and bronze_pronta(cur):
                log("Bronze já carregada — pulando (CNPJ_SKIP_BRONZE=1)")
            elif PULAR_BRONZE and not complementares_prontas(cur):
                # Caso comum ao atualizar um banco de uma execução anterior:
                # Empresas e Estabelecimentos já estão lá, faltam as bronze
                # pequenas. Carrega só elas e poupa os 30 min das grandes.
                log("Bronze existente sem Simples/Sócios — carregando só essas duas")
                cur.execute("DROP TABLE IF EXISTS bronze_simples, bronze_socios")
                cur.execute(DDL_SIMPLES.format(ts=CLAUSULA_TS))
                cur.execute(DDL_SOCIOS.format(ts=CLAUSULA_TS))
                copiar_shards(cur, "Simples*.zip", "bronze_simples")
                copiar_shards(cur, "Socios*.zip", "bronze_socios")
            else:
                log("Recriando tabelas bronze")
                cur.execute("DROP TABLE IF EXISTS bronze_empresas, "
                            "bronze_estabelecimentos, bronze_simples, "
                            "bronze_socios")
                cur.execute(DDL_EMPRESAS.format(ts=CLAUSULA_TS))
                cur.execute(DDL_ESTABELECIMENTOS.format(ts=CLAUSULA_TS))
                cur.execute(DDL_SIMPLES.format(ts=CLAUSULA_TS))
                cur.execute(DDL_SOCIOS.format(ts=CLAUSULA_TS))

                copiar_shards(cur, "Empresas*.zip", "bronze_empresas")
                copiar_shards(cur, "Estabelecimentos*.zip",
                              "bronze_estabelecimentos")
                copiar_shards(cur, "Simples*.zip", "bronze_simples")
                copiar_shards(cur, "Socios*.zip", "bronze_socios")

        # Persiste as bronze antes de validar. Sem isso, uma reprovação de
        # portão faria rollback de ~30 min de carga junto com a Gold.
        conn.commit()
        log("Bronze consolidada (commit)")

        with conn.cursor() as cur:
            cur.execute("SET work_mem = '512MB'")
            cur.execute("SET maintenance_work_mem = '2GB'")
            if TABLESPACE:
                cur.execute(f"SET temp_tablespaces = '{TABLESPACE}'")

            # Quantifica o que a quarentena vai descartar, antes de descartar.
            cur.execute("""
                SELECT count(*) FROM bronze_estabelecimentos
                WHERE matriz_filial = '1'
                  AND NOT (data_inicio_atividade ~ '^[0-9]{8}$'
                           AND to_date(data_inicio_atividade, 'YYYYMMDD')
                               BETWEEN DATE '1900-01-01' AND CURRENT_DATE)
            """)
            quarentena = cur.fetchone()[0]
            if quarentena:
                log(f"Quarentena: {quarentena:,} matrizes com data de abertura "
                    f"inválida serão excluídas da Gold")

            # As MVs dependem da Gold: captura antes do DROP CASCADE.
            dependentes = capturar_dependentes(cur)

            log("Construindo empresas_gold (join no Postgres)")
            # .replace em vez de .format: o SQL contém '^[0-9]{8}$', e chaves
            # literais quebrariam qualquer formatação por chaves.
            cur.execute(
                SQL_GOLD.replace("%LIMIAR%", str(LIMIAR_CAPITAL_SENTINELA))
            )

            rodar_checks(cur)

            log("Criando índices")
            cur.execute("CREATE INDEX idx_eg_cnae ON empresas_gold (cnae_fiscal)")
            cur.execute("CREATE INDEX idx_eg_municipio ON empresas_gold (cod_municipio)")
            cur.execute("CREATE INDEX idx_eg_uf ON empresas_gold (uf)")
            cur.execute("CREATE INDEX idx_eg_situacao ON empresas_gold (situacao_cadastral)")
            cur.execute("CREATE INDEX idx_eg_qtd_socios ON empresas_gold (qtd_socios)")
            cur.execute("CREATE INDEX idx_eg_data_abertura ON empresas_gold "
                        "USING brin (data_abertura) WITH (pages_per_range = 128)")
            # Índice parcial covering: só ~73% das linhas têm capital > 0, e o
            # ranking do dashboard só olha essas. Sem ele, o Q4 do benchmark
            # faz Seq Scan em 66,7 M de linhas (~12 s).
            cur.execute("""
                CREATE INDEX idx_eg_capital_positivo ON empresas_gold
                    (capital_social DESC, cnae_fiscal, cod_municipio)
                    WHERE capital_social > 0 AND NOT capital_sentinela
            """)
            # Regime: a comparação por safra filtra por foi_mei/foi_simples.
            cur.execute("CREATE INDEX idx_eg_regime ON empresas_gold "
                        "(foi_mei, foi_simples)")
            cur.execute("ANALYZE empresas_gold")

            recriar_dependentes(cur, dependentes)

        conn.commit()

    log(f"Concluído em {(time.time() - inicio) / 60:.1f} min")


if __name__ == "__main__":
    main()
