import json
import sys
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bike_bot as b  # noqa: E402

cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def check(name, got, want):
    assert got == want, f"{name}: esperava {want!r} i he obtingut {got!r}"


# ------------------------------------------------------------------ números
check("to_float 1", b.to_float("1.099,00"), 1099.0)
check("to_float 2", b.to_float("1,099.00"), 1099.0)
check("to_float 3", b.to_float("1099"), 1099.0)
check("to_float 4", b.to_float("35,20"), 35.2)

# ------------------------------------------------------------------ preu / original / estalvi
# Text real de les targetes de l'outlet de Canyon: preu, "Precio original", "Ahorras", quota
canyon = ("Grizl CF 8 ESC w/ ECLIPS SRAM Force XPLR AXS 2.499 € "
          "Precio original 3.999 € Ahorras 1.500 € o desde 42 €/mes")
check("canyon", b.resolve_prices(canyon), (2499.0, 3999.0))   # abans: agafava 1.500 € com a preu

canyon2 = "Grail CFR Di2 5.499 € Precio original 7.499 € Ahorras 2.000 € o desde 92 €/mes"
check("canyon2", b.resolve_prices(canyon2), (5499.0, 7499.0))

# Només "Ahorras" gran i sense preu original: es reconstrueix
only_save = "Grizl 7 GRX 1.499 € Ahorras 300 € o desde 125 €/mes"
check("només estalvi", b.resolve_prices(only_save), (1499.0, 1799.0))

# Bike24: PVPR + preu + "En lugar de"
bike24 = "Bianchi ARCADEX COMP PVPR 3.050,01 € 1.909,00 €* En lugar de 3.050,01 €"
check("bike24", b.resolve_prices(bike24), (1909.0, 3050.01))

# Bikestocks: "Precio base"
bs = "BICICLETA BIANCHI NIRONE ALLROAD GRX 600 2026 1.521,50 € Precio base 1.790,00 €"
check("bikestocks", b.resolve_prices(bs), (1521.5, 1790.0))

# Import amb signe menys enganxat (-1.000,00 €) no és el preu
check("menys", b.resolve_prices("Bianchi Impulso GRX 600 -1.000,00 € 1.499,00 € Precio base 2.499,00 €"),
      (1499.0, 2499.0))

# Dos preus sense etiqueta: el baix és l'actual
check("sense etiqueta", b.resolve_prices("Jakar 30 1.699,00 € 1.099,00 €"), (1099.0, 1699.0))

# Només percentatge
p, lp = b.resolve_prices("Silex 4000 1.881,00 € -14%")
check("percentatge preu", p, 1881.0)
assert abs(lp - 2187.2) < 1, lp

# Quota mensual i enviament mai són el preu
check("quota", b.resolve_prices("Bici GRX 1.299 € financiación desde 35,20 €/mes envío gratis 500 €"),
      (1299.0, None))
check("res", b.resolve_prices("Casco 49 € desde 12 €/mes"), (None, None))

check("pct", b.discount_pct({"price": 2499, "list_price": 3999}), 38)
check("pct cap", b.discount_pct({"price": 1099, "list_price": None}), None)

# Preu tatxat marcat amb <del> i amb una classe 'regular-price'
html_cards = """
<div class="grid">
 <div class="p"><a href="/decathlon-gravel-1" title="VAN RYSEL Bicicleta Gravel GRX 2x12">x</a>
   <span class="price">1.359,00 €</span><del>1.699,00 €</del></div>
 <div class="p"><a href="/presta-1" title="Megamo Jakar 30 2026 GRX">x</a>
   <span class="regular-price">1.699,00 €</span><span class="price">1.099,00 €</span></div>
 <div class="p"><a href="/cranks" title="Shimano GRX bielas 2x10">x</a><span>74,99 €</span></div>
 <div class="p"><a href="/canyon-card" title="Grizl CF 8 ESC w/ ECLIPS">x</a>
   <span>2.499 €</span><span>Precio original <s>3.999 €</s></span><span>Ahorras 1.500 €</span><span>o desde 42 €/mes</span></div>
</div>"""
cs = {c["url"]: c for c in b.extract_cards(BeautifulSoup(html_cards, "html.parser"), "https://shop.test/")}
check("card del", (cs["https://shop.test/decathlon-gravel-1"]["price"],
                   cs["https://shop.test/decathlon-gravel-1"]["list_price"]), (1359.0, 1699.0))
check("card regular-price", (cs["https://shop.test/presta-1"]["price"],
                             cs["https://shop.test/presta-1"]["list_price"]), (1099.0, 1699.0))
check("card canyon", (cs["https://shop.test/canyon-card"]["price"],
                      cs["https://shop.test/canyon-card"]["list_price"]), (2499.0, 3999.0))
assert "https://shop.test/cranks" not in cs

# ------------------------------------------------------------------ JSON-LD
ld = {"@type": "Product", "name": "Bicicleta Megamo Jakar 30 2026", "url": "/jakar",
      "offers": {"price": "1099", "priceCurrency": "EUR",
                 "availability": "https://schema.org/InStock"}}
ld_usd = {"@type": "Product", "name": "Bike GRX USD", "offers": {"price": "1500", "priceCurrency": "USD"}}
ld_out = {"@type": "Product", "name": "Silex 400 GRX", "url": "/silex",
          "offers": [{"price": "1168.07", "priceCurrency": "EUR",
                      "availability": "https://schema.org/OutOfStock"}]}
ld_list = {"@type": "Product", "name": "Silex 5000 GRX", "url": "/silex5",
           "offers": [{"priceSpecification": [
               {"price": "299.99", "priceCurrency": "EUR"},
               {"price": "2294.15", "priceCurrency": "EUR", "priceType": "https://schema.org/ListPrice"}]}]}
doc = "<html>" + "".join(
    f'<script type="application/ld+json">{json.dumps(x)}</script>' for x in (ld, ld_usd, ld_out, ld_list)
) + "</html>"
items = b.extract_jsonld(BeautifulSoup(doc, "html.parser"), "https://shop.test/cat")
check("jsonld noms", {i["name"] for i in items},
      {"Bicicleta Megamo Jakar 30 2026", "Silex 400 GRX", "Silex 5000 GRX"})
jak = [i for i in items if "Jakar" in i["name"]][0]
check("jsonld jakar", (jak["url"], jak["price"], jak["in_stock"]), ("https://shop.test/jakar", 1099.0, True))
sx = [i for i in items if "5000" in i["name"]][0]
check("jsonld listprice", (sx["price"], sx["list_price"]), (299.99, 2294.15))

# Microdata
micro = """<div itemscope itemtype="https://schema.org/Product"><a href="/m1" itemprop="url">
<span itemprop="name">Giant Revolt 1 GRX</span></a><meta itemprop="price" content="1399"></div>"""
mi = b.extract_microdata(BeautifulSoup(micro, "html.parser"), "https://shop.test/")
check("microdata", (mi[0]["name"], mi[0]["price"]), ("Giant Revolt 1 GRX", 1399.0))

# ------------------------------------------------------------------ filtres
f = cfg["filters"]
allitems = items + list(cs.values())
good = [i for i in allitems if b.matches(i, f)]
gn = {i["name"] for i in good}
assert "Bicicleta Megamo Jakar 30 2026" in gn
assert "Silex 400 GRX" not in gn                 # sense estoc
assert "Bike GRX USD" not in gn                  # no és en euros
assert "Shimano GRX bielas 2x10" not in gn       # component

# ------------------------------------------------------------------ missatge
for g in good:
    g.update(shop="Test", is_new=True, drop_from=None, size_ok=None)
good.sort(key=lambda i: b.score(i, cfg), reverse=True)
report = [
    {"name": "Canyon Outlet", "url": "https://canyon.test", "ok": False, "reason": "bloquejat (HTTP 403)", "n": 0},
    {"name": "Opcional", "url": "https://x.test", "ok": False, "reason": "x", "optional": True, "n": 0},
]
msg = b.build_message(cfg, good, report, len(good))
print(b.strip_html(msg))
assert "talla L" in msg and "−" in msg and "abans" in msg
assert "bloquejat (HTTP 403)" in msg and "Opcional" not in msg      # les opcionals no fan soroll
diag = b.build_diagnosis(report + [{"name": "Bona", "url": "https://b.test", "ok": True, "n": 12, "via": "navegador", "reason": ""}])
assert "✅ Bona — 12 productes (navegador)" in diag and "Opcional" in diag

# ------------------------------------------------------------------ RSS / paginació / robots
rss = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Bicicleta gravel Megamo Jakar 30 GRX por 1.099€</title>
<link>https://deals.test/jakar</link><description>&lt;p&gt;Antes 1.699€&lt;/p&gt;</description></item>
<item><title>Casco por 49€</title><link>https://deals.test/casco</link><description>casco</description></item>
</channel></rss>"""
r = b.extract_rss(rss, "https://deals.test/feed")
check("rss", (len(r), r[0]["price"], r[0]["list_price"]), (1, 1099.0, 1699.0))

src = {"url": "https://x.test/g", "pages": 3, "page_size": 40,
       "page_url": "https://x.test/g?from={from}&size={size}"}
check("pag from", b.page_urls(src),
      ["https://x.test/g", "https://x.test/g?from=40&size=40", "https://x.test/g?from=80&size=40"])
check("pag n", b.page_urls({"url": "https://y.test/c", "pages": 2, "page_url": "https://y.test/c?page={n}"})[1],
      "https://y.test/c?page=2")

# Servidor que no declara el charset (típic): el '€' no es pot trencar
class R:
    def __init__(self, content, ctype, enc="ISO-8859-1"):
        self.content, self.headers, self.encoding = content, {"content-type": ctype}, enc
        self.apparent_encoding = "utf-8"

    @property
    def text(self):
        return self.content.decode(self.encoding)


utf = "Jakar 30 1.099,00 € precio original 1.699,00 €".encode("utf-8")
check("sense charset", b.decode_response(R(utf, "text/html")), utf.decode("utf-8"))
check("amb charset", b.decode_response(R(utf, "text/html; charset=utf-8", "utf-8")), utf.decode("utf-8"))
latin = "Jakar 30 1.099,00 € ñ".encode("cp1252")
assert "€" in b.decode_response(R(b'<meta charset="windows-1252">' + latin, "text/html")), "cp1252"
check("preu amb text sense charset",
      b.resolve_prices(b.decode_response(R(utf, "text/html"))), (1099.0, 1699.0))

srcs = cfg["sources"]
assert len(srcs) >= 25, len(srcs)
assert any(s["name"].startswith("Decathlon") and s["pages"] == 5 for s in srcs)
assert any(s["name"] == "Canyon Outlet" and s.get("render") is True and s.get("load_more") for s in srcs)
assert sum(1 for s in srcs if s.get("optional")) >= 10     # fonts per provar que no fan soroll
assert all("url" in s and "name" in s for s in srcs)


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
