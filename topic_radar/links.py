"""One-click decision links for the e-mail, handled by the Cloudflare Worker in worker/worker.js.

Links are signed with HMAC-SHA256 of RADAR_LINK_SECRET (same value as the Worker's LINK_SECRET);
`sign` must stay byte-for-byte compatible with `sign` in worker.js.
"""

import hashlib
import hmac
from urllib.parse import urlencode


def sign(secret, *parts):
    return hmac.new(secret.encode(), ":".join(parts).encode(), hashlib.sha256).hexdigest()[:16]


def decision_link(cfg, action, paper_id, from_key, to_key, log_key=""):
    """action 'A': radar Proposti -> main collection to_key (radar copy to Accettati log_key);
    action 'R': radar Proposti -> radar Scartati to_key."""
    params = {"a": action, "p": paper_id, "f": from_key, "t": to_key, "l": log_key,
              "s": sign(cfg["secret"], action, paper_id, from_key, to_key, log_key)}
    return f"{cfg['worker_url'].rstrip('/')}/radar?{urlencode(params)}"


def force_link(cfg, nonce):
    return f"{cfg['worker_url'].rstrip('/')}/radar/force?{urlencode({'n': nonce, 's': sign(cfg['secret'], 'FORCE', nonce)})}"
