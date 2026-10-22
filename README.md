<p align="center">
 <img width=160px height=160px src="assets/logo.svg" alt="logo">
</p>

<h3 align="center">Zotero-arXiv-Daily · Gildarts777 fork</h3>

<p align="center">
  Daily paper recommendations from your Zotero library, plus a <b>Topic Radar</b> that helps you converge on a research niche.
</p>

> [!NOTE]
> This is a fork of **[TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily)**.
> For the original project, its full documentation and updates, go there. This page describes what the
> original does in short, and then what this fork adds on top of it.

## 📬 What the original project does

[Zotero-arXiv-Daily](https://github.com/TideDra/zotero-arxiv-daily) runs as a GitHub Actions workflow, at zero cost:

1. reads the papers in your Zotero library (optionally only some collections);
2. fetches the papers released the previous day on arXiv (and bioRxiv, medRxiv, chemRxiv);
3. ranks them by embedding similarity to your library, with recently added papers weighted more;
4. writes an LLM TL;DR for each one and resolves the affiliations;
5. e-mails you the list, with PDF and code links.

See the [upstream README](https://github.com/TideDra/zotero-arxiv-daily#readme) for the setup (secrets,
`CUSTOM_CONFIG`, local runs).

## ✨ What this fork adds

### 🎯 Topic Radar: converge on a niche (`topic_radar/`)

A second, independent pipeline. Instead of yesterday's arXiv papers, it looks at the whole literature around **one
Zotero collection** (your niche). It covers highly cited papers, A* venues (ACL, TACL, EMNLP, NeurIPS, ICML,
ICLR, AAAI, IJCAI…) and fast-rising arXiv preprints. It then e-mails the closest papers you don't have yet:

- candidates come from the citation graph of the collection (papers citing your papers, shared references,
  Semantic Scholar recommendations) and are ranked with SPECTER2 embeddings;
- every paper in the e-mail has one-click buttons, like "Add to Zotero": **➕ add it** to the niche or to one of
  its subcollections, or **🗑 discard it**. A small Cloudflare Worker applies the choice to Zotero right away.
  Discarded papers go to a second Zotero account and act as negative examples;
- nothing is ever proposed twice, and a run is skipped when nothing changed since the last e-mail;
- no paper data is written to this public repo or its logs: the state lives in the second Zotero account.

Two workflows: **Topic Radar** (skips unchanged days) and **Topic Radar (force)**.
Setup and details: [topic_radar/README.md](topic_radar/README.md) (in Italian).

### 📧 E-mail via the Brevo API

SMTP is often blocked on GitHub Actions runners, so `send_email` uses the [Brevo](https://www.brevo.com) HTTP API.
Secret: `API_SECRET_BREVO` (instead of `SENDER_PASSWORD`). Covered by `tests/test_utils.py` and
`tests/test_executor.py`, plus the manual workflows `test-brevo-api.yml` and `test-smtp.yml`.

### 📚 "Add to Zotero" button

Each paper in the daily e-mail has an **Add to Zotero** button that calls a Cloudflare Worker, which adds the arXiv
paper (with its TL;DR) to your library. Secret: `ZOTERO_ADD_URL` (the Worker URL; without it, no button is shown).
Tests: `tests/test_add_to_zotero.py` and `test-add-to-zotero.yml`; end-to-end: `test-email-with-zotero-button.yml`.

### 🔧 Smaller changes

- Affiliations: fixed truncation, with an "Unknown Affiliation" fallback.
- Defaults: `max_paper_num: 12`, `source: ['arxiv']`.
- **Send emails daily** has no cron. It is started by an external scheduler (or by hand), because GitHub's cron
  was too unreliable.

## 🔐 Secrets used by this fork

| Secret | Used by |
|---|---|
| `ZOTERO_ID`, `ZOTERO_KEY`, `SENDER`, `RECEIVER`, `OPENAI_API_KEY`, `OPENAI_API_BASE` | Send emails daily (as upstream) |
| `API_SECRET_BREVO` | both pipelines (e-mail) |
| `ZOTERO_ADD_URL` | Send emails daily (Add to Zotero button) |
| `RADAR_ZOTERO_ID`, `RADAR_ZOTERO_KEY`, `RADAR_WORKER_URL`, `RADAR_LINK_SECRET`, `RADAR_OPENAI_API_KEY`, `S2_API_KEY` | Topic Radar |

## 📃 License

AGPLv3, as the original project. See [LICENSE](LICENSE). All credit for the base pipeline goes to
[TideDra](https://github.com/TideDra/zotero-arxiv-daily) and its contributors.
