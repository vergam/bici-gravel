import sys, json
from pathlib import Path
from bs4 import BeautifulSoup
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bike_bot as b

# --- preus
assert b.to_float("1.099,00") == 1099.0
assert b.to_float("1,099.00") == 1099.0
assert b.to_float("1099") == 1099.0
assert b.to_float("35,20") == 35.2
assert b.find_prices("Antes 1.699,00 € ahora 1.099,00 €") == [1699.0, 1099.0]
assert b.find_prices("GRX 600 Jakar 30 1.099 €") == [1099.0]

# --- JSON-LD
ld = {"@type": "Product", "name": "Bicicleta Megamo Jakar 30 2026", "url": "/jakar",
      "offers": {"price": "1099", "priceCurrency": "EUR",
                 "availability": "https://schema.org/InStock"}}
ld_usd = {"@type": "Product", "name": "Bike GRX USD", "offers": {"price": "1500", "priceCurrency": "USD"}}
ld_out = {"@type": "Product", "name": "Silex 400 GRX", "url": "/silex",
          "offers": [{"price": "1168.07", "priceCurrency": "EUR",
                      "availability": "https://schema.org/OutOfStock"}]}
html = "<html>" + "".join(
    f'<script type="application/ld+json">{json.dumps(x)}</script>' for x in (ld, ld_usd, ld_out)
) + "</html>"
items = b.extract_jsonld(BeautifulSoup(html, "html.parser"), "https://shop.test/cat")
names = {i["name"] for i in items}
assert names == {"Bicicleta Megamo Jakar 30 2026", "Silex 400 GRX"}, names
jak = [i for i in items if "Jakar" in i["name"]][0]
assert jak["url"] == "https://shop.test/jakar" and jak["price"] == 1099 and jak["in_stock"] is True

# --- targetes (sense JSON-LD)
cards = """
<div class="grid">
 <div class="p"><a href="/canyon-grizl-7" title="Canyon Grizl 7 GRX RX820 12v">x</a>
   <span>1.799,00 €</span><span>1.499,00 €</span><span>35,20 €/mes</span></div>
 <div class="p"><a href="/kalazy">Ridley Kalazy Claris MDB 2x8</a><span>799,00 €</span></div>
 <div class="p"><a href="/cranks">Shimano GRX bielas 2x10</a><span>74,99 €</span></div>
 <div class="p"><a href="/arcadex">Bianchi Arcadex Comp GRX carbono</a><span>3.050,00 €</span><span>1.909,00 €</span></div>
</div>"""
cs = b.extract_cards(BeautifulSoup(cards, "html.parser"), "https://shop.test/")
byurl = {c["url"]: c for c in cs}
assert byurl["https://shop.test/canyon-grizl-7"]["price"] == 1499.0
assert byurl["https://shop.test/canyon-grizl-7"]["list_price"] == 1799.0
assert "https://shop.test/cranks" not in byurl  # <300 € descartat

# --- filtres
cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))
f = cfg["filters"]
allitems = items + cs
good = [i for i in allitems if b.matches(i, f)]
gn = {i["name"] for i in good}
print("Passen el filtre:", gn)
assert "Bicicleta Megamo Jakar 30 2026" in gn          # OK
assert "Canyon Grizl 7 GRX RX820 12v" in gn             # 1.499 <= 1.500
assert "Ridley Kalazy Claris MDB 2x8" not in gn         # Claris excl.
assert "Silex 400 GRX" not in gn                        # sense estoc
assert "Bianchi Arcadex Comp GRX carbono" not in gn     # 1.909 > 1.500

# --- missatge
for g in good:
    g["shop"] = "Test"; g["is_new"] = True; g["drop_from"] = None; g["size_ok"] = None
good.sort(key=lambda i: b.score(i, cfg), reverse=True)
msg = b.build_message(cfg, good, ["Canyon Outlet"], len(good))
print(msg)
assert "Canyon Outlet" in msg and "talla L" in msg
# --- RSS
rss = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Bicicleta gravel Megamo Jakar 30 GRX por 1.099€</title>
<link>https://deals.test/jakar</link><description>&lt;p&gt;Antes 1.699€&lt;/p&gt;</description></item>
<item><title>Casco por 49€</title><link>https://deals.test/casco</link><description>casco</description></item>
</channel></rss>"""
r = b.extract_rss(rss, "https://deals.test/feed")
assert len(r) == 1 and r[0]["price"] == 1099.0 and r[0]["url"] == "https://deals.test/jakar", r

# --- paginació
src = {"url": "https://x.test/g", "pages": 3, "page_size": 40,
       "page_url": "https://x.test/g?from={from}&size={size}"}
assert b.page_urls(src) == ["https://x.test/g", "https://x.test/g?from=40&size=40",
                            "https://x.test/g?from=80&size=40"], b.page_urls(src)
assert b.page_urls({"url": "https://x.test/g"}) == ["https://x.test/g"]
src2 = {"url": "https://y.test/c", "pages": 2, "page_url": "https://y.test/c?page={n}"}
assert b.page_urls(src2)[1] == "https://y.test/c?page=2"

# --- la config real carrega i les fonts desactivades/buides s'ignoren
assert any(s["name"] == "Decathlon" and s["pages"] == 5 for s in cfg["sources"])
off = [s for s in cfg["sources"] if s.get("enabled") is False]
assert len(off) >= 4 and all(not s["url"] for s in off)

# --- robots.txt: es respecta (simulat, sense xarxa)
class FakeResp:
    status_code = 200
    text = "User-agent: *\nDisallow: /search\n"
b._ROBOTS.clear()
orig = b.requests.get
b.requests.get = lambda *a, **k: FakeResp()
try:
    assert b.allowed_by_robots("https://shop.test/search?q=bici") is False
    assert b.allowed_by_robots("https://shop.test/gravel") is True
finally:
    b.requests.get = orig
print("OK")
