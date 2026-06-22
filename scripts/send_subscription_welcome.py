"""Send the manual subscription welcome email via Resend."""

from __future__ import annotations

import argparse
from pathlib import Path

import dotenv
from omegaconf import OmegaConf

from zotero_arxiv_daily2markdown.construct_email import (
    render_subscription_welcome_email,
    subscription_welcome_subject,
)
from zotero_arxiv_daily2markdown.utils import send_resend_html


def _load_config():
    root = Path(__file__).resolve().parent.parent
    dotenv.load_dotenv(root / ".env")
    base = OmegaConf.load(root / "config" / "base.yaml")
    custom = OmegaConf.load(root / "config" / "custom.yaml")
    config = OmegaConf.merge(base, custom)
    OmegaConf.resolve(config)
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["zh", "en"], default="zh")
    parser.add_argument("--recipient", action="append", default=[], help="Recipient email. Can be repeated.")
    parser.add_argument("--recipients", default="", help="Comma, semicolon, or newline separated recipients.")
    args = parser.parse_args()

    recipient_input = "\n".join(args.recipient + [args.recipients])
    if not recipient_input.strip():
        parser.error("at least one --recipient or --recipients value is required")

    config = _load_config()
    html = render_subscription_welcome_email(args.language)
    subject = subscription_welcome_subject(args.language)
    result = send_resend_html(
        config,
        html,
        recipient_input,
        subject,
        label=f"welcome-{args.language}",
    )
    print(result)


if __name__ == "__main__":
    main()
