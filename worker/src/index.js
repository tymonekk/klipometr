// Klipometr StoreMedia: zapisuje klipy dodane na stronie prosto do repo (Cloudflare Worker).
//
// POST /add     {"link": "..."}            -> zapisuje data/storemedia/<numer filmu>.json w repo
// POST /remove  {"id": "...", "token": "..."} albo {"id": "...", "pin": "..."} -> usuwa ten plik
//               (token dostaje urządzenie, które dodało klip; kod DELETE_PIN działa na każdym urządzeniu)
//
// Gdy TikTok nie przekieruje skróconego linku (np. blokuje Cloudflare), Worker zapisuje sam link jako
// data/storemedia/<czas>.json i numer filmu odczyta bot przy najbliższym przebiegu.
//
// Link może być skrócony (vm.tiktok.com, vt.tiktok.com, tiktok.com/t/...): Worker idzie za przekierowaniami
// TikToka do numeru filmu. Przyjmuje tylko filmy z konta ACCOUNT. Przypomnienie na Telegramie wysyła potem
// bot w GitHub Actions (scripts/remind.py), 6 dni po wrzuceniu klipu.
//
// Sekrety Workera: GITHUB_TOKEN (fine-grained, tylko repo klipometr, Contents: Read and write),
//                  DELETE_PIN (kod usuwania klipów, wpisywany raz na urządzeniu).
// Wdrożenie: patrz README (npx wrangler deploy w folderze worker/).

const REPO = "tymonekk/klipometr";
const ACCOUNT = "369_shoter";
const ORIGINS = ["https://tymonekk.github.io", "http://localhost:8765"];
const TIKTOK = /^https?:\/\/([a-z0-9-]+\.)*tiktok\.com(\/|$)/i;
const VIDEO = /\/(?:video|photo|v)\/(\d{15,22})/;
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

class Err extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export default {
  async fetch(req, env) {
    const origin = req.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin",
    };
    const reply = (status, body) => new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json; charset=utf-8" } });
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (req.method !== "POST") return reply(405, { error: "Tylko POST." });
    if (!ORIGINS.includes(origin)) return reply(403, { error: "Dozwolone tylko z Klipometru." });
    if (!env.GITHUB_TOKEN) return reply(500, { error: "Worker nie ma sekretu GITHUB_TOKEN." });
    let body;
    try { body = await req.json(); } catch { return reply(400, { error: "Złe dane." }); }
    const path = new URL(req.url).pathname;
    try {
      if (path === "/add") return reply(200, await add(String(body.link || ""), env));
      if (path === "/remove") return reply(200, await remove(String(body.id || ""), String(body.token || ""), String(body.pin || ""), env));
      return reply(404, { error: "Nie ma takiej ścieżki." });
    } catch (e) {
      return reply(e.status || 500, { error: e.status ? e.message : "Błąd Workera, spróbuj jeszcze raz." });
    }
  },
};

// ---------- link -> numer filmu ----------
async function resolve(raw) {
  let link = ((/https?:\/\/\S+/i.exec(raw) || [raw.trim()])[0] || "").trim();
  if (link && !/^https?:\/\//i.test(link)) link = "https://" + link;
  if (!TIKTOK.test(link)) throw new Err(400, "To nie jest link do TikToka.");
  let url = link;
  for (let i = 0; i < 6 && !VIDEO.test(new URL(url).pathname); i++) {
    let r;
    try {
      r = await fetch(url, { redirect: "manual", headers: { "User-Agent": UA, "Accept-Language": "pl,en;q=0.8" } });
    } catch {
      throw new Err(502, "TikTok nie odpowiada, spróbuj za chwilę.");
    }
    const loc = r.headers.get("Location");
    if (!loc) break;
    url = new URL(loc, url).toString();
    if (!TIKTOK.test(url)) break;
  }
  const p = new URL(url).pathname;
  const m = VIDEO.exec(p);
  if (!m) {
    if (!VIDEO.test(new URL(link).pathname)) return { link };   // skrócony link: niech spróbuje bot
    throw new Err(422, "Link nie prowadzi do filmu na TikToku.");
  }
  const acc = /\/@([\w.]+)/.exec(p);
  if (acc && acc[1].toLowerCase() !== ACCOUNT) throw new Err(422, "To nie jest film z konta @" + ACCOUNT + ".");
  const id = m[1];
  const up = Number(BigInt(id) >> 32n);
  if (up < 1.45e9 || up > Date.now() / 1000 + 86400) throw new Err(422, "Zły numer filmu w linku.");
  return { id, up };
}

// ---------- GitHub ----------
function gh(env, method, path, body) {
  return fetch(`https://api.github.com/repos/${REPO}/contents/${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "klipometr-storemedia",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function sign(env, id) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode("storemedia:" + env.GITHUB_TOKEN),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(id)));
  return [...mac.slice(0, 16)].map(b => b.toString(16).padStart(2, "0")).join("");
}

async function add(raw, env) {
  const res = await resolve(raw);
  if (!res.id) {
    const key = String(Date.now());
    const file = JSON.stringify({ link: res.link, added: new Date().toISOString() }, null, 1) + "\n";
    const r = await gh(env, "PUT", `data/storemedia/${key}.json`, { message: "StoreMedia: klip (skrócony link)", content: btoa(file) });
    if (!r.ok) throw new Err(502, `GitHub nie przyjął zapisu (${r.status}).`);
    return { ok: true, id: key, link: res.link, up: null, token: await sign(env, key) };
  }
  const { id, up } = res;
  const link = `https://www.tiktok.com/@${ACCOUNT}/video/${id}`;
  const file = JSON.stringify({ link, added: new Date().toISOString() }, null, 1) + "\n";
  const r = await gh(env, "PUT", `data/storemedia/${id}.json`, { message: `StoreMedia: klip ${id}`, content: btoa(file) });
  if (r.status === 422) throw new Err(409, "Ten klip już jest na liście.");
  if (!r.ok) throw new Err(502, `GitHub nie przyjął zapisu (${r.status}).`);
  return { ok: true, id, link, up, token: await sign(env, id) };
}

async function remove(id, token, pin, env) {
  if (!/^\d{10,22}$/.test(id)) throw new Err(400, "Zły numer klipu.");
  const byPin = env.DELETE_PIN && pin && pin === env.DELETE_PIN;
  if (!byPin && token !== await sign(env, id)) throw new Err(403, pin ? "Zły kod usuwania." : "Podaj kod usuwania.");
  const path = `data/storemedia/${id}.json`;
  const g = await gh(env, "GET", path);
  if (g.status === 404) return { ok: true };
  if (!g.ok) throw new Err(502, `GitHub nie odpowiada (${g.status}).`);
  const { sha } = await g.json();
  const r = await gh(env, "DELETE", path, { message: `StoreMedia: usunięty klip ${id}`, sha });
  if (!r.ok) throw new Err(502, `GitHub nie usunął pliku (${r.status}).`);
  return { ok: true };
}
