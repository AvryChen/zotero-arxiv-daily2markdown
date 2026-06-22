from .protocol import Paper
from datetime import datetime
import math
import html


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


def _get_revision_notice_html(revision_note: str) -> str:
  safe_note = html.escape(revision_note)
  return f"""
  <table border="0" cellpadding="0" cellspacing="0" width="100%" style="font-family: Arial, sans-serif; border: 1px solid #f0ad4e; border-radius: 8px; padding: 16px; background-color: #fff8e6;">
  <tr>
    <td style="font-size: 18px; font-weight: bold; color: #8a5a00;">
        {safe_note}
    </td>
  </tr>
  </table>
  """

def get_block_html(title:str, authors:str, rate:str, tldr:str, pdf_url:str, affiliations:str=None):
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
            <a href="{pdf_url}" style="display: inline-block; text-decoration: none; font-size: 14px; font-weight: bold; color: #fff; background-color: #d9534f; padding: 8px 16px; border-radius: 4px;">PDF</a>
        </td>
    </tr>
</table>
"""
    return block_template.format(title=title, authors=authors,rate=rate, tldr=tldr, pdf_url=pdf_url, affiliations=affiliations)

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


# ── Resend email (new, bilingual) ──────────────────────────────────────────

_RESEND_LABELS = {
    "zh": {
        "header_title": "arXiv Daily: 镍基超导日报",
        "overview_label": "今日速览",
        "relevance": "相关度",
        "tldr": "摘要",
        "authors": "作者",
        "affiliations": "机构",
        "pdf": "查看PDF",
        "no_papers": "今日无相关论文，休息一下！",
        "unsubscribe": "如需退订，请发送邮件至 support@nickelates.uk，主题为 'unsubscribe'。",
        "subject": "arXiv Daily 镍基超导日报",
    },
    "en": {
        "header_title": "arXiv Daily: Nickelate Superconductors",
        "overview_label": "Today's Overview",
        "relevance": "Relevance",
        "tldr": "TL;DR",
        "authors": "Authors",
        "affiliations": "Affiliations",
        "pdf": "View PDF",
        "no_papers": "No Papers Today. Take a Rest!",
        "unsubscribe": "To unsubscribe, email support@nickelates.uk with subject 'unsubscribe'.",
        "subject": "arXiv Daily: Nickelate Superconductors",
    },
}

_RESEND_CSS = """
  body {
    margin: 0; padding: 0; background-color: #f5f0e8;
    font-family: 'Georgia', 'Times New Roman', serif;
  }
  .container {
    max-width: 640px; margin: 0 auto; background-color: #ffffff;
    border: 1px solid #e0d8c8;
  }
  .header {
    background: linear-gradient(135deg, #8b6914 0%, #b8860b 50%, #d4a017 100%);
    padding: 28px 24px; text-align: center;
  }
  .header h1 {
    color: #ffffff; margin: 0; font-size: 22px; font-weight: 700;
    letter-spacing: 0.5px;
  }
  .header .date {
    color: rgba(255,255,255,0.85); font-size: 13px; margin-top: 6px;
  }
  .overview {
    padding: 20px 24px; background-color: #fdfaf3;
    border-bottom: 1px solid #e8e0d0;
  }
  .overview h2 {
    color: #8b6914; font-size: 16px; margin: 0 0 10px 0;
  }
  .overview p {
    color: #4a4030; font-size: 14px; line-height: 1.7; margin: 0;
  }
  .paper {
    padding: 20px 24px; border-bottom: 1px solid #f0ebe0;
  }
  .paper-title {
    font-size: 17px; font-weight: 700; color: #2a2218;
    margin: 0 0 8px 0; line-height: 1.4;
  }
  .paper-meta {
    font-size: 13px; color: #8a7a60; margin-bottom: 10px; line-height: 1.5;
  }
  .paper-meta strong { color: #6b5a40; }
  .tldr {
    font-size: 14px; color: #3a3028; line-height: 1.6;
    background-color: #fdfaf3; padding: 12px 16px;
    border-left: 3px solid #d4a017; margin-bottom: 10px;
  }
  .pdf-btn {
    display: inline-block; padding: 8px 20px;
    background-color: #b8860b; color: #ffffff !important;
    text-decoration: none; border-radius: 4px;
    font-size: 13px; font-weight: 600;
  }
  .footer {
    padding: 20px 24px; text-align: center;
    font-size: 11px; color: #b0a090;
    background-color: #fdfaf3;
  }
  .footer a { color: #8b6914; text-decoration: none; }
  .badge {
    display: inline-block; padding: 2px 10px;
    background-color: #f0e8d5; color: #8b6914;
    border-radius: 10px; font-size: 12px; font-weight: 600;
    margin-bottom: 10px;
  }
"""

_RESEND_FRAMEWORK = f"""<!DOCTYPE html>
<html lang="{{lang}}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{title}}</title>
  <style>{_RESEND_CSS}</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>{{header_title}}</h1>
      <div class="date">{{date}}</div>
    </div>
    {{overview_block}}
    {{paper_blocks}}
    <div class="footer">
      <p>{{unsubscribe}}</p>
    </div>
  </div>
</body>
</html>"""


def _render_one_paper_resend(p: Paper, labels: dict, language: str, *, max_authors: int = 5, max_affiliations: int = 5) -> str:
    """Render a single paper block for the Resend email."""
    author_list = [a for a in p.authors]
    num_authors = len(author_list)
    if num_authors <= max_authors:
        authors = ", ".join(author_list)
    else:
        authors = ", ".join(author_list[:3] + ["..."] + author_list[-2:])

    if p.affiliations:
        aff_list = p.affiliations[:max_affiliations]
        affiliations = ", ".join(aff_list)
        if len(p.affiliations) > max_affiliations:
            affiliations += ", ..."
    else:
        affiliations = None

    score = round(p.score, 1) if p.score is not None else None

    meta_parts = []
    if authors:
        meta_parts.append(f"<strong>{labels['authors']}:</strong> {html.escape(authors)}")
    if affiliations:
        meta_parts.append(f"<strong>{labels['affiliations']}:</strong> {html.escape(affiliations)}")
    if score is not None:
        meta_parts.append(f"<strong>{labels['relevance']}:</strong> {score}")

    meta_html = "<br>".join(meta_parts) if meta_parts else ""

    # Pick language-specific TLDR
    tldr_text = p.tldr_en if language == "en" else p.tldr
    tldr_text = tldr_text or ""

    return f"""
    <div class="paper">
      <div class="paper-title">{html.escape(p.title)}</div>
      <div class="paper-meta">{meta_html}</div>
      <div class="tldr"><strong>{labels['tldr']}:</strong> {html.escape(tldr_text)}</div>
      <a href="{html.escape(p.pdf_url or '#')}" class="pdf-btn">{labels['pdf']}</a>
    </div>"""


def render_email_resend(
    papers: list[Paper],
    *,
    language: str,
    overview: str = "",
    revision_note: str | None = None,
) -> str:
    """Build a language-specific HTML email for Resend delivery.

    Args:
        papers: List of papers to include.
        language: 'zh' or 'en'.
        overview: Language-specific daily overview text.
        revision_note: Optional revision banner text.
    """
    labels = _RESEND_LABELS.get(language, _RESEND_LABELS["en"])
    today = datetime.now().strftime("%Y-%m-%d")
    title = f"{labels['subject']} — {today}"

    # Overview block
    overview_block = ""
    if overview:
        overview_block = f"""
    <div class="overview">
      <h2>{labels['overview_label']}</h2>
      <p>{html.escape(overview)}</p>
    </div>"""

    # Paper blocks
    if not papers:
        paper_blocks = f"""
    <div class="paper">
      <div class="paper-title">{labels['no_papers']}</div>
    </div>"""
    else:
        parts = []
        if revision_note:
            parts.append(f"""
    <div class="paper" style="background-color:#fff8e6;border-left:3px solid #f0ad4e;">
      <div class="paper-title" style="color:#8a5a00;">{html.escape(revision_note)}</div>
    </div>""")
        for p in papers:
            parts.append(_render_one_paper_resend(p, labels, language))
        paper_blocks = "\n".join(parts)

    email_html = _RESEND_FRAMEWORK
    email_html = email_html.replace("{lang}", "zh-CN" if language == "zh" else "en")
    email_html = email_html.replace("{title}", html.escape(title))
    email_html = email_html.replace("{header_title}", labels["header_title"])
    email_html = email_html.replace("{date}", today)
    email_html = email_html.replace("{overview_block}", overview_block)
    email_html = email_html.replace("{paper_blocks}", paper_blocks)
    email_html = email_html.replace("{unsubscribe}", labels["unsubscribe"])

    return email_html


def render_emails_resend(
    papers: list[Paper],
    overview_zh: str = "",
    overview_en: str = "",
    revision_note: str | None = None,
) -> dict[str, str]:
    """Build both zh and en Resend emails.

    Returns:
        dict with 'zh' and 'en' HTML strings.
    """
    return {
        "zh": render_email_resend(papers, language="zh", overview=overview_zh, revision_note=revision_note),
        "en": render_email_resend(papers, language="en", overview=overview_en, revision_note=revision_note),
    }


_WELCOME_COPY = {
    "zh": {
        "lang": "zh-CN",
        "title": "订阅成功",
        "subtitle": "欢迎订阅 arXiv Daily: Nickelate Superconductors",
        "subject": "订阅成功：arXiv Daily 镍基超导日报",
        "paragraphs": [
            "你好，欢迎订阅 arXiv Daily: Nickelate Superconductors。",
            "这个项目会关注 nickelate superconductors 方向的 arXiv 新论文，并自动整理论文链接、相关度、摘要和每日速览。",
            "如果当天有新的相关论文，我们会发送邮件。如果当天没有新增论文，或者没有论文通过筛选，我们不会发送邮件，避免打扰。",
            "你的邮箱只会用于接收本项目相关邮件。邮件会逐个发送，其他订阅者看不到你的地址；你的个人信息也不会以任何形式向外泄露。",
            "这个项目由一名复旦大学本科大四学生独立开发和维护。项目还在持续改进中，欢迎你随时把建议、问题或漏掉的论文发到 support@nickelates.uk。",
        ],
        "footer": "如需退订，请发送邮件至 support@nickelates.uk，主题为 unsubscribe。",
    },
    "en": {
        "lang": "en",
        "title": "Subscription Confirmed",
        "subtitle": "Welcome to arXiv Daily: Nickelate Superconductors",
        "subject": "Subscription confirmed: arXiv Daily Nickelate Superconductors",
        "paragraphs": [
            "Hello, and welcome to arXiv Daily: Nickelate Superconductors.",
            "This project tracks new arXiv papers related to nickelate superconductors and sends a concise update with paper links, relevance scores, summaries, and a daily overview.",
            "When there are new relevant papers, you will receive an email. When there are no new papers, or no papers pass the relevance filter, we will not send an email.",
            "Your email address is used only for this project. Emails are sent one recipient at a time, so other subscribers cannot see your address. Your personal information will not be disclosed in any form.",
            "This project is independently developed and maintained by a fourth-year undergraduate student at Fudan University. Feedback, questions, and missing-paper reports are always welcome at support@nickelates.uk.",
        ],
        "footer": "To unsubscribe, email support@nickelates.uk with subject unsubscribe.",
    },
}


def subscription_welcome_subject(language: str = "zh") -> str:
    """Return the language-specific welcome email subject."""
    return _WELCOME_COPY.get(language, _WELCOME_COPY["zh"])["subject"]


def render_subscription_welcome_email(language: str = "zh") -> str:
    """Build the manual welcome email sent after a user subscribes."""
    copy = _WELCOME_COPY.get(language, _WELCOME_COPY["zh"])
    paragraphs = "\n".join(
        f"      <p>{html.escape(paragraph)}</p>"
        for paragraph in copy["paragraphs"]
    )
    footer = html.escape(copy["footer"])
    title = html.escape(copy["title"])
    subtitle = html.escape(copy["subtitle"])

    return f"""<!DOCTYPE html>
<html lang="{copy["lang"]}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>{_RESEND_CSS}</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>{title}</h1>
      <div class="date">{subtitle}</div>
    </div>
    <div class="overview">
{paragraphs}
    </div>
    <div class="footer">
      <p>{footer}</p>
    </div>
  </div>
</body>
</html>"""


# ── Legacy email (QQ SMTP, unchanged) ──────────────────────────────────────

def render_email(papers:list[Paper], revision_note: str | None = None) -> str:
    parts = []
    if revision_note:
        parts.append(_get_revision_notice_html(revision_note))
    if len(papers) == 0 :
        return framework.replace('__CONTENT__', "".join(parts) + get_empty_html())
    
    for p in papers:
        #rate = get_stars(p.score)
        rate = round(p.score, 1) if p.score is not None else 'Unknown'
        author_list = [a for a in p.authors]
        num_authors = len(author_list)
        if num_authors <= 5:
            authors = ', '.join(author_list)
        else:
            authors = ', '.join(author_list[:3] + ['...'] + author_list[-2:])
        if p.affiliations is not None:
            affiliations = p.affiliations[:5]
            affiliations = ', '.join(affiliations)
            if len(p.affiliations) > 5:
                affiliations += ', ...'
        else:
            affiliations = 'Unknown Affiliation'
        parts.append(get_block_html(p.title, authors, rate, p.tldr, p.pdf_url, affiliations))

    content = '<br>' + '</br><br>'.join(parts) + '</br>'
    return framework.replace('__CONTENT__', content)
