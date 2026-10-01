# Bot de Telegram per a ofertes de gravel

Cada dia revisa unes 35 botigues, outlets i webs de segona mà, i t'envia un resum
amb les millors ofertes que compleixen els teus requisits (`config.yaml`):
**preu, preu original i descompte en %**. També el pots llançar quan vulguis
des de Telegram (`/ofertes`).

## 1. Crear el bot a Telegram

1. Parla amb **@BotFather**, envia `/newbot` i guarda el **token**.
2. Escriu un "hola" al teu bot nou.
3. Obre `https://api.telegram.org/bot<EL_TEU_TOKEN>/getUpdates` al navegador i
   busca `"chat":{"id":` : aquest número és el teu **chat_id**.

## 2. Penjar-lo a GitHub (gratis, sense servidor)

1. Repositori **privat** amb el contingut d'aquesta carpeta (no la carpeta en si).
   La carpeta `.github/workflows/daily.yml` ha d'estar a l'arrel.
2. *Settings → Secrets and variables → Actions → New repository secret*:
   `TELEGRAM_TOKEN` i `TELEGRAM_CHAT_ID`.
3. *Actions → gravel-bot → Run workflow*. Després s'executa sol cada dia.

## 3. Llançar-lo des de Telegram (`/ofertes`)

Telegram no pot cridar GitHub directament, així que fem servir un **Cloudflare
Worker** (gratis, sense servidor) que fa de pont. Només respon al **teu** chat.

| Comanda | Què fa |
|---|---|
| `/ofertes` | Busca ofertes ara i te les envia |
| `/ofertes 1200` | El mateix, amb preu màxim de 1.200 € |
| `/diagnosi` | Prova totes les botigues i t'explica quines funcionen i quines no |

**Pas a pas (uns 10 minuts):**

1. **Token de GitHub.** A https://github.com/settings/personal-access-tokens/new :
   *Repository access → Only select repositories → bici-gravel*;
   *Permissions → Repository permissions → **Actions: Read and write***. Genera'l i copia'l
   (`github_pat_...`).
2. **Worker.** Crea un compte gratuït a https://dash.cloudflare.com → *Workers & Pages →
   Create → Create Worker*, posa-li de nom `gravel-bot` i prem *Deploy*. Després *Edit code*,
   esborra-ho tot, enganxa-hi el contingut de `telegram-trigger/worker.js` i *Deploy*.
3. **Secrets del Worker.** *Settings → Variables and Secrets → Add*, tipus **Secret**:

   | Nom | Valor |
   |---|---|
   | `TELEGRAM_TOKEN` | el token de @BotFather |
   | `ALLOWED_CHAT_ID` | el teu chat_id |
   | `WEBHOOK_SECRET` | una paraula llarga inventada (només lletres i números) |
   | `GITHUB_TOKEN` | el token del pas 1 |
   | `GITHUB_REPO` | `vergam/bici-gravel` |

   Desa i torna a fer *Deploy*.
4. **Connectar Telegram amb el Worker.** Obre al navegador (canvia les tres parts):
   `https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://gravel-bot.<EL_TEU_SUBDOMINI>.workers.dev&secret_token=<WEBHOOK_SECRET>`
   Ha de respondre `"Webhook was set"`. L'URL del Worker surt a la pàgina del Worker.
5. **Menú de comandes (opcional).**
   `https://api.telegram.org/bot<TOKEN>/setMyCommands?commands=[{"command":"ofertes","description":"Busca ofertes ara"},{"command":"diagnosi","description":"Estat de les botigues"}]`
6. Escriu `/start` al bot: et sortiran dos botons, **/ofertes** i **/diagnosi**.

Si alguna cosa falla, el bot t'ho diu per Telegram. El token de GitHub caduca
(l'any, com a màxim): quan deixi de funcionar, genera'n un de nou i canvia el secret.

## 4. Botigues i errors

A `config.yaml`, dins de `sources`, cada botiga és una pàgina de llistat:

```yaml
- name: Nom de la botiga
  url: https://...                  # URL de la categoria
  pages: 3                          # (opcional) pàgines a llegir
  page_url: "https://...?page={n}"  # (opcional) {n}, o {from} i {size}
  render: auto                      # auto | true (sempre navegador) | false
  load_more: "Ver más productos"    # (opcional) botó que cal prémer
  optional: true                    # (opcional) si falla, no surt al missatge diari
```

- Per a cada pàgina el bot prova primer una lectura normal i, si la web bloqueja o la
  pàgina carrega amb JavaScript, **obre un Chromium real** (a GitHub Actions ja s'instal·la sol).
- Les fonts amb `optional: true` són les que no he pogut verificar des d'on he treballat:
  s'intenten cada dia, però no et fan soroll al missatge. Executa **/diagnosi** i activa
  de manera definitiva (treu `optional`) les que surtin ✅.
- Quan una font normal falla, el missatge diari porta un ⚠️ amb **el motiu** (bloquejat HTTP 403,
  0 productes, robots.txt...) i **un enllaç directe** a la botiga.
- El bot respecta el `robots.txt` de cada web (`respect_robots: true`).
- Si una web bloqueja les IPs de GitHub fins i tot amb navegador, l'única sortida és executar el
  bot a casa teva (un `cron` en un ordinador o una Raspberry Pi):
  `0 8 * * * cd /ruta/a/gravel-bot && TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... python3 bike_bot.py`

## 5. Com decideix què t'envia

- Filtra per preu (`min_price`/`max_price`), per paraules que han de sortir
  (`include_any`: GRX, Apex, models de gravel) i per paraules prohibides (`exclude`: Claris, Sora...).
- Descarta els productes sense estoc.
- **Preu**: l'actual. **Descompte**: es calcula a partir del preu original (el tatxat o
  l'etiquetat com "Precio original", "PVP", "En lugar de"...). Mai es confon l'import
  estalviat ("Ahorras 1.500 €") ni les quotes mensuals amb el preu.
- Les ordena per descompte, bonus (carboni, GRX 600/800, 12 velocitats) i preu.
- Marca 🆕 les que no havia vist i 📉 les que han baixat de preu.

## Límits

- **La talla** només es pot comprovar si la botiga la indica a les dades de la fitxa.
  Normalment surt ❓: obre l'enllaç i mira si hi ha la teva talla.
- Si una botiga canvia el disseny, el bot pot deixar de trobar-hi res: t'ho avisarà amb ⚠️.
- Només té en compte el nom del producte; si una bici no diu "GRX" ni el model és a
  `include_any`, no la veurà. Afegeix-hi models a mesura que en descobreixis.

## Proves

```bash
python tests/test_offline.py     # preus, descomptes, filtres i missatges
python tests/test_browser.py     # navegador, paginació i informe (cal Playwright)
node telegram-trigger/test_worker.mjs
```
