from .protocol import Paper
import math
import urllib.parse
import os

# URL of your Cloudflare Worker, e.g. https://zotero-add.YOUR_SUBDOMAIN.workers.dev
# Set the ZOTERO_ADD_URL environment variable in your GitHub Actions secrets.
ZOTERO_ADD_URL = os.environ.get("ZOTERO_ADD_URL", "")

framework = """
<!DOCTYPE HTML>
<html>
<head>
<style>
.star-wrapper {
font-size: 1.3em; /* 调整星星大小 */
line-height: 1; /* 确保垂直对齐 */
display: inline-flex;
align-items: center; /* 保持对齐 */
}
.half-star {
display: inline-block;
width: 0.5em; /* 半颗星的宽度 */
overflow: hidden;
white-space: nowrap;
vertical-align: middle;
}
.full-star {
vertical-align: middle;
}
</style>
</head>
<body>
<div>
__CONTENT__
</div>
<br><br>
<div>
To unsubscribe, remove your email in your Github Action setting.
</div>
</body>
</html>
"""

def get_empty_html():
    block_template = """
<table border="0" cellpadding="0" cellspacing="0" width="100%" style="font-family: Arial, sans-serif; border: 1px solid #ddd; border-radius: 8px; padding: 16px; background-color: #f9f9f9;">
<tr>
<td style="font-size: 20px; font-weight: bold; color: #333;">
No Papers Today. Take a Rest!
</td>
</tr>
</table>
"""
    return block_template

def _extract_arxiv_id(url: str) -> str:
    """Extract the arXiv ID from a URL like https://arxiv.org/abs/2310.12345."""
    if "/abs/" in url:
        return url.split("/abs/")[-1].strip().rstrip("/")
    return ""

def _make_add_to_zotero_button(arxiv_id: str, tldr: str) -> str:
    """Return the HTML for the 'Add to Zotero' button, or empty string if not configured."""
    if not ZOTERO_ADD_URL or not arxiv_id:
        return ""
    params = {"id": arxiv_id}
    if tldr:
        params["tldr"] = tldr
    add_url = ZOTERO_ADD_URL.rstrip("/") + "/add?" + urllib.parse.urlencode(params)
    return (
        f'<a href="{add_url}" '
        'style="display: inline-block; text-decoration: none; font-size: 14px; '
        'font-weight: bold; color: #fff; background-color: #4A90D9; '
        'padding: 8px 16px; border-radius: 4px; margin-left: 8px;">'
        '📚 Add to Zotero</a>'
    )

def get_block_html(title:str, authors:str, rate:str, tldr:str, pdf_url:str,
                   affiliations:str=None, arxiv_id:str=""):
    add_button = _make_add_to_zotero_button(arxiv_id, tldr)
    block_template = """
<table border="0" cellpadding="0" cellspacing="0" width="100%" style="font-family: Arial, sans-serif; border: 1px solid #ddd; border-radius: 8px; padding: 16px; background-color: #f9f9f9;">
<tr>
<td style="font-size: 20px; font-weight: bold; color: #333;">
{title}
</td>
</tr>
<tr>
<td style="font-size: 14px; color: #666; padding: 8px 0;">
{authors}
<br>
<i>{affiliations}</i>
</td>
</tr>
<tr>
<td style="font-size: 14px; color: #333; padding: 8px 0;">
<strong>Relevance:</strong> {rate}
</td>
</tr>
<tr>
<td style="font-size: 14px; color: #333; padding: 8px 0;">
<strong>TLDR:</strong> {tldr}
</td>
</tr>
<tr>
<td style="padding: 8px 0;">
<a href="{pdf_url}" style="display: inline-block; text-decoration: none; font-size: 14px; font-weight: bold; color: #fff; background-color: #d9534f; padding: 8px 16px; border-radius: 4px;">PDF</a>{add_button}
</td>
</tr>
</table>
"""
    return block_template.format(
        title=title,
        authors=authors,
        rate=rate,
        tldr=tldr,
        pdf_url=pdf_url,
        affiliations=affiliations,
        add_button=add_button,
    )

def get_stars(score:float):
    full_star = '<span class="full-star">⭐</span>'
    half_star = '<span class="half-star">⭐</span>'
    low = 6
    high = 8
    if score <= low:
        return ''
    elif score >= high:
        return full_star * 5
    else:
        interval = (high-low) / 10
        star_num = math.ceil((score-low) / interval)
        full_star_num = int(star_num/2)
        half_star_num = star_num - full_star_num * 2
        return '<div class="star-wrapper">'+full_star * full_star_num + half_star * half_star_num + '</div>'

def render_email(papers:list[Paper]) -> str:
    parts = []
    if len(papers) == 0 :
        return framework.replace('__CONTENT__', get_empty_html())

    for p in papers:
        #rate = get_stars(p.score)
        rate = round(p.score, 1) if p.score is not None else 'Unknown'
        author_list = [a for a in p.authors]
        num_authors = len(author_list)
        if num_authors <= 5:
            authors = ', '.join(author_list)
        else:
            authors = ', '.join(author_list[:3] + ['...'] + author_list[-2:])

        if not p.affiliations:
            affiliations = 'Unknown Affiliation'
        elif len(p.affiliations) <= 5:
            affiliations = ', '.join(p.affiliations)
        else:
            affiliations = ', '.join(list(p.affiliations)[:5] + ['...'])
        tldr = p.tldr if p.tldr else p.abstract[:200] + '...' if p.abstract else ''
        arxiv_id = _extract_arxiv_id(p.url)

        parts.append(
            get_block_html(
                title=p.title,
                authors=authors,
                rate=rate,
                tldr=tldr,
                pdf_url=p.pdf_url or p.url,
                affiliations=affiliations,
                arxiv_id=arxiv_id,
            )
        )
        parts.append('<br>')

    content = '\n'.join(parts)
    return framework.replace('__CONTENT__', content)
