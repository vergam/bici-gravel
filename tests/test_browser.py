"""Prova el navegador de rescat amb un servidor local:
  - una pàgina que carrega els productes amb JavaScript i un botó "Ver más productos"
  - una pàgina que respon 403 a les peticions normals però deixa passar un navegador
  - la paginació, que s'atura quan una pàgina no aporta productes nous
Cal tenir instal·lat Playwright amb Chromium; si no, la prova es salta."""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bike_bot as b  # noqa: E402

try:
    import playwright  # noqa: F401
except ImportError:
    print("SKIP: playwright no instal·lat")
    sys.exit(0)


def card(i, price=2499, orig=3999):
    return (f'<div class="p"><a href="/p/{i}">Bicicleta Gravel GRX Model {i}</a>'
            f'<span>{price:,} €</span><span>Precio original {orig:,} €</span>'
            f'<span>Ahorras {orig - price:,} €</span><span>o desde 42 €/mes</span></div>').replace(",", ".")


JS_PAGE = """<html><body><div id="grid"></div><button id="more">Ver más productos</button>
<script>
function add(from, n){ for(let i=from;i<from+n;i++){
  const d=document.createElement('div'); d.className='p';
  d.innerHTML='<a href="/p/'+i+'">Bicicleta Gravel GRX Model '+i+'</a><span>2.499 €</span>'+
              '<span>Precio original 3.999 €</span><span>Ahorras 1.500 €</span><span>o desde 42 €/mes</span>';
  document.getElementById('grid').appendChild(d);} }
setTimeout(()=>add(1,4), 300);
let clicked=false;
document.getElementById('more').onclick=()=>{ if(!clicked){clicked=true; add(5,4);} document.getElementById('more').style.display='none'; };
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            return self._send(404, "no")
        if path == "/js.html":
            return self._send(200, JS_PAGE)
        if path == "/blocked.html":
            if "Sec-Fetch-Mode" not in self.headers:       # requests no l'envia, Chromium sí
                return self._send(403, "forbidden")
            return self._send(200, "<html><body>" + "".join(card(i) for i in range(20, 24)) + "</body></html>")
        if path == "/static.html":
            n = int(self.path.split("page=")[-1]) if "page=" in self.path else 1
            n = min(n, 3)                                   # la 4a pàgina repeteix la 3a
            return self._send(200, "<html><body>" + "".join(card(100 + n * 10 + k) for k in range(3)) + "</body></html>")
        return self._send(404, "no")


srv = HTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
b.RESPECT_ROBOTS = True

try:
    # 1) JavaScript + botó "Ver más productos"
    found, info = b.read_source({"name": "js", "url": base + "/js.html", "load_more": "Ver más productos"})
    names = sorted(i["name"] for i in found)
    assert len(found) == 8, (len(found), names)
    assert info["via"] == "navegador", info
    assert all(i["price"] == 2499.0 and i["list_price"] == 3999.0 for i in found), found[0]
    print("OK  JavaScript + 'Ver más':", len(found), "productes, preu", found[0]["price"], "abans", found[0]["list_price"])

    # 2) 403 a requests, però el navegador passa
    found, info = b.read_source({"name": "blocked", "url": base + "/blocked.html"})
    assert len(found) == 4 and info["via"] == "navegador", (len(found), info)
    print("OK  pàgina que bloqueja peticions normals:", len(found), "productes via", info["via"])

    # 3) Amb render:false s'informa del motiu
    try:
        b.read_source({"name": "blocked", "url": base + "/blocked.html", "render": False})
        raise SystemExit("hauria d'haver fallat")
    except Exception as e:
        assert "bloquejat (HTTP 403)" in b.describe_error(e), b.describe_error(e)
    print("OK  motiu de l'error: bloquejat (HTTP 403)")

    # 4) Paginació: s'atura quan una pàgina no té res de nou
    found, info = b.read_source({"name": "pag", "url": base + "/static.html?page=1", "pages": 6,
                                 "page_url": base + "/static.html?page={n}", "render": False})
    assert len(found) == 9, len(found)          # pàgines 1, 2 i 3 (3 productes cadascuna)
    print("OK  paginació:", len(found), "productes, s'ha aturat sola")

    # 5) collect(): informe per font, amb una font optional que falla
    cfg = {"respect_robots": True, "sources": [
        {"name": "js", "url": base + "/js.html", "load_more": "Ver más productos"},
        {"name": "inexistent", "url": base + "/no-existeix", "optional": True},
        {"name": "desactivada", "url": base + "/js.html", "enabled": False},
    ]}
    items, report = b.collect(cfg)
    byname = {r["name"]: r for r in report}
    assert byname["js"]["ok"] and byname["js"]["n"] == 8
    assert not byname["inexistent"]["ok"] and byname["inexistent"]["optional"]
    assert "desactivada" not in byname
    items2, report2 = b.collect(cfg, include_disabled=True)
    assert "desactivada" in {r["name"] for r in report2}
    print("OK  informe per font:", [(r["name"], r["ok"], r["reason"]) for r in report])
finally:
    b.close_browser()
    srv.shutdown()
print("OK")
