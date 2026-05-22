# Zotero arXiv Daily to Markdown

[English documentation](./README.md)

Zotero arXiv Daily to Markdown 会从 arXiv 构建每日论文速览。它用你的 Zotero 文献库作为兴趣语料，先根据标题和摘要筛选相关论文，再执行可审计的领域命中判定，只对 accepted 论文做规范化收录和全文抓取，随后调用兼容 OpenAI 的大模型生成摘要，最后把邮件和 Hugo Markdown 作为展示层导出。

本项目基于 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) 二次开发。当前版本重点强化了 arXiv 访问可靠性：全局请求限速、本地缓存、失败告警、历史回溯冷却，以及可选的 V2rayN 代理支持。

## 功能

- 从 arXiv RSS 或指定 arXiv 公告日期抓取论文。
- 读取 Zotero 文献库，并可按收藏夹路径筛选兴趣语料。
- 使用本地 SentenceTransformers 模型或 embedding API 做相关度排序。
- 通过 longlist 和 LLM 领域判定，只让 accepted 领域论文进入收录和展示。
- 仅对领域 accepted 论文抓全文，按 HTML、PDF、TeX source 顺序尝试。
- 输出规范化 capture 数据包，包括 `papers.jsonl`、`domain_decisions.json`、rejected 审计记录、run report、TXT、PDF 和每篇论文的 meta JSON。
- 通过兼容 Chat Completions 的 API 生成中英文摘要、作者机构和每日概览。
- 导出 HTML 邮件摘要和中英文 Hugo 文章；邮件发送是 best-effort，失败不会阻塞 Hugo 导出。
- 每次日常运行都会在下一次自动回看昨天的内容，并复用同一份 Zotero 兴趣语料排序；若历史 API 结果比当天推送多出或改动了论文，就覆盖昨天的 Hugo 输出。“昨日修订”邮件同样是 best-effort。
- 支持历史日期区间回溯、跳过已存在输出、失败后继续、日期间冷却。
- 可只让 arXiv 请求走 HTTP/SOCKS 代理，例如 V2rayN。
- 将 arXiv RSS/API/全文响应缓存到 `outputs/cache/arxiv`。
- arXiv 抓取失败、数据不完整或回溯某天失败时尽量发送告警邮件；告警邮件失败不会中断主流程。

## 工作流程

1. 加载 `config/default.yaml`，它会合并 `config/base.yaml` 和 `config/custom.yaml`。
2. 读取 Zotero 中的 `conferencePaper`、`journalArticle`、`preprint` 条目，并忽略没有摘要的条目。
3. 可选地用 `zotero.include_path` 和 `zotero.ignore_path` 筛选 Zotero 语料。
4. 从 RSS 或目标公告日期窗口抓取 arXiv 候选论文。最新/RSS 模式直接使用 RSS 中的 metadata，不再额外调用 arXiv API 补查每篇论文。
5. 只用标题和摘要对候选论文排序。
6. 应用 `executor.score_threshold` 和 `executor.longlist` 形成轻量 longlist。
7. 根据 `domain.topic` 对 longlist 做领域判定；accepted 论文进入 capture，rejected/uncertain 论文进入审计文件。
8. 仅对 accepted 论文抓全文，并保存规范化 capture 产物。
9. 为 accepted 论文生成 TL;DR、英文翻译、作者机构和每日概览。
10. 按配置尝试发送邮件；如果 SMTP 超时、登录失败或发送失败，只记录 warning 并继续。
11. 在设置 `hugo.output_dir` 时导出 Hugo Markdown。Markdown 是展示层；机器事实源是 capture JSONL。
12. 下一次日常运行会再用历史 API 回看昨天的文章，并使用同一份 Zotero 语料排序；若论文集合有差异则自动修正昨天的 capture/Hugo 输出。

## arXiv 访问策略

项目默认避免激进抓取：

- 所有 arXiv RSS/API/HTML/PDF/e-print 请求都经过同一个全局调度器。
- 默认 `User-Agent` 是 `arXiv Daily: Nickelate Superconductors (support@jxchen.org)`。
- 默认 arXiv 请求间隔是 `5` 秒。
- 遇到 429、403、5xx、超时和连接失败会指数退避并冷却。
- 历史回溯默认每处理一天后等待 `600` 秒。
- 不再给每个候选论文下载全文，只对领域 accepted 论文抓取。
- 已缓存的 arXiv 响应会被复用。
- 最新/RSS 模式不会再访问 arXiv API metadata 端点；指定日期和历史回溯仍会使用 `export.arxiv.org/api/query`。

## 环境要求

- Python `>=3.13`
- [uv](https://docs.astral.sh/uv/) 作为依赖管理工具
- Zotero user ID 和具有读取权限的 Zotero API key
- 兼容 OpenAI Chat Completions 的大模型 API
- 如果要发邮件，需要 SMTP 账号和授权码
- 如果要导出博客，需要 Hugo 站点的 `content` 目录或其它输出目录

默认本地 reranker 会通过 `sentence-transformers` 下载 Hugging Face 模型。如果不想加载本地 embedding 模型，可以使用 API reranker。

## 安装

```bash
git clone https://github.com/AvryChen/zotero-arxiv-daily2markdown.git
cd zotero-arxiv-daily2markdown
uv sync
```

创建并填写 `.env`：

```bash
cp .env.example .env
```

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

## 最小配置

大多数默认值在 `config/base.yaml` 中。建议把个人配置写入 `config/custom.yaml`，它会在 base 之后加载并覆盖同名字段。

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

## 重要配置

| 字段 | 作用 |
| --- | --- |
| `source.arxiv.category` | 要关注的 arXiv 分类，例如 `["cs.AI", "cs.CV"]` 或 `["cond-mat"]`。 |
| `source.arxiv.include_cross_list` | RSS 模式下是否包含交叉列表论文。 |
| `zotero.include_path` | 只使用收藏夹路径匹配这些 glob 规则的 Zotero 文献。 |
| `zotero.ignore_path` | 排除收藏夹路径匹配这些 glob 规则的 Zotero 文献。 |
| `executor.reranker` | `local` 使用 SentenceTransformers，`api` 使用 embedding API。 |
| `executor.max_paper_num` | 兼容旧配置；未设置 `display.max_paper_num` 时作为展示上限。 |
| `executor.longlist` | 通过分数预筛后送入领域判定的最大候选数。 |
| `executor.score_threshold` | 进入领域判定 longlist 的最低相关度分数。 |
| `domain.topic`, `domain.use_ai` | 领域判定主题，以及是否启用 LLM 判定。 |
| `domain.ai_confidence_threshold` | LLM accept 论文进入收录的最低置信度。 |
| `capture.enabled`, `capture.output_dir` | 是否输出规范化 capture 数据包及其根目录。 |
| `capture.fulltext_dir` | accepted 论文 TXT/PDF/meta 的输出目录。 |
| `display.max_paper_num` | 邮件和 Hugo 展示的 accepted 论文上限，不限制底层 capture 收录。 |
| `executor.target_date` | 运行单个 arXiv 公告日期，格式为 `YYYY-MM-DD`。 |
| `executor.start_date`, `executor.end_date` | 按闭区间回溯历史日期。 |
| `executor.historical_mode` | 历史回溯模式：`export_only` 或 `email_and_export`。 |
| `executor.historical_day_cooldown_seconds` | 历史回溯中两个已处理日期之间的冷却秒数。 |
| `executor.skip_existing` | 回溯时，如果中英文 Hugo 文件都已存在，则跳过该日期。 |
| `executor.continue_on_error` | 回溯时某一天失败后继续处理后续日期。 |
| `executor.fetch_strict` | arXiv 完整性校验发现缺页或缺 ID 时是否直接失败。 |
| `executor.cross_validate_dailyarxiv` | 对指定日期结果启用 dailyarxiv.com 交叉验证。 |
| `executor.arxiv_user_agent` | arXiv 请求使用的 User-Agent，应包含项目名和联系邮箱。 |
| `executor.arxiv_request_interval_seconds` | 所有 arXiv 请求之间的最小间隔秒数。 |
| `executor.arxiv_cache_enabled`, `executor.arxiv_cache_dir` | 是否启用 arXiv 本地缓存及缓存目录。 |
| `executor.error_email_enabled` | arXiv 抓取异常或完整性失败时是否发送告警邮件。 |
| `executor.arxiv_proxy_enabled`, `executor.arxiv_proxy_url` | 仅让 arXiv 请求走本地 HTTP/SOCKS 代理。 |
| `executor.arxiv_429_cooldown_seconds`, `executor.arxiv_failure_cooldown_seconds` | 连续遇到限流或连接失败后的额外冷却秒数。 |
| `email.smtp_timeout_seconds` | SMTP 连接、登录和发送的超时时间；超过后跳过邮件并继续导出。 |
| `hugo.output_dir` | Hugo 的 `content` 目录，或任何包含 `zh/posts` 与 `en/posts` 的输出目录。 |

## V2rayN 代理

代理默认关闭。开启后，只有 arXiv 请求会走代理；Zotero、OpenAI、SMTP 和 Hugo git 操作不受影响。

HTTP 代理，V2rayN 常见端口：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.arxiv_proxy_enabled=true \
  executor.arxiv_proxy_url=http://127.0.0.1:10809
```

SOCKS 代理：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.arxiv_proxy_enabled=true \
  executor.arxiv_proxy_url=socks5h://127.0.0.1:10808
```

等价的 `config/custom.yaml` 配置：

```yaml
executor:
  arxiv_proxy_enabled: true
  arxiv_proxy_url: http://127.0.0.1:10809
```

## API Reranker

如果不想用本地 embedding 模型，可以改用 embedding API：

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

## 运行

运行默认的最新论文流程：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py
```

这个模式包含“次日校正”：第二天的日常运行会再次通过历史 API 检查昨天的论文，并复用当前 Zotero 语料排序；必要时覆盖昨天的 Hugo 文件，并尝试补发一封说明差异的修订邮件。如果修订邮件失败，只要 Hugo 导出成功，校正仍会视为完成。

运行某个 arXiv 公告日期：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py executor.target_date="2026-05-01"
```

按正式流程回跑单日，不开启 debug，也不因为 Hugo 文件已存在而跳过：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.debug=false \
  executor.start_date="2026-05-19" \
  executor.end_date="2026-05-19" \
  executor.skip_existing=false
```

即使环境变量里配置了代理，也强制直连运行：

```bash
env -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY -u all_proxy -u https_proxy -u http_proxy \
  uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.debug=false \
  executor.start_date="2026-05-19" \
  executor.end_date="2026-05-19" \
  executor.skip_existing=false
```

回溯一个日期区间，只导出 Hugo 文件：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.start_date="2026-05-01" \
  executor.end_date="2026-05-07"
```

迁移旧版 Hugo 日报，把早期没有 capture 产物的内容改写成新格式：

```bash
uv run python scripts/migrate_legacy_hugo_capture.py \
  --content-dir /path/to/arxiv-daily/content \
  --output-dir data/capture \
  --cutoff-date 2026-04-30 \
  --hugo-output-dir /path/to/arxiv-daily/content
```

迁移脚本只把旧 Hugo Markdown 当作候选来源。对每个旧 arXiv ID，它会把旧版 AI 生成的 TL;DR/summary 当作轻量 `abstract`，交给主流程同一个 LLM 领域判定器做 accept/reject；在 LLM 判定 accept 之前不会访问 arXiv 抓 metadata 或全文。只有 accepted 论文才会触发 arXiv 全文抓取，并生成 capture TXT/meta。当天没有 accepted 论文时，会删除该日期的中英文 Hugo 日报。脚本修改 capture 或 Hugo 文件前，会先在 `data/capture/backups/legacy_hugo_capture_<timestamp>/` 下备份旧内容。

回溯一个日期区间，并为每天发送邮件：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.start_date="2026-05-01" \
  executor.end_date="2026-05-07" \
  executor.historical_mode=email_and_export
```

跳过已导出的日期，并在某天失败后继续：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.start_date="2026-05-01" \
  executor.end_date="2026-05-31" \
  executor.skip_existing=true \
  executor.continue_on_error=true
```

对指定日期启用 dailyarxiv 交叉验证：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.target_date="2026-05-01" \
  executor.cross_validate_dailyarxiv=true
```

Hydra 命令行覆盖可以修改任意配置：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  'source.arxiv.category=["cs.CL","cs.LG"]' \
  executor.max_paper_num=10 \
  executor.debug=true
```

## 输出

邮件输出是 HTML digest，包含论文标题、作者、机构、相关度分数、摘要和 PDF 链接。邮件只作为通知层：SMTP 超时、登录失败或发送失败会被记录并跳过，不会阻止 Hugo 导出或历史回溯继续。

启用 `capture.enabled` 后，会写入：

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

`papers.jsonl` 只包含领域 accepted 论文。`domain_decisions.json` 和 `rejected_candidates.jsonl` 会保留 accepted、rejected、uncertain 以及判定失败的审计记录。下游知识库构建应直接读取 capture 文件，而不是从 Hugo Markdown 反向解析。

在默认配置下，accepted 论文全文会保存到 `data/capture/fulltext/arxiv/`，每篇论文对应 `<arxiv_id>.txt`、`<arxiv_id>.pdf` 和 `<arxiv_id>.meta.json`。如果 accepted 论文只有 PDF 产物，exporter 会先从 PDF 抽取文本写入对应 TXT 文件，再考虑摘要兜底。如果某次运行没有论文通过领域判定，会写 run report，但不会为该日期新增全文文件。

配置 `hugo.output_dir` 后，会写入：

```text
<hugo.output_dir>/
  zh/posts/YYYY-MM-DD-arxiv-daily.md
  en/posts/YYYY-MM-DD-arxiv-daily.md
```

每篇 Hugo 文章会包含 front matter、每日概览、arXiv 公告时间窗口、相关度分数、作者列表、机构、原文链接和 AI 生成摘要。

如果 `hugo.auto_push` 为 true，或环境变量 `HUGO_AUTO_PUSH=true`，导出器会在 Hugo 仓库中执行 git 操作：pull rebase/autostash、add 生成文件、commit、push。

## 自动化

本地定时任务可以调用：

```bash
./run_daily.sh
./run_ubuntu.sh
```

Windows 可以运行：

```bat
run_daily.bat
```

GitHub Actions 工作流：

- `.github/workflows/main.yml` 手动运行每日推送流程，使用仓库变量和 secrets。
- `.github/workflows/test.yml` 手动运行 debug 推送流程。
- `.github/workflows/ci.yml` 在 push 和 pull request 时运行测试。
- `.github/workflows/keep-alive.yml` 定期更新 keep-alive 文件，避免计划任务被 GitHub 自动停用。

主 workflow 需要配置 `ZOTERO_ID`、`ZOTERO_KEY`、`SENDER`、`RECEIVER`、`SENDER_PASSWORD`、`OPENAI_API_KEY`、`OPENAI_API_BASE` 等 secrets。`CUSTOM_CONFIG` 建议配置为仓库变量，内容是运行时要写入 `config/custom.yaml` 的 YAML。

## 开发

运行默认测试：

```bash
uv run pytest
```

运行所有测试，包括 slow 和 live arXiv 测试：

```bash
uv run pytest -m ""
```

默认 pytest 配置会排除标记为 `slow` 和 `live_arxiv` 的测试。

项目结构：

```text
src/zotero_arxiv_daily2markdown/
  main.py                  Hydra 入口
  executor.py              端到端流程编排
  protocol.py              Paper 与 Zotero 语料数据模型
  domain_classifier.py     longlist 领域判定逻辑
  capture_exporter.py      规范化 capture JSON/TXT/PDF 导出
  retriever/               arXiv 抓取、缓存、代理与完整性校验
  reranker/                本地与 API embedding 重排序
  construct_email.py       HTML 邮件渲染
  hugo_exporter.py         Hugo Markdown 导出
  utils.py                 邮件、TeX、PDF 与通用工具

config/
  base.yaml                带注释的默认配置
  custom.yaml              本地覆盖配置
  default.yaml             Hydra 合并入口

tests/                     离线单元测试与集成测试
```

## 开源协议

本项目使用 AGPL-3.0 协议，派生自 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily)。如果你分发修改版本，请遵守原项目与本项目的许可要求，并保留相应署名。
