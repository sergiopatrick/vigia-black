"""Medição do WP Rocket na Sanar Pós, de hora em hora.

O WP Rocket só otimiza o HTML que o WordPress gera, e quem guarda esse HTML são as camadas de cache
do WordPress.com. A mesma URL alterna entre versão otimizada (CSS e JS minificados em cache/min, CSS
crítico com o resto assíncrono) e versão crua. Este script registra, por página e aparelho, o que o
visitante recebe e quando aquela versão foi gerada (last-modified), para medir o tamanho do problema
antes e depois de cada mudança.

Comandos
  medir [--local] [--forcar]   uma rodada (ignora se a hora já foi medida, a não ser com --forcar)
  resumo [--dias N]            tabela por página e aparelho a partir de estado/rocket/*.jsonl

Só lê o site. URL sempre limpa, porque com parâmetro desconhecido o WP Rocket não otimiza.
"""
import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import timedelta
from email.utils import parsedate_to_datetime

import httpx

import config as C
from vigia import DESAFIO, ESTADO, TZ, agora, log, salvar_no_git, ts

PASTA = ESTADO / "rocket"
SUFIXO = "vigia-black/1.0 (monitoramento Martech Sanar; +https://github.com/sergiopatrick/vigia-black)"
APARELHOS = {  # o cache do WordPress.com separa celular e desktop pelo agente
    "desktop": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/130.0.0.0 Safari/537.36 " + SUFIXO,
    "celular": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
               "Version/18.0 Mobile/15E148 Safari/604.1 " + SUFIXO,
}
CABECALHOS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def ler(html):
    """O que o WP Rocket fez neste HTML."""
    tamanho = len(html.encode())
    critico = re.search(r'<style id="rocket-critical-css">(.*?)</style>', html, re.S)
    html = re.sub(r"<noscript>.*?</noscript>", "", html, flags=re.S)  # cópia dos links para quem não tem JS
    links = re.findall(r"<link\b[^>]*>", html)
    rel = lambda l: (re.search(r"(?<![.\w])rel=['\"]?([\w-]+)", l) or [None, ""])[1]  # o onload troca this.rel
    css = [l for l in links if rel(l) == "stylesheet" or (rel(l) == "preload" and re.search(r"as=['\"]style", l))]
    return {
        "bytes": tamanho,
        "css": len(css),
        "css_min": sum("wp-content/cache/min/" in l for l in css),
        "css_async": sum("data-rocket-async" in l for l in css),
        "css_bloqueante": sum(rel(l) == "stylesheet" and "media='print'" not in l for l in css),
        "js_min": len(re.findall(r"<script\b[^>]*src=['\"][^'\"]*wp-content/cache/min/", html)),
        "critico": len(critico.group(1).encode()) if critico else 0,
    }


def gerado(r):
    lm = r.headers.get("last-modified")
    try:
        return parsedate_to_datetime(lm).astimezone(TZ).isoformat(timespec="seconds") if lm else None
    except (TypeError, ValueError):
        return None


def medir(a):
    if agora().strftime("%Y-%m-%d") >= C.ROCKET_FIM:
        log("medição do WP Rocket encerrada em", C.ROCKET_FIM)
        return
    hora = agora().strftime("%Y%m%d%H")
    arq = PASTA / f"{agora():%Y-%m-%d}.jsonl"
    if not a.forcar and not a.local and arq.exists() and f'"hora": "{hora}"' in arq.read_text():
        log("hora", hora, "já medida")
        return
    base = C.SITES["pos"]["base"]
    linhas = []
    with httpx.Client(timeout=40, follow_redirects=False, headers=CABECALHOS, http2=False) as cli:
        for caminho in C.ROCKET_PAGINAS:
            for aparelho, ua in APARELHOS.items():
                if linhas:
                    time.sleep(C.PAUSA_ENTRE_PAGINAS)
                item = {"ts": ts(), "hora": hora, "url": caminho, "aparelho": aparelho}
                try:
                    r = cli.get(base + caminho, headers={"User-Agent": ua})
                except httpx.HTTPError as e:
                    item["erro"] = type(e).__name__
                    linhas.append(item)
                    log(caminho, aparelho, "erro", item["erro"])
                    continue
                ac = r.headers.get("x-ac", "")
                item.update({
                    "status": r.status_code,
                    "gerado": gerado(r),
                    "batcache": r.headers.get("x-nananana", "").replace("Batcache-", "") or None,
                    "borda": ac.split()[-1] if ac else None,
                    "dc": (re.search(r"\.(\w+) ", ac) or [None, None])[1] if ac else None,
                })
                if r.status_code == 200:
                    if any(d in r.text for d in DESAFIO):
                        item["desafio"] = True
                    else:
                        item.update(ler(r.text))
                        item["otimizado"] = item["css_min"] > 0
                linhas.append(item)
                log(caminho, aparelho, r.status_code, item.get("batcache"), item.get("borda"),
                    "min", item.get("css_min"), "crit", item.get("critico"), "gerado", item.get("gerado"))
                if r.status_code == 429 or item.get("desafio"):
                    log("limitador do WordPress.com, parando a rodada")
                    break
            else:
                continue
            break
    if a.local:
        print(json.dumps(linhas, ensure_ascii=False, indent=1))
        return
    PASTA.mkdir(parents=True, exist_ok=True)
    with open(arq, "a") as f:
        for item in linhas:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    salvar_no_git(f"rocket {hora}")


def resumo(a):
    desde = (agora() - timedelta(days=a.dias)).isoformat()
    por = defaultdict(list)
    for arq in sorted(PASTA.glob("*.jsonl")):
        for linha in arq.read_text().splitlines():
            it = json.loads(linha)
            if it["ts"] >= desde and "otimizado" in it:
                por[(it["url"], it["aparelho"])].append(it)
    print("| Página | Aparelho | Medições | Otimizado | Com CSS crítico | Trocas | CSS na página | Versões cruas (gerado em) |")
    print("|---|---|---|---|---|---|---|---|")
    for (url, aparelho), its in sorted(por.items()):
        n = len(its)
        otim = sum(i["otimizado"] for i in its)
        crit = sum(i["critico"] > 0 for i in its)
        trocas = sum(x["otimizado"] != y["otimizado"] for x, y in zip(its, its[1:]))
        css = sorted({i["css"] for i in its})
        cruas = sorted({i["gerado"][5:16].replace("T", " ") for i in its if not i["otimizado"] and i.get("gerado")})
        print(f"| {url} | {aparelho} | {n} | {otim} ({otim * 100 // n}%) | {crit} | {trocas} | "
              f"{css[0]}{'' if len(css) == 1 else f' a {css[-1]}'} | {', '.join(cruas[:6])}{' ...' if len(cruas) > 6 else ''} |")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("medir")
    m.add_argument("--local", action="store_true", help="só mostra, não grava")
    m.add_argument("--forcar", action="store_true")
    r = sub.add_parser("resumo")
    r.add_argument("--dias", type=int, default=7)
    a = ap.parse_args()
    {"medir": medir, "resumo": resumo}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
