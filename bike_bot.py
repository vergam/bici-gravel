#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bot de Telegram que vigila botigues online i t'envia cada dia les millors
ofertes de bicis gravel que compleixen els teus requisits (config.yaml).

Ús:
  python bike_bot.py --get-chat-id   # mostra el teu chat_id (després d'escriure al bot)
  python bike_bot.py --test          # envia un missatge de prova
  python bike_bot.py --dry-run       # fa la cerca i ho imprimeix, sense enviar res
  python bike_bot.py                 # cerca i envia el resum per Telegram

Variables d'entorn: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""
import argparse
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

_NUM = r"(\d{1,3}(?:[.\u00a0 ]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_SUFFIX = re.compile(_NUM + r"\s*(?:€|EUR)", re.I)
_PREFIX = re.compile(r"€\s*" + _NUM)


def to_float(s):
    """Converteix '1.099,00', '1,099.00' o '1099' a float."""
    s = s.replace("\u00a0", "").replace(" ", "")
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


# ---------------------------------------------------------------- extracció

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


def get_text(url):
    if not allowed_by_robots(url):
        raise PermissionError("robots.txt no permet llegir aquesta URL")
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def fetch(url):
    return BeautifulSoup(get_text(url), "html.parser")


def extract_rss(xml_text, base_url):
    """Feed RSS/Atom (p. ex. el d'una cerca de Chollometro). El preu s'agafa
    del títol o de la descripció."""
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
        desc = BeautifulSoup(d.get("description") or d.get("summary") or "", "html.parser").get_text(" ")
        prices = [p for p in find_prices(f"{title} {desc}") if p >= 300]
        if not title or not link or not prices:
            continue
        # a Chollometro el preu final sol ser el primer que surt al títol
        first = find_prices(title)
        price = first[0] if first and first[0] >= 300 else min(prices)
        items.append({"name": title[:140], "url": urljoin(base_url, link), "price": price,
                      "list_price": None, "in_stock": None})
    return items


def page_urls(src):
    """Genera les URLs d'un llistat paginat.
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


def _offers_info(offers):
    """Retorna (preu_mínim, en_estoc) d'un camp 'offers' de JSON-LD."""
    if isinstance(offers, dict):
        offers = [offers]
    prices, stock = [], []
    for o in offers or []:
        if not isinstance(o, dict):
            continue
        cur = o.get("priceCurrency")
        specs = o.get("priceSpecification")
        if isinstance(specs, dict):
            specs = [specs]
        cands = [o.get("price"), o.get("lowPrice")]
        for sp in specs or []:
            if isinstance(sp, dict):
                cands.append(sp.get("price"))
                cur = cur or sp.get("priceCurrency")
        if cur and str(cur).upper() != "EUR":
            continue
        for c in cands:
            try:
                v = float(str(c).replace(",", "."))
                if v > 0:
                    prices.append(v)
            except (TypeError, ValueError):
                pass
        if o.get("availability") is not None:
            stock.append("outofstock" not in str(o["availability"]).lower())
    price = min(prices) if prices else None
    in_stock = any(stock) if stock else None
    return price, in_stock


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
            price, in_stock = _offers_info(node.get("offers"))
            if not name or not price:
                continue
            url = urljoin(base_url, node.get("url") or base_url)
            items.append({"name": name, "url": url, "price": price,
                          "list_price": None, "in_stock": in_stock})
    return items


def extract_cards(soup, base_url):
    """Plan B per a pàgines de llistat sense JSON-LD: busca enllaços amb preu a prop."""
    found = {}
    for a in soup.find_all("a", href=True):
        block, txt, prices = a, "", []
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
                if not x["href"].startswith(("#", "javascript"))
            }
            if len(paths) > 2:
                prices = []
                break
            prices = [p for p in find_prices(txt) if p >= 300]
            if prices:
                break
        if not prices or len(txt) > 700:
            continue
        name = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if len(name) < 8:
            img = a.find("img")
            name = (img.get("alt") or "").strip() if img else ""
        if len(name) < 8 or "€" in name:
            continue
        url = urljoin(base_url, a["href"])
        price = min(prices)
        lp = max(prices)
        item = {"name": name[:140], "url": url, "price": price,
                "list_price": lp if lp > price * 1.02 else None, "in_stock": None}
        if url not in found or len(name) > len(found[url]["name"]):
            found[url] = item
    return list(found.values())


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
    if item.get("list_price"):
        s += (1 - item["price"] / item["list_price"]) * 50
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

def read_source(src):
    """Llegeix una font (totes les seves pàgines) i retorna els productes."""
    found = []
    for url in page_urls(src):
        text = get_text(url)
        if src.get("type") == "rss":
            page = extract_rss(text, url)
        else:
            soup = BeautifulSoup(text, "html.parser")
            page = extract_jsonld(soup, url) or extract_cards(soup, url)
        if not page:
            break  # pàgina buida: no té sentit seguir paginant
        found += page
        time.sleep(1.5)
    return found


def collect(cfg):
    global RESPECT_ROBOTS
    RESPECT_ROBOTS = cfg.get("respect_robots", True)
    items, empty = {}, []
    for src in cfg.get("sources", []):
        if src.get("enabled", True) is False or not src.get("url"):
            continue
        try:
            found = read_source(src)
        except Exception as e:
            print(f"[!] {src['name']}: {e}", file=sys.stderr)
            empty.append(src["name"])
            continue
        print(f"[{src['name']}] {len(found)} productes")
        if not found:
            empty.append(src["name"])
        for it in found:
            it["shop"] = src["name"]
            items.setdefault(it["url"], it)
        time.sleep(1.5)
    for w in cfg.get("watch", []):
        try:
            soup = fetch(w["url"])
        except Exception as e:
            print(f"[!] {w.get('name', w['url'])}: error {e}", file=sys.stderr)
            empty.append(w.get("name", w["url"]))
            continue
        found = extract_jsonld(soup, w["url"])
        print(f"[{w.get('name', 'fitxa')}] {len(found)} productes")
        if not found:
            empty.append(w.get("name", w["url"]))
        for it in found:
            it["shop"] = w.get("name") or urlparse(w["url"]).netloc
            items.setdefault(it["url"], it)
        time.sleep(1.5)
    return list(items.values()), empty


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


def build_message(cfg, picks, empty, total):
    f = cfg["filters"]
    today = dt.date.today().strftime("%d/%m")
    head = (f"🚲 <b>Ofertes gravel · {today}</b>\n"
            f"Requisits: ≤ {fmt_eur(f['max_price'])} · talla {f.get('size', '-')}\n")
    if not picks:
        body = "\nAvui no he trobat res nou que compleixi els requisits."
    else:
        rows = []
        for n, it in enumerate(picks, 1):
            tags = []
            if it["is_new"]:
                tags.append("🆕")
            if it.get("drop_from"):
                tags.append(f"📉 abans {fmt_eur(it['drop_from'])}")
            price = fmt_eur(it["price"])
            if it.get("list_price"):
                pct = round((1 - it["price"] / it["list_price"]) * 100)
                price += f" (−{pct}%)"
            size_txt = ""
            if f.get("size"):
                mark = {True: "✅", False: "❌", None: "❓"}[it.get("size_ok")]
                size_txt = f" · talla {f['size']} {mark}"
            rows.append(
                f"{n}. {' '.join(tags)} <a href=\"{html.escape(it['url'], quote=True)}\">"
                f"{html.escape(it['name'])}</a>\n   {price}{size_txt} · {html.escape(it['shop'])}"
            )
        body = "\n" + "\n".join(rows)
    foot = f"\n\n{total} coincidències en total."
    if empty:
        foot += "\n⚠️ Sense resultats: " + html.escape(", ".join(empty))
    return head + body + foot


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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--get-chat-id", action="store_true")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    args = ap.parse_args()

    if args.get_chat_id:
        return get_chat_id()
    if args.test:
        send_telegram("✅ El bot de gravel funciona.")
        return

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    tg = cfg.get("telegram", {})
    state = load_state()

    items, empty = collect(cfg)
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
    msg = build_message(cfg, picks, empty, len(good))

    if args.dry_run:
        print(re.sub(r"<[^>]+>", "", html.unescape(msg)))
        return
    send_telegram(msg)
    today = dt.date.today().isoformat()
    for it in good:
        state[it["url"]] = {"price": it["price"], "name": it["name"], "seen": today}
    save_state(state)
    print("Enviat.")


if __name__ == "__main__":
    main()
