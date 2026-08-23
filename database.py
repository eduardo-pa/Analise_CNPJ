"""
Ponto único de resolução da credencial do banco.

Ordem de precedência, para credencial e para competência:
  1. Variável de ambiente          (desenvolvimento local, via .env)
  2. .streamlit/secrets.toml       (arquivo em disco)
  3. st.secrets                    (Streamlit Community Cloud)

Antes existiam duas fontes independentes — .env e secrets.toml — e trocar a
senha em uma delas deixava metade do projeto sem conectar. Agora há uma só
cadeia, e o secrets.toml é apenas o fallback de produção.
"""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

_SECRETS = Path(__file__).parent / ".streamlit" / "secrets.toml"

def _do_arquivo_secrets(chave: str) -> str | None:
    """Lê uma chave do .streamlit/secrets.toml, se o arquivo existir."""
    if not _SECRETS.exists():
        return None
    try:
        import tomllib                     # Python 3.11+
    except ModuleNotFoundError:            # pragma: no cover
        import tomli as tomllib            # type: ignore[no-redef]
    with _SECRETS.open("rb") as fh:
        valor = tomllib.load(fh).get(chave)
    return valor if isinstance(valor, str) and valor else None


def _do_st_secrets(chave: str) -> str | None:
    """Lê uma chave de st.secrets — o caminho do Streamlit Community Cloud.

    Lá os secrets são injetados no runtime e nem sempre existem como arquivo no
    diretório do app, então ler só o .toml deixaria a aplicação sem credencial
    justamente no deploy.

    O isinstance() não é preciosismo: a suíte de testes substitui o módulo
    streamlit por um MagicMock, e sem essa checagem `st.secrets.get()`
    devolveria outro MagicMock — que é "verdadeiro" e passaria adiante como se
    fosse uma URL de banco.
    """
    try:
        import streamlit as st               # noqa: PLC0415 — import tardio de propósito
        valor = st.secrets.get(chave)
    except Exception:
        return None
    return valor if isinstance(valor, str) and valor else None


def resolver_valor(chave_env: str, chave_secrets: str) -> str | None:
    """Ambiente primeiro, depois secrets.toml, depois st.secrets."""
    return (
        os.environ.get(chave_env)
        or _do_arquivo_secrets(chave_secrets)
        or _do_st_secrets(chave_secrets)
    )


# ---------------------------------------------------------------------------
# Competência da extração
#
# A base pública da RFB é uma foto mensal. A carga atual é a de fevereiro/2026,
# então não existe registro posterior a 2026-02 — e 2026 tem apenas dois meses
# de aberturas.
#
# Sem essa informação, toda série temporal despenca no último ponto e parece
# que o Brasil parou de abrir empresas. Não parou: a régua é que acaba ali.
# Os gráficos encerram a série em ULTIMO_ANO_COMPLETO.
#
# Definida DEPOIS dos resolvedores porque também precisa chegar pelos secrets:
# no Streamlit Community Cloud não existe .env, e ler só o ambiente deixaria a
# instância publicada presa no default para sempre.
#
# Ao carregar uma competência nova, ajuste CNPJ_COMPETENCIA no .env ou nos
# secrets (formato AAAA-MM-DD).
# ---------------------------------------------------------------------------
COMPETENCIA = date.fromisoformat(
    resolver_valor("CNPJ_COMPETENCIA", "CNPJ_COMPETENCIA") or "2026-02-28"
)

# Dezembro é o único mês em que o próprio ano da competência está completo.
ULTIMO_ANO_COMPLETO = (
    COMPETENCIA.year if COMPETENCIA.month == 12 else COMPETENCIA.year - 1
)


def resolver_url() -> str:
    """Devolve a URL de conexão, ou levanta erro explicando como configurar."""
    url = resolver_valor("DATABASE_URL", "db_url")
    if url:
        return url

    raise RuntimeError(
        "Credencial do banco não encontrada.\n"
        "  Local : copie .env.example para .env e preencha DATABASE_URL\n"
        "  Deploy: defina db_url nos secrets do Streamlit Community Cloud"
    )


SQLALCHEMY_DATABASE_URL = resolver_url()

# client_encoding explícito: sem ele, mensagens de erro do PostgreSQL em
# português (locale pt_BR no Windows) chegam em latin-1 e o psycopg2 estoura
# com UnicodeDecodeError, escondendo o erro real.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    # Postgres gerenciado (Neon, Supabase) derruba conexão ociosa e hiberna a
    # instância. pool_pre_ping testa a conexão antes de entregá-la;
    # pool_recycle aposenta a que passou de 5 min. Sem os dois, a primeira
    # interação depois de um tempo parado estoura com "server closed the
    # connection unexpectedly" — falha que só aparece em deploy, nunca local.
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"client_encoding": "utf8"},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Abre a sessão e garante o fechamento depois do uso."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
