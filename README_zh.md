# Zotero arXiv Daily to Markdown

[英文文档](./README.md)

Zotero arXiv Daily to Markdown 会抓取新的 arXiv 论文，用你的 Zotero 文献库作为兴趣语料进行相关度排序，再调用兼容 OpenAI 接口的大模型生成摘要，最后输出邮件和 Hugo 可用的 Markdown。

本项目基于 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) 二次开发。相比原项目，这里重点加入了 Zotero 收藏夹过滤、arXiv 抓取完整性校验、两轮相关度排序、Hugo 中英文分文件导出、历史日期回溯，以及可选的 Hugo 站点自动提交发布。

## 它能做什么

- 从 arXiv RSS 或指定 arXiv 公告日期窗口抓取论文。
- 读取你的 Zotero 文献库，用标题、摘要、收藏夹路径和入库时间建立兴趣语料。
- 使用本地 SentenceTransformers 模型或 embedding API 对新论文做相关度排序。
- 对入围论文按 HTML、PDF、TeX 源码的顺序尝试抓取全文。
- 通过兼容 OpenAI 的 Chat Completions API 生成论文摘要、英文翻译、作者机构和每日概览。
- 发送 HTML 邮件推送。
- 分别导出 Hugo 中文文章和英文文章到 `zh/posts/` 与 `en/posts/`。
- 支持按日期区间回溯生成历史内容，并可跳过已存在的 Hugo 文件。

## 工作流程

1. 从 `config/default.yaml` 加载配置；该文件会合并 `config/base.yaml` 与 `config/custom.yaml`。
2. 从 Zotero 读取 `conferencePaper`、`journalArticle`、`preprint` 类型条目，并忽略没有摘要的条目。
3. 根据 `zotero.include_path` 和 `zotero.ignore_path` 的 glob 规则筛选 Zotero 语料。
4. 从配置的 arXiv 分类抓取候选论文。
5. 使用标题和摘要做第一轮相关度排序。
6. 生成 longlist，补抓全文，生成摘要，再用英文 TL;DR 做第二轮排序。
7. 保留分数不低于 `executor.score_threshold` 的论文，并限制在 `executor.max_paper_num` 篇以内。
8. 生成机构信息与每日概览。
9. 当前运行需要发邮件时发送邮件；如果配置了 `hugo.output_dir`，同时导出 Hugo Markdown。

## 环境要求

- Python `>=3.13`
- [uv](https://docs.astral.sh/uv/) 作为依赖管理工具
- Zotero user ID 和具有读取权限的 Zotero API key
- 兼容 OpenAI Chat Completions 的大模型 API
- 如果要发邮件，需要 SMTP 账号和授权码
- 如果要导出博客，需要 Hugo 站点的 `content` 目录或其它输出目录

默认的本地 reranker 会通过 `sentence-transformers` 下载 Hugging Face 模型。如果不想在本地跑 embedding，可以改用 API reranker。

## 安装

```bash
git clone https://github.com/AvryChen/zotero-arxiv-daily2markdown.git
cd zotero-arxiv-daily2markdown
uv sync
```

创建环境变量文件：

```bash
cp .env.example .env
```

填写 `.env`：

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

## 配置

大多数默认值在 `config/base.yaml` 中。你自己的配置建议写进 `config/custom.yaml`，它会在 base 之后加载并覆盖同名字段。

最小配置示例：

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

常用配置项：

| 字段 | 作用 |
| --- | --- |
| `source.arxiv.category` | 要关注的 arXiv 分类，例如 `["cs.AI", "cs.CV"]` 或 `["cond-mat"]`。 |
| `source.arxiv.include_cross_list` | RSS 模式下是否包含交叉列表论文。 |
| `zotero.include_path` | 只使用收藏夹路径匹配这些 glob 规则的 Zotero 文献。 |
| `zotero.ignore_path` | 排除收藏夹路径匹配这些 glob 规则的 Zotero 文献。 |
| `executor.reranker` | `local` 使用 SentenceTransformers，`api` 使用 embedding API。 |
| `executor.longlist` | 第二轮补全文、生成摘要和重排前的候选论文数量。 |
| `executor.llm_concurrency` | longlist 阶段并发调用 LLM 生成摘要的数量。 |
| `executor.target_date` | 运行单个 arXiv 公告日期，格式为 `YYYY-MM-DD`。 |
| `executor.start_date`, `executor.end_date` | 按闭区间回溯历史日期。 |
| `executor.historical_mode` | 历史回溯模式：`export_only` 或 `email_and_export`。 |
| `executor.skip_existing` | 回溯时，如果中英文 Hugo 文件都已存在，则跳过该日期。 |
| `executor.continue_on_error` | 回溯时某一天失败后继续处理后续日期。 |
| `executor.fetch_strict` | arXiv 完整性校验发现缺页或缺 ID 时是否直接失败。 |
| `executor.cross_validate_dailyarxiv` | 对指定日期结果启用 dailyarxiv.com 交叉验证。 |
| `executor.arxiv_request_interval_seconds` | arXiv API 请求之间的最小间隔秒数。 |
| `executor.arxiv_429_cooldown_seconds` | 连续遇到 arXiv 429 后再次重试前的额外冷却秒数。 |
| `executor.arxiv_rss_retries` | 最新论文 RSS 请求失败时的重试次数。 |
| `executor.arxiv_rss_cooldown_seconds` | RSS 连续失败后再次重试前的额外冷却秒数。 |
| `hugo.output_dir` | Hugo 的 `content` 目录，或任何用于写入 `zh/posts` 与 `en/posts` 的目录。 |

如果使用 API embedding 排序，可以这样配置：

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

运行某个 arXiv 公告日期：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py executor.target_date="2026-05-01"
```

回溯一个日期区间，只导出 Hugo 文件，不发送历史邮件：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.start_date="2026-05-01" \
  executor.end_date="2026-05-07"
```

回溯一个日期区间，并为每天发送邮件：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.start_date="2026-05-01" \
  executor.end_date="2026-05-07" \
  executor.historical_mode=email_and_export
```

对指定日期启用 dailyarxiv 交叉验证：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.target_date="2026-05-01" \
  executor.cross_validate_dailyarxiv=true
```

也可以通过 Hydra 命令行覆盖任意配置：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py \
  'source.arxiv.category=["cs.CL","cs.LG"]' \
  executor.max_paper_num=10 \
  executor.debug=true
```

## 输出

邮件输出是 HTML digest，包含论文标题、作者、机构、相关度分数、摘要和 PDF 链接。

配置 `hugo.output_dir` 后，会写入：

```text
<hugo.output_dir>/
  zh/posts/YYYY-MM-DD-arxiv-daily.md
  en/posts/YYYY-MM-DD-arxiv-daily.md
```

每篇 Hugo 文章会包含 front matter、每日概览、arXiv 投稿处理时间范围、相关度分数、作者列表、机构、原文链接和 AI 生成摘要。

如果 `hugo.auto_push` 为 true，或环境变量 `HUGO_AUTO_PUSH=true`，导出器会在 Hugo 仓库中执行 git 操作：pull rebase/autostash、add 生成文件、commit、push。

## 自动化

本地定时任务可以调用项目自带脚本：

```bash
./run_daily.sh
./run_ubuntu.sh
```

Windows 可以运行：

```bat
run_daily.bat
```

仓库里也包含 GitHub Actions 工作流：

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
  retriever/               arXiv 抓取与完整性校验
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
