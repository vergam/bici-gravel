// Cloudflare Worker: rep els missatges de Telegram i llança el bot a GitHub Actions.
//
//   /ofertes            -> busca ofertes ara
//   /ofertes 1200       -> busca ofertes amb un preu màxim de 1.200 €
//   /diagnosi           -> comprova quines botigues es poden llegir
//   /start, /ajuda      -> mostra els botons
//
// Només respon al teu chat (ALLOWED_CHAT_ID) i només si la petició porta el secret
// del webhook (WEBHOOK_SECRET); qualsevol altra cosa s'ignora.
//
// Variables (Settings -> Variables and Secrets), totes com a "Secret":
//   TELEGRAM_TOKEN   token de @BotFather
//   ALLOWED_CHAT_ID  el teu chat_id
//   WEBHOOK_SECRET   una cadena llarga qualsevol (lletres i números)
//   GITHUB_TOKEN     token de GitHub (fine-grained) amb permís "Actions: Read and write"
//   GITHUB_REPO      p. ex. vergam/bici-gravel
// Opcionals: WORKFLOW_FILE (per defecte daily.yml), GITHUB_REF (per defecte main)

const KEYBOARD = {
  keyboard: [[{ text: "/ofertes" }, { text: "/diagnosi" }]],
  resize_keyboard: true,
  is_persistent: true,
};

export function parseCommand(text) {
  const m = /^\/([a-zA-Z_]+)(?:@\w+)?(?:\s+(.*))?$/.exec((text || "").trim());
  if (!m) return null;
  const cmd = m[1].toLowerCase();
  const arg = (m[2] || "").trim();
  if (cmd === "ofertes" || cmd === "ofertas") {
    const n = arg.replace(",", ".").match(/^(\d{3,5})(?:\.\d+)?\s*(?:€|eur|euros)?$/i);
    if (arg && !n) return { action: "help", error: "No entenc el preu. Exemple: /ofertes 1200" };
    return { action: "offers", maxPrice: n ? n[1] : "" };
  }
  if (cmd === "diagnosi" || cmd === "diagnostic") return { action: "diagnose" };
  if (cmd === "start" || cmd === "ajuda" || cmd === "help") return { action: "help" };
  return { action: "help", error: "Comanda desconeguda." };
}

async function telegram(env, method, body) {
  return fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function say(env, chatId, text, keyboard = true) {
  const body = { chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: true };
  if (keyboard) body.reply_markup = KEYBOARD;
  return telegram(env, "sendMessage", body);
}

export async function dispatchWorkflow(env, mode, maxPrice) {
  const file = env.WORKFLOW_FILE || "daily.yml";
  const ref = env.GITHUB_REF || "main";
  const res = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${file}/dispatches`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        accept: "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
        "user-agent": "gravel-bot-telegram-trigger",
        "content-type": "application/json",
      },
      body: JSON.stringify({ ref, inputs: { mode, max_price: maxPrice || "" } }),
    }
  );
  return res; // GitHub respon 204 sense cos si tot va bé
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
    }
    const msg = update.message || update.edited_message;
    if (!msg || !msg.text || String(msg.chat?.id) !== String(env.ALLOWED_CHAT_ID)) {
      return new Response("ok"); // altres xats: silenci
    }
    const chatId = msg.chat.id;
    const cmd = parseCommand(msg.text);
    if (!cmd) return new Response("ok");

    if (cmd.action === "help") {
      await say(
        env,
        chatId,
        (cmd.error ? `${cmd.error}\n\n` : "") +
          "🚲 <b>Bot de gravel</b>\n/ofertes — busca ofertes ara\n/ofertes 1200 — amb preu màxim\n/diagnosi — estat de les botigues"
      );
      return new Response("ok");
    }

    const res = await dispatchWorkflow(env, cmd.action === "diagnose" ? "diagnose" : "offers", cmd.maxPrice);
    if (res.status === 204) {
      await say(
        env,
        chatId,
        cmd.action === "diagnose"
          ? "🩺 Comprovant les botigues… t'envio el resultat d'aquí a 3-5 minuts."
          : `🔎 Buscant ofertes${cmd.maxPrice ? ` fins a ${cmd.maxPrice} €` : ""}… t'envio el resultat d'aquí a 2-4 minuts.`
      );
    } else {
      const detail = (await res.text()).slice(0, 200);
      await say(env, chatId, `⚠️ No he pogut llançar el bot (GitHub ha respost ${res.status}).\n<code>${detail.replace(/[<>&]/g, "")}</code>`);
    }
    return new Response("ok");
  },
};
