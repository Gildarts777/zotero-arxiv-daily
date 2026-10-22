"""The radar Zotero account (second account): proposals, rejections, accepted log and run state.

Layout, for each niche (root = "Topic Radar/<niche>"):
    <root>/Proposti   papers sent by e-mail, waiting for your decision
    <root>/Scartati   rejected papers (negative examples)
    <root>/Accettati  papers you moved into your main library (kept as a log)
A note tagged `topic-radar-state` stores the per-niche fingerprint used to skip unchanged days.
"""

import copy
import hashlib
import html
import json
import logging
import re

from pyzotero import zotero

from library import load_library

log = logging.getLogger(__name__)
PENDING, REJECTED, ACCEPTED = "Proposti", "Scartati", "Accettati"
STATE_TAG = "topic-radar-state"
SECTION_TAGS = {"niche": "radar:nicchia", "foundational": "radar:fondamentale",
                "top_venue": "radar:top-venue", "rising": "radar:emergente"}


def s2_tag(paper_id):
    return f"s2:{paper_id}"


class Account:
    """A Zotero user library reachable through the Web API (read + write)."""

    def __init__(self, user_id, api_key, lib=None):
        self.z = zotero.Zotero(user_id, "user", api_key)
        self.lib = lib or load_library("web", user_id, api_key)
        self._templates = {}

    def ensure_collection(self, path):
        """Create every missing level of 'A/B/C' and return the key of the last one."""
        parent, built = None, []
        for name in path.strip("/").split("/"):
            built.append(name)
            key = self.lib.find_collection("/".join(built))
            if not key:
                res = self.z.create_collections([{"name": name, "parentCollection": parent or False}])
                key = res["successful"]["0"]["key"]
                self.lib.collections[key] = (name, parent)
            parent = key
        return parent

    def template(self, item_type):
        if item_type not in self._templates:
            self._templates[item_type] = self.z.item_template(item_type)
        return copy.deepcopy(self._templates[item_type])

    def create(self, item):
        return self.z.create_items([item])["successful"]["0"]["key"]

    def set_collections(self, item, remove=(), add=()):
        cols = [k for k in item["data"]["collections"] if k not in remove] + [k for k in add]
        self.z.update_item({"key": item["key"], "version": item["version"], "collections": cols})


class RadarStore(Account):
    def niche_keys(self, root):
        return {sub: self.ensure_collection(f"{root}/{sub}") for sub in (PENDING, REJECTED, ACCEPTED)}

    def rejected_items(self, root):
        return self.lib.seeds(f"{root}/{REJECTED}", include_sub=False, required=False)

    # -- proposals ---------------------------------------------------------
    def add_pending(self, cands, root, run_date):
        pending = self.niche_keys(root)[PENDING]
        for c in cands:
            try:
                item = self.template(item_type(c))
                fill_item(item, c, pending)
                key = self.create(item)
                note = self.template("note")
                note.update(note=note_html(c, run_date), parentItem=key, tags=[{"tag": "topic-radar"}])
                self.create(note)
                c.zotero_key = key
            except Exception as e:  # keep going: the e-mail still lists the paper
                log.warning("Zotero add failed (%s)", type(e).__name__)

    def find_by_paper(self, paper_id):
        items = self.z.items(tag=s2_tag(paper_id))
        return next((i for i in items if i["data"]["itemType"] != "note"), None)

    def notes(self, item_key):
        return [n for n in self.z.children(item_key) if n["data"]["itemType"] == "note"]

    # -- run state -----------------------------------------------------------
    def _state_note(self):
        notes = self.z.items(tag=STATE_TAG, itemType="note")
        return notes[0] if notes else None

    def load_state(self):
        note = self._state_note()
        if not note:
            return {}
        m = re.search(r"<pre>(.*?)</pre>", note["data"]["note"], re.S)
        try:
            return json.loads(html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))) if m else {}
        except json.JSONDecodeError:
            log.warning("unreadable state note: starting fresh")
            return {}

    def save_state(self, state):
        body = ("<p>Topic Radar state - do not edit</p><pre>"
                + html.escape(json.dumps(state, indent=1, sort_keys=True)) + "</pre>")
        note = self._state_note()
        if note:
            self.z.update_item({"key": note["key"], "version": note["version"], "note": body})
        else:
            new = self.template("note")
            new.update(note=body, tags=[{"tag": STATE_TAG}])
            self.create(new)


def fingerprint(main_lib, radar_lib, profile, root):
    """Changes when papers enter/leave the niche (main account) or the rejected collection (radar account)."""
    seeds = main_lib.seeds(profile["collection"], profile.get("include_subcollections", True), required=False)
    rejected = radar_lib.seeds(f"{root}/{REJECTED}", include_sub=False, required=False)
    keys = sorted(f"n:{i.key}" for i in seeds) + sorted(f"r:{i.key}" for i in rejected)
    return hashlib.sha1("|".join(keys).encode()).hexdigest()


def accept_targets(main_lib, profile):
    """Collections a paper can be accepted into: the niche collection and its direct subcollections."""
    root = main_lib.find_collection(profile["collection"])
    if not root:
        return []
    return [(main_lib.collections[k][0], k) for k in [root] + main_lib.subcollections(root)]


# -- item building -------------------------------------------------------------
def item_type(c):
    dblp = ((c.paper.get("externalIds") or {}).get("DBLP") or "").lower()
    if c.venue and dblp.startswith("journals/") and not dblp.startswith("journals/corr/"):  # corr = arXiv
        return "journalArticle"
    if c.venue:
        return "conferencePaper"
    return "preprint"


def fill_item(item, c, collection_key):
    p = c.paper
    ext = p.get("externalIds") or {}
    item["title"] = p.get("title", "")
    item["creators"] = [split_name(a["name"]) for a in (p.get("authors") or []) if a.get("name")]
    item["abstractNote"] = p.get("abstract") or ""
    item["date"] = p.get("publicationDate") or str(p.get("year") or "")
    item["url"] = f"https://arxiv.org/abs/{ext['ArXiv']}" if ext.get("ArXiv") else \
        f"https://www.semanticscholar.org/paper/{p['paperId']}"
    if "DOI" in item and ext.get("DOI"):
        item["DOI"] = ext["DOI"]
    for field in ("proceedingsTitle", "conferenceName", "publicationTitle"):
        if field in item:
            item[field] = p.get("venue") or ""
    if "repository" in item and ext.get("ArXiv"):
        item["repository"] = "arXiv"
        item["archiveID"] = f"arXiv:{ext['ArXiv']}"
    extra = [f"Semantic Scholar: {p['paperId']}"]
    if ext.get("ArXiv"):
        extra.insert(0, f"arXiv: {ext['ArXiv']}")
    item["extra"] = "\n".join(extra)
    item["tags"] = [{"tag": "topic-radar"}, {"tag": SECTION_TAGS.get(c.section, "radar")},
                    {"tag": s2_tag(p["paperId"])}]
    item["collections"] = [collection_key]


def copy_item(data, collection_key):
    """Item data from one library, ready to be created in another one."""
    new = {k: v for k, v in data.items() if k not in ("key", "version", "dateAdded", "dateModified")}
    new["collections"] = [collection_key]
    new["relations"] = {}
    return new


def split_name(name):
    parts = name.split()
    if len(parts) == 1:
        return {"creatorType": "author", "name": name}
    return {"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]}


def note_html(c, run_date):
    e = html.escape
    rows = [f"<h2>Topic Radar · {e(run_date.isoformat())}</h2>"]
    if c.tldr:
        rows.append(f"<p><b>TL;DR</b>: {e(c.tldr)}</p>")
    if c.niche_note:
        rows.append(f"<p><b>Nicchia</b>: {e(c.niche_note)}</p>")
    rows.append(f"<p><b>Perché</b>: {e(c.reason)}</p>")
    return "\n".join(rows)
