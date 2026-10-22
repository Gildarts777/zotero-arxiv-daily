"""Decisions by e-mail.

Every paper in the digest has mailto: links ("➕ Masked", "🗑 Scarta"). A click opens a pre-filled e-mail
addressed to yourself (the decisions mailbox, by default RECEIVER); you send it, and the next run reads it
over IMAP. Commands are signed with an HMAC of the IMAP password, and only messages from RECEIVER count.
Processed Message-IDs are remembered in the radar state, so every command is applied once.

    RADAR A <paperId> <collectionKey> <sig>   accept into a collection of the main account
    RADAR R <paperId> <collectionKey> <sig>   reject (Scartati collection of the radar account)
    RADAR FORCE <nonce> <sig>                 run tomorrow even if nothing changed
"""

import email
import hashlib
import hmac
import imaplib
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr
from urllib.parse import quote

log = logging.getLogger(__name__)
CMD_RE = re.compile(r"RADAR\s+(A|R)\s+([0-9a-f]{40})\s+([A-Z0-9]{8})\s+([0-9a-f]{12})")
FORCE_RE = re.compile(r"RADAR\s+FORCE\s+(\w+)\s+([0-9a-f]{12})")


@dataclass
class Decision:
    action: str          # "A" accept, "R" reject
    paper_id: str
    collection: str


def mailbox_user(cfg):
    return cfg.get("imap_user") or cfg["receiver"]


def sign(secret, *parts):
    return hmac.new(secret.encode(), ":".join(parts).encode(), hashlib.sha256).hexdigest()[:12]


def mailto(to, subject, body):
    return f"mailto:{to}?subject={quote(subject)}&body={quote(body)}"


def decision_link(cfg, action, paper_id, collection_key, title, target_label):
    """cfg = the `decisions` config merged with `email` (receiver)."""
    subject = f"RADAR {action} {paper_id} {collection_key} {sign(cfg['imap_password'], action, paper_id, collection_key)}"
    verb = "Aggiungi a" if action == "A" else "Scarta da"
    body = f"{verb} {target_label}:\n{title}\n\n(Invia questa email così com'è.)"
    return mailto(mailbox_user(cfg), subject, body)


def force_link(cfg, nonce):
    subject = f"RADAR FORCE {nonce} {sign(cfg['imap_password'], 'FORCE', nonce)}"
    return mailto(mailbox_user(cfg), subject, "Esegui Topic Radar al prossimo giro anche senza cambiamenti.")


def imap_server(cfg):
    if cfg.get("imap_server"):
        return cfg["imap_server"]
    domain = mailbox_user(cfg).rsplit("@", 1)[-1].lower()
    return {"gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
            "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
            "live.com": "outlook.office365.com"}.get(domain, f"imap.{domain}")


def parse_commands(text, secret):
    decisions, force = [], False
    for action, pid, key, sig in CMD_RE.findall(text):
        if hmac.compare_digest(sig, sign(secret, action, pid, key)):
            decisions.append(Decision(action, pid, key))
        else:
            log.warning("ignored command with a bad signature")
    for nonce, sig in FORCE_RE.findall(text):
        if hmac.compare_digest(sig, sign(secret, "FORCE", nonce)):
            force = True
    return decisions, force


class Mailbox:
    def __init__(self, cfg):
        self.cfg = cfg
        self.imap = imaplib.IMAP4_SSL(imap_server(cfg), int(cfg.get("imap_port") or 993))
        self.imap.login(mailbox_user(cfg), cfg["imap_password"])
        self.imap.select(cfg.get("imap_folder") or "INBOX")

    def fetch(self, processed, days=21):
        """RADAR messages from RECEIVER in the last `days`, not yet processed.
        Returns (decisions in order, force flag, new Message-IDs)."""
        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
        status, data = self.imap.search(None, "SINCE", since, "SUBJECT", '"RADAR"')
        ids = data[0].split() if status == "OK" and data and data[0] else []
        decisions, force, new_ids = [], False, []
        receiver = self.cfg["receiver"].lower()
        for num in ids:
            _, msg_data = self.imap.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            mid = (msg.get("Message-ID") or f"{msg.get('Date')}|{msg.get('Subject')}").strip()
            if mid in processed or mid in new_ids:
                continue
            if parseaddr(msg.get("From", ""))[1].lower() != receiver:
                continue
            text = str(make_header(decode_header(msg.get("Subject", "")))) + "\n" + _body_text(msg)
            d, f = parse_commands(text, self.cfg["imap_password"])
            decisions += d
            force |= f
            new_ids.append(mid)  # also invalid ones: read once, never re-checked
        return decisions, force, new_ids

    def close(self):
        try:
            self.imap.logout()
        except Exception:
            pass


def _body_text(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    out = []
    for part in parts:
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True) or b""
            out.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    return "\n".join(out)
