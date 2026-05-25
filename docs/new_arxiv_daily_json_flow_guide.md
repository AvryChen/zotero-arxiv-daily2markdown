# New arXiv Daily JSON Flow Fix Guide

## 背景

`Generate arXiv` 现在已经完成了新抓取闭环：

1. 发现 arXiv 候选论文。
2. 用 title + abstract 做相似度 rerank。
3. 对 longlist 做 LLM 领域判定。
4. 只对 accepted 论文抓 HTML/PDF/TeX 全文并生成 TXT、meta、summary。
5. 输出 Hugo Markdown 和 capture 数据。

前端现在要求：Markdown 只是展示层，不再作为唯一数据源。新抓取流程必须同时输出结构化 daily JSON，让 `/daily/` 页面可以直接读 JSON，不再从 Markdown 反向解析。

本指引只处理“新抓取流程”。历史 Markdown backfill 不在本任务范围内。

## 目标

每次新抓取一个 arXiv 公告日，都要额外输出：

```text
data/daily/YYYY-MM-DD.json
```

这里的 `YYYY-MM-DD` 是 arXiv 公告日，也就是 `announcement_date`。

如果配置了 Hugo 输出目录，例如：

```text
hugo.output_dir = /path/to/arxiv-daily/content
```

则 daily JSON 应写到：

```text
/path/to/arxiv-daily/data/daily/YYYY-MM-DD.json
```

如果没有 Hugo 输出目录，则退回写到项目本地：

```text
data/daily/YYYY-MM-DD.json
```

## 日期规则

稳定必填日期只有一个：

```json
"announcement_date": "2026-05-21"
```

新抓取流程可以写真实运行时字段：

```json
"processed_at": "2026-05-22T20:00:00+08:00",
"generated_at": "2026-05-22T20:05:00+08:00"
```

要求：

- `announcement_date` 来自 `executor.target_date`。
- `processed_at` 是本次抓取、筛选、摘要流程实际开始或处理时刻。
- `generated_at` 是 daily JSON / Hugo 展示产物实际生成时刻。
- 不要用 Hugo Markdown front matter 的 `date` 伪造 `processed_at` 或 `generated_at`。
- 历史 backfill 可以写 `processed_at: null`、`generated_at: null`，但新抓取流程应尽量写真实时间。

## Daily JSON 结构

普通命中日报：

```json
{
  "schema_version": "1.0",
  "announcement_date": "2026-05-21",
  "processed_at": "2026-05-22T20:00:00+08:00",
  "generated_at": "2026-05-22T20:05:00+08:00",
  "timezone": "Asia/Shanghai",
  "arxiv_window": {
    "start": "2026-05-21T00:00:00Z",
    "end": "2026-05-22T00:00:00Z"
  },
  "query_scope": "nickelate superconductors",
  "empty": false,
  "candidate_count": 42,
  "longlisted_count": 30,
  "accepted_count": 3,
  "rejected_count": 24,
  "uncertain_count": 3,
  "displayed_count": 3,
  "overview": {
    "zh": "今日的亮点工作聚焦于...",
    "en": "Today's highlights focus on..."
  },
  "papers": []
}
```

空日报也必须输出 JSON：

```json
{
  "schema_version": "1.0",
  "announcement_date": "2026-05-22",
  "processed_at": "2026-05-23T20:00:00+08:00",
  "generated_at": "2026-05-23T20:01:00+08:00",
  "timezone": "Asia/Shanghai",
  "arxiv_window": null,
  "query_scope": "nickelate superconductors",
  "empty": true,
  "candidate_count": 0,
  "longlisted_count": 0,
  "accepted_count": 0,
  "rejected_count": 0,
  "uncertain_count": 0,
  "displayed_count": 0,
  "empty_reason": "No accepted papers matched the nickelate superconductors scope.",
  "overview": {
    "zh": null,
    "en": null
  },
  "papers": []
}
```

## Paper 字段

`papers` 数组应包含本次所有 accepted papers，而不是只包含被 Hugo 展示上限截断后的 papers。展示上限只影响邮件和旧 Hugo Markdown。

每篇 paper 结构：

```json
{
  "rank": 1,
  "paper_id": "2605.20653",
  "arxiv_id": "2605.20653",
  "arxiv_version": "v1",
  "source_ids": {
    "arxiv": "2605.20653v1",
    "doi": null,
    "zotero_key": null
  },
  "title_plain": "Pressure-induced superconductivity in epitaxially-stabilized Pr3Ni2O7 films",
  "title_tex": "Pressure-induced superconductivity in epitaxially-stabilized Pr$_3$Ni$_2$O$_7$ films",
  "title_original": "Pressure-induced superconductivity in epitaxially-stabilized Pr$_3$Ni$_2$O$_7$ films",
  "authors": [
    "Motoki Osada",
    "Chieko Terakura"
  ],
  "affiliations": [
    "The University of Tokyo",
    "RIKEN"
  ],
  "categories": [
    "cond-mat.supr-con"
  ],
  "primary_category": "cond-mat.supr-con",
  "abs_url": "https://arxiv.org/abs/2605.20653v1",
  "pdf_url": "https://arxiv.org/pdf/2605.20653v1",
  "score": 5.5961,
  "domain_decision": {
    "decision": "accept",
    "confidence": 0.95,
    "reason": "Directly addresses bilayer nickelate superconductivity.",
    "matched_concepts": [],
    "negative_evidence": []
  },
  "summary": {
    "zh": "本研究通过...",
    "en": "This study..."
  },
  "summary_status": {
    "zh": "machine_generated",
    "en": "machine_generated"
  },
  "source_metadata": {
    "id_type": "arxiv",
    "full_text_path": "data/capture/fulltext/arxiv/2605.20653.txt",
    "pdf_path": "data/capture/fulltext/arxiv/2605.20653.pdf",
    "text_sha256": "...",
    "pdf_sha256": "...",
    "full_text_source": "html"
  }
}
```

## ID 规范

arXiv 论文必须拆分 ID 和版本。

输入可能是：

```text
2605.20653v1
https://arxiv.org/abs/2605.20653v1
http://arxiv.org/abs/2605.20653v1
https://arxiv.org/pdf/2605.20653v1
```

输出必须是：

```json
{
  "paper_id": "2605.20653",
  "arxiv_id": "2605.20653",
  "arxiv_version": "v1",
  "source_ids": {
    "arxiv": "2605.20653v1"
  }
}
```

不要把 `2605.20653v1` 当页面主键。

## 标题规范

需要同时输出：

- `title_original`：原始标题。
- `title_tex`：允许轻量 TeX 的展示标题。
- `title_plain`：纯文本标题，禁止包含 HTML/XML/MathML。

最低清洗要求：

- `title_plain` 中不能出现 `<math`、`</math>`、`xmlns`。
- 去掉 HTML/XML 标签。
- 对 HTML entity 做 unescape。
- 不要为了清洗标题访问外部网络。

## arXiv URL 规范

用规范 HTTPS URL：

```text
https://arxiv.org/abs/<versioned_arxiv_id>
https://arxiv.org/pdf/<versioned_arxiv_id>
```

即使旧数据里是 `http://arxiv.org/abs/...`，新 JSON 也应输出 HTTPS。

## 代码落点建议

### 1. 新增 daily exporter

建议新增：

```text
src/zotero_arxiv_daily2markdown/daily_exporter.py
```

提供函数：

```python
export_daily_json(
    *,
    accepted_papers: list[Paper],
    display_papers: list[Paper],
    candidate_papers: list[Paper],
    domain_decisions: list[DomainDecision],
    overview_zh: str,
    overview_en: str,
    config: DictConfig,
    announcement_date: str,
    processed_at: str | None,
    report: dict,
) -> Path
```

### 2. Executor 接入点

在 `Executor._build_single_day_artifacts()` 中：

1. 记录 `processed_at`。
2. 完成 retrieval / rerank / domain decision / accepted fulltext / summaries。
3. 生成 `overview_zh`、`overview_en`。
4. 调用 `export_daily_json(...)`。

注意：当前 `capture_exporter` 在 overview 之前执行。daily JSON 需要 overview，因此应在 overview 生成之后写。

### 3. Hugo auto push

如果 daily JSON 写入 Hugo 网站 repo 的 `data/daily/YYYY-MM-DD.json`，自动 push 时必须把这个 JSON 一起 stage/commit/push。

现在 `hugo_exporter.export_to_hugo()` 只 add 中英文 Markdown。需要调整为能额外 add daily JSON，或让 daily exporter 复用 `_auto_push_hugo_paths()`。

### 4. Markdown front matter

新生成的 Markdown 不要继续写：

```yaml
lang: zh
lang: en
```

语言由目录决定：

```text
content/zh/...
content/en/...
```

## 字段来源映射

| Daily JSON 字段 | 新抓取流程来源 |
|---|---|
| `announcement_date` | `executor.target_date` |
| `processed_at` | 本次执行真实时间 |
| `generated_at` | 写 daily JSON 或 Hugo 时的真实时间 |
| `timezone` | 固定 `Asia/Shanghai` |
| `query_scope` | `domain.topic`，没有则用 `prompt.topic` |
| `candidate_count` | `len(all_papers)` |
| `longlisted_count` | `len(longlist_papers)` |
| `accepted_count` | `len(accepted_papers)` |
| `rejected_count` | `domain_decisions` 中 `decision == "reject"` 数量 |
| `uncertain_count` | `domain_decisions` 中 `decision == "uncertain"` 数量 |
| `displayed_count` | `len(display_papers)` |
| `overview.zh` | `overview_zh` |
| `overview.en` | `overview_en` |
| `papers` | `accepted_papers`，按 score 排序后的 accepted 全量 |

| Paper JSON 字段 | 新抓取流程来源 |
|---|---|
| `rank` | accepted papers 排序后序号 |
| `paper_id` | `paper.arxiv_id` 去掉版本号 |
| `arxiv_id` | 同 `paper_id` |
| `arxiv_version` | `paper.arxiv_id` 的版本号 |
| `source_ids.arxiv` | 带版本号 arXiv ID |
| `title_original` | `paper.title` |
| `title_tex` | `paper.title` |
| `title_plain` | 清洗后的 `paper.title` |
| `authors` | `paper.authors` |
| `affiliations` | `paper.affiliations or []` |
| `categories` | `paper.categories` |
| `primary_category` | `paper.primary_category` |
| `abs_url` | 规范化 arXiv abs URL |
| `pdf_url` | 规范化 arXiv PDF URL |
| `score` | `paper.score` |
| `domain_decision` | `paper.domain_decision.to_dict()` 精简后 |
| `summary.zh` | `paper.tldr` |
| `summary.en` | `paper.tldr_en` |
| `summary_status.zh/en` | 有 summary 时 `machine_generated`，缺失时 `missing` |
| `source_metadata.full_text_path` | `paper.full_text_path` |
| `source_metadata.pdf_path` | `paper.pdf_path` |
| `source_metadata.text_sha256` | `paper.text_sha256` |
| `source_metadata.pdf_sha256` | `paper.pdf_sha256` |
| `source_metadata.full_text_source` | `paper.full_text_source` |

## 测试要求

至少增加这些测试：

1. 命中日报写出 `data/daily/YYYY-MM-DD.json`。
2. 空日报也写出 JSON，且 `empty: true`、`papers: []`。
3. `paper_id` 不带版本号，`arxiv_version` 单独保存。
4. `abs_url`、`pdf_url` 输出 HTTPS。
5. `title_plain` 不包含 `<math`、`</math>`、`xmlns`。
6. accepted 多于 display 上限时，daily JSON 的 `papers` 包含全部 accepted，Hugo Markdown 仍只展示 display 上限。
7. `processed_at`、`generated_at` 来自真实运行时间，不从 Markdown front matter 读取。
8. 新生成 Markdown front matter 不再包含 `lang`。

推荐验证命令：

```bash
uv run pytest
```

## 验收清单

- [ ] 新抓取流程每个公告日都有 `data/daily/YYYY-MM-DD.json`。
- [ ] 空日报也有 JSON。
- [ ] arXiv `paper_id` 全部不带版本号。
- [ ] `arxiv_version` 正确保存。
- [ ] `title_plain` 不含 HTML/XML/MathML。
- [ ] `summary.zh`、`summary.en` 和 `summary_status` 同时存在。
- [ ] daily JSON 写入网站 repo 后会被一起提交和推送。
- [ ] Hugo Markdown 仍可生成，但不再是唯一数据源。
- [ ] 新 Markdown front matter 不再写 `lang`。
- [ ] `uv run pytest` 通过。

