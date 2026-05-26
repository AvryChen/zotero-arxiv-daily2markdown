# Generate arXiv 维护手册

本文档面向后续维护者，尤其是接手本项目的 AI coding agent。目标是让维护者先理解系统边界、文件结构、数据流和必须保持的约束，再开始改代码。

## 项目定位

`Generate arXiv` 是论文日报的生成端。它不是前端站点，也不是知识库本体，但会同时驱动这两个下游：

- 生成端负责从 arXiv 找候选、rerank、领域判定、抓取 accepted 论文全文、写 capture 数据、写 daily JSON、生成 Hugo Markdown、触发网站仓库提交。
- `arxiv-knowledge-builder` 负责把 capture 数据增量转成知识库数据，如 `papers.jsonl`、`paper_insights.json`、`paper_workflows.json`、`aligned_vocabulary.json`。
- Hugo/前端仓库负责把 source posts、`data/daily` 和 `data/knowledge` 渲染成双语 daily/papers/knowledge 页面，并由 GitHub push 触发 Cloudflare Pages 部署。

当前本地常用路径：

```text
/Users/chenjx/Documents/Generate arXiv
  生成端仓库，本手册所在仓库

/Users/chenjx/Documents/Generate arXiv/vendor/arxiv-knowledge-builder
  作为 editable dependency 使用的 AKB 代码

/Users/chenjx/Documents/arxiv-knowledge-builder/data
  常用 AKB 数据快照和历史恢复源

/Users/chenjx/Library/CloudStorage/OneDrive-个人/Important0917/学业与个人发展/Jzhao/Web/arxiv-daily
  Hugo/前端网站仓库；Cloudflare Pages 监听这个仓库的 GitHub push
```

路径可能会被环境变量或配置覆盖。不要把上面的本地路径写死进业务代码；如果要固化行为，优先通过 `config/base.yaml`、`config/custom.yaml` 或 `.env` 配置。

## 核心原则

维护时必须保持这些约束：

1. arXiv 候选发现使用 target-date/catchup 逻辑；默认定时运行处理“昨天”的公告日，不走 RSS 最新论文路径。
2. 领域判定前不能下载全文。LLM 领域判定输入只应包含 title、abstract、categories、published_at、embedding_score 和 topic 等轻量元数据。
3. 只有 accepted 论文才能进入展示、capture accepted 记录和知识库增量。
4. rejected、uncertain、LLM 失败、JSON 解析失败都必须进入审计文件，不进入展示或知识图谱。
5. capture 是机器事实源；Hugo Markdown 是展示层。下游不应从 Markdown 反向恢复事实。
6. accepted 数量可以超过展示上限。capture 写全部 accepted；邮件和 Hugo 只展示 `display.max_paper_num`。
7. accepted 论文全文文本抽取顺序是 HTML -> PDF -> TeX source。为了保存 capture PDF，PDF bytes 可能会在 HTML 抽取前预下载；只有 PDF 可用时，必须从 PDF 生成 TXT；全部失败时可以摘要兜底，但 meta 必须记录失败原因。
8. 空 accepted 日也要写 run report 和 daily JSON。默认日更要显示“昨天没有新论文”提示。
9. 下一次默认日更开始前要删除旧的空提示页面。
10. AKB 空更新不能覆盖已有非空 `data/knowledge`。否则前端 `knowledge:demo` 会从空包重建并清掉已有知识页面。

## 运行入口

主入口是：

```text
src/zotero_arxiv_daily2markdown/main.py
```

它使用 Hydra 加载：

```text
config/default.yaml -> config/base.yaml + config/custom.yaml
```

常用命令：

```bash
# 正常日更：处理昨天的 arXiv 公告日
uv run python src/zotero_arxiv_daily2markdown/main.py executor.debug=false

# 指定单日
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.debug=false \
  executor.target_date=2026-05-25

# 指定日期区间
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.debug=false \
  executor.start_date=2026-05-01 \
  executor.end_date=2026-05-07 \
  executor.skip_existing=false

# arXiv 走代理
uv run python src/zotero_arxiv_daily2markdown/main.py \
  executor.debug=false \
  executor.arxiv_proxy_enabled=true \
  executor.arxiv_proxy_url=http://127.0.0.1:10809

# 强制不走环境代理
env -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY -u all_proxy -u https_proxy -u http_proxy \
  uv run python src/zotero_arxiv_daily2markdown/main.py executor.debug=false
```

## 端到端数据流

```text
Zotero corpus
  -> rerank reference corpus
  -> arXiv catchup candidates for target date
  -> rerank by title + abstract
  -> score_threshold + longlist
  -> LLM domain classifier
  -> accepted papers only:
       full text fetch
       TL;DR / translation / affiliations
       capture artifacts
       AKB incremental update
       daily JSON
       Hugo posts + generated daily/papers/knowledge pages
       git commit/push website repo
  -> rejected/uncertain/failed:
       audit files only
```

默认日更的特殊点：

1. `Executor._run_default_daily()` 把 `executor.target_date` 临时设成昨天。
2. 处理前调用 `cleanup_empty_hugo_notices()` 删除旧空提示。
3. 如果当天 accepted 为 0 且 `executor.send_empty=false`，不发邮件，但调用 `export_empty_notice_to_hugo()` 写空提示。
4. 如果网站已有非空 `data/knowledge`，Hugo exporter 仍会运行 `npm run knowledge:demo` 和 `npm run build`，从而生成新版 `content/{en,zh}/daily/YYYY-MM-DD.md`。

## 主要代码结构

```text
src/zotero_arxiv_daily2markdown/
  main.py
    Hydra CLI 入口；初始化日志、读取 .env、创建 Executor。

  executor.py
    主编排器。负责 Zotero 语料、候选抓取、rerank、domain classifier、accepted enrich、
    capture、AKB 增量、daily JSON、邮件、Hugo 导出、默认日更和历史区间模式。

  protocol.py
    核心数据模型。Paper 增加了 arxiv_id、doi、categories、domain_decision、
    full_text_path、pdf_path、hash、full_text_source 等 capture/AKB 字段。

  domain_classifier.py
    LLM 领域判定。输入 longlist 的轻量 metadata，输出固定 JSON schema。
    malformed JSON、API 异常、遗漏 paper 都转成 uncertain。

  capture_exporter.py
    写 data/capture。accepted paper 写 papers.jsonl 和全文工件；
    所有判定写 domain_decisions.json；未 accepted 候选写 rejected_candidates.jsonl；
    每天写 runs/YYYY-MM-DD.json。

  daily_exporter.py
    写网站侧 data/daily/YYYY-MM-DD.json。这个 JSON 是前端 daily 页面和调试状态的重要输入。

  hugo_exporter.py
    写 Hugo source posts，生成/清理 empty notice，自动 pull/add/commit/push 网站仓库，
    并在合适时执行 knowledge:demo 和 Hugo build。

  legacy_hugo_migrator.py
    迁移旧 Hugo Markdown。旧 TL;DR 只作为 LLM domain classifier 的轻量 abstract 输入；
    accept 之前不能抓 arXiv metadata/fulltext。

  retriever/arxiv_retriever.py
    arXiv 入口。target-date 默认从 catchup HTML 解析候选，纳入 New submissions、
    Cross-lists、Replacements；失败才按配置回退 export API。
    还负责全局限速、缓存、代理、HTML/PDF/TeX 全文抓取。

  reranker/
    local.py 使用 SentenceTransformers；api.py 使用 embedding API。

  construct_email.py
    HTML 邮件渲染。

  utils.py
    邮件发送、PDF 抽取、TeX source 处理、布尔配置解析等通用函数。
```

## 配置结构

主要配置在 `config/base.yaml`，个人覆盖在 `config/custom.yaml`，环境变量在 `.env`。

关键配置：

```text
zotero.*
  Zotero user ID、API key、include/ignore collection path。

source.arxiv.category
  目标 arXiv 分类。本项目常见配置为 cond-mat；实际值以 `config/custom.yaml` 或运行时覆盖为准。

executor.debug
  调试开关。生产运行应显式设为 false。

executor.send_empty
  默认 false。没有 accepted 时不发空邮件，但默认日更会生成空提示页。

executor.target_date / start_date / end_date
  单日或区间运行。target_date 不能和 start/end 同时使用。

executor.target_date_source
  auto/catchup/api。auto 优先 catchup，失败回退 API。

executor.score_threshold / executor.longlist
  轻量预筛和送入 LLM 领域判定的 longlist 数量。

domain.topic / domain.use_ai / domain.ai_confidence_threshold
  领域判定主题、是否启用 AI、accept 最低置信度。

capture.*
  capture 根目录、fulltext 目录、是否保存 PDF/TXT/meta/rejected。

knowledge.*
  是否调用 AKB 增量、输出目录、批大小、alignment/vocabulary review。

hugo.*
  Hugo content 目录、自动 push、knowledge:demo/build 命令、生成目录列表。

display.max_paper_num
  展示上限，不限制 capture accepted 数量。
```

## 输出与文件归属

### 生成端本仓库

```text
data/capture/
  papers.jsonl
    累积 accepted paper records。只包含 accepted。

  domain_decisions.json
    累积领域判定结果。accepted/reject/uncertain 都在这里。

  rejected_candidates.jsonl
    rejected/uncertain/failed 候选审计记录。

  runs/YYYY-MM-DD.json
    单次运行报告，含 retrieved/longlisted/accepted/rejected/uncertain/displayed/captured。

  fulltext/arxiv/<arxiv_id>.txt
  fulltext/arxiv/<arxiv_id>.pdf
  fulltext/arxiv/<arxiv_id>.meta.json
    accepted 论文的全文、PDF 和抓取审计记录。

data/daily/YYYY-MM-DD.json
  如果没有 Hugo output_dir，本地 daily JSON 会写这里；有 Hugo 时写到网站仓库 data/daily。

outputs/cache/arxiv/
  arXiv RSS/API/catchup/HTML/PDF/source/fulltext 缓存。缓存可以删，但会增加后续网络请求。
```

### 网站仓库

当 `hugo.output_dir` 指向网站 `content` 目录时，生成端会写：

```text
<site>/data/daily/YYYY-MM-DD.json
  每日状态 JSON。

<site>/data/knowledge/
  AKB 输出包。knowledge:demo 读取这里生成页面。

<site>/content/en/posts/YYYY-MM-DD-arxiv-daily.md
<site>/content/zh/posts/YYYY-MM-DD-arxiv-daily.md
  Hugo source posts。

<site>/content/en/daily/
<site>/content/zh/daily/
<site>/content/en/papers/
<site>/content/zh/papers/
<site>/content/en/knowledge/
<site>/content/zh/knowledge/
  npm run knowledge:demo 生成的双语页面。
```

`content/*/daily`、`content/*/papers`、`content/*/knowledge` 是生成产物，不要手改。要改展示逻辑，应改网站仓库的 `scripts/build_knowledge_demo.mjs` 或对应前端模板，然后重新生成。

## AKB 集成

依赖声明在 `pyproject.toml`：

```toml
[tool.uv.sources]
arxiv-knowledge-builder = { path = "vendor/arxiv-knowledge-builder", editable = true }
```

因此运行生成端时，代码实际使用 `vendor/arxiv-knowledge-builder`。如果同时维护 `/Users/chenjx/Documents/arxiv-knowledge-builder`，要确认两个地方的源码是否同步。

`Executor._update_knowledge_base_for_daily()` 会在 capture run report 存在后调用：

```text
arxiv_knowledge_builder.update_knowledge_base_incremental()
```

重要行为：

- `accepted_paper_ids=None`，AKB 从 `data/capture/runs/YYYY-MM-DD.json` 读取 accepted IDs。
- AKB 读取 capture，而不是读取 Hugo Markdown。
- 空 accepted 日会返回 `status=empty_update`。
- 生成端的 `_run_atomic_knowledge_update()` 先在临时目录更新；如果更新结果没有 paper records 且最终目录已有非空知识库，就保留原 `data/knowledge`，避免空包覆盖知识页面。
- `_update_knowledge_base_for_daily()` 只有在知识包有可发布 records 时才把 knowledge paths 交给 Hugo exporter。

## Hugo/Cloudflare 发布链路

Hugo exporter 做的事：

1. 根据 `hugo.output_dir` 推断网站仓库根目录。
2. 自动 `git pull --rebase --autostash`。
3. 写 `content/{zh,en}/posts/YYYY-MM-DD-arxiv-daily.md` 或空提示 source posts。
4. 如果 `build_knowledge_pages=true` 且有可用 knowledge 包，运行：

```bash
KNOWLEDGE_DATA_DIR=<site>/data/knowledge npm run knowledge:demo
npm run build
```

5. stage source posts、`data/daily`、必要的 `data/knowledge` 和 generated content dirs。
6. commit + push。
7. Cloudflare Pages 看到网站 GitHub 仓库更新后自动部署。

注意：

- 如果当天没有 accepted，也仍然需要生成 `content/{en,zh}/daily/YYYY-MM-DD.md`，否则新版 daily 入口看不到空提示。当前实现通过“已有非空 knowledge 包也可触发 knowledge:demo”保证这一点。
- 如果 `data/knowledge/papers.jsonl` 是空的，禁止运行 `knowledge:demo`，否则会删除已有 generated paper/knowledge 页面。
- 网站仓库出现未提交改动时，先判断是否是本流程生成的内容。不要随意 reset。

## 空提示页逻辑

默认日更处理昨天的公告日。例如北京时间 2026-05-26 运行时处理 2026-05-25。

如果 2026-05-25 没有 accepted 论文：

```text
content/en/posts/2026-05-25-arxiv-daily.md
content/zh/posts/2026-05-25-arxiv-daily.md
content/en/daily/2026-05-25.md
content/zh/daily/2026-05-25.md
```

会显示“昨天没有新论文”。下一次默认日更开始前，`cleanup_empty_hugo_notices()` 会删除旧 source posts 和 generated daily notice，再处理新的昨天日期。

## 历史迁移脚本

脚本入口：

```text
scripts/migrate_legacy_hugo_capture.py
src/zotero_arxiv_daily2markdown/legacy_hugo_migrator.py
```

用途：把旧逻辑生成的 Hugo Markdown 迁移成新 capture 格式。

关键约束：

- 默认 cutoff 是 `2026-04-30`。
- 旧 Hugo Markdown 只作为候选来源。
- LLM accept 之前不能访问 arXiv 抓 metadata 或全文。
- 旧 TL;DR/summary 可作为轻量 abstract 输入给同一个 domain classifier。
- 只有 accepted 才抓 HTML/PDF/TeX 生成真实全文 TXT/meta。
- 当天没有 accepted 时删除该日期中英文 Hugo 日报。
- 修改前会在 `data/capture/backups/legacy_hugo_capture_<timestamp>/` 下备份。

命令示例：

```bash
uv run python scripts/migrate_legacy_hugo_capture.py \
  --content-dir /path/to/arxiv-daily/content \
  --output-dir data/capture \
  --cutoff-date 2026-04-30 \
  --hugo-output-dir /path/to/arxiv-daily/content
```

## 修改指南

### 改 arXiv 抓取

主要文件：

```text
src/zotero_arxiv_daily2markdown/retriever/arxiv_retriever.py
tests/retriever/test_arxiv_retriever.py
tests/retriever/test_arxiv_integrity.py
```

必须保持：

- catchup 优先。
- New submissions、Cross-lists、Replacements 都纳入。
- 429/403/5xx/timeout 有退避和冷却。
- 只对 accepted 论文抓全文。
- `User-Agent` 和请求间隔不要移除。

### 改领域判定

主要文件：

```text
src/zotero_arxiv_daily2markdown/domain_classifier.py
src/zotero_arxiv_daily2markdown/protocol.py
tests/test_domain_classifier.py
```

必须测试：

- accept/reject/uncertain。
- 低置信度 accept 不进入 accepted。
- malformed JSON -> uncertain。
- API 异常 -> uncertain。
- LLM response 遗漏某篇 paper -> uncertain。

### 改 capture 输出

主要文件：

```text
src/zotero_arxiv_daily2markdown/capture_exporter.py
tests/test_capture_exporter.py
```

必须保持：

- `papers.jsonl` 只写 accepted。
- `domain_decisions.json` 保留所有判定。
- `rejected_candidates.jsonl` 写 rejected/uncertain/failed。
- accepted 论文写 TXT/PDF/meta。
- PDF-only 时从 PDF 抽 TXT。
- 全文失败摘要兜底时，meta 记录 `full_text_available=false` 和错误路径。

### 改 AKB 增量

主要文件：

```text
src/zotero_arxiv_daily2markdown/executor.py
vendor/arxiv-knowledge-builder/src/arxiv_knowledge_builder/
tests/test_executor.py
vendor/arxiv-knowledge-builder/tests/
```

必须保持：

- capture 是 AKB 输入源。
- capture decision 复用，不重复用 LLM 判定领域。
- capture + Markdown/Zotero 同 ID 合并时，capture 的全文路径和 domain decision 不被覆盖。
- empty_update 不应花 LLM token，也不能覆盖已有非空 `data/knowledge`。

### 改 Hugo/网站发布

主要文件：

```text
src/zotero_arxiv_daily2markdown/hugo_exporter.py
src/zotero_arxiv_daily2markdown/daily_exporter.py
tests/test_hugo_exporter.py
tests/test_daily_exporter.py
```

必须保持：

- 中英 source posts 都写。
- 中英 generated daily 页面都写。
- empty notice HTML 不能有会被 Goldmark 误判的空行。
- 空 knowledge package 不能触发 `knowledge:demo`。
- 非空已有 knowledge package 可以触发 daily 页面生成。
- 邮件失败不能阻塞 Hugo/capture。

## 验证命令

改代码后至少运行：

```bash
uv run pytest
python -m compileall -q src
git diff --check
```

如果改到 AKB 子模块，也要在子模块里跑：

```bash
cd vendor/arxiv-knowledge-builder
uv run pytest
python -m compileall -q src
git diff --check
```

如果改到网站构建或 Hugo exporter，并且本地有网站仓库：

```bash
cd /path/to/arxiv-daily
KNOWLEDGE_DATA_DIR=/path/to/arxiv-daily/data/knowledge npm run knowledge:demo
npm run build
```

线上式冒烟测试：

```bash
uv run python src/zotero_arxiv_daily2markdown/main.py executor.debug=false
```

运行后检查：

```bash
git status --short --branch
git -C /path/to/arxiv-daily status --short --branch
cat data/capture/runs/YYYY-MM-DD.json
cat /path/to/arxiv-daily/data/daily/YYYY-MM-DD.json
```

## 常见问题

### arXiv 429 或连接失败

优先检查：

- `executor.arxiv_request_interval_seconds`
- `executor.historical_day_cooldown_seconds`
- `executor.arxiv_cache_enabled`
- `executor.arxiv_proxy_enabled`
- `executor.arxiv_proxy_url`
- 是否意外开启了大量历史区间回溯

如果只是指定日期候选发现，优先走 catchup 页面：

```text
https://arxiv.org/catchup/cond-mat/YYYY-MM-DD?abs=True
```

代码中对应 `executor.target_date_source=auto|catchup`。较早日期的 catchup 页面可能不可用；如果 catchup 返回不可用或缺页，再考虑 API 或历史已有数据。

### LLM domain classifier malformed JSON

现有行为是把 longlist 全部转成 uncertain，不进入展示或知识库。检查：

```text
data/capture/domain_decisions.json
data/capture/rejected_candidates.jsonl
data/capture/runs/YYYY-MM-DD.json
```

不要为了“有内容”绕过 domain classifier。需要修 prompt 或 JSON 解析时，先加测试。

### 没有生成全文 TXT

只有 accepted 论文才会生成全文工件。先看当天 run report：

```text
data/capture/runs/YYYY-MM-DD.json
```

如果 `accepted_count=0`，没有 TXT 是正确行为。如果 accepted 论文只有 PDF，`capture_exporter.py` 会尝试从 PDF 抽 TXT；失败才摘要兜底。

### daily 页面没有出现

检查：

- `hugo.output_dir` 是否指向网站 `content` 目录。
- `<site>/data/knowledge/papers.jsonl` 是否非空。
- `hugo.build_knowledge_pages` 是否为 true。
- `content/{en,zh}/posts/YYYY-MM-DD-arxiv-daily.md` 是否存在。
- `content/{en,zh}/daily/YYYY-MM-DD.md` 是否存在。
- Hugo exporter 日志里是否运行了 `npm run knowledge:demo` 和 `npm run build`。

### Cloudflare Pages 没更新

Cloudflare Pages 由网站仓库 GitHub push 触发。先确认网站仓库不是只在本地生成：

```bash
git -C /path/to/arxiv-daily log --oneline -3
git -C /path/to/arxiv-daily status --short --branch
```

如果没有新 commit/push，Cloudflare 不会部署。

### 内存占用高

默认本地 reranker 会加载 SentenceTransformers。可以改用 API reranker：

```yaml
executor:
  reranker: api
```

或者在历史回溯时缩小日期范围、确认只做一次 corpus embedding、避免并发启动多份进程。

## 接手修改前清单

后续 AI 开始改动前，先做：

1. `git status --short --branch`
2. 查看 `config/custom.yaml` 和 `.env` 的配置意图，但不要泄露 secret。
3. 阅读 `README_zh.md` 和本文档。
4. 确认任务影响哪个边界：retriever、domain、capture、AKB、Hugo、网站构建。
5. 找对应测试文件，先写或更新回归测试。
6. 修改后跑相关测试，再跑全量 `uv run pytest`。
7. 如果涉及网站输出，实际运行 `knowledge:demo` 和 `npm run build`。
8. 如果涉及线上日更，跑一次 `executor.debug=false` 冒烟测试，并检查两个仓库状态。

## 不要做的事

- 不要用 Hugo Markdown 作为知识库事实源。
- 不要在 LLM accept 前抓 arXiv metadata/fulltext。
- 不要让空 `data/knowledge` 触发 `knowledge:demo`。
- 不要把 `executor.send_empty` 默认改回 true。
- 不要把生产运行改成 debug。
- 不要删除 arXiv 请求间隔、User-Agent、冷却或缓存逻辑。
- 不要在没有备份的情况下批量改历史 capture/Hugo 文件。
- 不要对网站仓库或本仓库执行 `git reset --hard` 之类破坏性操作，除非用户明确要求。
