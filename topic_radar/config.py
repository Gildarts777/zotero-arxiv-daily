"""Config loading (lightweight: used by the decision step before the heavy dependencies are installed)."""

import os
import re
from pathlib import Path

import yaml

HERE = Path(__file__).parent
# On a public repo the GitHub Actions logs are public: never log titles or collection names there.
PUBLIC_LOGS = bool(os.environ.get("GITHUB_ACTIONS"))


def _expand(obj):
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v) for v in obj]
    if isinstance(obj, str):
        m = re.fullmatch(r"\$\{(\w+)(?::-(.*))?\}", obj)
        if m:
            return os.environ.get(m.group(1)) or m.group(2)
    return obj


def load_config(path=HERE / "config.yaml"):
    return _expand(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def expand_profiles(cfg):
    """A profile's `collection` may list several collections separated by ';'."""
    out = []
    for prof in cfg["profiles"]:
        for path in str(prof.get("collection") or "").split(";"):
            path = path.strip().strip("/")
            if path:
                out.append({**prof, "collection": path, "name": prof.get("name") or path})
    if not out:
        raise SystemExit("No reference collection: set the RADAR_COLLECTION variable (e.g. Diffusion/Masked)")
    return out


def decisions_cfg(cfg):
    """Mailbox settings for decisions: the `decisions` section plus the receiver address."""
    return {"receiver": cfg["email"]["receiver"], **cfg.get("decisions", {})}


def radar_root(cfg, profile):
    """Collection path, in the radar account, holding this niche's Proposti / Scartati / Accettati."""
    return cfg["radar_zotero"]["root"].format(name=profile["name"])
