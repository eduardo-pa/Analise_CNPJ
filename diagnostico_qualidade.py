"""
Diagnóstico de duas anomalias vistas no dashboard rodando sobre a base real.

    python diagnostico_qualidade.py

Não altera nada. Só lê e imprime. Roda em ~1 minuto sobre 66 milhões de linhas
porque toda contagem é agregada no banco.

O QUE ELE INVESTIGA
-------------------

1. A curva "MEI sobrevive menos?" mostrava MEI e Simples Nacional em 100,0% aos
   5 anos, contra 48,0% do regime normal. Sobrevivência de exatamente 100% não
   é resultado: é ausência de baixadas no grupo.

   A suspeita é que `opcao_simples` / `opcao_mei` do arquivo da Receita são o
   status ATUAL da empresa no registro do Simples, não um histórico. Uma
   empresa que fechou sai do registro — então filtrar por `opcao_mei = true`
   seleciona, por construção, empresas vivas. Segmentar uma curva de
   sobrevivência por um atributo do presente é condicionar na sobrevivência:
   o mesmo erro de levantamento que o projeto inteiro existe para não cometer.

   Se a suspeita estiver certa, a correção é classificar por quem JÁ FOI
   optante (`data_opcao_mei` preenchida), não por quem É optante hoje.

2. "Maiores Empresas do Recorte" listava holdings com capital social de
   R$ 999.999.999.999,00 — doze noves. Valor assim não é capital: é
   preenchimento. Ele domina Capital Total, Média de Capital e o donut de
   capital por cidade.
"""

import sys

from sqlalchemy import text

from database import engine

LARGURA = 78

# Uma data de verdade no arquivo da Receita: oito dígitos que não são todos
# zero. As formas de "sem data" que aparecem no conjunto são '', '0' e
# '00000000' — e é a última que derruba a checagem ingênua, porque não é vazia,
# não é o texto '0', e ainda assim não é data nenhuma.
#
# `{col}` é substituído pelo nome da coluna. É interpolação de identificador em
# constante literal deste módulo, não de entrada externa.
DATA_REAL = "(CASE WHEN {col} ~ '^[0-9]{{8}}$' AND {col} !~ '^0+$' THEN {col} END)"


def titulo(txt: str) -> None:
    print()
    print("=" * LARGURA)
    print(txt)
    print("=" * LARGURA)


def tabela(conn, sql: str, params: dict | None = None) -> list[tuple]:
    return list(conn.execute(text(sql), params or {}))


def existe(conn, nome: str) -> bool:
    return conn.execute(
        text("SELECT to_regclass(:n)"), {"n": nome}
    ).scalar() is not None


# ---------------------------------------------------------------------------
# 1. Regime tributário x sobrevivência
# ---------------------------------------------------------------------------
def checar_regime(conn) -> bool:
    titulo("1. REGIME TRIBUTARIO — a curva de 100% e viés de sobrevivência?")

    if not existe(conn, "empresas_gold"):
        print("  empresas_gold não existe. Rode o ETL primeiro.")
        return False

    print("\n  [a] Situação cadastral por opção ATUAL (o que a Gold tem hoje)\n")
    linhas = tabela(conn, """
        SELECT
            CASE WHEN opcao_mei              THEN 'MEI'
                 WHEN opcao_simples          THEN 'Simples Nacional'
                 WHEN opcao_simples IS FALSE THEN 'Regime normal'
                 ELSE                             'Nao informado' END AS regime,
            count(*)                                        AS total,
            count(*) FILTER (WHERE situacao_cadastral = 8)  AS baixadas,
            round(100.0 * count(*) FILTER (WHERE situacao_cadastral = 8)
                  / NULLIF(count(*), 0), 3)                 AS pct_baixadas
        FROM empresas_gold
        GROUP BY 1 ORDER BY 2 DESC
    """)
    print(f"  {'regime':<20}{'total':>14}{'baixadas':>14}{'% baixadas':>13}")
    print("  " + "-" * (LARGURA - 4))
    suspeito = []
    for regime, total, baixadas, pct in linhas:
        print(f"  {regime:<20}{total:>14,}{baixadas:>14,}{float(pct or 0):>12.3f}%")
        if regime in ("MEI", "Simples Nacional") and float(pct or 0) < 1.0:
            suspeito.append(regime)

    if suspeito:
        print()
        print("  >> CONFIRMADO. " + " e ".join(suspeito) + " praticamente não têm")
        print("     baixadas. Não é que essas empresas não fechem — é que ao")
        print("     fechar elas deixam de constar como optantes, e o filtro as")
        print("     descarta. A curva de 100% era o viés, não o achado.")
    else:
        print()
        print("  >> Não confirmado por esta via. Veja [b] e [c] antes de concluir.")

    if not existe(conn, "bronze_simples"):
        print("\n  bronze_simples não existe nesta base — pulando [b] a [d].")
        print("  (Rode o ETL sem CNPJ_SKIP_BRONZE para tê-la.)")
        return bool(suspeito)

    print("\n  [b] Que formas de 'data' o arquivo do Simples usa\n")
    for coluna in ("data_opcao_mei", "data_opcao_simples"):
        linhas = tabela(conn, f"""
            SELECT
                CASE WHEN {coluna} IS NULL          THEN '(NULL)'
                     WHEN {coluna} = ''             THEN '(vazio)'
                     WHEN {coluna} ~ '^0+$'         THEN
                          '(so zeros, ' || length({coluna}) || ' caracteres)'
                     WHEN {coluna} ~ '^[0-9]{{8}}$' THEN 'data YYYYMMDD'
                     ELSE '(outro: ' || left({coluna}, 12) || ')' END AS forma,
                count(*) AS qtd
            FROM bronze_simples
            GROUP BY 1 ORDER BY 2 DESC LIMIT 6
        """)
        print(f"  {coluna}")
        for forma, qtd in linhas:
            print(f"      {forma:<36}{qtd:>14,}")
        print()

    print("  [c] Quem está no arquivo do Simples, por situação da empresa\n")
    linhas = tabela(conn, """
        SELECT
            CASE g.situacao_cadastral WHEN 2 THEN 'Ativa'
                                      WHEN 8 THEN 'Baixada'
                                      WHEN 4 THEN 'Inapta'
                                      ELSE 'Outra' END          AS situacao,
            count(*)                                            AS empresas,
            count(s.cnpj_basico)                                AS no_arquivo,
            count(*) FILTER (WHERE s.opcao_mei = 'S')           AS mei_hoje,
            count(s.data_opcao_mei)                             AS ja_foi_mei
        FROM empresas_gold g
        LEFT JOIN (
            -- A regra de "data de verdade" é DATA_REAL, definida abaixo.
            --
            -- A primeira versão desta consulta usava NOT IN ('', '0') e devolveu
            -- "já foi MEI" exatamente igual a "está no arquivo" nas quatro
            -- linhas — o que é impossível e denunciou o próprio erro. O arquivo
            -- da Receita representa data ausente como 00000000, oito zeros, que
            -- passa por qualquer comparação com '0'.
            SELECT lpad(cnpj_basico, 8, '0') AS cnpj_basico,
                   max(NULLIF(opcao_mei, ''))                AS opcao_mei,
                   max(""" + DATA_REAL.format(col="data_opcao_mei") + """)
                                                             AS data_opcao_mei
            FROM bronze_simples GROUP BY 1
        ) s USING (cnpj_basico)
        GROUP BY 1 ORDER BY 2 DESC
    """)
    print(f"  {'situação':<12}{'empresas':>13}{'no arquivo':>13}"
          f"{'MEI hoje':>12}{'já foi MEI':>13}")
    print("  " + "-" * (LARGURA - 4))
    resgataveis = 0
    for situacao, empresas, no_arquivo, mei_hoje, ja_foi in linhas:
        print(f"  {situacao:<12}{empresas:>13,}{no_arquivo:>13,}"
              f"{mei_hoje:>12,}{ja_foi:>13,}")
        if situacao == "Baixada":
            resgataveis = ja_foi

    print("\n  [d] Veredito sobre a correção proposta\n")
    if resgataveis > 100_000:
        print(f"  {resgataveis:,} empresas BAIXADAS têm data_opcao_mei preenchida.")
        print("  O histórico existe no arquivo — a Receita mantém a data de adesão")
        print("  mesmo depois da baixa. Classificar por 'já foi optante' em vez de")
        print("  'é optante hoje' recupera a análise, e a comparação por safra")
        print("  volta a medir regime em vez de medir estar vivo.")
        print("\n  ACAO: aplicar a correção (colunas foi_mei / foi_simples).")
    else:
        print(f"  Apenas {resgataveis:,} empresas baixadas têm data de adesão ao MEI.")
        print("  O arquivo do Simples não guarda o histórico das que fecharam.")
        print("  Com esta fonte a pergunta 'MEI sobrevive menos?' NÃO tem resposta")
        print("  possível — qualquer curva por regime vai medir sobrevivência.")
        print("\n  ACAO: remover o gráfico e registrar o limite da fonte.")

    return bool(suspeito)


# ---------------------------------------------------------------------------
# 2. Capital social sentinela
# ---------------------------------------------------------------------------
def checar_capital(conn) -> bool:
    titulo("2. CAPITAL SOCIAL — valores-sentinela")

    print("\n  [a] Todo valor de capital acima de R$ 100 bilhões\n")
    # O primeiro corte aqui era "acima de R$ 1 milhão, os 12 mais repetidos", e
    # não servia: R$ 2.000.000,00 aparece 20 mil vezes porque é um número
    # redondo que gente de verdade escreve. Os sentinelas somem debaixo disso.
    #
    # Acima de R$ 100 bilhões não existe empresa brasileira — a maior
    # capitalização social do país está na ordem de R$ 200 bilhões e são
    # pouquíssimas. Nesta faixa dá para listar TUDO e olhar valor por valor,
    # em vez de rankear por frequência.
    linhas = tabela(conn, """
        SELECT capital_social, count(*) AS qtd
        FROM empresas_gold
        WHERE capital_social >= 100000000000
        GROUP BY 1 ORDER BY 1 DESC LIMIT 25
    """)
    print(f"  {'capital':>26}{'empresas':>12}   observação")
    print("  " + "-" * (LARGURA - 4))
    for capital, qtd in linhas:
        texto = f"R$ {float(capital):,.2f}".replace(",", "@").replace(".", ",")
        texto = texto.replace("@", ".")
        nota = ""
        s = f"{int(capital)}"
        if set(s) == {"9"} and len(s) >= 9:
            nota = "<-- só noves: preenchimento"
        elif s.count("0") >= len(s) - 1 and len(s) >= 11:
            nota = "<-- número redondo suspeito"
        print(f"  {texto:>26}{qtd:>12,}   {nota}")

    print("\n  [b] Impacto no total\n")
    linha = tabela(conn, """
        SELECT
            sum(capital_social)                                   AS total,
            sum(capital_social) FILTER (WHERE capital_social >= 5e11) AS total_sentinela,
            count(*) FILTER (WHERE capital_social >= 5e11)         AS qtd_sentinela,
            round(avg(capital_social)::numeric, 2)                 AS media,
            round(avg(capital_social) FILTER
                  (WHERE capital_social < 5e11)::numeric, 2)       AS media_sem,
            round(percentile_cont(0.5) WITHIN GROUP
                  (ORDER BY capital_social)::numeric, 2)           AS mediana,
            -- A métrica que o painel exibe: mediana sobre capital POSITIVO e
            -- não-sentinela. A mediana da linha acima inclui os 26% de
            -- empresas com capital zero e responde outra pergunta.
            round(percentile_cont(0.5) WITHIN GROUP
                  (ORDER BY capital_social) FILTER
                  (WHERE capital_social > 0
                     AND capital_social < 5e11)::numeric, 2)       AS mediana_pos,
            round(avg(capital_social) FILTER
                  (WHERE capital_social > 0
                     AND capital_social < 5e11)::numeric, 2)       AS media_pos
        FROM empresas_gold
    """)[0]
    total, tot_sent, qtd_sent, media, media_sem, mediana, mediana_pos, media_pos = linha
    pct = 100.0 * float(tot_sent or 0) / float(total or 1)

    print(f"  empresas acima de R$ 500 bilhões ....... {qtd_sent:,}")
    print(f"  fatia do capital nacional que elas são .. {pct:.1f}%")
    print(f"  média de capital COM elas ............... R$ {float(media):,.2f}")
    print(f"  média de capital SEM elas ............... R$ {float(media_sem):,.2f}")
    print(f"  mediana, toda a base .................... R$ {float(mediana):,.2f}")
    print()
    print("  Sobre capital POSITIVO, sem sentinela — é o que o painel exibe:")
    print(f"    média ................................. R$ {float(media_pos):,.2f}")
    print(f"    mediana ............................... R$ {float(mediana_pos):,.2f}")
    if media_pos and mediana_pos and float(mediana_pos) > 0:
        razao = float(media_pos) / float(mediana_pos)
        print(f"    a média é {razao:,.0f}x a mediana — e nenhum sentinela")
        print("    participa dessas duas contas. É a forma da distribuição.")

    contaminado = qtd_sent and pct > 5
    if contaminado:
        print()
        print(f"  >> CONFIRMADO. {qtd_sent:,} empresas respondem por {pct:.1f}% do")
        print("     capital declarado do país. Isso não é concentração econômica,")
        print("     é campo preenchido com nove. Toda métrica baseada em SOMA ou")
        print("     MÉDIA de capital está contaminada; a MEDIANA não está.")

    print("\n  [c] Onde elas estão\n")
    linhas = tabela(conn, """
        SELECT COALESCE(m.descricao, 'cod ' || g.cod_municipio::text) AS cidade,
               g.uf, count(*) AS qtd
        FROM empresas_gold g
        LEFT JOIN municipios_referencia m ON m.codigo = g.cod_municipio
        WHERE g.capital_social >= 5e11
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 8
    """)
    for cidade, uf, qtd in linhas:
        print(f"  {cidade[:34]:<36}{uf or '--':>4}{qtd:>10,}")

    return bool(contaminado)


def main() -> int:
    print("Diagnóstico de qualidade — base real")
    achados = []
    with engine.connect() as conn:
        if checar_regime(conn):
            achados.append("regime tributário enviesado por status atual")
        if checar_capital(conn):
            achados.append("capital social contaminado por valor-sentinela")

    titulo("RESUMO")
    if achados:
        for a in achados:
            print(f"  - {a}")
        print("\n  Ambos têm correção pronta no repositório. Veja o README.")
    else:
        print("  Nenhuma das duas anomalias se confirmou nesta base.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
