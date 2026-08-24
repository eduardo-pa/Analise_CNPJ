"""
Nomes da CNAE 2.0 — divisões (2 dígitos) e seções (A–U).

Existe porque o dashboard exibia "47", "56", "94" como se fossem rótulos. São
códigos de divisão da Classificação Nacional de Atividades Econômicas, e o
número sozinho não diz nada a ninguém que não trabalhe com a tabela aberta ao
lado. "47" é comércio varejista — o maior setor do país, com 16 milhões de
empresas.

A seção serve para dar um nível acima da divisão: o treemap usa Seção → Divisão,
e aí clicar num bloco navega para dentro de um agrupamento que faz sentido, em
vez de só ampliar o retângulo.

Fonte: IBGE / CONCLA, CNAE 2.0. As faixas de divisão por seção são as oficiais.
"""

# Divisão (2 dígitos) → nome. Encurtados o suficiente para caber num rótulo de
# gráfico, mas sem virar apelido: quem conhece a CNAE reconhece.
DIVISOES: dict[str, str] = {
    "01": "Agricultura e pecuária",
    "02": "Produção florestal",
    "03": "Pesca e aquicultura",
    "05": "Extração de carvão mineral",
    "06": "Extração de petróleo e gás",
    "07": "Extração de minerais metálicos",
    "08": "Extração de minerais não-metálicos",
    "09": "Apoio à extração de minerais",
    "10": "Produtos alimentícios",
    "11": "Bebidas",
    "12": "Produtos do fumo",
    "13": "Produtos têxteis",
    "14": "Confecção de vestuário",
    "15": "Couro e calçados",
    "16": "Produtos de madeira",
    "17": "Celulose e papel",
    "18": "Impressão e reprodução",
    "19": "Derivados de petróleo e biocombustíveis",
    "20": "Produtos químicos",
    "21": "Farmoquímicos e farmacêuticos",
    "22": "Borracha e plástico",
    "23": "Minerais não-metálicos",
    "24": "Metalurgia",
    "25": "Produtos de metal",
    "26": "Informática, eletrônicos e ópticos",
    "27": "Máquinas e materiais elétricos",
    "28": "Máquinas e equipamentos",
    "29": "Veículos automotores",
    "30": "Outros equipamentos de transporte",
    "31": "Móveis",
    "32": "Produtos diversos",
    "33": "Manutenção e instalação de máquinas",
    "35": "Eletricidade e gás",
    "36": "Captação e distribuição de água",
    "37": "Esgoto",
    "38": "Coleta e tratamento de resíduos",
    "39": "Descontaminação e gestão de resíduos",
    "41": "Construção de edifícios",
    "42": "Obras de infraestrutura",
    "43": "Serviços especializados para construção",
    "45": "Comércio e reparação de veículos",
    "46": "Comércio atacadista",
    "47": "Comércio varejista",
    "49": "Transporte terrestre",
    "50": "Transporte aquaviário",
    "51": "Transporte aéreo",
    "52": "Armazenamento e apoio ao transporte",
    "53": "Correio e entregas",
    "55": "Alojamento",
    "56": "Alimentação",
    "58": "Edição",
    "59": "Cinema, vídeo e música",
    "60": "Rádio e televisão",
    "61": "Telecomunicações",
    "62": "Serviços de tecnologia da informação",
    "63": "Prestação de serviços de informação",
    "64": "Serviços financeiros",
    "65": "Seguros e previdência",
    "66": "Auxiliares de finanças e seguros",
    "68": "Atividades imobiliárias",
    "69": "Jurídicas, contabilidade e auditoria",
    "70": "Sedes de empresas e consultoria",
    "71": "Arquitetura, engenharia e testes",
    "72": "Pesquisa e desenvolvimento",
    "73": "Publicidade e pesquisa de mercado",
    "74": "Outras atividades profissionais",
    "75": "Atividades veterinárias",
    "77": "Aluguéis não-imobiliários",
    "78": "Agenciamento e locação de mão de obra",
    "79": "Agências de viagem e turismo",
    "80": "Vigilância, segurança e investigação",
    "81": "Serviços para edifícios e paisagismo",
    "82": "Escritório e apoio administrativo",
    "84": "Administração pública e seguridade",
    "85": "Educação",
    "86": "Atenção à saúde humana",
    "87": "Saúde e assistência em residências coletivas",
    "88": "Assistência social sem alojamento",
    "90": "Artes, criação e espetáculos",
    "91": "Patrimônio cultural e ambiental",
    "92": "Jogos de azar e apostas",
    "93": "Esporte, recreação e lazer",
    "94": "Organizações associativas",
    "95": "Reparação de equipamentos e objetos",
    "96": "Outros serviços pessoais",
    "97": "Serviços domésticos",
    "99": "Organismos internacionais",
}

# Seção (letra) → (nome, faixas de divisão). As faixas são inclusivas nos dois
# extremos e cobrem a CNAE inteira sem sobreposição.
SECOES: list[tuple[str, str, list[tuple[int, int]]]] = [
    ("A", "Agropecuária",                    [(1, 3)]),
    ("B", "Indústrias extrativas",           [(5, 9)]),
    ("C", "Indústrias de transformação",     [(10, 33)]),
    ("D", "Eletricidade e gás",              [(35, 35)]),
    ("E", "Água, esgoto e resíduos",         [(36, 39)]),
    ("F", "Construção",                      [(41, 43)]),
    ("G", "Comércio",                        [(45, 47)]),
    ("H", "Transporte e armazenagem",        [(49, 53)]),
    ("I", "Alojamento e alimentação",        [(55, 56)]),
    ("J", "Informação e comunicação",        [(58, 63)]),
    ("K", "Atividades financeiras",          [(64, 66)]),
    ("L", "Atividades imobiliárias",         [(68, 68)]),
    ("M", "Profissionais e técnicas",        [(69, 75)]),
    ("N", "Administrativas e complementares", [(77, 82)]),
    ("O", "Administração pública",           [(84, 84)]),
    ("P", "Educação",                        [(85, 85)]),
    ("Q", "Saúde e assistência social",      [(86, 88)]),
    ("R", "Artes, esporte e recreação",      [(90, 93)]),
    ("S", "Outros serviços",                 [(94, 96)]),
    ("T", "Serviços domésticos",             [(97, 97)]),
    ("U", "Organismos internacionais",       [(99, 99)]),
]


def nome_divisao(codigo: str | int) -> str:
    """'47' → 'Comércio varejista'. Código desconhecido devolve 'Divisão NN'."""
    chave = str(codigo).zfill(2)
    return DIVISOES.get(chave, f"Divisão {chave}")


def nome_secao(codigo: str | int) -> str:
    """Seção da CNAE a que a divisão pertence.

    Divisão fora de todas as faixas cai em 'Não classificado' em vez de sumir —
    a base tem CNAEs antigos e códigos truncados, e um setor que desaparece do
    treemap sem aviso é pior que um rótulo feio.
    """
    try:
        n = int(str(codigo)[:2])
    except (TypeError, ValueError):
        return "Não classificado"
    for _letra, nome, faixas in SECOES:
        if any(ini <= n <= fim for ini, fim in faixas):
            return nome
    return "Não classificado"
