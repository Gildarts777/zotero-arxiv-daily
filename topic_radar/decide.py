"""Step 1 of a run (light: no torch). Tells whether the radar run is needed.

    python decide.py            # print run=true/false (also to $GITHUB_OUTPUT)
    python decide.py --force    # same, but always run=true

Your ➕ / 🗑 clicks are applied to Zotero immediately by the Cloudflare Worker; here we only look at the
result. If no niche changed since its last digest (nothing accepted or rejected, nothing added to or removed
from the niche or Scartati by hand), the run is skipped: it would only reproduce the same state.
Override: --force, the "Topic Radar (force)" workflow, or the "run anyway" link in the e-mail (it leaves a
note tagged topic-radar-force in the radar account, consumed here).
"""

import argparse
import logging
import os

from config import expand_profiles, load_config, radar_root
from library import load_library
from radar_store import FORCE_TAG, RadarStore, fingerprint

log = logging.getLogger("decide")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(args.config) if args.config else load_config()
    zc, rc = cfg["zotero"], cfg["radar_zotero"]
    radar = RadarStore(rc["user_id"], rc["api_key"])

    force = args.force
    requests_ = radar.z.items(tag=FORCE_TAG)
    if requests_:
        force = True
        for note in requests_:
            radar.z.delete_item(note)
        log.info("run requested from the e-mail link")

    main_lib = load_library(zc.get("mode", "local"), zc.get("user_id"), zc.get("api_key"))
    state = radar.load_state()
    profiles = expand_profiles(cfg)
    changed = [p for p in profiles
               if state.get(p["collection"], {}).get("fingerprint")
               != fingerprint(main_lib, radar.lib, p, radar_root(cfg, p))]
    skip_enabled = cfg.get("links", {}).get("skip_if_unchanged", True)
    run = force or not skip_enabled or bool(changed)
    log.info("niches changed: %d/%d -> run=%s", len(changed), len(profiles), str(run).lower())
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"run={str(run).lower()}\nforce={str(force).lower()}\n")
    print(f"run={str(run).lower()} force={str(force).lower()}")


if __name__ == "__main__":
    main()
