# 🏛️ Demografia Empresarial Brasil

> Pipeline e dashboard sobre a base pública de CNPJ da Receita Federal — 66,7 milhões de empresas — para responder quanto tempo uma empresa brasileira dura, onde ela abre e por que fecha.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?style=flat&logo=postgresql&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.x-FF4B4B?style=flat&logo=streamlit&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-Interactive-3F4F75?style=flat&logo=plotly&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)
[![CI](https://github.com/eduardo-pa/Analise_CNPJ/actions/workflows/ci.yml/badge.svg)](https://github.com/eduardo-pa/Analise_CNPJ/actions/workflows/ci.yml)

O join roda no PostgreSQL, não em pandas — a base não cabe em memória. O dashboard consulta views materializadas, nunca a tabela crua.

---

## 📌 O que este projeto é

Ele começou errado, e a correção é a parte interessante.

A primeira versão publicou que **empresas baixadas no Brasil duraram em média 44,5 anos**. O número passou por todas as validações de qualidade de dado que existiam: zero datas nulas, zero datas futuras, zero duplicatas. E estava uma ordem de grandeza fora — o IBGE aponta que ~60% das empresas não chegam aos 5 anos.

A base estava limpa. O **pipeline** é que estava errado, em três camadas independentes:

| Camada | O erro | Efeito |
|---|---|---|
| **ETL** | Cruzava `Empresas0.zip` com `Estabelecimentos0.zip` assumindo shard 0 ↔ shard 0. Empresas vem ordenado por CNPJ; Estabelecimentos vem embaralhado. | A interseção das primeiras 200 mil linhas de cada: **1 registro**. Analisava 10,6 M de empresas de 66,7 M — 16% do país, por acidente, com 81% empilhados nos anos 2020. |
| **Métrica** | Media `hoje − data_abertura` sobre todas as empresas, inclusive as já fechadas. | Media idade desde a fundação, não sobrevivência. Empresa aberta em 1990 e baixada em 1995 contava 36 anos em vez de 5. |
| **Apresentação** | Estimava a mediana nacional como média ponderada das medianas dos 20 setores de maior mediana. | Viés de seleção somado a "média de medianas não é mediana": **20,9 anos** contra os **3,3** reais. |

Nenhum dos três aparece numa inspeção do dado. Todos aparecem numa validação cruzada contra uma fonte externa.

> **0% de datas inválidas não significa 0% de conclusões inválidas.**
> Validar o dado não é validar o pipeline.

É daí que vem o resto do projeto: portões de qualidade que abortam a carga, uma suíte que roda contra PostgreSQL de verdade em vez de mocks, e testes de regressão nomeados pelos bugs que já foram ao ar.

### O Brasil, sobre a base corrigida

| Indicador | Valor |
|---|---|
| Empresas registradas | 66.682.481 |
| Baixadas | 46,6% |
| Ativas | 40,3% |
| Sobrevivência mediana das baixadas | 3,3 anos |
| Capital social zero | 26,3% |

Um cuidado que vale para qualquer leitura destes números: aqui "morreu" significa **baixa formal** no cadastro. Empresa que parou de operar e nunca deu baixa segue contada como viva. É uma pergunta diferente da que o IBGE responde (cessação de atividade), e é por isso que as taxas de sobrevivência aqui são mais otimistas que as dele.

---

## 🚀 Rodar em 5 minutos (dados sintéticos)

O pipeline real precisa de ~7 GB de arquivos da Receita e ~75 GB de tabelas intermediárias. Para avaliar o projeto sem isso, há uma base de demonstração com o mesmo esquema:

```bash
git clone https://github.com/eduardo-pa/Analise_CNPJ
cd Analise_CNPJ
pip install -r requirements.txt

cp .env.example .env          # o padrão já aponta para o Postgres do compose
docker compose up -d
python demo/criar_demo.py     # cria a gold sintética + as 13 views

streamlit run app.py
```

> ⚠️ Os dados da demonstração são **gerados**, não são a base da Receita. Toda consulta, view e teste roda igual em cima deles — mas nenhum número ali descreve o Brasil.

## 🗄️ Rodar sobre a base real

**1.** Baixe os arquivos de uma competência em [dados abertos da Receita](https://dadosabertos.rfb.gov.br/CNPJ/): `Empresas0..9.zip`, `Estabelecimentos0..9.zip`, `Simples.zip`, `Cnaes.zip`, `Municipios.zip`, `Naturezas.zip`, `Motivos.zip`.

**2.** Configure o `.env`:

```ini
DATABASE_URL=postgresql://usuario:senha@localhost:5432/cnpj
CNPJ_DIR=C:\caminho\para\os\zips
CNPJ_COMPETENCIA=2026-02-28
# Opcional: manda as tabelas bronze (~75 GB) para outro volume
CNPJ_TABLESPACE_DIR=D:\cnpj\pgdata
```

**3.** Execute:

```bash
python carregar_referencias.py                       # CNAEs, municípios, motivos
python etl_cnpj.py                                   # bronze → gold, com portões
python aplicar_indices.py mv_base_dashboard.sql
python aplicar_indices.py mv_correcoes_painel.sql
python aplicar_indices.py mv_analises_sobrevivencia.sql
python aplicar_indices.py mv_analises_avancadas.sql
streamlit run app.py
```

A carga completa leva de 40 min a 1h30, dependendo do disco. Para reaproveitar as tabelas bronze já carregadas:

```powershell
$env:CNPJ_SKIP_BRONZE = "1"      # PowerShell
```
```cmd
set CNPJ_SKIP_BRONZE=1           :: cmd.exe
```

O ETL registra na primeira linha se a variável chegou até ele — no PowerShell, `set` cria variável de shell, não de ambiente, e o Python não enxerga.

---

## 🏗️ Arquitetura

```
ZIPs da Receita
      │  COPY via stream, sem descompactar em disco
      ▼
  bronze_empresas · bronze_estabelecimentos · bronze_simples   (~75 GB, UNLOGGED)
      │  join no PostgreSQL + 7 portões de qualidade
      ▼
  empresas_gold        66,7 M linhas · 1 linha por empresa (matriz)
      │  agregações pré-calculadas
      ▼
  13 views materializadas
      │
      ▼
  Streamlit (app.py)
```

**Query-First.** Nenhuma agregação acontece em pandas. `COUNT`, `SUM`, `percentile_cont` e `GROUP BY` rodam no banco; o Python recebe o resultado pronto. A versão antiga do painel puxava 30 mil linhas cruas com `LIMIT` sem `ORDER BY` e agregava em memória — o que não devolve uma amostra, devolve as primeiras linhas que o plano de execução produzir.

### Portões de qualidade

O `etl_cnpj.py` aborta a carga se qualquer um falhar — a transação inteira sofre rollback e a Gold anterior continua de pé:

| Portão | Regra | O que ele pega |
|---|---|---|
| Linhas na Gold | > 40 M | Join que colapsou |
| Retenção Gold/Empresas | > 90% | Perda silenciosa no join |
| CNPJs duplicados | = 0 | Fan-out em algum join |
| Datas nulas / fora de faixa | = 0 | Parsing de data quebrado |
| **Concentração por década** | **< 60%** | **O bug de shard. A base antiga tinha 81%; a nova, 39,4%** |
| Cobertura Simples/MEI | > 20% | `LEFT JOIN` que não casou chave |
| Baixadas sem motivo | < 50% | Coluna não carregada |

---

## 📊 O dashboard

| Seção | Pergunta que responde |
|---|---|
| Análise estratégica | Quantas empresas, quanto capital, abertas quando |
| Distribuição setorial | Que setores concentram empresas e capital |
| Sobrevivência empresarial | Quanto tempo duram, por setor, em faixas de idade |
| Curva por safra | De 100 empresas abertas em X, quantas seguem vivas a cada aniversário |
| Natalidade × mortalidade | Em que anos o país fechou mais empresas do que abriu |
| Regime tributário | MEI sobrevive menos? — comparado dentro da mesma safra |
| Motivo da baixa | Encerramento voluntário ou cancelamento por omissão? |
| Capital por município | Trajetória do capital típico nas maiores cidades |
| Comparador regional | Dois a quatro estados ou municípios lado a lado |

Duas decisões metodológicas atravessam todos os gráficos:

**Janela de observação.** A curva de sobrevivência de cada safra termina onde a base permite observar. Uma safra de 2020, numa competência de 2026, não informa sobrevivência aos 10 anos — estender a linha desenharia um platô de 100% que é falta de tempo decorrido, não solidez.

**Ano parcial.** A base é uma foto mensal. Na competência de fevereiro/2026, o ano de 2026 tem dois meses de registros. Toda série temporal encerra no último ano completo; incluir o parcial desenha uma queda que não existe.

---

## 🧪 Testes

```bash
python -m pytest tests/ -v                    # unidade, sem banco

docker compose up -d && python demo/criar_demo.py
CNPJ_TEST_DSN=postgresql://cnpj:cnpj_local@localhost:5432/cnpj \
  python -m pytest tests/test_integracao_mvs.py -v
```

**104 testes.** Os de integração são regressões dos bugs reais do projeto: um falha se alguém voltar a derivar UF de `LEFT(cod_municipio, 2)` (o campo `municipio` da Receita é código interno da RFB, não IBGE — São Paulo é 7107), outro se alguma década voltar a concentrar mais de 60% da base, outro se a sobrevivência voltar a contar empresas ativas.

Rodam contra um PostgreSQL de verdade por decisão: um teste que mocka a resposta do banco prova que o Python sabe ler o DataFrame que ele mesmo inventou, não que a consulta funciona. O CI ainda renderiza o dashboard inteiro pelo `AppTest` do Streamlit, para pegar gráfico que quebra em tela sem quebrar no import.

---

## 📂 Estrutura

```
Analise_CNPJ/
├── app.py                          # dashboard Streamlit (ponto de entrada)
├── comparador_regional.py          # página de comparação entre regiões
├── database.py                     # resolução de credencial + competência da base
├── etl_cnpj.py                     # ZIPs → bronze → gold, com portões
├── carregar_referencias.py         # tabelas de apoio (CNAE, município, motivo)
├── aplicar_indices.py              # executa arquivos .sql avulsos
├── refresh_views.py                # REFRESH das views materializadas
├── benchmark_queries.py            # medição das consultas do painel
├── metricas_post.py                # números de divulgação, apurados na base inteira
├── diagnostico_dashboard.py        # prova numérica dos bugs de apresentação
├── diagnostico_tablespace.py       # diagnóstico do tablespace das bronze
├── demo/                           # base sintética para rodar sem os dados reais
├── tests/                          # unidade + integração
└── *.sql                           # definição das views materializadas
```

---

## 🛠️ Stack

PostgreSQL 16+ · Python 3.11+ · Streamlit · Plotly · SQLAlchemy · psycopg2 · pandas · pytest · Docker

## 📄 Fonte dos dados

[Dados abertos do CNPJ — Receita Federal](https://dadosabertos.rfb.gov.br/CNPJ/). Dados públicos, sem informação pessoal de sócios pessoa física neste recorte. Competência de referência da carga atual: fevereiro/2026.

## 👤 Autor

**Eduardo Amorim**

[![GitHub](https://img.shields.io/badge/GitHub-eduardo--pa-181717?style=flat&logo=github)](https://github.com/eduardo-pa)
