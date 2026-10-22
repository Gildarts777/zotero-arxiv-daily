"""Topic Radar: every day, the papers closest to one Zotero collection (your niche) that you don't have yet.

Usage:
    python radar.py --dry-run             # report in output/*.html only
    python radar.py --send --zotero       # e-mail with decision links + proposals in the radar account
    RADAR_COLLECTION="Diffusion/Masked" python radar.py --dry-run   # another reference collection

Niches that did not change since their last digest are skipped unless --force (see decide.py).
"""

import argparse
import logging
import re
import secrets
from collections import Counter
from datetime import date

import numpy as np

from config import HERE, PUBLIC_LOGS, decisions_cfg, expand_profiles, load_config, radar_root
from embeddings import Encoder
from library import load_library, norm_title
from llm import Summarizer
from ranking import (SECTIONS, Candidate, assign_sections, compile_venues, detect_venue,
                     in_library, paper_parts, similarity)
from report import render_html, send_email
from s2 import S2Client, api_key_from_env

log = logging.getLogger("radar")


# -- pipeline ----------------------------------------------------------------
def resolve_papers(s2, items):
    """Map Zotero items to S2 papers (by arXiv id / DOI, then by exact title)."""
    ids = [f"arXiv:{s.arxiv}" if s.arxiv else (f"DOI:{s.doi}" if s.doi else None) for s in items]
    lookup = [i for i in ids if i]
    found = dict(zip(lookup, s2.batch(lookup))) if lookup else {}
    papers = []
    for item, sid in zip(items, ids):
        p = found.get(sid) if sid else None
        if p is None and item.title:
            p = s2.match_title(item.title)
            if p and norm_title(p.get("title")) != norm_title(item.title):
                p = None
        papers.append(p)
    return papers


def collect_candidates(s2, seed_papers, negative_ids, profile, cfg):
    src = cfg["sources"]
    seed_ids = [p["paperId"] for p in seed_papers if p]
    seed_set = set(seed_ids)
    cands = {}

    def add(p, source):
        if p and p.get("paperId") and p["paperId"] not in seed_set:
            c = cands.setdefault(p["paperId"], Candidate(paper=p))
            c.sources.add(source)
            if not c.paper.get("abstract") and p.get("abstract"):
                c.paper = {**c.paper, **p}

    # 1) references of the seeds -> how many seeds cite each work (foundational signal)
    ref_count = Counter()
    for p in s2.batch(seed_ids, fields="references.paperId"):
        for r in (p or {}).get("references") or []:
            if r.get("paperId"):
                ref_count[r["paperId"]] += 1
    ref_ids = [pid for pid, n in ref_count.items()
               if n >= src.get("reference_min_seed_refs", 2) and pid not in seed_set]
    for p in s2.batch(ref_ids):
        add(p, "references")
    log.info("references: %d works cited by several seeds", len(ref_ids))

    # 2) papers citing the seeds -> how many seeds each one cites (niche signal)
    cite_count = Counter()
    for sid in seed_ids:
        for p in s2.citations(sid, limit=src.get("citations_per_seed", 500)):
            if p.get("paperId"):
                cite_count[p["paperId"]] += 1
                add(p, "citations")
    log.info("citations: %d papers cite at least one seed", len(cite_count))

    # 3) S2 recommendations (positives = seeds, negatives = rejected papers)
    recs = s2.recommendations(seed_ids[:100], negative_ids[:100], limit=src.get("recommendations_limit", 500))
    for p in recs:
        add(p, "recommendations")
    log.info("recommendations: %d", len(recs))

    # 4) optional keyword searches sorted by citations
    for q in profile.get("queries") or []:
        res = s2.bulk_search(q, year_from=profile.get("year_from"),
                             min_citations=src.get("bulk_min_citations", 10),
                             max_results=src.get("bulk_max_results", 1000))
        for p in res:
            add(p, "search")
        log.info("search: %d", len(res))

    for pid, c in cands.items():
        c.seed_refs = ref_count.get(pid, 0)
        c.cites_seeds = cite_count.get(pid, 0)
    return list(cands.values())


def merged_index(*libs):
    out = {"doi": set(), "arxiv": set(), "title": set()}
    for lib in libs:
        for k, v in lib.index().items():
            out[k] |= v
    return out


def run_profile(profile, cfg, lib, radar_lib, s2, encoder, today, venues):
    """lib = main account (niche + rest); radar_lib = radar account (proposals and rejections) or None."""
    root = radar_root(cfg, profile)
    seeds = lib.seeds(profile["collection"], profile.get("include_subcollections", True))
    if not seeds:
        raise SystemExit("The reference collection is empty")
    rejected = radar_lib.seeds(f"{root}/Scartati", include_sub=False, required=False) if radar_lib else []
    log.info("%d seed papers, %d rejected", len(seeds), len(rejected))

    seed_papers = resolve_papers(s2, seeds)
    log.info("%d/%d seeds found on Semantic Scholar", sum(p is not None for p in seed_papers), len(seeds))
    negative_ids = [p["paperId"] for p in resolve_papers(s2, rejected) if p]

    cands = collect_candidates(s2, seed_papers, negative_ids, profile, cfg)
    # Everything in either account is excluded: your library, and every paper already proposed
    # (pending, rejected or accepted) in the radar account - so nothing is proposed twice.
    index = merged_index(lib, radar_lib) if radar_lib else lib.index()
    kept = [c for c in cands if c.paper.get("title") and not in_library(c.paper, index)]
    log.info("%d candidates, %d already in Zotero -> %d kept", len(cands), len(cands) - len(kept), len(kept))

    seed_parts = [(s.title, s.abstract) if s.abstract else paper_parts(p or {"title": s.title})
                  for s, p in zip(seeds, seed_papers)]
    seed_emb = encoder.encode(seed_parts)
    cand_emb = encoder.encode([paper_parts(c.paper) for c in kept])
    sims = similarity(seed_emb, cand_emb)
    # Contrast with the rest of the library: generic papers (Transformer, BERT, benchmarks)
    # are similar to everything you read, niche papers mostly to this collection.
    seed_keys = {s.key for s in seeds}
    rest_parts = [(i.title, i.abstract) for i in lib.items if i.key not in seed_keys and i.title]
    sims_rest = similarity(encoder.encode(rest_parts), cand_emb) if rest_parts else np.zeros_like(sims)
    rej_parts = [(i.title, i.abstract) for i in rejected if i.title]
    sims_neg = similarity(encoder.encode(rej_parts), cand_emb) if rej_parts else np.zeros_like(sims)
    sel = cfg["selection"]
    for c, s, r, n in zip(kept, sims, sims_rest, sims_neg):
        c.sim, c.sim_rest, c.sim_neg = float(s), float(r), float(n)
        c.rel = c.sim - sel.get("contrast_weight", 0.5) * c.sim_rest - sel.get("rejected_weight", 0.5) * c.sim_neg
        c.venue = detect_venue(c.paper, venues)

    pools = assign_sections(kept, len(seeds), cfg, today)
    per = sel["per_section"]
    picked = {s: pools[s][:per.get(s, 0)] for s in SECTIONS if per.get(s, 0)}
    stats = {"n_seeds": len(seeds), "n_candidates": len(cands), "n_in_library": len(cands) - len(kept)}
    return picked, stats, [s.title for s in seeds]


def decision_actions(email_cfg, targets, reject_key):
    """Per-paper mailto links: accept into each target collection, or reject."""
    from mail_decisions import decision_link

    def actions(c):
        pid, title = c.paper["paperId"], c.paper.get("title", "")
        links = [(f"➕ {label}", decision_link(email_cfg, "A", pid, key, title, label)) for label, key in targets]
        links.append(("🗑 Scarta", decision_link(email_cfg, "R", pid, reject_key, title, "Scartati")))
        return links
    return actions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=HERE / "config.yaml")
    ap.add_argument("--send", action="store_true", help="send the report by e-mail")
    ap.add_argument("--zotero", action="store_true", help="add proposals to the radar Zotero account")
    ap.add_argument("--dry-run", action="store_true", help="report only: no e-mail, no Zotero, no state")
    ap.add_argument("--force", action="store_true", help="run even the niches that did not change")
    ap.add_argument("--verbose", action="store_true", help="log titles (never on public CI logs)")
    args = ap.parse_args()
    if args.dry_run:
        args.send = args.zotero = False
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "urllib3", "sentence_transformers", "adapters"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    show_titles = args.verbose or not PUBLIC_LOGS

    cfg = load_config(args.config)
    today = date.today()
    zc, rc = cfg["zotero"], cfg["radar_zotero"]
    lib = load_library(zc.get("mode", "local"), zc.get("user_id"), zc.get("api_key"))
    radar = None
    if rc.get("user_id") and rc.get("api_key"):
        from radar_store import RadarStore
        radar = RadarStore(rc["user_id"], rc["api_key"])
    elif args.zotero:
        raise SystemExit("--zotero needs RADAR_ZOTERO_ID and RADAR_ZOTERO_KEY")
    log.info("library: %d items%s", len(lib.items), f", radar account: {len(radar.lib.items)} items" if radar else "")

    state = radar.load_state() if radar else {}
    profiles = expand_profiles(cfg)
    if radar and not args.force and cfg.get("decisions", {}).get("skip_if_unchanged", True):
        from radar_store import fingerprint
        todo = [p for p in profiles
                if state.get(p["collection"], {}).get("fingerprint") != fingerprint(lib, radar.lib, p, radar_root(cfg, p))]
        if len(todo) < len(profiles):
            log.info("%d/%d niches unchanged since their last digest: skipped", len(profiles) - len(todo), len(profiles))
        profiles = todo
    if not profiles:
        return

    s2 = S2Client(api_key_from_env(), cache_dir=HERE / "cache" / "s2", ttl_days=cfg["sources"].get("cache_days", 7))
    encoder = Encoder(cfg["embedding"])
    summarizer = Summarizer(cfg.get("llm"))
    venues = compile_venues(cfg["venues"])
    out_dir = HERE / "output"
    out_dir.mkdir(exist_ok=True)

    for profile in profiles:
        root = radar_root(cfg, profile)
        picked, stats, seed_titles = run_profile(profile, cfg, lib, radar.lib if radar else None,
                                                 s2, encoder, today, venues)
        papers = [c for cands in picked.values() for c in cands]
        for c in papers:
            c.tldr, c.niche_note = summarizer.summarize(profile["name"], seed_titles[:40], c.paper)

        actions = footer = None
        if args.zotero:
            from radar_store import REJECTED, accept_targets
            radar.add_pending(papers, root, today)
            log.info("added %d/%d proposals to the radar account", sum(bool(c.zotero_key) for c in papers), len(papers))
            reject_key = radar.niche_keys(root)[REJECTED]
            dcfg = decisions_cfg(cfg)
            if dcfg.get("imap_password"):
                actions = decision_actions(dcfg, accept_targets(lib, profile), reject_key)
                from mail_decisions import force_link
                footer = [("🔁 Mandami comunque nuovi paper al prossimo giro", force_link(dcfg, secrets.token_hex(4)))]
            else:
                log.warning("RADAR_IMAP_PASSWORD not set: e-mail without decision buttons")

        body = render_html(profile["name"], picked, stats, today, actions=actions, footer=footer)
        slug = re.sub(r"\W+", "-", profile["name"]).strip("-").lower()
        out = out_dir / f"{today.isoformat()}-{slug}.html"
        out.write_text(body, encoding="utf-8")
        log.info("%d papers selected", len(papers))
        if show_titles:
            for section, cands in picked.items():
                for c in cands:
                    log.info("  [%s] %.2f %s | %s", section, c.score, c.paper["title"][:80], c.reason)

        if args.send:
            send_email(cfg["email"], f"Topic Radar · {profile['name']} · {len(papers)} paper", body)
            log.info("e-mail sent")
        if radar and not args.dry_run:
            from radar_store import fingerprint
            radar.lib = load_library("web", rc["user_id"], rc["api_key"])  # sees the new Scartati/Proposti keys
            state[profile["collection"]] = {"fingerprint": fingerprint(lib, radar.lib, profile, root),
                                            "last_run": today.isoformat()}
            radar.save_state(state)


if __name__ == "__main__":
    main()
