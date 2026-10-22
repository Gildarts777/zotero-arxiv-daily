"""Relevance, impact and section assignment for candidate papers."""

import math
import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from library import norm_doi, norm_title

SECTIONS = ("niche", "foundational", "top_venue", "rising")


@dataclass
class Candidate:
    paper: dict
    seed_refs: int = 0            # how many seed papers cite it
    cites_seeds: int = 0          # how many seed papers it cites (bibliographic coupling)
    sources: set = field(default_factory=set)
    sim: float = 0.0              # similarity to the topic collection
    sim_rest: float = 0.0         # similarity to the rest of the library
    sim_neg: float = 0.0          # similarity to rejected papers
    rel: float = 0.0              # sim - contrast_weight * sim_rest - rejected_weight * sim_neg
    venue: str | None = None      # short A* venue name, None if not A*
    age_months: float = 0.0
    section: str | None = None
    score: float = 0.0
    reason: str = ""
    tldr: str | None = None
    niche_note: str | None = None
    zotero_key: str | None = None

    @property
    def cites(self):
        return self.paper.get("citationCount") or 0

    @property
    def cites_per_month(self):
        return self.cites / max(self.age_months, 1.0)


# -- venues ----------------------------------------------------------------
def compile_venues(venues_cfg):
    """venues_cfg: {exclude: [regexes], list: {short_name: {dblp: [prefixes], names: [regexes]}}}"""
    out = []
    for short, spec in venues_cfg["list"].items():
        out.append((short,
                    tuple(p.lower() for p in spec.get("dblp", [])),
                    [re.compile(n, re.I) for n in spec.get("names", [])]))
    exclude = [re.compile(x, re.I) for x in venues_cfg.get("exclude", [])]
    return out, exclude


def detect_venue(paper, venues):
    venue_list, exclude = venues
    dblp = ((paper.get("externalIds") or {}).get("DBLP") or "").lower()
    names = [v.get("name") if isinstance(v, dict) else v
             for v in (paper.get("venue"), paper.get("publicationVenue"), paper.get("journal"))]
    names = [n for n in names if isinstance(n, str)]
    if any(rx.search(n) for rx in exclude for n in names if n):
        return None  # e.g. Findings / workshops share the main conference's DBLP prefix
    for short, prefixes, regexes in venue_list:
        if dblp and any(dblp.startswith(p) for p in prefixes):
            return short
        if any(rx.search(n) for rx in regexes for n in names if n):
            return short
    return None


# -- helpers ---------------------------------------------------------------
def age_in_months(paper, today):
    pd = paper.get("publicationDate")
    if pd:
        y, m, d = (int(x) for x in pd.split("-"))
        start = date(y, m, d)
    elif paper.get("year"):
        start = date(int(paper["year"]), 7, 1)
    else:
        return 0.0
    return max((today - start).days / 30.44, 0.0)


def paper_parts(paper):
    abstract = paper.get("abstract") or ((paper.get("tldr") or {}).get("text")) or ""
    return paper.get("title") or "", abstract


def in_library(paper, lib_index):
    ext = paper.get("externalIds") or {}
    if norm_doi(ext.get("DOI")) in lib_index["doi"]:
        return True
    if ext.get("ArXiv") and ext["ArXiv"] in lib_index["arxiv"]:
        return True
    return norm_title(paper.get("title")) in lib_index["title"]


def percentile_rank(values):
    """0..1 rank of each value inside its array (ties averaged)."""
    v = np.asarray(values, dtype=float)
    if len(v) <= 1:
        return np.ones_like(v)
    order = v.argsort().argsort()
    return order / (len(v) - 1)


def similarity(seed_emb, cand_emb, top_k=3):
    """Mean of the top-k cosine similarities to the seed papers (embeddings are L2-normalised)."""
    sims = cand_emb @ seed_emb.T
    k = min(top_k, sims.shape[1])
    return np.sort(sims, axis=1)[:, -k:].mean(axis=1)


# -- main selection ----------------------------------------------------------
def assign_sections(cands, n_seeds, cfg, today):
    """Decide the section of each candidate and compute its score. Returns {section: [Candidate]}."""
    sel = cfg["selection"]
    w_rel = sel.get("relevance_weight", 0.6)
    min_seed_refs = max(sel["foundational_min_seed_refs"], math.ceil(sel.get("foundational_min_seed_frac", 0) * n_seeds))
    rels = [c.rel for c in cands] or [0.0]
    rel_floor = np.quantile(rels, sel["min_relevance_quantile"])
    found_floor = np.quantile(rels, sel["foundational_min_relevance_quantile"])

    niche_floor = np.quantile(rels, sel["niche_min_relevance_quantile"])

    pools = {s: [] for s in SECTIONS}
    for c in cands:
        c.age_months = age_in_months(c.paper, today)
        years = c.age_months / 12
        if c.rel >= niche_floor and c.age_months <= sel["niche_max_age_years"] * 12:
            c.section = "niche"
        elif (c.seed_refs >= min_seed_refs and c.rel >= found_floor
                and c.cites <= sel.get("foundational_max_citations", float("inf"))):
            c.section = "foundational"
        elif c.rel < rel_floor:
            continue
        elif c.venue and years <= sel["max_age_years"] and c.cites >= sel.get("top_venue_min_citations", 0):
            c.section = "top_venue"
        elif (not c.venue and c.age_months <= sel["rising_max_age_months"]
              and c.cites_per_month >= sel["rising_min_cites_per_month"]):
            c.section = "rising"
        else:
            continue
        pools[c.section].append(c)

    for section, pool in pools.items():
        if not pool:
            continue
        rel = percentile_rank([c.rel for c in pool])
        if section == "niche":
            # almost pure similarity; coupling (citing many of your papers) as tie-breaker
            imp = percentile_rank([c.cites_seeds + c.seed_refs for c in pool])
        elif section == "foundational":
            imp = percentile_rank([c.seed_refs + math.log1p(c.cites) / 10 for c in pool])
        elif section == "top_venue":
            imp = percentile_rank([math.log1p(c.cites_per_month * 12) for c in pool])
        else:
            imp = percentile_rank([c.cites_per_month for c in pool])
        w = sel.get("niche_relevance_weight", 0.85) if section == "niche" else w_rel
        for c, r, i in zip(pool, rel, imp):
            c.score = w * r + (1 - w) * i
            c.reason = explain(c, n_seeds)
        pool.sort(key=lambda c: c.score, reverse=True)
    return pools


def explain(c, n_seeds):
    p = c.paper
    year = p.get("year") or "?"
    bits = []
    if c.cites_seeds:
        bits.append(f"cita {c.cites_seeds}/{n_seeds} tuoi paper")
    if c.seed_refs:
        bits.append(f"citato da {c.seed_refs}/{n_seeds} tuoi paper")
    bits.append(f"{c.venue or 'arXiv/altro'} {year}")
    bits.append(f"{c.cites} citazioni (~{c.cites_per_month:.1f}/mese)")
    if p.get("influentialCitationCount"):
        bits.append(f"{p['influentialCitationCount']} influenti")
    bits.append(f"similarità {c.sim:.2f} (resto libreria {c.sim_rest:.2f})")
    if c.sim_neg:
        bits.append(f"scartati {c.sim_neg:.2f}")
    return " · ".join(bits)
