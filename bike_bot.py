#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bot de Telegram que vigila botigues online i t'envia les millors ofertes
de bicis gravel que compleixen els teus requisits (config.yaml).

Ús:
  python bike_bot.py --get-chat-id   # mostra el teu chat_id (després d'escriure al bot)
  python bike_bot.py --test          # envia un missatge de prova
  python bike_bot.py --dry-run       # fa la cerca i ho imprimeix, sense enviar res
  python bike_bot.py                 # cerca i envia el resum per Telegram
  python bike_bot.py --diagnose      # estat de cada botiga (afegeix --all per provar-ho tot)

Variables d'entorn: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
Opcionals: MAX_PRICE (sobreescriu el preu màxim), ON_DEMAND=true (ignora only_changes)
"""
import argparse
import atexit
import datetime as dt
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,ca;q=0.8,en;q=0.6",
}

# ---------------------------------------------------------------- preus

_NUM = r"(\d{1,3}(?:[.  ]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_SUFFIX = re.compile(_NUM + r"\s*(?:€|EUR)", re.I)
_PREFIX = re.compile(r"€\s*" + _NUM)

# Etiquetes que van JUST ABANS d'un import i en canvien el significat.
_SAVE_LBL = (r"ahorras|ahorra|ahorro|you save|save up to|save|estalvies|estalvi|"
             r"descuento|dto\.?|discount|rebaja")
_LIST_LBL = (r"precio original|precio regular|precio base|precio anterior|precio de lista|"
             r"precio habitual|precio sin descuento|original price|regular price|list price|"
             r"pvpr?\.?|pvr|msrp|rrp|antes|en lugar de|instead of|was|before|abans|"
             r"preu original|preu habitual")
_IGNORE_LBL = r"env[ií]o|shipping|gastos|financi\w*|cuota\w*|garant\w*"
_SAVE_RX = re.compile(r"\b(?:%s)[^0-9€]{0,6}$" % _SAVE_LBL)
_LIST_RX = re.compile(r"\b(?:%s)[^0-9€]{0,14}$" % _LIST_LBL)
_IGNORE_RX = re.compile(r"\b(?:%s)[^0-9€]{0,14}$" % _IGNORE_LBL)
_MONTH_RX = re.compile(r"\s*(?:€|eur)?\s*(?:/|al\s|por\s|per\s|x\s)\s*(?:mes|month|mo\b)", re.I)
_PCT_RX = re.compile(r"(?<![\w.])[-−–]\s?(\d{1,2})\s?%")


def to_float(s):
    """Converteix '1.099,00', '1,099.00' o '1099' a float."""
    s = s.replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 1 and len(parts[-1]) == 3:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 1 and len(parts[-1]) == 3:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def find_prices(text):
    out = []
    for rx in (_SUFFIX, _PREFIX):
        for m in rx.finditer(text):
            v = to_float(m.group(1))
            if v:
                out.append(v)
    return out


def price_mentions(text):
    """Retorna [(posició, import, tipus)] on tipus és:
    price (preu actual) | list (preu original) | save (import estalviat) |
    monthly (quota mensual) | ignore (enviament, finançament...)."""
    seen, out = set(), []
    for rx in (_SUFFIX, _PREFIX):
        for m in rx.finditer(text):
            v = to_float(m.group(1))
            if not v:
                continue
            key = m.start(1)
            if key in seen:
                continue
            seen.add(key)
            before = text[max(0, m.start() - 34):m.start()].lower()
            after = text[m.end():m.end() + 16].lower()
            stripped = before.rstrip()
            if before and before[-1] in "-−–":
                kind = "save"            # "-1.000,00 €" enganxat al número
            elif _SAVE_RX.search(stripped):
                kind = "save"            # "Ahorras 1.500 €"
            elif _MONTH_RX.match(after):
                kind = "monthly"         # "desde 92 €/mes"
            elif _IGNORE_RX.search(stripped):
                kind = "ignore"
            elif _LIST_RX.search(stripped):
                kind = "list"            # "Precio original 3.999 €"
            else:
                kind = "price"
            out.append((m.start(), v, kind))
    out.sort()
    return out


def resolve_prices(text, floor=300.0):
    """Del text d'una targeta, treu (preu_actual, preu_original) o (None, None).
    Mai confon l'import estalviat ni la quota mensual amb el preu."""
    cur, lst, sav = [], [], []
    for _pos, v, kind in price_mentions(text):
        if kind == "price" and v >= floor:
            cur.append(v)
        elif kind == "list" and v >= floor:
            lst.append(v)
        elif kind == "save":
            sav.append(v)
    if not cur:
        return None, None
    price, list_price = cur[0], None
    bigger = [x for x in lst if x > price * 1.01]
    if bigger:
        list_price = max(bigger)
    elif len(set(cur)) >= 2:
        # dos preus sense etiqueta: el més baix és l'actual, l'altre el tatxat
        lo, hi = min(cur), max(cur)
        if 1.02 <= hi / lo <= 2.6:
            price, list_price = lo, hi
    if list_price is None and sav and 0 < sav[0] < price * 1.5:
        list_price = price + sav[0]
    if list_price is None:
        m = _PCT_RX.search(text)
        if m and 3 <= int(m.group(1)) <= 80:
            list_price = price / (1 - int(m.group(1)) / 100)
    if list_price is not None and list_price <= price * 1.01:
        list_price = None
    return price, (round(list_price, 2) if list_price else None)


def discount_pct(item):
    if item.get("list_price") and item["list_price"] > item["price"]:
        return round((1 - item["price"] / item["list_price"]) * 100)
    return None


# ---------------------------------------------------------------- xarxa

_ROBOTS = {}
RESPECT_ROBOTS = True


def allowed_by_robots(url):
    """Respecta el robots.txt de cada web. Si no es pot llegir, es permet."""
    if not RESPECT_ROBOTS:
        return True
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _ROBOTS:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers=HEADERS, timeout=15)
            if r.status_code >= 400:
                rp = None
            else:
                rp.parse(r.text.splitlines())
        except Exception:
            rp = None
        _ROBOTS[base] = rp
    rp = _ROBOTS[base]
    return True if rp is None else rp.can_fetch(HEADERS["User-Agent"], url)


def decode_response(r):
    """Descodifica bé el text encara que el servidor no digui el charset
    (requests, en aquest cas, suposa ISO-8859-1 i el '€' es converteix en 'â\x82¬',
    amb la qual cosa no es trobaria cap preu)."""
    if "charset" in r.headers.get("content-type", "").lower() and r.encoding:
        return r.text
    try:
        return r.content.decode("utf-8")
    except UnicodeDecodeError:
        pass
    meta = re.search(rb"charset=[\"']?([\w-]+)", r.content[:4000], re.I)
    enc = meta.group(1).decode() if meta else (r.apparent_encoding or "latin-1")
    try:
        return r.content.decode(enc, errors="replace")
    except LookupError:
        return r.content.decode("latin-1", errors="replace")


def get_text(url):
    if not allowed_by_robots(url):
        raise PermissionError("robots.txt no permet llegir aquesta URL")
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return decode_response(r)


def fetch(url):
    return BeautifulSoup(get_text(url), "html.parser")


def describe_error(e):
    if isinstance(e, PermissionError):
        return "el robots.txt de la web no ho permet"
    if isinstance(e, requests.HTTPError) and e.response is not None:
        c = e.response.status_code
        if c in (401, 403, 429):
            return f"bloquejat (HTTP {c})"
        if c == 404:
            return "pàgina no trobada (HTTP 404)"
        return f"error HTTP {c}"
    if isinstance(e, requests.Timeout):
        return "temps d'espera esgotat"
    if isinstance(e, requests.ConnectionError):
        return "no s'ha pogut connectar"
    return str(e)[:80] or e.__class__.__name__


# ---------------------------------------------------------------- navegador (JavaScript)

_PW = {"pw": None, "browser": None, "ctx": None, "failed": False}


def browser_available():
    if _PW["failed"]:
        return False
    if _PW["ctx"] is not None:
        return True
    if os.environ.get("GRAVEL_BROWSER", "1") == "0":
        return False
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=HEADERS["User-Agent"], locale="es-ES",
                                  viewport={"width": 1400, "height": 1000})
        _PW.update(pw=pw, browser=browser, ctx=ctx)
        atexit.register(close_browser)
        return True
    except Exception as e:
        print(f"[!] Navegador no disponible: {e}", file=sys.stderr)
        _PW["failed"] = True
        return False


def close_browser():
    try:
        if _PW["browser"]:
            _PW["browser"].close()
        if _PW["pw"]:
            _PW["pw"].stop()
    except Exception:
        pass
    _PW.update(pw=None, browser=None, ctx=None)


def render_html(url, load_more=None, scrolls=6, max_clicks=15):
    """Obre la pàgina amb un Chromium real, deixa que carregui el JavaScript,
    baixa fins al final i prem el botó 'Veure més' si n'hi ha."""
    if not allowed_by_robots(url):
        raise PermissionError("robots.txt no permet llegir aquesta URL")
    if not browser_available():
        raise RuntimeError("navegador no disponible")
    page = _PW["ctx"].new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        for label in (r"aceptar todo|aceptar|accept all|acepto|aceitar|d'acord", ):
            try:
                btn = page.get_by_role("button", name=re.compile(label, re.I)).first
                if btn.is_visible(timeout=1500):
                    btn.click(timeout=2000)
                    page.wait_for_timeout(500)
            except Exception:
                pass
        for _ in range(scrolls):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(500)
        if load_more:
            rx = re.compile(load_more, re.I)
            for _ in range(max_clicks):
                try:
                    btn = page.get_by_role("button", name=rx)
                    if btn.count() == 0:
                        btn = page.get_by_text(rx)
                    if btn.count() == 0 or not btn.first.is_visible():
                        break
                    btn.first.click(timeout=3000)
                    page.wait_for_timeout(1200)
                    page.mouse.wheel(0, 4000)
                except Exception:
                    break
        return page.content()
    finally:
        page.close()


# ---------------------------------------------------------------- extracció

def _walk(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for k in ("@graph", "itemListElement", "item", "hasVariant", "mainEntity"):
            if k in node:
                yield from _walk(node[k])


def _is_product(node):
    t = node.get("@type")
    ts = t if isinstance(t, list) else [t]
    return any(x in ("Product", "ProductGroup") for x in ts)


def _num(x):
    try:
        v = float(str(x).replace(",", "."))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _offers_info(offers):
    """Retorna (preu, preu_original, en_estoc) d'un camp 'offers' de JSON-LD."""
    if isinstance(offers, dict):
        offers = [offers]
    prices, lists, stock = [], [], []
    for o in offers or []:
        if not isinstance(o, dict):
            continue
        cur = o.get("priceCurrency")
        specs = o.get("priceSpecification")
        if isinstance(specs, dict):
            specs = [specs]
        cands = [o.get("price"), o.get("lowPrice")]
        for sp in specs or []:
            if not isinstance(sp, dict):
                continue
            cur = cur or sp.get("priceCurrency")
            ptype = str(sp.get("priceType", "")).lower()
            if any(k in ptype for k in ("listprice", "srp", "strikethrough")):
                lists.append(sp.get("price"))
            else:
                cands.append(sp.get("price"))
        if cur and str(cur).upper() != "EUR":
            continue
        prices += [v for v in map(_num, cands) if v]
        if o.get("availability") is not None:
            stock.append("outofstock" not in str(o["availability"]).lower())
    price = min(prices) if prices else None
    lp = [v for v in map(_num, lists) if v and price and v > price * 1.01]
    return price, (max(lp) if lp else None), (any(stock) if stock else None)


def extract_jsonld(soup, base_url):
    items = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
        except Exception:
            continue
        for node in _walk(data):
            if not _is_product(node):
                continue
            name = str(node.get("name") or "").strip()
            price, list_price, in_stock = _offers_info(node.get("offers"))
            if not name or not price:
                continue
            url = urljoin(base_url, node.get("url") or base_url)
            items.append({"name": name, "url": url, "price": price,
                          "list_price": list_price, "in_stock": in_stock})
    return items


def extract_microdata(soup, base_url):
    """Targetes amb itemtype=Product / itemprop=price (WooCommerce, PrestaShop...)."""
    items = []
    for node in soup.select('[itemtype*="schema.org/Product"]'):
        name_el = node.select_one('[itemprop="name"]')
        price_el = node.select_one('[itemprop="price"]')
        if not name_el or not price_el:
            continue
        price = _num(price_el.get("content") or price_el.get_text(strip=True))
        link = node.select_one('[itemprop="url"]') or node.find("a", href=True)
        href = (link.get("href") or link.get("content")) if link else None
        if not price or not href:
            continue
        items.append({"name": name_el.get_text(" ", strip=True)[:140] or "?",
                      "url": urljoin(base_url, href), "price": price,
                      "list_price": None, "in_stock": None})
    return items


_OLD_CLS = re.compile(r"(?:^|[-_ ])(old|was|before|regular|original|compare|strike|crossed|"
                      r"line-through|rrp|msrp|prev)(?:$|[-_ ])", re.I)


def mark_old_prices(soup):
    """Posa l'etiqueta 'precio original' davant dels preus tatxats (<del>, <s>
    o classes tipus 'old-price') perquè el parser els reconegui com a originals."""
    for el in soup.find_all(True):
        cls = " ".join(el.get("class") or [])
        if el.name in ("del", "s", "strike") or (cls and _OLD_CLS.search(cls)):
            t = el.get_text(" ", strip=True)
            if t and len(t) < 30 and find_prices(t) and not el.get("data-old"):
                el["data-old"] = "1"
                el.insert(0, " precio original ")


def extract_cards(soup, base_url):
    """Plan B per a llistats sense dades estructurades: busca enllaços que tinguin
    un preu a prop i en separa preu actual, preu original i descompte."""
    mark_old_prices(soup)
    found = {}
    for a in soup.find_all("a", href=True):
        if a["href"].startswith(("#", "javascript", "mailto")):
            continue
        block, txt, price, lp = a, "", None, None
        for _ in range(5):
            if block.parent is None:
                break
            block = block.parent
            txt = block.get_text(" ", strip=True)
            if len(txt) > 700:
                break
            # si el bloc ja conté més d'un producte, hem pujat massa
            paths = {
                urljoin(base_url, x["href"]).split("?")[0].split("#")[0]
                for x in block.find_all("a", href=True)
                if not x["href"].startswith(("#", "javascript", "mailto"))
            }
            if len(paths) > 2:
                price = None
                break
            price, lp = resolve_prices(txt)
            if price:
                break
        if not price or len(txt) > 700:
            continue
        name = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if len(name) < 8:
            img = a.find("img")
            name = (img.get("alt") or "").strip() if img else ""
        if len(name) < 8 or "€" in name:
            continue
        url = urljoin(base_url, a["href"])
        item = {"name": name[:140], "url": url, "price": price,
                "list_price": lp, "in_stock": None}
        if url not in found or len(name) > len(found[url]["name"]):
            found[url] = item
    return list(found.values())


def extract_rss(xml_text, base_url):
    """Feed RSS/Atom. El preu s'agafa del títol o de la descripció."""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        d = {c.tag.split("}")[-1]: (c.text or "") for c in node}
        title = d.get("title", "").strip()
        link = d.get("link", "").strip()
        if not link:
            for c in node:
                if c.tag.split("}")[-1] == "link" and c.get("href"):
                    link = c.get("href")
        desc = BeautifulSoup(d.get("description") or d.get("summary") or "",
                             "html.parser").get_text(" ")
        price, lp = resolve_prices(f"{title} {desc}")
        if not title or not link or not price:
            continue
        items.append({"name": title[:140], "url": urljoin(base_url, link), "price": price,
                      "list_price": lp, "in_stock": None})
    return items


def parse_page(text, url, src):
    if src.get("type") == "rss":
        return extract_rss(text, url)
    soup = BeautifulSoup(text, "html.parser")
    ld = extract_jsonld(soup, url)
    micro = extract_microdata(soup, url)
    if src.get("type") == "product":
        return ld or micro
    for cand in (ld, micro):
        if len(cand) >= 5:
            return cand
    cards = extract_cards(soup, url)
    return max((ld, micro, cards), key=len)


# ---------------------------------------------------------------- filtres

def matches(item, filters):
    text = f"{item['name']} {item['url']}".lower()
    if item.get("in_stock") is False:
        return False
    if not (filters["min_price"] <= item["price"] <= filters["max_price"]):
        return False
    if any(x.lower() in text for x in filters.get("exclude", [])):
        return False
    inc = filters.get("include_any", [])
    if inc and not any(x.lower() in text for x in inc):
        return False
    return True


def score(item, cfg):
    t = f"{item['name']} {item['url']}".lower()
    s = 0.0
    pct = discount_pct(item)
    if pct:
        s += pct * 0.5
    for kw, bonus in (cfg.get("bonus") or {}).items():
        if str(kw).lower() in t:
            s += bonus
    s -= item["price"] / 100
    return s


def check_size(url, size):
    """True/False si el JSON-LD de la fitxa diu clarament si hi ha estoc d'aquesta
    talla; None si no es pot saber (la majoria de botigues no ho diuen)."""
    try:
        soup = fetch(url)
    except Exception:
        return None
    rx = re.compile(rf"(?<![A-Za-z0-9]){re.escape(size)}(?![A-Za-z0-9])")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
        except Exception:
            continue
        for node in _walk(data):
            for o in (node.get("offers") if isinstance(node.get("offers"), list) else []):
                if not isinstance(o, dict):
                    continue
                label = " ".join(str(o.get(k, "")) for k in ("name", "sku", "size", "description"))
                if rx.search(label) and o.get("availability"):
                    return "outofstock" not in str(o["availability"]).lower()
    return None


# ---------------------------------------------------------------- recollida

def page_urls(src):
    """URLs d'un llistat paginat.
    - pages: nombre de pàgines a llegir (per defecte 1)
    - page_url: plantilla amb {n} (1,2,3...) o {from} (0,size,2*size...) i {size}
    """
    pages = int(src.get("pages", 1))
    tpl = src.get("page_url")
    if pages <= 1 or not tpl:
        return [src["url"]]
    size = int(src.get("page_size", 40))
    urls = [src["url"]]
    for n in range(2, pages + 1):
        urls.append(tpl.format(n=n, size=size, **{"from": (n - 1) * size}))
    return urls


def load_and_parse(src, url):
    """Llegeix una pàgina: primer amb HTTP normal i, si falla o surt buida
    (JavaScript, bloqueig), amb un navegador real. Retorna (productes, via, motiu)."""
    mode = src.get("render", "auto")
    reason = None
    if mode is not True:
        try:
            items = parse_page(get_text(url), url, src)
            if items or mode is False:
                return items, "http", None
            reason = "0 productes (pàgina buida o carrega amb JavaScript)"
        except PermissionError:
            raise
        except Exception as e:
            reason = describe_error(e)
            gone = (isinstance(e, requests.HTTPError) and e.response is not None
                    and e.response.status_code in (404, 410))
            if mode is False or gone:       # si la pàgina no existeix, el navegador no hi farà res
                raise
    try:
        text = render_html(url, src.get("load_more"))
    except PermissionError:
        raise
    except Exception as e:
        raise RuntimeError(reason or str(e)[:80])
    items = parse_page(text, url, src)
    if not items:
        reason = (reason or "") + (" · ni amb navegador" if reason else "0 productes amb navegador")
    return items, "navegador", reason


def read_source(src):
    """Llegeix una font (totes les seves pàgines). Retorna (productes, info)."""
    info = {"via": "http", "reason": ""}
    found, seen = [], set()
    first_via = None
    for i, url in enumerate(page_urls(src)):
        try:
            if i > 0 and first_via == "navegador":
                s2 = dict(src, render=True)
            else:
                s2 = src
            page, via, reason = load_and_parse(s2, url)
        except Exception:
            if i == 0:
                raise
            break
        if i == 0:
            first_via = via
            info["via"] = via
            if reason:
                info["reason"] = reason
        new = [p for p in page if p["url"] not in seen]
        if not new:
            break
        seen.update(p["url"] for p in new)
        found += new
        time.sleep(1.2)
    if found:
        info["reason"] = ""
    return found, info


def collect(cfg, include_disabled=False):
    """Llegeix totes les fonts. Retorna (productes, informe per font)."""
    global RESPECT_ROBOTS
    RESPECT_ROBOTS = cfg.get("respect_robots", True)
    items, report = {}, []
    srcs = list(cfg.get("sources", []))
    for w in cfg.get("watch", []):
        srcs.append(dict(w, type="product"))
    for src in srcs:
        url = src.get("url")
        if not url:
            continue
        disabled = src.get("enabled", True) is False
        if disabled and not include_disabled:
            continue
        name = src.get("name") or urlparse(url).netloc
        rep = {"name": name, "url": url, "n": 0, "ok": False, "via": "", "reason": "",
               "optional": bool(src.get("optional")), "disabled": disabled}
        try:
            found, info = read_source(src)
            rep.update(via=info["via"], reason=info["reason"], n=len(found), ok=bool(found))
            if not found and not rep["reason"]:
                rep["reason"] = "0 productes"
        except Exception as e:
            found = []
            rep["reason"] = describe_error(e)
        report.append(rep)
        extra = rep["reason"] if rep["reason"] != "0 productes" else ""
        print(f"[{name}] {rep['n']} productes" + (f" · {extra}" if extra else ""))
        for it in found:
            it["shop"] = name
            items.setdefault(it["url"], it)
        time.sleep(1.2)
    return list(items.values()), report


# ---------------------------------------------------------------- estat

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- missatge

def fmt_eur(v):
    return f"{v:,.0f} €".replace(",", ".")


def build_message(cfg, picks, report, total, title="Ofertes gravel"):
    f = cfg["filters"]
    today = dt.date.today().strftime("%d/%m")
    head = (f"🚲 <b>{html.escape(title)} · {today}</b>\n"
            f"Requisits: ≤ {fmt_eur(f['max_price'])} · talla {f.get('size') or '-'}\n")
    if not picks:
        body = "\nAvui no he trobat res nou que compleixi els requisits."
    else:
        rows = []
        for n, it in enumerate(picks, 1):
            tags = []
            if it.get("is_new"):
                tags.append("🆕")
            if it.get("drop_from"):
                tags.append(f"📉 abans {fmt_eur(it['drop_from'])}")
            line = f"💶 <b>{fmt_eur(it['price'])}</b>"
            pct = discount_pct(it)
            if pct:
                line += f" · −{pct}% (abans {fmt_eur(it['list_price'])})"
            if f.get("size"):
                mark = {True: "✅", False: "❌", None: "❓"}[it.get("size_ok")]
                line += f" · talla {f['size']} {mark}"
            line += f" · {html.escape(it['shop'])}"
            rows.append(
                f"{n}. {' '.join(tags)} <a href=\"{html.escape(it['url'], quote=True)}\">"
                f"{html.escape(it['name'])}</a>\n   {line}"
            )
        body = "\n" + "\n".join(rows)
    foot = f"\n\n{total} coincidències en total."
    bad = [r for r in (report or []) if not r["ok"] and not r.get("optional")]
    if bad:
        parts = [f"<a href=\"{html.escape(r['url'], quote=True)}\">{html.escape(r['name'])}</a>"
                 f" ({html.escape(r['reason'] or 'sense resultats')})" for r in bad[:10]]
        foot += "\n⚠️ No he pogut llegir: " + ", ".join(parts)
    return head + body + foot


def build_diagnosis(report):
    ok = [r for r in report if r["ok"]]
    ko = [r for r in report if not r["ok"]]
    lines = [f"🩺 <b>Estat de les botigues</b> · {len(ok)} ✅ / {len(ko)} ❌\n"]
    for r in ok:
        via = " (navegador)" if r["via"] == "navegador" else ""
        lines.append(f"✅ {html.escape(r['name'])} — {r['n']} productes{via}")
    for r in ko:
        extra = " (prova)" if r.get("disabled") or r.get("optional") else ""
        lines.append(f"❌ <a href=\"{html.escape(r['url'], quote=True)}\">{html.escape(r['name'])}</a>"
                     f"{extra} — {html.escape(r['reason'] or 'sense resultats')}")
    return "\n".join(lines)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        sys.exit("Falten TELEGRAM_TOKEN i/o TELEGRAM_CHAT_ID.")
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for c in chunks:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": c, "parse_mode": "HTML",
                  "disable_web_page_preview": "true"},
            timeout=25,
        )
        if not r.ok:
            sys.exit(f"Telegram ha respost {r.status_code}: {r.text}")


def get_chat_id():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        sys.exit("Falta TELEGRAM_TOKEN.")
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=25)
    r.raise_for_status()
    ids = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
           for u in r.json().get("result", []) if "message" in u}
    if not ids:
        sys.exit("Cap missatge trobat. Escriu-li alguna cosa al bot i torna-ho a provar.")
    for cid, name in ids.items():
        print(f"chat_id = {cid}  ({name})")


# ---------------------------------------------------------------- main

def strip_html(msg):
    return re.sub(r"<[^>]+>", "", html.unescape(msg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--get-chat-id", action="store_true")
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--all", action="store_true", help="amb --diagnose, prova també les fonts desactivades")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    args = ap.parse_args()

    if args.get_chat_id:
        return get_chat_id()
    if args.test:
        send_telegram("✅ El bot de gravel funciona.")
        return

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if os.environ.get("MAX_PRICE", "").strip():
        try:
            cfg["filters"]["max_price"] = float(os.environ["MAX_PRICE"].replace(",", "."))
        except ValueError:
            pass
    tg = cfg.get("telegram", {})
    if os.environ.get("ON_DEMAND", "").lower() == "true":
        tg["only_changes"] = False

    if args.diagnose:
        _items, report = collect(cfg, include_disabled=args.all)
        msg = build_diagnosis(report)
        print(strip_html(msg))
        if not args.dry_run:
            send_telegram(msg)
        return

    state = load_state()
    items, report = collect(cfg)
    good = [i for i in items if matches(i, cfg["filters"])]
    drop_pct = tg.get("price_drop_pct", 5)
    for it in good:
        old = state.get(it["url"])
        it["is_new"] = old is None
        it["drop_from"] = None
        if old and it["price"] <= old["price"] * (1 - drop_pct / 100):
            it["drop_from"] = old["price"]
    good.sort(key=lambda i: score(i, cfg), reverse=True)
    picks = good[: tg.get("max_items", 8)]

    size = cfg["filters"].get("size")
    for it in picks:
        it["size_ok"] = check_size(it["url"], size) if size else None
        time.sleep(1)

    changed = [p for p in picks if p["is_new"] or p["drop_from"]]
    if tg.get("only_changes") and not changed:
        picks = []
    if not picks and not tg.get("notify_when_empty", True):
        print("Res a notificar.")
        return
    msg = build_message(cfg, picks, report, len(good))

    if args.dry_run:
        print(strip_html(msg))
        return
    send_telegram(msg)
    today = dt.date.today().isoformat()
    for it in good:
        state[it["url"]] = {"price": it["price"], "name": it["name"], "seen": today}
    save_state(state)
    print("Enviat.")


if __name__ == "__main__":
    main()
