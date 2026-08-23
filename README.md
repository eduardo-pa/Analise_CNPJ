# 🏛️ Demografia Empresarial Brasil

> Pipeline de dados e dashboard analítico sobre a base pública de CNPJ da Receita Federal — **66,7 milhões de empresas** — para medir quanto tempo uma empresa brasileira dura, onde ela abre e por que fecha.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?style=flat&logo=postgresql&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.x-FF4B4B?style=flat&logo=streamlit&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-Interactive-3F4F75?style=flat&logo=plotly&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)
[![CI](https://github.com/eduardo-pa/Analise_CNPJ/actions/workflows/ci.yml/badge.svg)](https://github.com/eduardo-pa/Analise_CNPJ/actions/workflows/ci.yml)

---

## 📊 O que ele responde

Quatro perguntas que a base pública permite responder, e quase nenhuma análise responde direito:

| Pergunta | Resposta sobre a base completa |
|---|---|
| Quanto tempo uma empresa brasileira dura? | Mediana de **3,3 anos** entre as baixadas |
| Quantas empresas o país tem, de fato? | **66.682.481** registradas · 40,3% ativas · 46,6% baixadas |
| Empresa brasileira nasce capitalizada? | **26,3%** abrem com capital social zero |
| MEI sobrevive menos que empresa de regime normal? | Comparável safra a safra no dashboard, controlando pela idade |

Cada número sai de uma view materializada sobre as 66,7 M de linhas — não de amostra, não de estimativa.

## ⚡ Destaques técnicos

- **Join de 66,7 M × 69,9 M linhas no PostgreSQL**, não em pandas — a base não cabe em memória. Os CSVs entram por `COPY` direto do ZIP, sem descompactar em disco.
- **Query-First**: `COUNT`, `SUM`, `percentile_cont` e `GROUP BY` rodam no banco; o Python recebe agregado pronto. Nenhuma consulta do dashboard traz linha crua.
- **7 portões de qualidade** que abortam a carga com rollback em vez de gravar dado silenciosamente errado.
- **125 testes**, dos quais 21 rodam contra um PostgreSQL de verdade — não contra mocks.
- **CI que renderiza o dashboard inteiro** a cada push, para pegar gráfico que quebra em tela sem quebrar no import.
- **Base de demonstração sintética**: qualquer pessoa clona e roda em 5 minutos, sem baixar os 7 GB da Receita.

---

## 🚀 Rodar em 5 minutos

```bash
git clone https://github.com/eduardo-pa/Analise_CNPJ
cd Analise_CNPJ
pip install -r requirements.txt

cp .env.example .env          # o padrão já aponta para o Postgres do compose
docker compose up -d
python demo/criar_demo.py     # gold sintética + as 15 views materializadas

streamlit run app.py
```

> ⚠️ A base de demonstração é **gerada**, com o mesmo esquema da real. Todo o código — consultas, views, testes — roda igual em cima dela, mas nenhum número ali descreve o Brasil. Para os números reais, veja [rodar sobre a base completa](#-rodar-sobre-a-base-completa).

---

## 🏗️ Arquitetura

```
ZIPs da Receita Federal
      │  COPY via stream, sem descompactar em disco
      ▼
  bronze_empresas · bronze_estabelecimentos · bronze_simples   (~75 GB, UNLOGGED)
      │  join no PostgreSQL + 7 portões de qualidade
      ▼
  empresas_gold        66,7 M linhas · 1 linha por empresa (matriz)
      │  agregações pré-calculadas
      ▼
  15 views materializadas
      │
      ▼
  Streamlit (app.py)
```

Decisões que sustentam isso:

**Bronze em tablespace separado.** As tabelas intermediárias ocupam ~75 GB e são descartáveis. `CNPJ_TABLESPACE_DIR` manda elas para outro volume; a Gold, que é o que o dashboard lê, fica sempre no volume padrão.

**`UNLOGGED` na bronze.** São tabelas de passagem — pagar WAL por elas é desperdício num carregamento de meia hora.

**Views materializadas em vez de consulta ao vivo.** O painel responde em milissegundos sobre uma tabela de 66,7 M de linhas porque nunca a consulta diretamente.

---

## 🔬 Como sei que os números estão certos

Análise sobre dezenas de milhões de registros tem um problema estrutural: **o dado não avisa quando o pipeline está errado**. Uma versão anterior deste projeto apurou sobrevivência média de 44,5 anos. O número passou por zero datas nulas, zero duplicatas, zero datas futuras — e estava uma ordem de grandeza fora do que o IBGE publica.

Quem pegou foi a validação cruzada contra fonte externa, não a inspeção do dado. As três causas eram independentes e nenhuma delas é visível olhando a tabela:

| Camada | Causa | Efeito |
|---|---|---|
| ETL | Cruzava `Empresas0.zip` com `Estabelecimentos0.zip` assumindo shard 0 ↔ shard 0. Empresas vem ordenado por CNPJ; Estabelecimentos vem embaralhado. | Interseção das primeiras 200 mil linhas de cada: **1 registro**. O pipeline rodava sobre 16% do país. |
| Métrica | `hoje − data_abertura` sobre todas as empresas, inclusive as fechadas. | Media idade desde a fundação, não sobrevivência. |
| Apresentação | Mediana nacional estimada como média ponderada das medianas dos 20 setores de maior mediana. | Viés de seleção somado a "média de medianas não é mediana". |

**O que existe hoje por causa disso:**

**Portões de qualidade.** O ETL aborta a carga e faz rollback se qualquer um falhar — a Gold anterior continua de pé:

| Portão | Regra | O que pega |
|---|---|---|
| Linhas na Gold | > 40 M | Join que colapsou |
| Retenção Gold/Empresas | > 90% | Perda silenciosa no join |
| CNPJs duplicados | = 0 | Fan-out em algum join |
| Datas nulas / fora de faixa | = 0 | Parsing de data quebrado |
| **Concentração por década** | **< 60%** | **O desalinhamento de shards: a base errada tinha 81%; a correta, 39,4%** |
| Cobertura Simples/MEI | > 20% | `LEFT JOIN` que não casou chave |
| Baixadas sem motivo | < 50% | Coluna não carregada |

**Testes de regressão nomeados pelo defeito.** Um falha se alguém voltar a derivar UF de `LEFT(cod_municipio, 2)` — o campo `municipio` da Receita é código interno da RFB, não IBGE, e São Paulo lá é 7107. Outro falha se alguma década voltar a concentrar mais de 60% da base. Outro se a sobrevivência voltar a contar empresas ativas.

**Integração contra banco real.** Teste que mocka a resposta do PostgreSQL prova que o Python sabe ler o DataFrame que ele mesmo inventou — não que a consulta funciona. Os 21 testes de integração rodam SQL de verdade contra um Postgres de verdade, no CI.

> **0% de datas inválidas não significa 0% de conclusões inválidas.** Validar o dado não é validar o pipeline.

---

## 📈 O dashboard

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

Uma nota que vale para qualquer leitura: aqui "morreu" significa **baixa formal** no cadastro. Empresa que parou de operar e nunca deu baixa segue contada como viva. É pergunta diferente da que o IBGE responde (cessação de atividade) — e é por isso que as taxas de sobrevivência aqui são mais otimistas que as dele.

---

## 🧪 Testes

```bash
python -m pytest tests/ -v                    # unidade, sem banco

docker compose up -d && python demo/criar_demo.py
CNPJ_TEST_DSN=postgresql://cnpj:cnpj_local@localhost:5432/cnpj \
  python -m pytest tests/test_integracao_mvs.py -v
```

O CI roda os dois níveis a cada push, contra um PostgreSQL provisionado no runner, e termina renderizando o dashboard inteiro pelo `AppTest` do Streamlit.

---

## 🗄️ Rodar sobre a base completa

**1.** Baixe uma competência em [dados abertos da Receita](https://dadosabertos.rfb.gov.br/CNPJ/): `Empresas0..9.zip`, `Estabelecimentos0..9.zip`, `Simples.zip`, `Cnaes.zip`, `Municipios.zip`, `Naturezas.zip`, `Motivos.zip`.

**2.** Configure o `.env`:

```ini
DATABASE_URL=postgresql://usuario:senha@localhost:5432/cnpj
CNPJ_DIR=C:\caminho\para\os\zips
CNPJ_COMPETENCIA=2026-02-28
CNPJ_TABLESPACE_DIR=D:\cnpj\pgdata     # opcional: bronze em outro volume
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

Carga completa: 40 min a 1h30, dependendo do disco. Para reaproveitar bronze já carregada:

```powershell
$env:CNPJ_SKIP_BRONZE = "1"      # PowerShell
```
```cmd
set CNPJ_SKIP_BRONZE=1           :: cmd.exe
```

O ETL registra na primeira linha se a variável chegou até ele — no PowerShell, `set` cria variável de shell, não de ambiente.

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
├── metricas_post.py                # apuração dos números sobre a base inteira
├── diagnostico_dashboard.py        # verificação numérica das métricas do painel
├── diagnostico_tablespace.py       # diagnóstico do tablespace das bronze
├── demo/                           # base sintética para rodar sem os dados reais
├── tests/                          # unidade + integração
└── *.sql                           # definição das views materializadas
```

## 🛠️ Stack

PostgreSQL 16+ · Python 3.11+ · Streamlit · Plotly · SQLAlchemy · psycopg2 · pandas · pytest · Docker · GitHub Actions

## 📄 Fonte dos dados

[Dados abertos do CNPJ — Receita Federal](https://dadosabertos.rfb.gov.br/CNPJ/). Dados públicos, sem informação pessoal de sócios pessoa física neste recorte. Competência de referência: fevereiro/2026.

## 👤 Autor

**Eduardo Amorim**

[![GitHub](https://img.shields.io/badge/GitHub-eduardo--pa-181717?style=flat&logo=github)](https://github.com/eduardo-pa)
