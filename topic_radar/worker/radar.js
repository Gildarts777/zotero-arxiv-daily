// ============================================================================
// Topic Radar - one-click ➕ / 🗑 buttons of the Topic Radar e-mail.
//
// Paste this whole block at the END of the existing "Add to Zotero" Worker (zotero-add-paper), and add
// this line to its router, right before `return new Response("Not found", ...)`:
//
//     if (url.pathname.startsWith("/radar")) return handleRadar(request, env);
//
// Secrets to add to the Worker (Settings -> Variables and Secrets -> type "Secret"):
//     RADAR_LINK_SECRET   same value as the GitHub secret RADAR_LINK_SECRET
//     RADAR_ZOTERO_ID     second (radar) Zotero account: numeric user ID
//     RADAR_ZOTERO_KEY    second (radar) Zotero account: API key with write access
// The main account reuses the Worker's existing ZOTERO_USER_ID / ZOTERO_API_KEY (write access needed).
//
//   GET /radar?a=A&p=<paperId>&f=<from>&t=<to>&l=<log>&s=<sig>
//       copy the paper (and its notes) from the radar account into collection <to> of the main account,
//       then move it from Proposti <from> to Accettati <log> in the radar account.
//   GET /radar?a=R&p=<paperId>&f=<from>&t=<to>&l=&s=<sig>
//       move it from Proposti <from> to Scartati <to> in the radar account.
//   GET /radar/force?n=<nonce>&s=<sig>
//       leave a note in the radar account so that the next "Topic Radar" run does not skip.
// Links are signed by the GitHub workflow (HMAC-SHA256), so nobody else can trigger them.
// ============================================================================

async function handleRadar(request, env) {
  const url = new URL(request.url);
  const q = url.searchParams;
  try {
    if (url.pathname === "/radar/force") {
      await radarVerify(env, q.get("s"), "FORCE", q.get("n") || "");
      await radarZotero(env, "radar", "POST", "/items", [{
        itemType: "note", note: "<p>Topic Radar: run requested</p>",
        tags: [{ tag: "topic-radar-force" }], collections: [], relations: {},
      }]);
      return radarPage("🔁 Fatto", "Al prossimo giro Topic Radar ti manderà nuovi paper anche senza cambiamenti.");
    }
    if (url.pathname === "/radar") {
      const [a, p, f, t, l] = ["a", "p", "f", "t", "l"].map((k) => q.get(k) || "");
      await radarVerify(env, q.get("s"), a, p, f, t, l);
      return radarPage(...(await radarDecide(env, a, p, f, t, l)));
    }
    return radarPage("Topic Radar", "Niente da fare qui.", 404);
  } catch (e) {
    return radarPage("⚠️ Errore", e.message, e.status || 500);
  }
}

async function radarDecide(env, action, paperId, from, to, log) {
  const items = await radarZotero(env, "radar", "GET",
    `/items?tag=${encodeURIComponent("s2:" + paperId)}&format=json`);
  const item = items.find((i) => i.data.itemType !== "note");
  if (!item) throw radarError(404, "Paper non trovato nell'account radar.");
  const title = item.data.title || "";
  if (!item.data.collections.includes(from)) {
    return ["✔️ Già fatto", `Hai già scelto per «${title}».`];
  }
  const rest = item.data.collections.filter((k) => k !== from);

  if (action === "A") {
    const copy = radarStrip(item.data);
    copy.collections = [to];
    copy.relations = {};
    const newKey = await radarCreate(env, "main", copy);
    const children = await radarZotero(env, "radar", "GET", `/items/${item.key}/children?format=json`);
    for (const c of children.filter((c) => c.data.itemType === "note")) {
      const note = radarStrip(c.data);
      delete note.collections;
      note.parentItem = newKey;
      note.relations = {};
      await radarCreate(env, "main", note);
    }
    await radarPatch(env, item, { collections: [...rest, log] });
    return ["➕ Aggiunto", `«${title}» è nella tua libreria Zotero.`];
  }
  if (action === "R") {
    await radarPatch(env, item, { collections: [...rest, to] });
    return ["🗑 Scartato", `«${title}» è negli scarti: i paper simili verranno penalizzati.`];
  }
  throw radarError(400, "Azione sconosciuta.");
}

// -- Zotero Web API (main account = the Worker's existing ZOTERO_USER_ID / ZOTERO_API_KEY) --
async function radarZotero(env, which, method, path, body, extraHeaders = {}) {
  const id = which === "main" ? env.ZOTERO_USER_ID : env.RADAR_ZOTERO_ID;
  const key = which === "main" ? env.ZOTERO_API_KEY : env.RADAR_ZOTERO_KEY;
  const res = await fetch(`https://api.zotero.org/users/${id}${path}`, {
    method,
    headers: { "Zotero-API-Key": key, "Zotero-API-Version": "3", "Content-Type": "application/json", ...extraHeaders },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw radarError(502, `Zotero ${method} ${res.status}`);
  return res.status === 204 ? null : res.json();
}

async function radarCreate(env, which, data) {
  const res = await radarZotero(env, which, "POST", "/items", [data]);
  const ok = res.successful && res.successful["0"];
  if (!ok) throw radarError(502, "Zotero ha rifiutato l'elemento.");
  return ok.key;
}

function radarPatch(env, item, fields) {
  return radarZotero(env, "radar", "PATCH", `/items/${item.key}`, fields,
    { "If-Unmodified-Since-Version": String(item.version) });
}

function radarStrip(data) {
  const out = { ...data };
  for (const k of ["key", "version", "dateAdded", "dateModified", "parentItem"]) delete out[k];
  return out;
}

// -- signing: must match topic_radar/links.py --------------------------------------------------
async function radarSign(secret, ...parts) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(parts.join(":")));
  return [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

async function radarVerify(env, sig, ...parts) {
  if (!env.RADAR_LINK_SECRET) throw radarError(500, "RADAR_LINK_SECRET non configurato nel Worker.");
  const expected = await radarSign(env.RADAR_LINK_SECRET, ...parts);
  if (!sig || sig.length !== expected.length) throw radarError(403, "Link non valido.");
  let diff = 0;
  for (let i = 0; i < sig.length; i++) diff |= sig.charCodeAt(i) ^ expected.charCodeAt(i);
  if (diff) throw radarError(403, "Link non valido.");
}

function radarError(status, message) {
  return Object.assign(new Error(message), { status });
}

function radarPage(title, text, status = 200) {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">` +
    `<title>${esc(title)}</title><body style="font-family:system-ui,sans-serif;max-width:520px;margin:15vh auto;padding:0 16px">` +
    `<h2>${esc(title)}</h2><p>${esc(text)}</p><p style="color:#888">Puoi chiudere questa pagina.</p></body>`,
    { status, headers: { "Content-Type": "text/html; charset=utf-8" } });
}
