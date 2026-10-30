"""Vigia da Black November nos sites da Sanar Pós e do Cetrus.

Comandos
  no-ar                      site, vitrine e checkout respondem (a cada 5 min)
  jornada --site pos|cetrus  navega como aluno e confere curso, preço, checkout e formulário (toda hora)
  velocidade                 PageSpeed das páginas principais (3x ao dia)
  formulario --site ...      envia um cadastro de teste de verdade (2x ao dia)
  resumo                     resumo do dia anterior no Slack (9h)
  teste-slack                manda uma mensagem de teste para o canal

Tudo só lê o site, com exceção do formulário de teste.
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx

import config as C

RAIZ = Path(__file__).parent
ESTADO = RAIZ / "estado"
TZ = ZoneInfo(C.FUSO)
UA_HTTP = "vigia-black/1.0 (monitoramento Martech Sanar; +https://github.com/sergiopatrick/vigia-black)"
DESAFIO = ("Checking your browser", "Confirm you are human", "Checking search engine crawler")
ERRO_PHP = ("There has been a critical error", "Fatal error", "Erro crítico", "Error establishing a database connection")


# ------------------------------------------------------------------ utilidades

def agora():
    return datetime.now(TZ)


def ts():
    return agora().isoformat(timespec="seconds")


def nc(url):
    return f"{url}{'&' if '?' in url else '?'}nc={random.randint(10**8, 10**9)}"


def log(*a):
    print(agora().strftime("%H:%M:%S"), *a, flush=True)


def cents(txt):
    return int(re.sub(r"\D", "", txt))


def brl(c):
    s = f"{c / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


class Estado:
    """Um arquivo por tarefa e site, para dois jobs nunca mexerem no mesmo arquivo."""

    def __init__(self, nome):
        self.nome = nome
        self.arq = ESTADO / f"{nome}.json"
        self.d = json.loads(self.arq.read_text()) if self.arq.exists() else {}
        self.d.setdefault("falhas", {})
        self.d.setdefault("base", {})

    def salvar(self):
        ESTADO.mkdir(exist_ok=True)
        self.arq.write_text(json.dumps(self.d, ensure_ascii=False, indent=1, sort_keys=True))

    def evento(self, **e):
        pasta = ESTADO / "eventos" / agora().strftime("%Y-%m-%d")
        pasta.mkdir(parents=True, exist_ok=True)
        with open(pasta / f"{self.nome}.jsonl", "a") as f:
            f.write(json.dumps({"ts": ts(), **e}, ensure_ascii=False) + "\n")


def pasta_prints():
    p = ESTADO / "prints" / agora().strftime("%Y-%m-%d")
    p.mkdir(parents=True, exist_ok=True)
    return p


def url_print(caminho):
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo or not caminho:
        return None
    rel = Path(caminho).relative_to(ESTADO).as_posix()
    return f"https://raw.githubusercontent.com/{repo}/estado/{rel}"


def url_execucao():
    repo, run = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    return f"https://github.com/{repo}/actions/runs/{run}" if repo and run else None


def salvar_no_git(msg):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        subprocess.run(["bash", str(RAIZ / "estado.sh"), "salvar", msg], check=False)


# ------------------------------------------------------------------ Slack

EMOJI = {":rotating_light:": "🚨", ":white_check_mark:": "✅", ":hourglass:": "⏳", ":bar_chart:": "📊",
         ":wave:": "👋", ":warning:": "⚠️", ":large_yellow_circle:": "🟡"}

def slack(texto, blocos=None):
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if not hook:
        log("[slack desligado]", texto)
        return
    if "/triggers/" in hook:
        # webhook do Workflow Builder do Slack da Sanar, que só aceita a variável de texto "mensagem"
        partes = []
        for bl in blocos or []:
            if bl["type"] == "section":
                partes.append(bl["text"]["text"])
            elif bl["type"] == "image":
                partes.append(f"Print {bl['image_url']}")
            elif bl["type"] == "context":
                partes += [e["text"] for e in bl["elements"]]
        corpo = "\n".join(partes) or texto
        corpo = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2 \1", corpo)
        for cod, emo in EMOJI.items():
            corpo = corpo.replace(cod, emo)
        corpo = re.sub(r"(^|\s)[*_]([^*_\n]+)[*_](?=\s|$)", r"\1\2", corpo, flags=re.M)
        payload = {"mensagem": corpo}
    else:
        payload = {"text": texto, "blocks": blocos or []}
    try:
        r = httpx.post(hook, json=payload, timeout=20)
        if r.status_code != 200:
            log("slack respondeu", r.status_code, r.text[:200])
    except httpx.HTTPError as e:
        log("slack falhou", e)


def bloco_alerta(titulo, linhas, url=None, imagem=None):
    b = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{titulo}*\n" + "\n".join(linhas)}}]
    if imagem:
        b.append({"type": "image", "image_url": imagem, "alt_text": "print da página"})
    links = [x for x in (f"<{url}|Abrir a página>" if url else None,
                         f"<{url_execucao()}|Detalhes do teste>" if url_execucao() else None) if x]
    if links:
        b.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(links)}]})
    return b


def avisar(est, itens, confirmar=1):
    """Decide o que vira alerta.

    itens: dicts com id, ok, sev ('critico' ou 'atencao'), msg, url, site, print.
    confirmar: quantas rodadas seguidas a falha precisa aparecer antes do alerta
    (no-ar usa 2, a jornada já reconfere dentro da própria rodada e usa 1).
    """
    falhas = est.d["falhas"]
    agora_s = time.time()
    envios = []
    for it in itens:
        f = falhas.get(it["id"])
        if it["ok"]:
            if f:
                if f.get("alertado"):
                    dur = int((agora_s - f["desde_s"]) / 60)
                    envios.append(("ok", {**it, "msg": f.get("msg", "")}, f"Voltou ao normal depois de {dur} min"))
                    est.evento(tipo="recuperado", id=it["id"], minutos=dur)
                else:
                    est.evento(tipo="oscilacao", id=it["id"], msg=f.get("msg"))
                del falhas[it["id"]]
            continue
        if not f:
            f = falhas[it["id"]] = {"desde": ts(), "desde_s": agora_s, "vezes": 0, "alertado": False}
        f["vezes"] += 1
        f["msg"] = it["msg"]
        if it["sev"] != "critico":
            if f["vezes"] == 1:
                est.evento(tipo="atencao", id=it["id"], msg=it["msg"], url=it.get("url"))
            continue
        if f["vezes"] < confirmar:
            continue
        if not f["alertado"]:
            f.update(alertado=True, ultimo_alerta=agora_s)
            envios.append(("alerta", it, it["msg"]))
            est.evento(tipo="alerta", id=it["id"], msg=it["msg"], url=it.get("url"))
        elif agora_s - f.get("ultimo_alerta", 0) >= C.LEMBRAR_A_CADA:
            f["ultimo_alerta"] = agora_s
            horas = round((agora_s - f["desde_s"]) / 3600, 1)
            envios.append(("lembrete", it, f"{it['msg']} (continua há {horas} h)"))
    return envios


def enviar(envios):
    for tipo, it, msg in envios:
        site = C.SITES.get(it.get("site"), {}).get("nome", it.get("site", ""))
        if tipo == "ok":
            slack(f":white_check_mark: {site} · {msg}",
                  bloco_alerta(f":white_check_mark: {site} · voltou ao normal", [it["msg"], msg], it.get("url")))
        else:
            emoji = ":rotating_light:" if tipo == "alerta" else ":hourglass:"
            texto = f"{emoji} {site} · {msg}"
            slack(texto, bloco_alerta(f"{emoji} {site} · {msg}",
                                      [f"Desde {agora().strftime('%d/%m %H:%M')}"],
                                      it.get("url"), url_print(it.get("print"))))


# ------------------------------------------------------------------ checkout (API pública do próprio checkout)

_env_checkout = None


def env_checkout(cli):
    global _env_checkout
    if _env_checkout is None:
        js = cli.get(C.CHECKOUT_ENV).text
        _env_checkout = dict(re.findall(r"(\w+):\s*'([^']*)'", js))
    return _env_checkout


def consultar_checkout(cli, link):
    """Devolve (ok, motivo, dados) para um link de checkout /produto?productId&pricingConfigId."""
    q = parse_qs(urlparse(link).query)
    pid, cfg = q.get("productId", [None])[0], q.get("pricingConfigId", [None])[0]
    if not pid or not cfg:
        return False, "link de checkout sem produto", {}
    try:
        env = env_checkout(cli)
        r = cli.get(f"{env['KONG_PROXY_URL']}/checkout-api/product/{pid}/price/{cfg}",
                    headers={"x-api-key": env["KONG_PROXY_API_KEY"]})
    except (httpx.HTTPError, KeyError) as e:
        return False, f"checkout não respondeu ({type(e).__name__})", {}
    if r.status_code != 200:
        return False, f"checkout respondeu erro {r.status_code}", {}
    d = r.json()
    prod, preco = d.get("product") or {}, d.get("price") or {}
    dados = {"nome": preco.get("name"), "mensal": (preco.get("showcase") or {}).get("monthlyAmount"),
             "total": (preco.get("showcase") or {}).get("amount")}
    if prod.get("status") != "PUBLISHED":
        return False, f"produto do checkout está {prod.get('status')}", dados
    if preco.get("status") != "active":
        return False, f"preço do checkout está {preco.get('status')}", dados
    return True, "", dados


# ------------------------------------------------------------------ no-ar (httpx, leve)

def cmd_no_ar(_):
    est = Estado("no-ar")
    itens = []
    with httpx.Client(headers={"User-Agent": UA_HTTP}, timeout=30, follow_redirects=True) as cli:
        for sk, s in C.SITES.items():
            for p in s["no_ar"]:
                url = s["base"] + p
                t0 = time.time()
                try:
                    r = cli.get(nc(url))
                    dt = round(time.time() - t0, 1)
                    corpo = r.text[:200000]
                    if r.status_code >= 500:
                        ok, msg = False, f"Site com erro {r.status_code} em {p}"
                    elif any(x in corpo for x in DESAFIO):
                        ok, msg = True, ""  # o desafio do WordPress.com prova que o site está de pé
                        est.evento(tipo="desafio", site=sk, url=url, status=r.status_code)
                    elif any(x in corpo for x in ERRO_PHP):
                        ok, msg = False, f"Página com erro crítico do WordPress em {p}"
                    elif r.status_code != 200:
                        ok, msg = False, f"{p} respondeu {r.status_code}"
                    else:
                        ok, msg = True, ""
                except httpx.HTTPError as e:
                    dt, ok, msg = round(time.time() - t0, 1), False, f"Site não respondeu em {p} ({type(e).__name__})"
                itens.append({"id": f"{sk}|no-ar|{p}", "site": sk, "ok": ok, "sev": "critico", "msg": msg, "url": url})
                est.evento(tipo="no-ar", site=sk, url=url, ok=ok, s=dt)
                time.sleep(3)

        # checkout: o mesmo para Sanar Pós e Cetrus, conferido pelo curso de referência
        ref = Estado("jornada-pos").d["base"].get("checkout_ref")
        if ref:
            ok, motivo, _ = consultar_checkout(cli, ref)
            itens.append({"id": "checkout|no-ar", "site": "pos", "ok": ok, "sev": "critico",
                          "msg": f"Checkout fora do ar ({motivo})", "url": ref})
            est.evento(tipo="no-ar", site="checkout", ok=ok)

    envios = avisar(est, itens, confirmar=2)
    est.salvar()
    falhas = [i for i in itens if not i["ok"]]
    log(f"no-ar {len(itens) - len(falhas)}/{len(itens)} ok", [i["msg"] for i in falhas])
    salvar_no_git(f"no-ar {ts()}")
    enviar(envios)


# ------------------------------------------------------------------ jornada (navegador real)

LER_PAGINA = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const links = [...document.querySelectorAll('a[href]')];
  const checkout = links.filter(a => a.href.includes('/produto?') && a.href.includes('productId=')).map(a => a.href);
  const ctaVis = links.filter(vis).filter(a => /matricul|inscrev|compr|garant/i.test(a.innerText || '')).length;
  const forms = [...document.querySelectorAll('form.hs-form, form[id^="hsForm_"]')].filter(vis)
    .map(f => [...f.querySelectorAll('input,select,textarea')].filter(i => i.type !== 'hidden' && i.type !== 'submit').length);
  const txt = document.body ? document.body.innerText : '';
  return {
    title: document.title, texto: txt.slice(0, 400),
    robots: (document.querySelector('meta[name=robots]') || {}).content || '',
    precos: (txt.match(/R\$\s?[\d.]+,\d{2}/g) || []).slice(0, 12),
    checkout: [...new Set(checkout)], cta: ctaVis,
    form_campos: forms.length ? Math.max(...forms) : 0,
    gtm: !!window.google_tag_manager,
    produtos_linkados: [...new Set(links.map(a => a.pathname).filter(p => /^\/(cursos\/[^/]+|pos-[^/]+)\/$/.test(p)))].length,
    estoura: document.documentElement.scrollWidth > window.innerWidth + 8,
    nao_encontrado: /curso não encontrado|página não encontrada|page not found/i.test(txt.slice(0, 3000)),
  };
}
"""


def lista_cursos(cli, s, est):
    try:
        xml = cli.get(s["base"] + s["sitemap"]).text
        urls = [u for u in re.findall(r"<loc>([^<]+)</loc>", xml) if "/wp-content/" not in u]
        caminhos = [urlparse(u).path for u in urls]
        caminhos = [p for p in caminhos if p not in s["ignorar"]]
        if caminhos:
            est.d["base"]["_cursos"] = caminhos
    except httpx.HTTPError:
        pass
    return est.d["base"].get("_cursos", [])


def escolher(s, est, cursos):
    prio = [p for p in s["prioritarias"]]
    resto = [p for p in cursos if p not in prio]
    n = max(0, int(os.environ.get("VIGIA_POR_RODADA", s["por_rodada"])) - len(prio))
    i = est.d["base"].get("_rodizio", 0) % max(1, len(resto))
    fatia = (resto[i:] + resto[:i])[:n]
    est.d["base"]["_rodizio"] = i + n
    return prio + fatia


def escolher_turma(page):
    """Cursos presenciais do Cetrus pedem unidade e turma antes do Quero me inscrever.
    Devolve quantas turmas o seletor oferece (None quando a página não tem seletor)."""
    try:
        u = page.locator("select.unit-select:visible").first
        if not u.count():
            return None
        u.select_option(index=1)
        page.wait_for_timeout(1500)
        t = page.locator("select.course-select:visible").first
        n = t.locator("option").count() - 1
        if n > 0:
            t.select_option(index=1)
            page.wait_for_timeout(800)
        return max(n, 0)
    except Exception:
        return 0


def abrir(page, url, clicar=None):
    """Abre a página e devolve o que ela mostra. status None = navegador não conseguiu abrir."""
    erros = []

    def pegar(e):
        erros.append(str(e)[:120])

    page.on("pageerror", pegar)
    t0 = time.time()
    try:
        r = page.goto(nc(url), wait_until="load", timeout=60000)
        status = r.status if r else None
        carregou = round(time.time() - t0, 1)
        page.wait_for_timeout(3500)
        turmas = None
        if clicar:
            turmas = escolher_turma(page)
            try:
                page.get_by_text(clicar, exact=False).first.click(timeout=8000)
                page.wait_for_timeout(4000)
            except Exception:
                pass
        else:
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(2500)
        info = page.evaluate(LER_PAGINA)
    except Exception as e:
        return {"status": None, "erro": f"{type(e).__name__}: {str(e)[:120]}", "s": round(time.time() - t0, 1)}
    finally:
        page.remove_listener("pageerror", pegar)
    info.update(status=status, s=carregou, erros_js=len(erros), turmas=turmas)
    info["desafio"] = any(x in info["title"] + info["texto"] for x in DESAFIO)
    info["erro_php"] = any(x in info["texto"] for x in ERRO_PHP)
    return info


def avaliar(sk, s, p, info, base, cli, eh_curso):
    """Transforma o que a página mostrou em itens de checagem, comparando com a linha de base."""
    url = s["base"] + p
    pid = f"{sk}|{p}"
    it = []

    def add(chave, ok, msg, sev="critico"):
        it.append({"id": f"{pid}|{chave}", "site": sk, "ok": ok, "sev": sev, "msg": msg, "url": url})

    if info.get("status") is None:
        add("abre", False, f"Página não abriu no navegador ({info.get('erro', '')[:60]})")
        return it, {}
    abre = info["status"] == 200 and not info["erro_php"] and not info["nao_encontrado"] and bool(info["title"])
    motivo = (f"respondeu {info['status']}" if info["status"] != 200 else "erro crítico do WordPress"
              if info["erro_php"] else "mostra curso ou página não encontrada" if info["nao_encontrado"] else "sem título")
    add("abre", abre, f"Página fora do ar, {motivo}")
    if not abre:
        return it, {}

    b = base.get(p, {})
    novo = {"index": "noindex" not in info["robots"].lower(), "gtm": info["gtm"],
            "form": info["form_campos"] >= 3, "cta": info["cta"] > 0}
    if b.get("index"):
        add("google", novo["index"], "Página escondida do Google (noindex)")
    if b.get("gtm"):
        add("medicao", novo["gtm"], "Tag de medição (GTM) não carregou", "atencao")
    if b.get("form"):
        add("formulario", novo["form"], "Formulário de contato sumiu da página")
    if b.get("cta"):
        add("cta", novo["cta"], "Botão de matrícula ou inscrição sumiu")

    if p in s["fixas"] and p != "/":
        novo["cards"] = info["produtos_linkados"]
        if b.get("cards"):
            add("vitrine", info["produtos_linkados"] >= b["cards"] * 0.5,
                f"Vitrine mostrando poucos cursos ({info['produtos_linkados']} contra {b['cards']} de costume)")

    if eh_curso and info.get("turmas") is not None:
        novo["turmas"] = info["turmas"] > 0
        if b.get("turmas"):
            add("turmas", info["turmas"] > 0, "Curso sem turma para escolher, o aluno não consegue se inscrever")

    if eh_curso and sk == "pos":
        precos = {cents(x) for x in info["precos"]}
        novo["preco"] = bool(precos)
        if b.get("preco"):
            add("preco", bool(precos), "Preço sumiu da página do curso")
        links = info["checkout"]
        novo["checkout"] = bool(links)
        if b.get("checkout"):
            add("checkout-link", bool(links), "Botão Matricule-se sem link para o checkout")
        if links:
            ok, motivo, dados = consultar_checkout(cli, links[0])
            add("checkout", ok, f"Checkout do curso com problema, {motivo}")
            if ok and dados.get("mensal"):
                bate = dados["mensal"] in precos
                novo["valor"] = bate
                if b.get("valor"):
                    add("valor", bate, f"Preço do site diferente do checkout (checkout {brl(dados['mensal'])}/mês, "
                                       f"site {', '.join(sorted(set(info['precos'])))[:60] or 'sem preço'})")
    return it, novo


CHAVES_DE_BASE = ("google", "medicao", "formulario", "cta", "vitrine", "preco", "checkout-link", "valor", "turmas")


def aceitar_novo_normal(est, base, horas=12):
    """Mudança que ficou mais de 12 h alertada vira o novo normal (ex. o time tirou o preço de propósito)."""
    for fid, f in list(est.d["falhas"].items()):
        partes = fid.split("|")
        if len(partes) == 3 and partes[2] in CHAVES_DE_BASE and f.get("alertado") \
                and time.time() - f["desde_s"] > horas * 3600:
            chave = {"google": "index", "medicao": "gtm", "formulario": "form", "vitrine": "cards",
                     "checkout-link": "checkout"}.get(partes[2], partes[2])
            base.setdefault(partes[1], {})[chave] = False if chave != "cards" else 0
            del est.d["falhas"][fid]
            est.evento(tipo="novo-normal", id=fid, msg=f.get("msg"))


def cmd_jornada(a):
    from playwright.sync_api import sync_playwright

    sk = a.site
    s = C.SITES[sk]
    est = Estado(f"jornada-{sk}")
    base = est.d["base"]
    cli = httpx.Client(headers={"User-Agent": UA_HTTP}, timeout=30, follow_redirects=True)
    cursos = lista_cursos(cli, s, est)
    alvo = s["fixas"] + escolher(s, est, cursos) + [p for p in os.environ.get("VIGIA_EXTRA", "").split(",") if p]
    clicar = s["formulario"]["abrir"]
    log(f"jornada {sk}: {len(alvo)} páginas ({len(cursos)} cursos no sitemap)")

    itens, novos, infos, desafios = [], {}, {}, 0
    with sync_playwright() as pw:
        nav = pw.chromium.launch()
        ctx = nav.new_context(viewport={"width": 1366, "height": 900}, locale="pt-BR")
        page = ctx.new_page()

        def rodar(lista):
            nonlocal desafios
            res = {}
            for p in lista:
                eh_curso = p not in s["fixas"]
                info = abrir(page, s["base"] + p, clicar if eh_curso else None)
                if info.get("desafio"):
                    desafios += 1
                    log("  desafio do WordPress.com em", p)
                    time.sleep(C.PAUSA_ENTRE_PAGINAS)
                    continue
                its, nv = avaliar(sk, s, p, info, base, cli, eh_curso)
                res[p] = (info, its, nv)
                ruins = [i["msg"] for i in its if not i["ok"]]
                log(f"  {p} {info.get('status')} {info.get('s')}s", ruins or "ok")
                time.sleep(C.PAUSA_ENTRE_PAGINAS)
            return res

        res = rodar(alvo)

        # segunda conferência só do que falhou de forma crítica
        suspeitas = [p for p, (_, its, _) in res.items() if any(not i["ok"] and i["sev"] == "critico" for i in its)]
        if suspeitas:
            espera = int(os.environ.get("VIGIA_RECONFERIR", C.RECONFERIR_EM))
            log(f"reconferindo {len(suspeitas)} páginas em {espera}s")
            time.sleep(espera)
            res2 = rodar(suspeitas)
            for p, (info, its, nv) in res2.items():
                if any(not i["ok"] and i["sev"] == "critico" for i in its):
                    arq = pasta_prints() / f"{sk}-{re.sub(r'[^a-z0-9]+', '-', p.lower()).strip('-') or 'home'}-{agora():%H%M}.png"
                    try:
                        page.goto(nc(s["base"] + p), wait_until="load", timeout=60000)
                        page.wait_for_timeout(3000)
                        page.screenshot(path=str(arq))
                        for i in its:
                            i["print"] = str(arq)
                    except Exception:
                        pass
                res[p] = (info, its, nv)

        # celular: páginas fixas e 3 cursos da rodada, só layout
        cel = nav.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR")
        pc = cel.new_page()
        for p in s["fixas"] + [p for p in alvo if p not in s["fixas"]][:3]:
            info = abrir(pc, s["base"] + p)
            if info.get("status") == 200 and not info.get("desafio"):
                itens.append({"id": f"{sk}|{p}|celular", "site": sk, "ok": not info["estoura"], "sev": "atencao",
                              "msg": "Layout estourando a largura no celular", "url": s["base"] + p})
            time.sleep(C.PAUSA_ENTRE_PAGINAS)
        nav.close()

    for p, (info, its, nv) in res.items():
        itens += its
        if nv and all(i["ok"] for i in its if i["sev"] == "critico"):
            # linha de base só aprende com página saudável
            base[p] = {**base.get(p, {}), **{k: v for k, v in nv.items() if v not in (None, False) or k not in base.get(p, {})}}
            if sk == "pos" and info.get("checkout") and not base.get("checkout_ref"):
                base["checkout_ref"] = info["checkout"][0]
        infos[p] = {"s": info.get("s"), "erros_js": info.get("erros_js"), "status": info.get("status")}

    aceitar_novo_normal(est, base)

    if desafios >= max(3, len(alvo) // 2):
        itens.append({"id": f"{sk}|robo-bloqueado", "site": sk, "ok": False, "sev": "critico",
                      "msg": f"O robô foi barrado pelo WordPress.com em {desafios} páginas, os testes desta hora ficaram cegos",
                      "url": s["base"]})
    else:
        itens.append({"id": f"{sk}|robo-bloqueado", "site": sk, "ok": True, "sev": "critico", "msg": "", "url": s["base"]})

    envios = avisar(est, itens, confirmar=1)
    falhas = [i for i in itens if not i["ok"]]
    est.evento(tipo="jornada", site=sk, paginas=len(res), desafios=desafios,
               criticas=sum(1 for i in falhas if i["sev"] == "critico"), atencao=sum(1 for i in falhas if i["sev"] != "critico"),
               tempo_medio=round(sum(v["s"] or 0 for v in infos.values()) / max(1, len(infos)), 1), paginas_info=infos)
    est.salvar()
    log(f"jornada {sk}: {len(res)} páginas, {len(falhas)} falhas, {desafios} desafios")
    salvar_no_git(f"jornada {sk} {ts()}")
    enviar(envios)
    status_jornada(est, sk, s, itens, len(res), infos, desafios)


def status_jornada(est, sk, s, itens, n_paginas, infos, desafios):
    """Mensagem de toda hora, mesmo com tudo certo."""
    def conta(chave):
        sel = [i for i in itens if i["id"].endswith("|" + chave)]
        return sum(1 for i in sel if i["ok"]), len(sel)

    abertas = [f for f in est.d["falhas"].values() if f.get("alertado")]
    atencao = [i for i in itens if not i["ok"] and i["sev"] != "critico"]
    tempo = round(sum(v["s"] or 0 for v in infos.values()) / max(1, len(infos)), 1)
    hora = agora().strftime("%Hh%M")
    linhas = [f"{n_paginas} páginas testadas no computador e 5 no celular · abrem em {str(tempo).replace('.', ',')} s em média"]
    partes = []
    for chave, nome in (("checkout", "checkout com o curso certo"), ("valor", "preço igual ao do checkout"),
                        ("turmas", "cursos com turma para escolher"), ("formulario", "formulários na página")):
        ok, tot = conta(chave)
        if tot:
            partes.append(f"{nome} {ok}/{tot}")
    if partes:
        linhas.append(" · ".join(partes))
    if atencao:
        linhas.append(f":large_yellow_circle: {len(atencao)} {'ponto' if len(atencao) == 1 else 'pontos'} para olhar no resumo das 9h")
    if desafios:
        linhas.append(f"O WordPress.com pediu verificação em {desafios} páginas (não é queda)")
    if abertas:
        titulo = f":rotating_light: {s['nome']} · rodada das {hora} com {len(abertas)} {'problema' if len(abertas) == 1 else 'problemas'} em aberto"
        linhas += [f"• {f['msg']}" for f in abertas[:5]]
    else:
        titulo = f":white_check_mark: {s['nome']} · rodada das {hora}, tudo funcionando"
    slack(titulo, bloco_alerta(titulo, linhas))


# ------------------------------------------------------------------ velocidade (PageSpeed Insights)

def lighthouse(url):
    cmd = ["npx", "-y", "lighthouse@12.8.2", url, "--only-categories=performance", "--form-factor=mobile",
           "--output=json", "--quiet", "--chrome-flags=--headless=new --no-sandbox"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, ValueError):
        return None


def cmd_velocidade(_):
    key = os.environ.get("PSI_KEY")
    medidas = []
    for sk, paginas in C.VELOCIDADE.items():
        est = Estado(f"velocidade-{sk}")
        for p in paginas:
            url = C.SITES[sk]["base"] + p
            params = {"url": url, "strategy": "mobile", "category": "performance"}
            if key:
                params["key"] = key
            d, fonte = {}, "pagespeed"
            if key:
                try:
                    d = httpx.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params,
                                  timeout=120).json()
                except (httpx.HTTPError, ValueError):
                    d = {}
            if "lighthouseResult" not in d:
                # sem chave do PageSpeed, mede com o Lighthouse no próprio servidor (mesma métrica de laboratório)
                d, fonte = {"lighthouseResult": lighthouse(nc(url))}, "lighthouse"
            if not d["lighthouseResult"]:
                est.evento(tipo="velocidade", site=sk, url=url, erro="medição falhou")
                continue
            lh = d["lighthouseResult"]
            au = lh["audits"]
            campo = (d.get("loadingExperience") or {}).get("metrics", {})
            m = {
                "nota": round((lh["categories"]["performance"]["score"] or 0) * 100),
                "lcp": round(au["largest-contentful-paint"]["numericValue"] / 1000, 1),
                "cls": round(au["cumulative-layout-shift"]["numericValue"], 3),
                "tbt": round(au["total-blocking-time"]["numericValue"]),
                "lcp_campo": (campo.get("LARGEST_CONTENTFUL_PAINT_MS") or {}).get("percentile"),
                "inp_campo": (campo.get("INTERACTION_TO_NEXT_PAINT") or {}).get("percentile"),
                "cls_campo": (campo.get("CUMULATIVE_LAYOUT_SHIFT_SCORE") or {}).get("percentile"),
            }
            b = est.d["base"].setdefault(p, {"notas": []})
            b["notas"] = (b["notas"] + [m["nota"]])[-15:]
            est.evento(tipo="velocidade", site=sk, url=url, fonte=fonte, **m)
            log(f"velocidade {sk} {p}", m)
            medidas.append((sk, p, m))
            time.sleep(5)
        est.salvar()
    salvar_no_git(f"velocidade {ts()}")
    if medidas:
        linhas = [f"{C.SITES[sk]['nome']} {p} · nota {m['nota']}/100 · LCP {str(m['lcp']).replace('.', ',')} s · CLS {m['cls']}"
                  for sk, p, m in medidas]
        titulo = f":bar_chart: Velocidade no celular · {agora():%Hh%M}"
        slack(titulo, bloco_alerta(titulo, linhas))


# ------------------------------------------------------------------ formulário de teste (envio real)

def cmd_formulario(a):
    from playwright.sync_api import sync_playwright

    sk = a.site
    s = C.SITES[sk]
    est = Estado(f"formulario-{sk}")
    email_base = os.environ.get("VIGIA_EMAIL_TESTE")
    fone = os.environ.get("VIGIA_TELEFONE_TESTE")
    if not email_base or not fone:
        log("formulário de teste desligado, faltam VIGIA_EMAIL_TESTE e VIGIA_TELEFONE_TESTE")
        return
    usuario, dominio = email_base.split("@")
    email = f"{usuario}+vigia-{sk}-{agora():%Y%m%d%H%M}@{dominio}"
    url = s["base"] + s["formulario"]["url"]
    resultado = {"ok": False, "motivo": "", "status": None}

    with sync_playwright() as pw:
        nav = pw.chromium.launch()
        page = nav.new_page(viewport={"width": 1366, "height": 900}, locale="pt-BR")
        respostas = []
        page.on("response", lambda r: ("submissions" in r.url or "/submit" in r.url) and "hsforms" in r.url
                and respostas.append(r.status))
        try:
            page.goto(nc(url), wait_until="load", timeout=60000)
            page.wait_for_timeout(4000)
            if s["formulario"]["abrir"]:
                escolher_turma(page)
                page.get_by_text(s["formulario"]["abrir"], exact=False).first.click(timeout=10000)
                page.wait_for_timeout(4000)
            form = page.locator("form.hs-form:visible, form[id^='hsForm_']:visible").first
            form.wait_for(timeout=15000)
            form.locator("input[name=fullname]").fill("Teste Vigia Black (não atender)")
            form.locator("input[name=email]").fill(email)
            form.locator("input[name=phone]").fill(fone)
            # selects obrigatórios (etapa de carreira etc.) e o que eles abrirem
            for _ in range(3):
                for sel in form.locator("select:visible").all():
                    if not sel.input_value():
                        opcoes = [o for o in sel.locator("option").all() if o.get_attribute("value")]
                        if opcoes:
                            sel.select_option(opcoes[0].get_attribute("value"))
                            page.wait_for_timeout(800)
                for inp in form.locator("input[required]:visible, textarea[required]:visible").all():
                    tipo = inp.get_attribute("type") or "text"
                    if tipo in ("checkbox", "radio"):
                        inp.check()
                    elif not inp.input_value():
                        inp.fill("Teste")
            if a.simular:
                page.screenshot(path=str(pasta_prints() / f"{sk}-formulario-simulado.png"))
                log("simulação: formulário preenchido, sem enviar")
                nav.close()
                return
            form.locator("input[type=submit], button[type=submit]").first.click()
            page.wait_for_timeout(8000)
            enviado = page.locator(".submitted-message:visible").count() > 0 or form.count() == 0 or not form.is_visible()
            st = respostas[-1] if respostas else None
            resultado.update(status=st, ok=bool(enviado or st == 200),
                             motivo="" if (enviado or st == 200) else f"HubSpot não confirmou o envio (resposta {st})")
        except Exception as e:
            resultado["motivo"] = f"não consegui preencher ou enviar ({type(e).__name__})"
        if not resultado["ok"]:
            arq = pasta_prints() / f"{sk}-formulario-{agora():%H%M}.png"
            try:
                page.screenshot(path=str(arq))
                resultado["print"] = str(arq)
            except Exception:
                pass
        nav.close()

    # confirmação no próprio HubSpot, quando houver token
    tok = os.environ.get("HUBSPOT_TOKEN")
    if resultado["ok"] and tok:
        time.sleep(60)
        try:
            r = httpx.post("https://api.hubapi.com/crm/v3/objects/contacts/search", timeout=30,
                           headers={"Authorization": f"Bearer {tok}"},
                           json={"filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}]})
            achou = r.status_code == 200 and r.json().get("total", 0) > 0
            resultado["no_hubspot"] = achou
            if not achou:
                resultado.update(ok=False, motivo="formulário enviado, mas o contato não apareceu no HubSpot")
        except httpx.HTTPError:
            pass

    item = {"id": f"{sk}|formulario-teste", "site": sk, "ok": resultado["ok"], "sev": "critico",
            "msg": f"Formulário de teste falhou, {resultado['motivo']}", "url": url, "print": resultado.get("print")}
    envios = avisar(est, [item], confirmar=1)
    est.evento(tipo="formulario", site=sk, email=email, **{k: v for k, v in resultado.items() if k != "print"})
    est.salvar()
    log(f"formulário {sk}", resultado)
    salvar_no_git(f"formulario {sk} {ts()}")
    enviar(envios)
    if resultado["ok"]:
        titulo = f":white_check_mark: {s['nome']} · formulário de teste das {agora():%Hh%M} enviado e recebido pelo HubSpot"
        slack(titulo, bloco_alerta(titulo, [f"Cadastro {email}"], url))


# ------------------------------------------------------------------ resumo diário

def cmd_resumo(a):
    dia = (agora() - timedelta(days=1)).strftime("%Y-%m-%d") if not a.dia else a.dia
    pasta = ESTADO / "eventos" / dia
    evs = []
    for f in sorted(pasta.glob("*.jsonl")) if pasta.exists() else []:
        evs += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    dd = datetime.strptime(dia, "%Y-%m-%d").strftime("%d/%m")
    if not evs:
        slack(f"Vigia da Black · {dd} sem registros", bloco_alerta(f":warning: Vigia da Black · {dd}",
              ["Não encontrei nenhum teste registrado neste dia. O robô pode ter parado."]))
        return

    linhas = []
    for sk, s in C.SITES.items():
        noar = [e for e in evs if e["tipo"] == "no-ar" and e.get("site") == sk]
        jorn = [e for e in evs if e["tipo"] == "jornada" and e.get("site") == sk]
        form = [e for e in evs if e["tipo"] == "formulario" and e.get("site") == sk]
        vel = [e for e in evs if e["tipo"] == "velocidade" and e.get("site") == sk and "nota" in e]
        alertas = [e for e in evs if e["tipo"] == "alerta" and e["id"].startswith(sk)]
        atenc = {e["id"]: e for e in evs if e["tipo"] == "atencao" and e["id"].startswith(sk)}
        osc = [e for e in evs if e["tipo"] == "oscilacao" and e["id"].startswith(sk)]
        up = sum(1 for e in noar if e["ok"])
        pct = f"{100 * up / len(noar):.1f}%".replace(".", ",") if noar else "sem dado"
        tempo = round(sum(e.get("tempo_medio", 0) for e in jorn) / len(jorn), 1) if jorn else None
        nota = round(sum(e["nota"] for e in vel) / len(vel)) if vel else None
        linhas.append(f"*{s['nome']}*")
        linhas.append(f"No ar {pct} das checagens · {len(jorn)} jornadas completas"
                      + (f" · página abre em {str(tempo).replace('.', ',')} s em média" if tempo else ""))
        linhas.append(f"Formulário de teste {sum(1 for e in form if e.get('ok'))} de {len(form)} ok"
                      + (f" · velocidade no celular {nota}/100" if nota is not None else ""))
        if alertas:
            linhas.append(f":rotating_light: {len(alertas)} alertas · " + "; ".join(e["msg"] for e in alertas[:4]))
        if atenc:
            linhas.append(f":large_yellow_circle: Para olhar · " + "; ".join(
                f"{e['msg']} ({urlparse(e.get('url') or '').path})" for e in list(atenc.values())[:5]))
        if osc:
            linhas.append(f"{len(osc)} oscilações que voltaram sozinhas")
        if not alertas and not atenc:
            linhas.append(":white_check_mark: Nenhum problema no dia")
        linhas.append("")
    desafios = sum(1 for e in evs if e["tipo"] == "desafio") + sum(e.get("desafios", 0) for e in evs if e["tipo"] == "jornada")
    if desafios:
        linhas.append(f"_O WordPress.com barrou o robô {desafios} vezes no dia (não é queda do site)._")
    slack(f"Vigia da Black · resumo de {dd}", bloco_alerta(f":bar_chart: Vigia da Black · resumo de {dd}", linhas))
    log("\n".join(linhas))


def cmd_teste_slack(_):
    slack("Vigia da Black · teste de conexão", bloco_alerta(":wave: Vigia da Black conectado",
          ["A partir de agora os alertas da Sanar Pós e do Cetrus chegam aqui."]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("comando", choices=["no-ar", "jornada", "velocidade", "formulario", "resumo", "teste-slack"])
    ap.add_argument("--site", choices=list(C.SITES))
    ap.add_argument("--dia")
    ap.add_argument("--simular", action="store_true", help="formulário: preenche e não envia")
    a = ap.parse_args()
    if agora().strftime("%Y-%m-%d") >= C.FIM and a.comando != "resumo":
        log("Black encerrada, vigia parado")
        return
    if a.comando in ("jornada", "formulario") and not a.site:
        ap.error("--site é obrigatório")
    {"no-ar": cmd_no_ar, "jornada": cmd_jornada, "velocidade": cmd_velocidade, "formulario": cmd_formulario,
     "resumo": cmd_resumo, "teste-slack": cmd_teste_slack}[a.comando](a)


if __name__ == "__main__":
    sys.exit(main())
