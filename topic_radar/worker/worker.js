// Topic Radar decisions: the ➕ / 🗑 buttons in the e-mail point here. One click = done.
//
//   GET /radar?a=A&p=<paperId>&f=<from>&t=<to>&l=<log>&s=<sig>
//       A: copy the paper (and its notes) from the radar account into collection <to> of the main
//          account, then move it from Proposti <from> to Accettati <log> in the radar account.
//   GET /radar?a=R&p=<paperId>&f=<from>&t=<to>&l=&s=<sig>
//       R: move it from Proposti <from> to Scartati <to> in the radar account.
//   GET /radar/force?n=<nonce>&s=<sig>
//       leave a note in the radar account so that the next "Topic Radar" run does not skip.
//
// Links are signed with HMAC-SHA256(LINK_SECRET) by the GitHub workflow, so nobody else can trigger them.
// Secrets (wrangler secret put ...): LINK_SECRET, MAIN_ZOTERO_ID, MAIN_ZOTERO_KEY, RADAR_ZOTERO_ID, RADAR_ZOTERO_KEY.

const API = "https://api.zotero.org";
const FORCE_TAG = "topic-radar-force";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const q = url.searchParams;
    try {
      if (url.pathname === "/radar/force") {
        await verify(env, q.get("s"), "FORCE", q.get("n") || "");
        await zotero(env, "radar", "POST", "/items", [{
          itemType: "note", note: "<p>Topic Radar: run requested</p>",
          tags: [{ tag: FORCE_TAG }], collections: [], relations: {},
        }]);
        return page("🔁 Fatto", "Al prossimo giro Topic Radar ti manderà nuovi paper anche senza cambiamenti.");
      }
      if (url.pathname === "/radar") {
        const [a, p, f, t, l] = ["a", "p", "f", "t", "l"].map((k) => q.get(k) || "");
        await verify(env, q.get("s"), a, p, f, t, l);
        return page(...(await decide(env, a, p, f, t, l)));
      }
      return page("Topic Radar", "Niente da fare qui.", 404);
    } catch (e) {
      return page("⚠️ Errore", e.message, e.status || 500);
    }
  },
};

async function decide(env, action, paperId, from, to, log) {
  const items = await zotero(env, "radar", "GET", `/items?tag=${encodeURIComponent("s2:" + paperId)}&format=json`);
  const item = items.find((i) => i.data.itemType !== "note");
  if (!item) throw httpError(404, "Paper non trovato nell'account radar.");
  const title = item.data.title || "";
  if (!item.data.collections.includes(from)) {
    return ["✔️ Già fatto", `Hai già scelto per «${title}».`];
  }
  const rest = item.data.collections.filter((k) => k !== from);

  if (action === "A") {
    const copy = strip(item.data);
    copy.collections = [to];
    copy.relations = {};
    const newKey = await createOne(env, "main", copy);
    const children = await zotero(env, "radar", "GET", `/items/${item.key}/children?format=json`);
    for (const c of children.filter((c) => c.data.itemType === "note")) {
      const note = strip(c.data);
      delete note.collections;
      note.parentItem = newKey;
      note.relations = {};
      await createOne(env, "main", note);
    }
    await patch(env, item, { collections: [...rest, log] });
    return ["➕ Aggiunto", `«${title}» è nella tua libreria Zotero.`];
  }
  if (action === "R") {
    await patch(env, item, { collections: [...rest, to] });
    return ["🗑 Scartato", `«${title}» è negli scarti: i paper simili verranno penalizzati.`];
  }
  throw httpError(400, "Azione sconosciuta.");
}

// -- Zotero Web API -----------------------------------------------------------
function account(env, which) {
  return which === "main"
    ? { id: env.MAIN_ZOTERO_ID, key: env.MAIN_ZOTERO_KEY }
    : { id: env.RADAR_ZOTERO_ID, key: env.RADAR_ZOTERO_KEY };
}

async function zotero(env, which, method, path, body, extraHeaders = {}) {
  const { id, key } = account(env, which);
  const res = await fetch(`${API}/users/${id}${path}`, {
    method,
    headers: { "Zotero-API-Key": key, "Zotero-API-Version": "3", "Content-Type": "application/json", ...extraHeaders },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw httpError(502, `Zotero ${method} ${res.status}`);
  return res.status === 204 ? null : res.json();
}

async function createOne(env, which, data) {
  const res = await zotero(env, which, "POST", "/items", [data]);
  const ok = res.successful && res.successful["0"];
  if (!ok) throw httpError(502, "Zotero ha rifiutato l'elemento.");
  return ok.key;
}

function patch(env, item, fields) {
  return zotero(env, "radar", "PATCH", `/items/${item.key}`, fields,
    { "If-Unmodified-Since-Version": String(item.version) });
}

function strip(data) {
  const out = { ...data };
  for (const k of ["key", "version", "dateAdded", "dateModified", "parentItem"]) delete out[k];
  return out;
}

// -- signing ------------------------------------------------------------------
async function sign(secret, ...parts) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(parts.join(":")));
  return [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

async function verify(env, sig, ...parts) {
  const expected = await sign(env.LINK_SECRET, ...parts);
  if (!sig || sig.length !== expected.length) throw httpError(403, "Link non valido.");
  let diff = 0;
  for (let i = 0; i < sig.length; i++) diff |= sig.charCodeAt(i) ^ expected.charCodeAt(i);
  if (diff) throw httpError(403, "Link non valido.");
}

function httpError(status, message) {
  return Object.assign(new Error(message), { status });
}

function page(title, text, status = 200) {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">` +
    `<title>${esc(title)}</title><body style="font-family:system-ui,sans-serif;max-width:520px;margin:15vh auto;padding:0 16px">` +
    `<h2>${esc(title)}</h2><p>${esc(text)}</p><p style="color:#888">Puoi chiudere questa pagina.</p></body>`,
    { status, headers: { "Content-Type": "text/html; charset=utf-8" } });
}
