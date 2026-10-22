"""HTML report (also used as e-mail body) and SMTP delivery."""

import html
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

SECTION_TITLES = {
    "niche": ("🎯", "Più vicini alla tua nicchia",
              "Ordinati per somiglianza con la collezione; citazioni e venue non contano."),
    "foundational": ("🏛", "Fondamentali che ti mancano",
                     "Lavori citati da molti paper della tua collezione: la base dell'argomento, anche se datati."),
    "top_venue": ("⭐", "Top nelle venue A*",
                  "I lavori più rilevanti e più citati (per età) pubblicati nelle venue selezionate."),
    "rising": ("🚀", "Emergenti (solo arXiv)",
               "Preprint recenti non ancora in una venue A*, ma con una velocità di citazione alta."),
}


def paper_link(p):
    ext = p.get("externalIds") or {}
    if ext.get("ArXiv"):
        return f"https://arxiv.org/abs/{ext['ArXiv']}"
    if ext.get("DOI"):
        return f"https://doi.org/{ext['DOI']}"
    return f"https://www.semanticscholar.org/paper/{p['paperId']}"


def render_html(profile_name, picked, stats, today, actions=None, footer=None):
    """actions(c) -> [(label, href)] decision links per paper; footer -> [(label, href)]."""
    e = html.escape

    def buttons(c):
        if not actions:
            return ""
        links = " ".join(f'<a href="{e(href)}" style="display:inline-block;margin:6px 6px 0 0;padding:3px 9px;'
                         f'border:1px solid #bbb;border-radius:12px;font-size:12px;color:#222;text-decoration:none">'
                         f'{e(label)}</a>' for label, href in actions(c))
        return f"<div>{links}</div>"

    parts = [f"""<html><body style="font-family:Segoe UI,Arial,sans-serif;max-width:760px;margin:auto;color:#222">
<h2 style="margin-bottom:4px">Topic Radar · {e(profile_name)}</h2>
<div style="color:#666;font-size:13px">{today.isoformat()} · {stats['n_seeds']} paper seme ·
{stats['n_candidates']} candidati · {stats['n_in_library']} esclusi perché già in Zotero o già proposti</div>"""]
    if actions:
        parts.append('<div style="color:#666;font-size:13px;margin-top:6px">Per ogni paper scegli una direzione: '
                     '<b>➕</b> lo aggiunge alla tua libreria nella collezione indicata (e stringe la nicchia), '
                     "<b>🗑</b> lo scarta (i paper simili verranno penalizzati). Il clic apre un'email già pronta: "
                     "inviala così com'è. Se non scegli nulla, il radar si ferma finché non lo fai.</div>")
    for section, cands in picked.items():
        icon, title, desc = SECTION_TITLES[section]
        parts.append(f'<h3 style="margin-top:28px">{icon} {title}</h3>'
                     f'<div style="color:#666;font-size:13px;margin-bottom:8px">{desc}</div>')
        if not cands:
            parts.append('<div style="color:#999;font-size:13px">Nessun paper nuovo in questa sezione.</div>')
        for c in cands:
            p = c.paper
            authors = ", ".join(a["name"] for a in (p.get("authors") or [])[:4])
            tldr = c.tldr or (p.get("abstract") or "")[:400]
            niche = (f'<div style="font-size:13px;margin-top:2px"><i>Nicchia:</i> {e(c.niche_note)}</div>'
                     if c.niche_note else "")
            parts.append(f"""<div style="border-left:3px solid #ccc;padding:4px 12px;margin:12px 0">
<a href="{e(paper_link(p))}" style="font-weight:600;color:#1a4fa0;text-decoration:none">{e(p.get('title', ''))}</a>
<div style="font-size:12px;color:#555;margin:2px 0">{e(authors)}</div>
<div style="font-size:12px;color:#2b6b2b">{e(c.reason)}</div>
<div style="font-size:13px;margin-top:4px">{e(tldr)}</div>{niche}{buttons(c)}</div>""")
    if footer:
        parts.append('<hr style="margin-top:28px">' + " · ".join(
            f'<a href="{e(href)}" style="font-size:13px">{e(label)}</a>' for label, href in footer))
    parts.append("</body></html>")
    return "\n".join(parts)


def send_email(cfg, subject, body_html):
    """Brevo HTTP API when BREVO_API_KEY is set (SMTP is often blocked on GitHub Actions), else SMTP."""
    if cfg.get("brevo_api_key"):
        r = requests.post("https://api.brevo.com/v3/smtp/email", timeout=30,
                          headers={"api-key": cfg["brevo_api_key"], "Accept": "application/json"},
                          json={"sender": {"name": "Topic Radar", "email": cfg["sender"]},
                                "to": [{"email": cfg["receiver"]}], "subject": subject, "htmlContent": body_html})
        if not r.ok:
            raise RuntimeError(f"Brevo API error {r.status_code}")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["receiver"]
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    port = int(cfg["smtp_port"])
    if port == 465:
        server = smtplib.SMTP_SSL(cfg["smtp_server"], port)
    else:
        server = smtplib.SMTP(cfg["smtp_server"], port)
        server.starttls()
    with server:
        server.login(cfg["sender"], cfg["sender_password"])
        server.sendmail(cfg["sender"], [cfg["receiver"]], msg.as_string())
