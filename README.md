# Zotero arXiv Daily to Markdown

[Chinese documentation](./README_zh.md)

Zotero arXiv Daily to Markdown builds a daily research digest from arXiv papers. It uses your Zotero library as the relevance profile, ranks new papers by title and abstract, applies an auditable domain decision step, captures accepted papers into machine-readable JSON/TXT/PDF artifacts, summarizes them with an OpenAI-compatible LLM, then exports email and Hugo Markdown as display outputs.

This repository is a customized fork of [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily). The current version focuses on reliable arXiv access: global request throttling, cache reuse, failure alerts, historical backfill cooldowns, and optional V2rayN-compatible proxy support.

For maintainers and future AI coding agents, read [the maintenance manual](./docs/maintenance_manual.md) before changing the pipeline. It explains the producer/knowledge/site boundaries, file ownership, output contracts, and the checks required after edits.

## Features

- Fetch papers from an explicit arXiv announcement date; the default scheduled run processes yesterday's date with the same target-date path.
- Build a relevance profile from Zotero papers, with optional collection-path filtering.
- Rank papers with either a local SentenceTransformers model or an embedding API.
- Use a longlist plus LLM domain classification so only accepted domain papers enter capture and display outputs.
- Fetch full text only after domain acceptance, trying HTML, PDF, then TeX source.
- Write a normalized capture package with `papers.jsonl`, `domain_decisions.json`, rejected-candidate audit records, run reports, TXT, PDF, and per-paper meta JSON.
- Generate Chinese/English summaries, affiliations, and a daily overview through a Chat Completions-compatible API.
- Export an HTML email digest and bilingual Hugo posts; email delivery is best-effort and will not block Hugo export.
- Before each default scheduled run, remove stale "No new papers yesterday" Hugo notices from earlier empty days.
- Backfill historical date ranges with skip-existing, continue-on-error, and day-level cooldown controls.
- Route only arXiv requests through an optional HTTP/SOCKS proxy such as V2rayN.
- Cache arXiv RSS/API/full-text responses under `outputs/cache/arxiv`.
- Send best-effort alert emails when arXiv fetching fails, returns incomplete data, or a historical day fails.

## Pipeline

1. Load `config/default.yaml`, which merges `config/base.yaml` and `config/custom.yaml`.
2. Read Zotero `conferencePaper`, `journalArticle`, and `preprint` items, ignoring items without abstracts.
3. Optionally filter the Zotero corpus with `zotero.include_path` and `zotero.ignore_path`.
4. Fetch arXiv candidates for a target announcement date. The default `auto` source first parses the arXiv catchup page, including new submissions, cross-lists, and replacements, and falls back to the export API only if catchup fails. When no explicit date is configured, the CLI temporarily sets `executor.target_date` to yesterday, so the scheduled path uses the same date-based arXiv logic as a manual target run.
5. Rank candidates using title and abstract only.
6. Apply `executor.score_threshold` and `executor.longlist` to form a lightweight longlist.
7. Classify longlisted papers against `domain.topic`; accepted papers are captured, while rejected and uncertain papers are kept in audit files.
8. Fetch full text only for accepted papers, then save normalized capture artifacts.
9. Generate TL;DRs, English translations, affiliations, and a daily overview for accepted papers.
10. Try to send email when enabled; SMTP failures are logged and skipped so Hugo export can continue.
11. Export Hugo Markdown when `hugo.output_dir` is configured. Markdown is a display layer; capture JSONL is the machine-readable fact source.
12. If the default scheduled run finds no accepted papers, write a temporary bilingual Hugo notice saying there were no new papers yesterday; the next scheduled run removes stale notices before processing the next date.

## arXiv Access Policy

The project intentionally avoids aggressive crawling:

- All arXiv RSS/API/HTML/PDF/e-print requests pass through one global request scheduler.
- The default `User-Agent` is `arXiv Daily: Nickelate Superconductors (support@jxchen.org)`.
- The default arXiv request interval is `5` seconds.
- 429, 403, 5xx, timeout, and connection failures use exponential backoff and cooldowns.
- Historical backfill waits `600` seconds between processed dates by default.
- Full text is not downloaded for every candidate, only for domain-accepted papers.
- Cached arXiv responses are reused when available.
- The default scheduled run and manual target-date runs use the arXiv catchup HTML page for candidate discovery, then fetch full text only after domain acceptance. The export API is a fallback, not the first choice.

## Requirements

- Python `>=3.13`
- [uv](https://docs.astral.sh/uv/) for dependency management
- Zotero user ID and a Zotero API key with read access
- An OpenAI-compatible Chat Completions API
- SMTP credentials for email delivery
- A Hugo content directory if you want Markdown export

The local reranker uses a Hugging Face model through `sentence-transformers`. If you do not want local embedding downloads or GPU/CPU model loading, use the API reranker.

## Installation

```bash
git clone https://github.com/AvryChen/zotero-arxiv-daily2markdown.git
cd zotero-arxiv-daily2markdown
uv sync
```

Create and fill `.env`:

```bash
cp .env.example .env
```

```dotenv
ZOTERO_ID=your_zotero_id
ZOTERO_KEY=your_zotero_api_key

OPENAI_API_KEY=your_llm_api_key
OPENAI_API_BASE=https://api.openai.com/v1
MODEL=gpt-4o-mini

LEGACY_EMAIL_ENABLED=false

HUGO_OUTPUT_DIR=/path/to/your/hugo/content
HUGO_AUTO_PUSH=false
DEBUG=false
```

## Minimal Configuration

Most defaults live in `config/base.yaml`. Put your own overrides in `config/custom.yaml`; it is loaded after the base config.

```yaml
zotero:
  user_id: ${oc.env:ZOTERO_ID}
  api_key: ${oc.env:ZOTERO_KEY}
  include_path: null
  ignore_path: null

source:
  arxiv:
    category: ["cond-mat"]
    include_cross_list: true

executor:
  source: ["arxiv"]
  reranker: local
  max_paper_num: 20
  longlist: 80
  score_threshold: 3.0

domain:
  topic: "nickelate superconductors"
  use_ai: true
  ai_confidence_threshold: 0.5

capture:
  enabled: true
  output_dir: data/capture
  fulltext_dir: data/capture/fulltext

display:
  max_paper_num: 20

llm:
  api:
    key: ${oc.env:OPENAI_API_KEY}
    base_url: ${oc.env:OPENAI_API_BASE}
  generation_kwargs:
    model: ${oc.env:MODEL}
  language: Chinese

hugo:
  output_dir: ${oc.env:HUGO_OUTPUT_DIR,null}
```

## Important Configuration

| Field | Purpose |
| --- | --- |
| `source.arxiv.category` | arXiv categories to watch, for example `["cs.AI", "cs.CV"]` or `["cond-mat"]`. |
| `source.arxiv.include_cross_list` | Include cross-listed RSS entries. |
| `zotero.include_path` | Only use Zotero papers whose collection path matches one of these glob patterns. |
| `zotero.ignore_path` | Exclude Zotero papers whose collection path matches one of these glob patterns. |
| `executor.reranker` | `local` for SentenceTransformers or `api` for embedding API reranking. |
| `executor.max_paper_num` | Backward-compatible display limit when `display.max_paper_num` is unset. |
| `executor.longlist` | Maximum candidates sent to domain classification after score filtering. |
| `executor.score_threshold` | Minimum relevance score required to enter the domain-classification longlist. |
| `domain.topic`, `domain.use_ai` | Domain topic and toggle for LLM-based acceptance. |
| `domain.ai_confidence_threshold` | Minimum LLM confidence for accepted papers. |
| `capture.enabled`, `capture.output_dir` | Enable normalized capture output and choose its root directory. |
| `capture.fulltext_dir` | Directory for accepted-paper TXT/PDF/meta artifacts. |
| `display.max_paper_num` | Maximum accepted papers shown in email and Hugo output; does not limit capture. |
| `executor.target_date` | Run one arXiv announcement date in `YYYY-MM-DD` format. |
| `executor.target_date_source` | Candidate source for target-date runs: `auto` uses catchup first and falls back to API, `catchup` disables API fallback, and `api` preserves the old export API path. |
| `executor.start_date`, `executor.end_date` | Backfill a date range, inclusive. |
| `executor.historical_mode` | `export_only` or `email_and_export` for historical runs. |
| `executor.historical_day_cooldown_seconds` | Cooldown between processed historical dates. |
| `executor.skip_existing` | Skip a historical date when both Hugo language files already exist. |
| `executor.continue_on_error` | Continue a historical run after one date fails. |
| `executor.fetch_strict` | Fail when arXiv integrity checks detect missing pages or IDs. |
| `executor.cross_validate_dailyarxiv` | Compare target-date arXiv results with dailyarxiv.com. |
| `executor.arxiv_user_agent` | User-Agent used for arXiv requests; include a project name and contact email. |
| `executor.arxiv_request_interval_seconds` | Minimum spacing between all arXiv requests. |
| `executor.arxiv_cache_enabled`, `executor.arxiv_cache_dir` | Enable local arXiv cache and choose the cache directory. |
| `executor.error_email_enabled` | Send alert email on arXiv fetch errors or integrity failures. |
| `executor.arxiv_proxy_enabled`, `executor.arxiv_proxy_url` | Route only arXiv requests through a local HTTP/SOCKS proxy. |
| `executor.arxiv_429_cooldown_seconds`, `executor.arxiv_failure_cooldown_seconds` | Extra cooldown after repeated rate-limit or connection failures. |
| `email.smtp_timeout_seconds` | SMTP connect/login/send timeout. Email failures are logged and skipped after this timeout. |
| `hugo.output_dir` | Hugo `content` directory, or any directory with `zh/posts` and `en/posts`. |

## V2rayN Proxy

Proxying is disabled by default. When enabled, only arXiv requests use the proxy; Zotero, OpenAI, SMTP, and Hugo git operations are unaffected.

HTTP proxy, common V2rayN port:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.arxiv_proxy_enabled=true \
  executor.arxiv_proxy_url=http://127.0.0.1:10809
```

SOCKS proxy:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.arxiv_proxy_enabled=true \
  executor.arxiv_proxy_url=socks5h://127.0.0.1:10808
```

Equivalent `config/custom.yaml` snippet:

```yaml
executor:
  arxiv_proxy_enabled: true
  arxiv_proxy_url: http://127.0.0.1:10809
```

## API Reranker

Use this if you prefer embedding API ranking instead of a local model:

```yaml
executor:
  reranker: api

reranker:
  api:
    key: ${oc.env:OPENAI_API_KEY}
    base_url: ${oc.env:OPENAI_API_BASE}
    model: text-embedding-3-large
    batch_size: 64
```

## Running

Default scheduled workflow, processing yesterday's arXiv announcement date:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py
```

This mode first removes stale empty-day Hugo notices, then runs the same logic as `executor.target_date="<yesterday>"`; it does not use the RSS latest-paper path. If no papers are accepted for the domain, it writes bilingual Hugo notice pages saying "No new papers yesterday" instead of a normal empty paper list.

One arXiv announcement date:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py executor.target_date="2026-05-01"
```

Production-style single-day backfill, without debug mode and without skipping an existing Hugo post:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.debug=false \
  executor.start_date="2026-05-19" \
  executor.end_date="2026-05-19" \
  executor.skip_existing=false
```

Force a direct, no-proxy run even when proxy environment variables are set:

```bash
env -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY -u all_proxy -u https_proxy -u http_proxy \
  uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.debug=false \
  executor.start_date="2026-05-19" \
  executor.end_date="2026-05-19" \
  executor.skip_existing=false
```

Historical backfill, Hugo export only:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.start_date="2026-05-01" \
  executor.end_date="2026-05-07"
```

Legacy Hugo migration for old posts that were generated before capture artifacts existed:

```bash
uv run python scripts/migrate_legacy_hugo_capture.py \
  --content-dir /path/to/arxiv-daily/content \
  --output-dir data/capture \
  --cutoff-date 2026-04-30 \
  --hugo-output-dir /path/to/arxiv-daily/content
```

The migration treats old Hugo Markdown as a candidate source only. For each legacy arXiv ID, it reuses the old generated TL;DR/summary as the lightweight `abstract` input to the same LLM domain classifier used by the main workflow; it does not fetch arXiv metadata or full text before the LLM accept/reject decision. Only accepted papers trigger arXiv full-text fetching and capture TXT/meta output. Dates with no accepted papers have their old bilingual Hugo posts removed. The script creates a backup under `data/capture/backups/legacy_hugo_capture_<timestamp>/` before changing capture or Hugo files.

Historical backfill with email for each day:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.start_date="2026-05-01" \
  executor.end_date="2026-05-07" \
  executor.historical_mode=email_and_export
```

Skip already exported dates and continue after failures:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.start_date="2026-05-01" \
  executor.end_date="2026-05-31" \
  executor.skip_existing=true \
  executor.continue_on_error=true
```

Enable dailyarxiv cross-validation for a target date:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.target_date="2026-05-01" \
  executor.cross_validate_dailyarxiv=true
```

Hydra overrides can change any config value:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  'source.arxiv.category=["cs.CL","cs.LG"]' \
  executor.max_paper_num=10 \
  executor.debug=true
```

## Output

Email output is an HTML digest with paper titles, authors, affiliations, relevance scores, summaries, and PDF links. Email is treated as notification only: SMTP timeout, login, or send failures are logged and do not stop Hugo export or historical backfill.

When `capture.enabled` is true, the capture exporter writes:

```text
data/capture/
  papers.jsonl
  domain_decisions.json
  rejected_candidates.jsonl
  runs/YYYY-MM-DD.json
  fulltext/arxiv/<arxiv_id>.txt
  fulltext/arxiv/<arxiv_id>.pdf
  fulltext/arxiv/<arxiv_id>.meta.json
```

`papers.jsonl` contains only accepted domain papers. `domain_decisions.json` and `rejected_candidates.jsonl` keep the audit trail for accepted, rejected, uncertain, and failed classification results. Downstream knowledge-base tooling should read capture files directly instead of parsing Hugo Markdown.

For the default configuration, accepted-paper full text is saved under `data/capture/fulltext/arxiv/` as `<arxiv_id>.txt`, `<arxiv_id>.pdf`, and `<arxiv_id>.meta.json`. If an accepted paper only has a PDF artifact, the exporter extracts the PDF into the corresponding TXT file before falling back to the abstract. If no paper is accepted for a run, the run report is still written but no full-text files are created for that date.

When `hugo.output_dir` is set, the exporter writes:

```text
<hugo.output_dir>/
  zh/posts/YYYY-MM-DD-arxiv-daily.md
  en/posts/YYYY-MM-DD-arxiv-daily.md
```

Each post includes front matter, a daily overview, the arXiv announcement window, relevance scores, author lists, affiliations, source links, and AI-generated summaries.

If `hugo.auto_push` is true or `HUGO_AUTO_PUSH=true`, the exporter will run git operations in the Hugo repository: pull with rebase/autostash, add generated files, commit, and push.

## Automation

Local scheduled runs can call:

```bash
./run_daily.sh
./run_ubuntu.sh
```

On Windows:

```bat
run_daily.bat
```

GitHub Actions workflows:

- `.github/workflows/main.yml` runs the digest workflow manually with repository variables and secrets.
- `.github/workflows/test.yml` runs the same digest entrypoint manually against the checked-out repository.
- `.github/workflows/ci.yml` runs the test suite on pushes and pull requests.
- `.github/workflows/keep-alive.yml` periodically updates a keep-alive file for scheduled workflows.

For the main workflow, configure secrets such as `ZOTERO_ID`, `ZOTERO_KEY`, `OPENAI_API_KEY`, and `OPENAI_API_BASE`. The legacy SMTP email path is forced off in GitHub Actions. Configure `CUSTOM_CONFIG` as a repository variable containing the YAML content that should replace `config/custom.yaml`.

## Development

Run the default test suite:

```bash
uv run pytest
```

Run all tests, including slow and live arXiv tests:

```bash
uv run pytest -m ""
```

The default pytest configuration excludes tests marked `slow` and `live_arxiv`.

Project layout:

```text
src/zotero_arxiv_daily2markdown/
  main.py                  Hydra entry point
  executor.py              End-to-end orchestration
  protocol.py              Paper and corpus data models
  domain_classifier.py     Longlist domain decision logic
  capture_exporter.py      Normalized capture JSON/TXT/PDF exporter
  retriever/               arXiv retrieval, cache, proxy, and integrity checks
  reranker/                Local and API embedding rerankers
  construct_email.py       HTML email rendering
  hugo_exporter.py         Hugo Markdown export
  utils.py                 Email, TeX, PDF, and helper utilities

config/
  base.yaml                Documented defaults
  custom.yaml              Local overrides
  default.yaml             Hydra merge entry

tests/                     Offline unit and integration tests
```

## License

This project is licensed under AGPL-3.0. It is derived from [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily); keep the original license terms and attribution when redistributing modified versions.
