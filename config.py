"""O que o vigia testa em cada site. Mudar aqui não exige mexer no resto."""

FUSO = "America/Sao_Paulo"
FIM = "2026-12-01"  # depois desta data o vigia para sozinho

SITES = {
    "pos": {
        "nome": "Sanar Pós",
        "base": "https://pos.sanar.com.br",
        "no_ar": ["/", "/cursos/"],
        "fixas": ["/", "/cursos/"],
        "sitemap": "/product-sitemap.xml",
        "ignorar": ["/shop/", "/teste-com-forms/"],
        # cursos da Black que entram em TODA rodada (slugs); o resto entra em rodízio
        "prioritarias": [],
        "por_rodada": 40,  # a Sanar Pós tem ~39 cursos, então cobre todos toda hora
        "formulario": {"url": "/pos-graduacao-em-pediatria-a/", "abrir": None},
    },
    "cetrus": {
        "nome": "Cetrus",
        "base": "https://cetrus.com.br",
        "no_ar": ["/", "/vitrine/"],
        "fixas": ["/", "/vitrine/"],
        "sitemap": "/product-sitemap.xml",
        "ignorar": ["/shop/"],
        "prioritarias": [],
        "por_rodada": 20,  # ~428 cursos em rodízio, todos passam a cada ~22 h
        "formulario": {"url": "/cursos/pos-graduacao-lato-sensu-em-ressonancia-magnetica-musculoesqueletica/",
                       "abrir": "Quero me inscrever"},
    },
}

CHECKOUT_ENV = "https://checkout.cetrus.sanar.com.br/env-config.js"
CHECKOUT_HOST = "checkout.cetrus.sanar.com.br"

# Páginas medidas no PageSpeed (3x ao dia)
VELOCIDADE = {
    "pos": ["/", "/cursos/", "/pos-graduacao-em-pediatria-a/"],
    "cetrus": ["/", "/vitrine/", "/cursos/pos-graduacao-lato-sensu-em-ressonancia-magnetica-musculoesqueletica/"],
}

PAUSA_ENTRE_PAGINAS = 6       # segundos; o WordPress.com bloqueia rajada
RECONFERIR_EM = 300           # segundos até a segunda conferência de uma falha crítica
LEMBRAR_A_CADA = 2 * 3600     # alerta ainda aberto ganha lembrete a cada 2 h

# Medição do WP Rocket na Sanar Pós (rocket.py), toda hora aos 37 min, até ROCKET_FIM
ROCKET_FIM = "2026-11-09"
ROCKET_PAGINAS = [
    "/", "/cursos/", "/sobre/", "/materiais-gratuitos/", "/coordenadores/",
    "/melhores-pos-graduacoes-para-medicos/", "/area/atencao-primaria-e-saude-publica/",
    "/pos-graduacao-em-cardiologia/",        # produto v2
    "/pos-graduacao-em-endocrinologia/",     # produto v2
    "/pos-graduacao-em-terapia-intensiva/",  # produto v1
    "/curso-psicofarmacos/",                 # curso livre
    "/coordenadores/dr-saulo-ciasca/",
]
