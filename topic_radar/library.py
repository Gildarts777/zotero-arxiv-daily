"""Read the Zotero library: seed papers of one collection + identifiers of the whole library."""

import re
from dataclasses import dataclass, field

from pyzotero import zotero

from config import PUBLIC_LOGS

SKIP_TYPES = {"attachment", "note", "annotation"}
ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:|10\.48550/arxiv\.)(\d{4}\.\d{4,5})", re.I)


def norm_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def norm_doi(d):
    d = (d or "").strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", d) or None


def arxiv_id(*texts):
    for t in texts:
        m = ARXIV_RE.search(t or "")
        if m:
            return m.group(1)
    return None


@dataclass
class LibItem:
    key: str
    title: str
    abstract: str
    doi: str | None
    arxiv: str | None
    collections: list = field(default_factory=list)


@dataclass
class Library:
    items: list
    collections: dict  # key -> (name, parentKey or None)

    def collection_path(self, key):
        parts = []
        while key:
            name, parent = self.collections[key]
            parts.append(name)
            key = parent
        return "/".join(reversed(parts))

    def subcollections(self, key):
        return sorted((k for k, (_, p) in self.collections.items() if p == key),
                      key=lambda k: self.collections[k][0].lower())

    def find_collection(self, path):
        target = path.strip("/").lower()
        return next((k for k in self.collections if self.collection_path(k).lower() == target), None)

    def resolve_collection(self, path, include_sub=True, required=True):
        """Collection keys matching a path like 'Diffusion' or 'Diffusion/Masked'."""
        target = path.strip("/").lower()
        keys = {k for k in self.collections if self.collection_path(k).lower() == target}
        if not keys:
            if not required:
                return set()
            if PUBLIC_LOGS:
                raise SystemExit("Zotero collection not found (check RADAR_COLLECTION)")
            raise SystemExit(f"Zotero collection not found: {path!r}. "
                             f"Available: {sorted(self.collection_path(k) for k in self.collections)}")
        if include_sub:
            changed = True
            while changed:
                new = {k for k, (_, p) in self.collections.items() if p in keys} - keys
                keys |= new
                changed = bool(new)
        return keys

    def seeds(self, path, include_sub=True, required=True):
        keys = self.resolve_collection(path, include_sub, required)
        return [it for it in self.items if keys.intersection(it.collections)]

    def index(self):
        """Identifier sets used to exclude papers already in the library."""
        return {
            "doi": {i.doi for i in self.items if i.doi},
            "arxiv": {i.arxiv for i in self.items if i.arxiv},
            "title": {norm_title(i.title) for i in self.items if i.title},
        }


def load_library(mode="local", user_id=None, api_key=None):
    if mode == "local":
        z = zotero.Zotero(0, "user", local=True)
    else:
        z = zotero.Zotero(user_id, "user", api_key)
    cols = {c["key"]: (c["data"]["name"], c["data"]["parentCollection"] or None)
            for c in z.everything(z.collections())}
    items = []
    for it in z.everything(z.top()):
        d = it["data"]
        if d["itemType"] in SKIP_TYPES:
            continue
        doi = norm_doi(d.get("DOI"))
        items.append(LibItem(
            key=d["key"],
            title=d.get("title", ""),
            abstract=d.get("abstractNote", ""),
            doi=doi,
            arxiv=arxiv_id(d.get("url"), d.get("extra"), doi, d.get("archiveID")),
            collections=d.get("collections", []),
        ))
    return Library(items, cols)
