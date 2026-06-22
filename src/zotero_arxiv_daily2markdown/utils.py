import tarfile
import re
import glob
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import parseaddr, formataddr
from loguru import logger
import datetime
from omegaconf import DictConfig
import pymupdf
import pymupdf.layout
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.layout.activate()

def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)

import pymupdf4llm  # noqa: E402

def extract_tex_code_from_tar(file_path:str, paper_id:str) -> dict[str,str]:
    try:
        tar = tarfile.open(file_path)
    except tarfile.ReadError:
        logger.debug(f"Failed to find main tex file of {paper_id}: Not a tar file.")
        return None
 
    tex_files = [f for f in tar.getnames() if f.endswith('.tex')]
    if len(tex_files) == 0:
        logger.debug(f"Failed to find main tex file of {paper_id}: No tex file.")
        tar.close()
        return None
    
    bbl_file = [f for f in tar.getnames() if f.endswith('.bbl')]
    match len(bbl_file) :
        case 0:
            if len(tex_files) > 1:
                logger.debug(f"Cannot find main tex file of {paper_id} from bbl: There are multiple tex files while no bbl file.")
                main_tex = None
            else:
                main_tex = tex_files[0]
        case 1:
            main_name = bbl_file[0].replace('.bbl','')
            main_tex = f"{main_name}.tex"
            if main_tex not in tex_files:
                logger.debug(f"Cannot find main tex file of {paper_id} from bbl: The bbl file does not match any tex file.")
                main_tex = None
        case _:
            logger.debug(f"Cannot find main tex file of {paper_id} from bbl: There are multiple bbl files.")
            main_tex = None

    if main_tex is None:
        logger.debug(f"Trying to choose tex file containing the document block as main tex file of {paper_id}")
    #read all tex files
    file_contents = {}
    for t in tex_files:
        f = tar.extractfile(t)
        content = f.read().decode('utf-8',errors='ignore')
        #remove comments
        content = re.sub(r'%.*\n', '\n', content)
        content = re.sub(r'\\begin{comment}.*?\\end{comment}', '', content, flags=re.DOTALL)
        content = re.sub(r'\\iffalse.*?\\fi', '', content, flags=re.DOTALL)
        #remove redundant \n
        content = re.sub(r'\n+', '\n', content)
        content = re.sub(r'\\\\', '', content)
        #remove consecutive spaces
        content = re.sub(r'[ \t\r\f]{3,}', ' ', content)
        if main_tex is None and re.search(r'\\begin\{document\}', content) and not any(w in t for w in ['example', 'sample']):
            main_tex = t
            logger.debug(f"Choose {t} as main tex file of {paper_id}")
        file_contents[t] = content
    
    if main_tex is not None:
        main_source:str = file_contents[main_tex]
        #find and replace all included sub-files
        include_files = re.findall(r'\\input\{(.+?)\}', main_source) + re.findall(r'\\include\{(.+?)\}', main_source)
        for f in include_files:
            if not f.endswith('.tex'):
                file_name = f + '.tex'
            else:
                file_name = f
            main_source = main_source.replace(f'\\input{{{f}}}', file_contents.get(file_name, ''))
        file_contents["all"] = main_source
    else:
        logger.debug(f"Failed to find main tex file of {paper_id}: No tex file containing the document block.")
        file_contents["all"] = None
        
    tar.close()
    return file_contents

def extract_markdown_from_pdf(file_path:str) -> str:
    return pymupdf4llm.to_markdown(file_path,use_ocr=False,header=False,footer=False,ignore_code=True)

def glob_match(path:str, pattern:str) -> bool:
    re_pattern = glob.translate(pattern,recursive=True)
    return re.match(re_pattern, path) is not None

def send_email(config:DictConfig, html:str, subject: str | None = None):
    sender = config.email.sender
    receiver = config.email.receiver
    password = config.email.sender_password
    smtp_server = config.email.smtp_server
    smtp_port = int(config.email.smtp_port)
    smtp_timeout = float(config.email.get("smtp_timeout_seconds", 20))
    def _format_addr(s):
        name, addr = parseaddr(s)
        return formataddr((Header(name, 'utf-8').encode(), addr))

    msg = MIMEText(html, 'html', 'utf-8')
    msg['From'] = _format_addr('Github Action <%s>' % sender)
    msg['To'] = _format_addr('You <%s>' % receiver)
    today = datetime.datetime.now().strftime('%Y/%m/%d')
    msg['Subject'] = Header(subject or f'Daily arXiv {today}', 'utf-8').encode()

    def _connect_starttls():
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=smtp_timeout)
        server.starttls()
        return server

    def _connect_ssl():
        return smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=smtp_timeout)

    def _connect_plain():
        return smtplib.SMTP(smtp_server, smtp_port, timeout=smtp_timeout)

    server = None
    if smtp_port == 465:
        try:
            server = _connect_ssl()
        except Exception as e:
            logger.debug(f"Failed to use SSL. {e}\nTry to use TLS.")
            try:
                server = _connect_starttls()
            except Exception as e:
                logger.debug(f"Failed to use TLS. {e}\nTry to use plain text.")
                server = _connect_plain()
    else:
        try:
            server = _connect_starttls()
        except Exception as e:
            logger.debug(f"Failed to use TLS. {e}\nTry to use SSL.")
            try:
                server = _connect_ssl()
            except Exception as e:
                logger.debug(f"Failed to use SSL. {e}\nTry to use plain text.")
                server = _connect_plain()

    try:
        server.login(sender, password)
        server.sendmail(sender, [receiver], msg.as_string())
    finally:
        server.quit()


# ── Resend email sender (new, bilingual, separate from legacy QQ SMTP) ─────

def _parse_recipients(raw: str) -> list[str]:
    """Parse a comma/semicolon/newline-separated recipient string into a list.

    Handles env-var style strings like ``"a@b.com, c@d.com"``.
    """
    if not raw or not raw.strip():
        return []
    # Split on commas, semicolons, or newlines
    parts = re.split(r"[,;\n]+", raw)
    return [p.strip() for p in parts if p.strip() and "@" in p]


def send_email_resend(
    config: DictConfig,
    html_zh: str,
    html_en: str,
    *,
    subject_date: str | None = None,
) -> dict:
    """Send bilingual daily emails to separate zh/en lists via Resend SMTP.

    Does **not** touch the legacy ``send_email()`` (QQ SMTP) code path.

    Args:
        config: Hydra config; expects a ``resend_email`` section.
        html_zh: Chinese email HTML body (with overview + TLDRs).
        html_en: English email HTML body.
        subject_date: ``YYYY-MM-DD`` date label for the subject line.

    Returns:
        ``{"zh": {...}, "en": {...}}`` with per-language send results.
    """
    cfg = config.resend_email
    recipients_zh = _parse_recipients(cfg.recipients_zh)
    recipients_en = _parse_recipients(cfg.recipients_en)

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    date_label = subject_date or today

    _LABELS = {
        "zh": {"subject": f"arXiv Daily 镍基超导日报 — {date_label}"},
        "en": {"subject": f"arXiv Daily: Nickelate Superconductors — {date_label}"},
    }

    results: dict = {"zh": None, "en": None}

    for lang, html_body, recipients in [
        ("zh", html_zh, recipients_zh),
        ("en", html_en, recipients_en),
    ]:
        results[lang] = send_resend_html(
            config,
            html_body,
            recipients,
            _LABELS[lang]["subject"],
            label=lang,
        )

    return results


def send_resend_html(
    config: DictConfig,
    html_body: str,
    recipients: str | list[str] | tuple[str, ...],
    subject: str,
    *,
    label: str = "email",
) -> dict:
    """Send one Resend HTML email body to explicit recipients.

    This is for manual emails such as subscription welcome messages. It does
    not read the configured zh/en daily lists unless the caller passes them in.
    """
    cfg = config.resend_email
    if isinstance(recipients, str):
        recipient_list = _parse_recipients(recipients)
    else:
        recipient_list = [str(recipient).strip() for recipient in recipients if "@" in str(recipient)]

    if not recipient_list:
        logger.info(f"Resend: no {label} recipients configured, skipping.")
        return {"sent": False, "reason": "no_recipients"}

    sender = formataddr((
        Header(cfg.sender_name, "utf-8").encode(),
        cfg.sender_email,
    ))

    try:
        server = smtplib.SMTP_SSL(
            cfg.smtp_server,
            int(cfg.smtp_port),
            timeout=float(cfg.get("smtp_timeout_seconds", 20)),
        )
        server.login("resend", cfg.api_key)
    except Exception as exc:
        logger.warning(f"Resend {label}: SMTP_SSL connection failed: {exc}")
        return {"sent": False, "error": str(exc)}

    sent_count = 0
    last_error = None
    try:
        for recipient in recipient_list:
            msg = MIMEText(html_body, "html", "utf-8")
            msg["From"] = sender
            msg["To"] = recipient
            msg["Subject"] = Header(subject, "utf-8").encode()
            try:
                server.sendmail(cfg.sender_email, [recipient], msg.as_string())
                sent_count += 1
                logger.info(f"Resend {label} email sent to {recipient}")
            except Exception as exc:
                logger.warning(f"Resend {label} email to {recipient} failed: {exc}")
                last_error = str(exc)
    finally:
        server.quit()

    if sent_count > 0:
        return {"sent": True, "recipients": sent_count}
    return {"sent": False, "error": last_error or "unknown"}
