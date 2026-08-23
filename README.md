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
| Quanto capital tem uma empresa típica? | **Mediana**, não média — a média nacional é R$ 5,7 milhões e não descreve empresa nenhuma |
| MEI sobrevive menos que empresa de regime normal? | Comparável safra a safra no dashboard, controlando pela idade |
| Empresa de sócio único dura menos? | Comparável por faixa de sócios, dentro da mesma safra |

Cada número sai de uma view materializada sobre as 66,7 M de linhas — não de amostra, não de estimativa.

## ⚡ Destaques técnicos

- **Join de 66,7 M × 69,9 M linhas no PostgreSQL**, não em pandas — a base não cabe em memória. Os CSVs entram por `COPY` direto do ZIP, sem descompactar em disco.
- **Query-First**: `COUNT`, `SUM`, `percentile_cont` e `GROUP BY` rodam no banco; o Python recebe agregado pronto. Nenhuma consulta do dashboard traz linha crua.
- **11 portões de qualidade** que abortam a carga com rollback em vez de gravar dado silenciosamente errado.
- **115 testes**, dos quais 32 rodam contra um PostgreSQL de verdade — não contra mocks.
- **CI que renderiza o dashboard inteiro** a cada push, para pegar gráfico que quebra em tela sem quebrar no import.
- **Base de demonstração sintética**: qualquer pessoa clona e roda em 5 minutos, sem baixar os 7 GB da Receita.

---

## 🌐 Demonstração online

**[▶ Abrir o dashboard](https://demografia-empresarial-brasil.streamlit.app/)**

A instância pública roda sobre uma base **sintética** de 300 mil empresas, com o mesmo esquema e o mesmo código da real. Serve para ver o produto funcionando; os números do Brasil são os desta página, apurados sobre as 66,7 milhões de linhas.

<details>
<summary>Como publicar sua própria instância</summary>

**1. Banco no [Neon](https://neon.tech)** (Postgres puro, plano gratuito). Crie um projeto e copie a connection string.

**2. Popule com a base de demonstração**, do seu terminal (não do SQL Editor do Neon — é comando de shell, não SQL):

```powershell
# PowerShell
$env:DATABASE_URL = "postgresql://user:senha@ep-xxx.neon.tech/neondb?sslmode=require"
python demo/criar_demo.py
```
```bash
# bash / zsh
DATABASE_URL="postgresql://user:senha@ep-xxx.neon.tech/neondb?sslmode=require" \
  python demo/criar_demo.py
```

**3. [Streamlit Community Cloud](https://share.streamlit.io)**: conecte o repositório, aponte para `app.py` e configure os secrets:

```toml
db_url = "postgresql://user:senha@ep-xxx.neon.tech/neondb?sslmode=require"
CNPJ_COMPETENCIA = "2026-02-28"
modo_demo = true
```

`modo_demo = true` faz o app entrar sem login e exibir o banner de dados sintéticos. **Ligue apenas sobre a base de demonstração** — sobre dados reais, isso deixaria o dashboard aberto sem autenticação.

Ambos os serviços hibernam por inatividade no plano gratuito: a primeira visita depois de um tempo parado leva alguns segundos para acordar.

</details>

---

## 🚀 Rodar em 5 minutos

```bash
git clone https://github.com/eduardo-pa/Analise_CNPJ
cd Analise_CNPJ
pip install -r requirements.txt

cp .env.example .env          # o padrão já aponta para o Postgres do compose
docker compose up -d
python demo/criar_demo.py     # gold sintética + as 17 views materializadas

streamlit run app.py
```

> ⚠️ A base de demonstração é **gerada**, com o mesmo esquema da real. Todo o código — consultas, views, testes — roda igual em cima dela, mas nenhum número ali descreve o Brasil. Para os números reais, veja [rodar sobre a base completa](#-rodar-sobre-a-base-completa).

---

## 🏗️ Arquitetura

```
ZIPs da Receita Federal
      │  COPY via stream, sem descompactar em disco
      ▼
  bronze_empresas · bronze_estabelecimentos · bronze_simples · bronze_socios
      │  join no PostgreSQL + 9 portões de qualidade
      ▼
  empresas_gold        66,7 M linhas · 1 linha por empresa (matriz)
      │  agregações pré-calculadas
      ▼
  17 views materializadas
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
| Empresas com ao menos um sócio | > 10% | `LEFT JOIN` de sócios que não casou chave |
| **Menor taxa de baixadas entre os regimes** | **> 1%** | **Grupo definido por um atributo que exige estar vivo** |
| Empresas marcadas como capital-sentinela | < 0,1% | Corte de outlier apagando empresa real |

**Testes de regressão nomeados pelo defeito.** Um falha se alguém voltar a derivar UF de `LEFT(cod_municipio, 2)` — o campo `municipio` da Receita é código interno da RFB, não IBGE, e São Paulo lá é 7107. Outro falha se alguma década voltar a concentrar mais de 60% da base. Outro se a sobrevivência voltar a contar empresas ativas.

**Integração contra banco real.** Teste que mocka a resposta do PostgreSQL prova que o Python sabe ler o DataFrame que ele mesmo inventou — não que a consulta funciona. Os 32 testes de integração rodam SQL de verdade contra um Postgres de verdade, no CI.

> **0% de datas inválidas não significa 0% de conclusões inválidas.** Validar o dado não é validar o pipeline.

### Dois erros que a base real revelou depois

Os portões acima nasceram do bug dos 44,5 anos. Rodar o dashboard sobre as 66,7 milhões de linhas expôs mais dois — e nenhum deles teria sido pego olhando a tabela.

**Sobrevivência do MEI em 100,0%.** A tela de regime tributário exibia MEI e Simples Nacional com 100,0% de sobrevivência aos 5 anos, contra 48% do regime normal. Parece descoberta; é tautologia. A Receita marca como optante do MEI apenas quem está no regime **agora** — quem fecha sai do registro. O grupo "MEI" continha, por definição, só empresas vivas.

Segmentar uma série histórica por um atributo do presente é condicionar na sobrevivência. É o mesmo erro dos 44,5 anos vestido de outro jeito, e a lição é que ele não vem de uma linha de código ruim: vem de ler uma coluna como se ela descrevesse o passado.

A correção separa as duas perguntas em colunas distintas na camada Gold — `opcao_mei` (é optante hoje) e `foi_mei` (aderiu algum dia, da `data_opcao_mei`, que a Receita preserva depois da baixa). As views de sobrevivência usam a segunda. O portão novo mede a taxa de baixadas do **pior** regime: se algum grupo vier sem empresas fechadas, a carga aborta.

**Capital social com doze noves.** O ranking de maiores empresas era uma lista de holdings com exatamente `R$ 999.999.999.999,00` repetido — o teto de um campo de 12 dígitos, preenchido até estourar. Um punhado de linhas assim respondia por **dois terços do capital declarado do país**: dava 67% do capital nacional a uma única cidade no gráfico de pizza e punha a média nacional de capital em R$ 5,7 milhões.

Para calibrar: a maior capitalização social legítima do Brasil está na ordem de R$ 200 bilhões. O corte fica em R$ 500 bilhões — mais que o dobro disso, e ainda assim abaixo dos doze noves.

O primeiro limiar que escrevi foi R$ 1 trilhão, "com folga". Ele não pegava nada: `999.999.999.999` é exatamente um centavo *menor* que um trilhão. Um número redondo escolhido por cima de um sentinela que é o teto de um campo passa por baixo dele. Quem pegou foi o `diagnostico_qualidade.py` reportando zero ocorrências numa base onde elas visivelmente existiam.

Duas mudanças, e a segunda importa mais que a primeira:

1. A Gold marca esses valores em `capital_sentinela`. A empresa **continua na base** e continua contada em toda análise que não seja de capital — o que sai da conta é o valor, não a linha.
2. O painel passou a exibir **mediana** de capital, não média. Capital social é distribuição de cauda pesada: mesmo sem nenhum sentinela, a média descreve o topo e não o país. O valor típico é R$ 45 mil, não R$ 5,7 milhões.

Um portão vigia o próprio limiar: se ele passar a marcar mais de 0,1% da base, deixou de remover preenchimento e começou a remover capital legítimo — que é pior que o problema original.

`diagnostico_qualidade.py` roda as duas investigações contra a base real e imprime a evidência.

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
| Sociedade | Empresa de sócio único dura menos? — por faixa de sócios |
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

**1.** Baixe uma competência em [dados abertos da Receita](https://dadosabertos.rfb.gov.br/CNPJ/): `Empresas0..9.zip`, `Estabelecimentos0..9.zip`, `Socios0..9.zip`, `Simples.zip`, `Cnaes.zip`, `Municipios.zip`, `Naturezas.zip`, `Motivos.zip`.

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
python aplicar_indices.py mv_socios.sql
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
├── diagnostico_qualidade.py        # investiga viés de regime e capital-sentinela
├── diagnostico_tablespace.py       # diagnóstico do tablespace das bronze
├── demo/                           # base sintética para rodar sem os dados reais
├── tests/                          # unidade + integração
└── *.sql                           # definição das views materializadas
```

## 🛠️ Stack

PostgreSQL 16+ · Python 3.11+ · Streamlit · Plotly · SQLAlchemy · psycopg2 · pandas · pytest · Docker · GitHub Actions

## 📄 Fonte dos dados

[Dados abertos do CNPJ — Receita Federal](https://dadosabertos.rfb.gov.br/CNPJ/). Competência de referência: fevereiro/2026.

**Privacidade.** O `Socios.zip` é o único arquivo do conjunto que traz dado de pessoa física — nome do sócio e CPF parcialmente mascarado. É público, mas isso não é licença para espalhá-lo: aqui esses campos ficam na camada bronze e **não chegam à tabela consultada pelo dashboard**, que recebe apenas contagens (quantos sócios, quantos PJ, quantos PF). Nenhuma view materializada, gráfico, exportação ou instância publicada carrega identificação de sócio, e há um teste de integração que falha se alguém propagar uma dessas colunas.

## 👤 Autor

**Eduardo Amorim**

[![GitHub](https://img.shields.io/badge/GitHub-eduardo--pa-181717?style=flat&logo=github)](https://github.com/eduardo-pa)
