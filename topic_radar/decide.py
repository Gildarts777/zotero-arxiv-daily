"""Step 1 of the daily run (light: no torch). Applies the decisions received by e-mail and tells whether
today's radar run is needed.

    python decide.py            # apply decisions, print run=true/false (also to $GITHUB_OUTPUT)
    python decide.py --force    # same, but always run=true

Skipping: if no niche changed since the last digest (no paper accepted/rejected, nothing added to or
removed from the niche or Scartati collections by hand), the run is skipped - it would only reproduce the
same state. Override with --force, the workflow's "force" input, or the "run anyway" link in the e-mail.
"""

import argparse
import logging
import os

from config import decisions_cfg, expand_profiles, load_config, radar_root
from library import load_library
from mail_decisions import Mailbox
from radar_store import ACCEPTED, PENDING, REJECTED, Account, RadarStore, copy_item, fingerprint

log = logging.getLogger("decide")


def apply_decisions(decisions, main, radar):
    """Accept = copy the pending item (+ its notes) into the main account; reject = move it to Scartati."""
    done = 0
    for d in decisions:
        item = radar.find_by_paper(d.paper_id)
        if not item:
            log.warning("decision for a paper not in the radar account: skipped")
            continue
        root_key = next((k for k in item["data"]["collections"]
                         if radar.lib.collections.get(k, ("",))[0] == PENDING), None)
        if root_key is None:
            log.info("paper already decided: skipped")
            continue
        parent = radar.lib.collections[root_key][1]
        siblings = {radar.lib.collections[k][0]: k for k in radar.lib.subcollections(parent)}
        if d.action == "A":
            if d.collection not in main.lib.collections:
                log.warning("accept target collection no longer exists: skipped")
                continue
            new_key = main.create(copy_item(item["data"], d.collection))
            for note in radar.notes(item["key"]):
                main.create({**copy_item(note["data"], d.collection), "parentItem": new_key, "collections": []})
            radar.set_collections(item, remove=[root_key], add=[siblings[ACCEPTED]])
        else:
            if d.collection != siblings.get(REJECTED):
                log.warning("reject command for another niche: skipped")
                continue
            radar.set_collections(item, remove=[root_key], add=[d.collection])
        done += 1
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(args.config) if args.config else load_config()
    zc, rc = cfg["zotero"], cfg["radar_zotero"]
    main_acc = Account(zc["user_id"], zc["api_key"],
                       load_library(zc.get("mode", "local"), zc.get("user_id"), zc.get("api_key")))
    radar = RadarStore(rc["user_id"], rc["api_key"])

    force = args.force
    state = radar.load_state()
    mailbox = None
    try:
        mailbox = Mailbox(decisions_cfg(cfg))
        processed = state.get("_processed", [])
        decisions, force_mail, new_ids = mailbox.fetch(set(processed))
        force |= force_mail
        n = apply_decisions(decisions, main_acc, radar)
        if new_ids:
            state["_processed"] = (processed + new_ids)[-500:]
            radar.save_state(state)
        log.info("e-mail decisions: %d received, %d applied%s", len(decisions), n, ", run forced" if force_mail else "")
    except Exception as e:  # mailbox problems must not block the radar
        log.warning("could not read decisions from the mailbox (%s)", type(e).__name__)
    finally:
        if mailbox:
            mailbox.close()

    # Reload: accepted papers are now in the niche, rejected ones in Scartati.
    main_lib = load_library(zc.get("mode", "local"), zc.get("user_id"), zc.get("api_key"))
    radar_lib = load_library("web", rc["user_id"], rc["api_key"])
    changed = []
    for profile in expand_profiles(cfg):
        fp = fingerprint(main_lib, radar_lib, profile, radar_root(cfg, profile))
        if state.get(profile["collection"], {}).get("fingerprint") != fp:
            changed.append(profile)
    skip_enabled = cfg.get("decisions", {}).get("skip_if_unchanged", True)
    run = force or not skip_enabled or bool(changed)
    log.info("niches changed: %d/%d -> run=%s", len(changed), len(expand_profiles(cfg)), str(run).lower())
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"run={str(run).lower()}\nforce={str(force).lower()}\n")
    print(f"run={str(run).lower()} force={str(force).lower()}")


if __name__ == "__main__":
    main()
