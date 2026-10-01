# Bot de Telegram per a ofertes de gravel

Cada dia revisa unes quantes botigues i t'envia un resum amb les millors
ofertes que compleixen els teus requisits (`config.yaml`).

## 1. Crear el bot a Telegram

1. Obre Telegram i parla amb **@BotFather**.
2. Envia `/newbot`, posa-li un nom i un usuari acabat en `bot`.
3. Et donarà un **token** (del tipus `123456:ABC...`). Guarda'l.
4. Busca el teu bot nou i envia-li qualsevol missatge (p. ex. "hola").

## 2. Provar-ho en local

```bash
pip install -r requirements.txt

export TELEGRAM_TOKEN="el_teu_token"
python bike_bot.py --get-chat-id          # imprimeix el teu chat_id
export TELEGRAM_CHAT_ID="el_teu_chat_id"

python bike_bot.py --test                 # missatge de prova a Telegram
python bike_bot.py --dry-run              # cerca i ho mostra per pantalla
python bike_bot.py                        # cerca i ho envia
```

## 3. Que s'executi cada dia

### Opció A: GitHub Actions (gratis, sense servidor)

1. Crea un repositori **privat** a GitHub i puja-hi aquesta carpeta.
2. A *Settings → Secrets and variables → Actions* afegeix
   `TELEGRAM_TOKEN` i `TELEGRAM_CHAT_ID`.
3. A la pestanya *Actions* pots llançar-lo a mà amb "Run workflow".
   Després s'executa sol cada dia (`.github/workflows/daily.yml`).

Nota: algunes botigues poden bloquejar les IPs de GitHub. Si veus molts
"⚠️ Sense resultats", fes servir l'opció B.

### Opció B: el teu ordinador o una Raspberry Pi (cron)

```
0 8 * * * cd /ruta/a/gravel-bot && TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... python3 bike_bot.py
```

## Com decideix què t'envia

- Filtra per preu (`min_price`/`max_price`), paraules que han de sortir
  (`include_any`: GRX, Apex, models concrets) i paraules prohibides (`exclude`: Claris, Sora...).
- Descarta productes marcats com a sense estoc.
- Les ordena per descompte, bonus (carboni, GRX 600/800, 12 velocitats) i preu.
- Marca 🆕 les que no havia vist i 📉 les que han baixat de preu.

## Afegir més botigues

A `config.yaml`, dins de `sources`, cada botiga és una pàgina de llistat de gravel:

```yaml
- name: Nom de la botiga
  url: https://...                  # URL de la categoria
  pages: 3                          # (opcional) pàgines a llegir
  page_url: "https://...?page={n}"  # (opcional) plantilla de paginació: {n}, o {from} i {size}
```

- Les fonts amb `enabled: false` i `url: ""` (Bike Ocasion, Bicimarket, outlets d'Orbea i
  Specialized, feed RSS de Chollometro) són buides: enganxa-hi l'URL i activa-les.
- Per a feeds RSS/Atom fes servir `type: rss`.
- El bot respecta el `robots.txt` de cada web (`respect_robots: true`): si una URL està
  prohibida, la salta i t'ho diu amb un ⚠️.

## Límits (vés amb ull)

- **La talla** només es pot comprovar si la botiga ho indica al JSON-LD de la fitxa.
  Normalment surt ❓: obre l'enllaç i mira si hi ha la teva talla.
- Les botigues que carreguen els productes amb JavaScript poden donar 0 resultats.
- Si una botiga canvia el disseny, el bot pot deixar de trobar-hi res: t'avisarà amb ⚠️.
- La paginació no està implementada: afegeix a `sources` les pàgines 2, 3...
  com a fonts separades si les vols.
- El filtre mira el nom del producte; si una bici no diu "GRX" ni el model és a `include_any`,
  no la veurà. Afegeix-hi models a mida que en descobreixis.
