# Zotero arXiv Daily to Markdown

[Chinese documentation](./README_zh.md)

Zotero arXiv Daily to Markdown watches new arXiv papers, compares them with your own Zotero library, summarizes the most relevant papers with an OpenAI-compatible LLM, and publishes the result as email and Hugo-ready Markdown.

This project is a customized fork of [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily). The main additions are Zotero collection filtering, stronger arXiv fetch integrity checks, two-pass reranking, bilingual Hugo export, date backfill, and optional automatic publishing to a Hugo site.

## What It Does

- Fetches papers from arXiv RSS feeds or from an explicit arXiv announcement date window.
- Builds a relevance profile from your Zotero papers, using titles, abstracts, collection paths, and recency.
- Reranks new papers with either a local SentenceTransformers model or an embedding API.
- Fetches full text for shortlisted papers with HTML, PDF, then TeX source fallback.
- Generates paper summaries, English translations, affiliations, and a daily overview through an OpenAI-compatible chat API.
- Sends an HTML email digest.
- Exports separate Hugo posts under `zh/posts/` and `en/posts/`.
- Supports historical backfill across a date range, with optional skip-existing behavior.

## How The Pipeline Works

1. Load configuration from `config/default.yaml`, which merges `config/base.yaml` and `config/custom.yaml`.
2. Read Zotero items of type `conferencePaper`, `journalArticle`, and `preprint`, ignoring items without abstracts.
3. Optionally filter the Zotero corpus with `zotero.include_path` and `zotero.ignore_path` glob patterns.
4. Retrieve arXiv papers from the configured categories.
5. First-pass rerank with title and abstract.
6. Build a longlist, fetch full text, generate summaries, and rerank again with the English TL;DR.
7. Keep papers above `executor.score_threshold`, capped by `executor.max_paper_num`.
8. Generate affiliations and a daily overview.
9. Send email if enabled for the current run, then export Hugo Markdown if `hugo.output_dir` is set.

## Requirements

- Python `>=3.13`
- [uv](https://docs.astral.sh/uv/) for dependency management
- A Zotero user ID and Zotero API key with library read access
- An OpenAI-compatible chat completion API
- SMTP credentials if you want email delivery
- A Hugo site content directory if you want Markdown export

The default local reranker downloads a Hugging Face model through `sentence-transformers`. If you prefer not to run local embeddings, use the API reranker instead.

## Installation

```bash
git clone https://github.com/AvryChen/zotero-arxiv-daily2markdown.git
cd zotero-arxiv-daily2markdown
uv sync
```

Create your environment file:

```bash
cp .env.example .env
```

Fill in the values in `.env`:

```dotenv
ZOTERO_ID=your_zotero_id
ZOTERO_KEY=your_zotero_api_key

OPENAI_API_KEY=your_llm_api_key
OPENAI_API_BASE=https://api.openai.com/v1
MODEL=gpt-4o-mini

SENDER=your_email@example.com
RECEIVER=receiver_email@example.com
SENDER_PASSWORD=your_smtp_password

HUGO_OUTPUT_DIR=/path/to/your/hugo/content
HUGO_AUTO_PUSH=false
DEBUG=false
```

## Configuration

Most project defaults live in `config/base.yaml`. Put your local overrides in `config/custom.yaml`; this file is loaded after the base config.

Minimal example:

```yaml
zotero:
  user_id: ${oc.env:ZOTERO_ID}
  api_key: ${oc.env:ZOTERO_KEY}
  include_path: null
  ignore_path: null

source:
  arxiv:
    category: ["cs.AI", "cs.CV", "cs.LG"]
    include_cross_list: true

executor:
  source: ["arxiv"]
  reranker: local
  max_paper_num: 20
  score_threshold: 3.0

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

Useful configuration fields:

| Field | Purpose |
| --- | --- |
| `source.arxiv.category` | arXiv categories to watch, for example `["cs.AI", "cs.CV"]` or `["cond-mat"]`. |
| `source.arxiv.include_cross_list` | Include cross-listed papers from RSS feeds. |
| `zotero.include_path` | Only use Zotero papers whose collection path matches one of these glob patterns. |
| `zotero.ignore_path` | Exclude Zotero papers whose collection path matches one of these glob patterns. |
| `executor.reranker` | `local` for SentenceTransformers or `api` for embedding API reranking. |
| `executor.longlist` | Number of candidates enriched with full text and LLM summaries before the second rerank. |
| `executor.llm_concurrency` | Concurrent LLM requests for longlist summary generation. |
| `executor.target_date` | Run a single arXiv announcement date in `YYYY-MM-DD` format. |
| `executor.start_date`, `executor.end_date` | Run a historical date range, inclusive. |
| `executor.historical_mode` | `export_only` or `email_and_export` for historical runs. |
| `executor.skip_existing` | Skip a historical date when both Hugo language files already exist. |
| `executor.continue_on_error` | Continue a historical run after one date fails. |
| `executor.fetch_strict` | Fail when arXiv fetch integrity checks detect missing pages or IDs. |
| `executor.cross_validate_dailyarxiv` | Compare target-date arXiv results with dailyarxiv.com. |
| `executor.arxiv_request_interval_seconds` | Minimum spacing between arXiv API requests. |
| `executor.arxiv_429_cooldown_seconds` | Extra cooldown before retrying after repeated arXiv 429 responses. |
| `executor.arxiv_rss_retries` | Retry count for latest-paper RSS requests. |
| `executor.arxiv_rss_cooldown_seconds` | Extra cooldown before retrying after repeated RSS failures. |
| `hugo.output_dir` | Hugo `content` directory, or any directory where `zh/posts` and `en/posts` should be written. |

For API-based embedding reranking, configure:

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

Run the default latest-paper workflow:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py
```

Run for one arXiv announcement date:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py executor.target_date="2026-05-01"
```

Backfill a date range and only export Hugo files:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.start_date="2026-05-01" \
  executor.end_date="2026-05-07"
```

Backfill a date range and send email for each date:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.start_date="2026-05-01" \
  executor.end_date="2026-05-07" \
  executor.historical_mode=email_and_export
```

Enable dailyarxiv cross-validation for a target date:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.target_date="2026-05-01" \
  executor.cross_validate_dailyarxiv=true
```

Hydra overrides can change any config value from the command line:

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  'source.arxiv.category=["cs.CL","cs.LG"]' \
  executor.max_paper_num=10 \
  executor.debug=true
```

## Output

Email output is rendered as an HTML digest with title, authors, affiliations, relevance score, summary, and PDF link.

When `hugo.output_dir` is set, the exporter writes:

```text
<hugo.output_dir>/
  zh/posts/YYYY-MM-DD-arxiv-daily.md
  en/posts/YYYY-MM-DD-arxiv-daily.md
```

Each post includes front matter, a daily overview, the arXiv submission processing window, relevance scores, author lists, affiliations, source links, and AI-generated summaries.

If `hugo.auto_push` is true or `HUGO_AUTO_PUSH=true`, the exporter will run git operations in the Hugo repository: pull with rebase/autostash, add the generated files, commit, and push.

## Automation

Local scheduled runs can call one of the bundled scripts:

```bash
./run_daily.sh
./run_ubuntu.sh
```

On Windows:

```bat
run_daily.bat
```

The repository also contains GitHub Actions workflows:

- `.github/workflows/main.yml` runs the digest workflow manually with repository variables and secrets.
- `.github/workflows/test.yml` runs a debug digest workflow manually.
- `.github/workflows/ci.yml` runs the test suite on pushes and pull requests.
- `.github/workflows/keep-alive.yml` periodically updates a keep-alive file for scheduled workflows.

For the main workflow, configure secrets such as `ZOTERO_ID`, `ZOTERO_KEY`, `SENDER`, `RECEIVER`, `SENDER_PASSWORD`, `OPENAI_API_KEY`, and `OPENAI_API_BASE`. Configure `CUSTOM_CONFIG` as a repository variable containing the YAML content that should replace `config/custom.yaml` during the workflow.

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
  retriever/               arXiv retrieval and integrity checks
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

This project is licensed under AGPL-3.0. It is derived from [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily); please keep the original license terms and attribution in mind when redistributing modified versions.
