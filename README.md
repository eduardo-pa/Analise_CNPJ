# 🏛️ Análise de Dados CNPJ — Brasil
> Plataforma de Business Intelligence para análise da base pública de CNPJs da Receita Federal do Brasil.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.x-FF4B4B?style=flat&logo=streamlit&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?style=flat&logo=postgresql&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-Interactive-3F4F75?style=flat&logo=plotly&logoColor=white)
![Status](https://img.shields.io/badge/Status-Concluído-28a745?style=flat)
![TCC](https://img.shields.io/badge/TCC-Impacta%202026-0066CC?style=flat)

---

## 📌 Sobre o Projeto

Sistema de BI ponta-a-ponta desenvolvido como **Trabalho de Conclusão de Curso** na Faculdade Impacta (Tecnólogo em Análise e Desenvolvimento de Sistemas, 2026).

O projeto processa a base bruta de CNPJs da RFB — **66,7 milhões de empresas**, a base pública completa — e entrega um dashboard interativo para exploração de:

- 📊 **Demografia empresarial** — distribuição por estado, município e setor
- 💰 **Capital social** — rankings, médias e tendências históricas
- 📈 **Crescimento** — evolução de abertura de empresas por ano e região
- 🗺️ **Mapa coroplético** — densidade empresarial por UF
- ⏱️ **Sobrevivência empresarial** — tempo médio de atividade por setor (CNAE)

---

## ✅ Status das Funcionalidades

| Funcionalidade | Status |
|---|---|
| Pipeline ETL sobre a base completa (20 shards) | ✅ Concluído |
| Portões de qualidade automáticos | ✅ Concluído |
| Arquitetura Medallion (Bronze → Silver → Gold) | ✅ Concluído |
| Dashboard interativo (Plotly) | ✅ Concluído |
| Sistema de autenticação (bcrypt) | ✅ Concluído |
| Gestão de perfil e exclusão de conta (LGPD) | ✅ Concluído |
| Filtros dinâmicos por setor e município | ✅ Concluído |
| Mapa coroplético por UF | ✅ Concluído |
| Análise de sobrevivência empresarial | ✅ Concluído |
| Gráfico de crescimento por município | ✅ Concluído |
| Gráfico de bolhas — Ano × Capital × Município | ✅ Concluído |
| Treemap de setores econômicos | ✅ Concluído |
| Exportação CSV e Excel | ✅ Concluído |
| Otimização com índices e Materialized Views | ✅ Concluído |
| Testes automatizados (pytest) | ✅ Concluído |
| Benchmark de performance de queries | ✅ Concluído |

---

## 🏗️ Arquitetura

### Medallion

```
Dados RFB (ZIP)
      ↓
  [Bronze]  → Dados brutos ingeridos via chunking
      ↓
  [Silver]  → Dados higienizados e tipados
      ↓
  [Gold]    → empresas_gold (tabela otimizada para o dashboard)
```

### Princípio Query-First

Todo processamento pesado ocorre **dentro do PostgreSQL**. O Streamlit nunca carrega tabelas inteiras na RAM — recebe apenas sumarizações.

```
Usuário seleciona filtro
        ↓
   Python monta SQL parametrizado
        ↓
   PostgreSQL agrega e retorna resumo
        ↓
   Pandas converte → Plotly renderiza
```

### Otimizações de Performance

Medições sobre a base completa (**66.682.481 empresas**), média de 5 execuções.
Reproduza com `python benchmark_queries.py`.

| Query | Antes | Depois | Ganho | Recurso |
|---|---|---|---|---|
| Ranking por capital social | 11.988 ms | **1,87 ms** | 6.400× | Índice parcial covering `idx_eg_capital_positivo` |
| Sobrevivência empresarial | 9.388 ms | **0,53 ms** | 17.700× | Materialized View `mv_sobrevivencia_setor` |
| Treemap de setores | — | **0,53 ms** | — | Materialized View `mv_treemap_setores` |
| Bolhas Ano × Capital | — | **171 ms** | — | Materialized View `mv_bolhas_ano_municipio` |
| Crescimento por município | — | **275 ms** | — | MV + índice `mv_crescimento_municipio` |
| Mapa coroplético | — | **2.526 ms** | — | Índice B-tree `idx_eg_municipio` |

Dois padrões de otimização carregam o dashboard:

**Índice parcial covering** no ranking por capital. Só ~73% das empresas têm
capital social positivo, e a query olha apenas essas — o índice as cobre por
inteiro (filtro + colunas do JOIN), sem tocar o heap. Sem ele, Seq Scan sobre
66,7 M de linhas com desvio-padrão de 15 s, sintoma de disputa por cache.

**Pré-agregação em Materialized View** na sobrevivência. A query bruta varre as
31 M de empresas baixadas e calcula percentis por setor toda vez (~9 s). A MV
calcula isso uma vez; o dashboard lê o resultado pronto em meio milissegundo.

#### Queries que permanecem em varredura completa — e por quê

| Query | Tempo | Por que nenhum índice ajuda |
|---|---|---|
| Análise estratégica | 65 ms | Agrega a base inteira — ler tudo é o plano ótimo |
| Data Quality | 5.181 ms | Conta ocorrências sobre todos os 66,7 M de registros |

Índice só compensa quando descarta a maior parte da tabela. Quando a query
precisa de quase todas as linhas, Seq Scan **é** o plano correto — forçar um
índice deixaria mais lento. A Data Quality roda sob demanda, não a cada
interação, então o custo é aceitável.

---

## 🛠️ Stack Tecnológica

| Camada | Tecnologia |
|---|---|
| Frontend / Orquestração | Streamlit |
| Linguagem | Python 3.10+ |
| Banco de Dados | PostgreSQL |
| ORM / Driver | SQLAlchemy + Psycopg2 |
| Manipulação de Dados | Pandas |
| Visualização | Plotly Express |
| Segurança | bcrypt |
| Testes | pytest + pytest-mock |
| Versionamento | Git / GitHub |

---

## 📂 Estrutura do Projeto

```
Analise_CNPJ/
├── dashboard_tcc.py            # App principal Streamlit
├── database.py                 # Conexão (credenciais via ambiente)
├── etl_cnpj.py                 # ETL completo: 20 shards → Bronze → Gold
├── carregar_referencias.py     # CNAEs, municípios e naturezas jurídicas
├── metricas_post.py            # Recalcula as métricas divulgadas
├── analise_grafica.py          # Gráficos de capital social
├── gerar_relatorio.py          # Relatório executivo em PDF
├── comparador_regional.py      # Comparação entre UFs
├── benchmark_queries.py        # Benchmark de performance
├── refresh_views.py            # Atualização das Materialized Views
├── criar_mv_comparador.py      # Criação da MV do comparador
├── checar_mvs.py               # Diagnóstico das Materialized Views
├── views_materializadas.sql    # DDL das MVs (gerado pelo ETL)
├── init_indexes.sql            # Índices da camada Gold
├── mv_comparador_uf_kpis.sql   # DDL da MV de KPIs por UF
├── tests/
│   ├── conftest.py
│   ├── test_security.py
│   ├── test_queries.py
│   ├── test_dashboard.py
│   └── test_comparador_regional.py
├── .env.example                # Template de configuração
├── requirements.txt
└── .gitignore
```

---

## ⚙️ Como Executar Localmente

### 1. Pré-requisitos

- Python 3.10+
- PostgreSQL 14+

### 2. Clone o repositório

```bash
git clone https://github.com/eduardo-pa/Analise_CNPJ
cd Analise_CNPJ
```

### 3. Instale as dependências

```bash
pip install -r requirements.txt
```

### 4. Configure as credenciais

```bash
cp .env.example .env
# Edite o .env com a URL do PostgreSQL e o caminho dos ZIPs da Receita
```

O `.env` está no `.gitignore`. Nenhuma credencial existe no código.

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DATABASE_URL` | sim | `postgresql://usuario:senha@host:5432/tcc_cnpj` |
| `CNPJ_DIR` | sim | Pasta com `Empresas0..9.zip` e `Estabelecimentos0..9.zip` |
| `CNPJ_TABLESPACE_DIR` | não | Disco alternativo para as tabelas Bronze (~45 GB) |
| `CNPJ_SKIP_BRONZE` | não | `1` reaproveita a Bronze já carregada |

### 5. Execute o ETL

```bash
python carregar_referencias.py   # CNAEs, municípios, naturezas
python etl_cnpj.py               # Bronze → Gold, ~25 min
```

O ETL lê os **20 arquivos** da Receita (10 de Empresas, 10 de Estabelecimentos),
carrega via `COPY` nas tabelas Bronze, monta a Gold com um join no PostgreSQL e
só então valida. Se qualquer portão de qualidade reprovar, a carga é abortada e
a Gold anterior permanece intacta.

### 6. Execute o dashboard

```bash
python -m streamlit run dashboard_tcc.py
```

---

## 🧪 Testes

```bash
# Rodar todos os testes
python -m pytest tests/ -v

# Rodar benchmark de performance
python benchmark_queries.py

# Atualizar Materialized Views
python refresh_views.py
```

---

## 📊 Schema do Banco

```sql
-- Bronze (transitória, recriada a cada carga)
bronze_empresas          -- 66,7 M · arquivo Empresas da RFB, 7 colunas
bronze_estabelecimentos  -- 69,9 M · arquivo Estabelecimentos da RFB, 30 colunas

-- Referência
cnaes_referencia      -- código + descrição das atividades econômicas
municipios_referencia -- código + descrição dos municípios
naturezas_referencia  -- tipos societários

-- Gold (consumida pelo dashboard)
empresas_gold        -- 66,7 M · uma linha por empresa (só matrizes)
                     -- cnpj_basico, razao_social, natureza_juridica,
                     -- capital_social, data_abertura, cnae_fiscal,
                     -- cod_municipio, uf, situacao_cadastral, data_situacao

-- Aplicação
usuarios             -- autenticação (senha com hash bcrypt)
mv_crescimento_municipio   -- Materialized View: crescimento por município/ano
mv_bolhas_ano_municipio    -- Materialized View: capital × ano × município
mv_treemap_setores         -- Materialized View: distribuição por setor
mv_densidade_uf            -- Materialized View: densidade por UF
```

---

## 🧪 Qualidade de Dados

A camada Gold só é publicada se passar por seis portões automáticos. Um deles
nasceu de um bug real encontrado no projeto — está documentado aqui porque a
lição vale mais que o acerto.

| Portão | Critério |
|---|---|
| Volume | Gold com mais de 40 M de linhas |
| Retenção | Gold preserva > 90% das linhas de Bronze |
| Unicidade | Zero `cnpj_basico` duplicado |
| Completude | Zero data de abertura nula |
| Domínio | Zero data fora de 1900 → hoje |
| **Distribuição** | **Nenhuma década concentra mais de 60% da base** |

O último portão é o mais importante. A primeira versão do ETL cruzava
`Empresas0.zip` com `Estabelecimentos0.zip` assumindo que o shard 0 de um
correspondia ao shard 0 do outro. Não corresponde: os arquivos de Empresas vêm
ordenados por CNPJ, os de Estabelecimentos vêm embaralhados. A interseção
medida entre as primeiras 200 mil linhas de cada foi de **1 registro**.

O resultado era uma Gold com 10,6 M de empresas em vez de 66,7 M — **16% da
base, recortado por acidente** — com 81% dos registros concentrados nos anos
2020. Nenhuma validação de campo detectava isso, porque cada campo
individualmente estava correto. Validar o dado não é o mesmo que validar o
pipeline.

Também são quarentenados na origem: bytes NUL nos CSVs da Receita e registros
com data de abertura impossível.

### Números da base (fevereiro/2026)

| Métrica | Valor |
|---|---|
| Empresas registradas | 66.682.481 |
| Baixadas | 46,6% |
| Ativas | 40,3% |
| Sobrevivência das baixadas | mediana de 3,3 anos |
| Idade das ativas | mediana de 4,9 anos |
| Capital social zero | 26,3% |
| Registradas após 2010 | 72,3% |

A mediana de 3,3 anos é consistente com o dado do IBGE de que cerca de 60% das
empresas brasileiras não chegam aos cinco anos de atividade.

---

## 🔒 Segurança e LGPD

- Senhas armazenadas com hash **bcrypt** — nunca em texto puro
- Queries com **parâmetros vinculados** (SQLAlchemy `text()`) — sem SQL injection
- Credenciais via variáveis de ambiente (`.env`) — nunca hardcoded no código
- **Direito ao esquecimento** implementado — exclusão definitiva de conta
- Dados utilizados são **públicos** (base aberta da Receita Federal)

---

## 👤 Autor

**Eduardo** — Tecnólogo em Análise e Desenvolvimento de Sistemas
Faculdade Impacta — Turma 2026

[![GitHub](https://img.shields.io/badge/GitHub-eduardo--pa-181717?style=flat&logo=github)](https://github.com/eduardo-pa)

---

## 📄 Fonte dos Dados

Base pública de CNPJs disponibilizada pela **Receita Federal do Brasil**.
Dados atualizados mensalmente em: [dados.gov.br](https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj)
