# Topic Radar

Trova i paper **più vicini a una collezione Zotero** (la tua nicchia) che non hai ancora e te li manda per email.
Da ogni paper scegli una direzione con un clic: dentro la nicchia (o una sua sottocollezione) oppure tra gli
scarti. Pensato per convergere su una nicchia stretta, per esempio per scrivere una survey.

## Il ciclo

```
 account Zotero principale                         account Zotero "radar" (secondo account)
 ┌─────────────────────────┐                       ┌──────────────────────────────────┐
 │ Diffusion  ◄── semi     │                       │ Topic Radar/<nicchia>/           │
 │  ├ Masked               │◄──── ➕ (clic) ───────┤   Proposti   (in attesa)         │
 │  └ Continuous           │                       │   Accettati  (registro)          │
 └─────────────────────────┘                       │   Scartati   ◄── 🗑 (clic)       │
            │                                      └──────────────────────────────────┘
            ▼                                                     ▲
   grafo citazioni + SPECTER2 ──► email con ➕/🗑 per ogni paper ─┘ (i proposti finiscono in "Proposti")
```

1. **Email**: ogni paper ha i pulsanti `➕ Diffusion`, `➕ Masked`, `➕ Continuous` (la collezione di riferimento e le
   sue sottocollezioni dirette) e `🗑 Scarta`.
2. **Un clic e basta**, come "Add to Zotero" nel daily: il pulsante apre una pagina di conferma e la scelta è già
   applicata su Zotero dallo stesso Cloudflare Worker di "Add to Zotero" (codice in `worker/radar.js`). Nessuna email da inviare. Puoi decidere
   solo su alcuni paper; un secondo clic sullo stesso paper non fa nulla.
   - `➕`: copia il paper, con la nota TL;DR, nella collezione scelta dell'account principale. Diventa un seme e la
     nicchia si stringe in quella direzione;
   - `🗑`: lo sposta in `Scartati` dell'account radar. I paper simili vengono penalizzati e quello scartato fa da
     esempio negativo.
4. **Nessun paper viene mai riproposto**: tutto ciò che è stato proposto resta nell'account radar.
5. **Giri senza cambiamenti saltati**: se dall'ultima email non hai deciso nulla e non hai toccato a mano né la
   nicchia né gli `Scartati`, il workflow **Topic Radar** si ferma dopo un controllo leggero (pochi secondi, senza
   installare torch). Rifarebbe solo lo stesso calcolo.

### Due comandi

| Workflow | Quando |
|---|---|
| **Topic Radar** | il giro normale: si ferma se non è cambiato niente |
| **Topic Radar (force)** | gira comunque |

Si lanciano da **Actions → Run workflow** oppure dal tuo sistema esterno, come "Send emails daily". Il campo
*collection* permette un giro una tantum su un'altra cartella. Anche il link "🔁 Mandami comunque nuovi paper" in
fondo all'email forza il giro successivo. Per spegnere tutto: variabile `RADAR_ENABLED=false`.

I link dei pulsanti sono firmati (HMAC con `RADAR_LINK_SECRET`): un link alterato o inventato viene rifiutato
dal Worker.

## Repo pubblico: cosa resta privato

Nulla dei tuoi paper finisce nel repo. Lo stato vive nell'account radar: le collezioni più una nota
`topic-radar-state`. Anche i **log** di Actions, pubblici su un repo pubblico, riportano solo conteggi, senza
titoli né nomi di collezione. Il report non viene caricato come artifact; la cache di Semantic Scholar sta nella
cache di Actions, che non è scaricabile pubblicamente.

## Sezioni dell'email

| Sezione | Criterio | Default |
|---|---|---|
| 🎯 **Nicchia** | top 10% per somiglianza; poi quanti tuoi paper cita / da quanti è citato | 8 |
| 🏛 **Fondamentali** | citati da ≥ 3 dei tuoi paper (qualsiasi età, < 20k citazioni) | 3 |
| ⭐ **Top venue A\*** | ACL, TACL, EMNLP, NeurIPS, ICML, ICLR, AAAI, IJCAI… ≤ 6 anni | 3 |
| 🚀 **Emergenti** | non A*, ≤ 18 mesi, ≥ 3 citazioni/mese | 3 |

Quantità e soglie sono in `config.yaml` → `selection`.

## Setup

1. Crea il **secondo account Zotero** e, su zotero.org/settings/keys, una chiave con **accesso in scrittura**.
   Anche la chiave dell'account principale (`ZOTERO_KEY`) deve avere la scrittura, perché i ➕ aggiungono lì.
2. Scegli un **segreto per i link**: una stringa casuale lunga, per esempio
   `python -c "import secrets; print(secrets.token_hex(32))"`.
3. **Aggiorna il Worker "Add to Zotero"** (`zotero-add-paper` su Cloudflare): i pulsanti usano lo stesso Worker.
   - **Edit code**: incolla in fondo al file tutto il contenuto di [`worker/radar.js`](worker/radar.js) e aggiungi
     nel router, subito prima di `return new Response("Not found", ...)`, la riga
     `if (url.pathname.startsWith("/radar")) return handleRadar(request, env);`. Poi **Deploy**.
   - **Settings → Variables and Secrets**: aggiungi come *Secret* `RADAR_LINK_SECRET` (il segreto del punto 2),
     `RADAR_ZOTERO_ID` e `RADAR_ZOTERO_KEY` (il secondo account). L'account principale usa `ZOTERO_USER_ID` e
     `ZOTERO_API_KEY` che il Worker ha già; quella chiave deve avere la **scrittura**.

   Il pulsante "Add to Zotero" del daily continua a funzionare come prima.
4. **Secrets** su GitHub (Settings → Secrets and variables → Actions):

   | Secret | Note |
   |---|---|
   | `ZOTERO_ID`, `SENDER`, `RECEIVER`, `API_SECRET_BREVO`, `OPENAI_API_BASE`, `ZOTERO_ADD_URL` | già presenti per "Send emails daily" (l'URL del Worker è `ZOTERO_ADD_URL`) |
   | `ZOTERO_KEY` | già presente, ma serve anche la **scrittura** |
   | `RADAR_ZOTERO_ID`, `RADAR_ZOTERO_KEY` | il secondo account (ID numerico + chiave con scrittura) |
   | `RADAR_LINK_SECRET` | il segreto del punto 2 (lo stesso messo nel Worker) |
   | `RADAR_OPENAI_API_KEY` | la chiave del nuovo account LLM (stesso sito di `OPENAI_API_BASE`) |
   | `S2_API_KEY` | consigliata, gratuita: https://www.semanticscholar.org/product/api#api-key-form |

5. **Variables**:

   | Variable | Esempio |
   |---|---|
   | `RADAR_COLLECTION` | **obbligatoria**: `Diffusion/Masked`; più nicchie separate da `;` (un'email per nicchia) |
   | `LLM_MODEL` | il modello usato su quel sito |
   | `RADAR_ENABLED` | `false` spegne il radar (zotero-arxiv-daily non viene toccato) |

6. Lancia **Topic Radar** (il primo giro parte sempre), poi aggiungilo al tuo sistema che avvia i workflow al mattino.

**Cambiare nicchia** = cambiare `RADAR_COLLECTION`.

## Uso locale

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
python radar.py --dry-run                    # solo report in output/ (nessuna email, niente Zotero)
python decide.py                             # dice se serve un giro (run=true/false)
python radar.py --send --zotero              # come il workflow (--force per ignorare il salto)
```

L'account principale si può leggere dall'API locale di Zotero desktop (`ZOTERO_MODE=local`). I ➕ però scrivono
via web, quindi servono comunque `ZOTERO_ID` e `ZOTERO_KEY`. `--verbose` mostra i titoli nei log. `adapters`
(per SPECTER2) richiede transformers 4.x: in locale usa un virtualenv dedicato. Senza `BREVO_API_KEY` l'email parte
via SMTP (`SMTP_SERVER`, `SMTP_PORT`, `SENDER_PASSWORD`).

## LLM

`llm.provider`: `openai` (qualsiasi endpoint compatibile OpenAI: `RADAR_OPENAI_API_KEY` + `OPENAI_API_BASE` +
`LLM_MODEL`), `anthropic` (Claude via SDK ufficiale, `ANTHROPIC_API_KEY`, default `claude-opus-5-5` con effort
basso) oppure `none` (TL;DR di Semantic Scholar). Per ogni paper produce un TL;DR e una riga su come si collega alla
nicchia; finiscono nell'email e nella nota Zotero del paper.

## Limiti noti

- Il clic esegue subito l'azione. Alcuni servizi email aziendali (es. Outlook con "Safe Links") aprono in automatico
  i link per controllarli, e così attiverebbero i pulsanti: con Gmail non succede.
- Senza `S2_API_KEY` Semantic Scholar risponde spesso 429: un giro completo può superare i 10 minuti.
- Con SPECTER2 qualche paper di *image* diffusion con testo (es. Imagen) può passare: scartalo e verrà penalizzato.
