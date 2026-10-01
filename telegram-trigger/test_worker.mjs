// Prova el worker sense xarxa: simula Telegram i GitHub amb un fetch fals.
import worker, { parseCommand } from "./worker.js";
import assert from "node:assert/strict";

// ---- parseCommand
assert.deepEqual(parseCommand("/ofertes"), { action: "offers", maxPrice: "" });
assert.deepEqual(parseCommand("/ofertes 1200"), { action: "offers", maxPrice: "1200" });
assert.deepEqual(parseCommand("/ofertes 1.200 €".replace(".", "")), { action: "offers", maxPrice: "1200" });
assert.deepEqual(parseCommand("/ofertes@gravel_bot 900"), { action: "offers", maxPrice: "900" });
assert.equal(parseCommand("/ofertes molt").action, "help");
assert.deepEqual(parseCommand("/diagnosi"), { action: "diagnose" });
assert.equal(parseCommand("/start").action, "help");
assert.equal(parseCommand("hola"), null);

// ---- fetch fals
const calls = [];
let githubStatus = 204;
globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), init });
  if (String(url).startsWith("https://api.github.com/")) {
    return new Response(githubStatus === 204 ? null : '{"message":"Resource not accessible"}', { status: githubStatus });
  }
  return new Response("{}", { status: 200 });
};

const env = {
  TELEGRAM_TOKEN: "123:ABC", ALLOWED_CHAT_ID: "42", WEBHOOK_SECRET: "s3cret",
  GITHUB_TOKEN: "ghp_x", GITHUB_REPO: "vergam/bici-gravel",
};
const req = (body, secret = "s3cret", method = "POST") =>
  new Request("https://w.example/", {
    method, headers: { "x-telegram-bot-api-secret-token": secret, "content-type": "application/json" },
    body: method === "POST" ? JSON.stringify(body) : undefined,
  });
const msg = (text, chat = 42) => ({ message: { text, chat: { id: chat } } });

// 1) /ofertes 1200 del teu chat: llança el workflow amb els inputs correctes i respon
let r = await worker.fetch(req(msg("/ofertes 1200")), env);
assert.equal(r.status, 200);
const gh = calls.find((c) => c.url.includes("api.github.com"));
assert.equal(gh.url, "https://api.github.com/repos/vergam/bici-gravel/actions/workflows/daily.yml/dispatches");
assert.equal(gh.init.headers.authorization, "Bearer ghp_x");
assert.deepEqual(JSON.parse(gh.init.body), { ref: "main", inputs: { mode: "offers", max_price: "1200" } });
const tg = calls.find((c) => c.url.includes("api.telegram.org"));
assert.match(JSON.parse(tg.init.body).text, /Buscant ofertes fins a 1200/);

// 2) /diagnosi
calls.length = 0;
await worker.fetch(req(msg("/diagnosi")), env);
assert.deepEqual(JSON.parse(calls[0].init.body).inputs, { mode: "diagnose", max_price: "" });

// 3) Un altre xat: s'ignora (ni GitHub ni Telegram)
calls.length = 0;
r = await worker.fetch(req(msg("/ofertes", 999)), env);
assert.equal(r.status, 200);
assert.equal(calls.length, 0);

// 4) Secret incorrecte: 403 i res més
r = await worker.fetch(req(msg("/ofertes"), "dolent"), env);
assert.equal(r.status, 403);
assert.equal(calls.length, 0);

// 5) GET i JSON malformat
r = await worker.fetch(req(null, "s3cret", "GET"), env);
assert.equal(r.status, 200);
r = await worker.fetch(new Request("https://w.example/", {
  method: "POST", headers: { "x-telegram-bot-api-secret-token": "s3cret" }, body: "no és json" }), env);
assert.equal(r.status, 400);

// 6) GitHub falla: t'ho diu
githubStatus = 403;
calls.length = 0;
await worker.fetch(req(msg("/ofertes")), env);
const last = JSON.parse(calls[calls.length - 1].init.body).text;
assert.match(last, /No he pogut llançar el bot \(GitHub ha respost 403\)/);

// 7) /start mostra els botons
calls.length = 0;
await worker.fetch(req(msg("/start")), env);
const start = JSON.parse(calls[0].init.body);
assert.ok(start.reply_markup.keyboard[0].some((b) => b.text === "/ofertes"));

console.log("OK worker");
