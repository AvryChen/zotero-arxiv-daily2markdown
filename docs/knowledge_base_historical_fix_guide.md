# Knowledge Base Historical Data Fix Guide

## 背景

网站前端现在要求知识库数据与每日 arXiv 数据使用同一套稳定主键和结构化字段。旧版 `arxiv-knowledge-builder` 输出已经能生成：

```text
papers.jsonl
paper_insights.json
paper_workflows.json
facet_vocabulary.json
aligned_vocabulary.json
```

但旧数据里常见问题是：

- arXiv `paper_id` 可能带版本号，例如 `2605.20653v1`。
- 部分 Zotero/Markdown 来源使用 `title:...` 作为长期主键。
- `papers.jsonl` 缺少 `title_plain`、`title_tex`、`source_ids`、`summary_status`。
- `aligned_vocabulary[].evidence_papers` 可能引用旧 ID 或 `title:...`。
- capture、Markdown、Zotero 三路同一论文可能没有合并到同一个规范主键。
- 标题里可能有 HTML/XML/MathML，污染页面标题和 URL。

本指引只处理“以往知识库内容的自动修正”。目标是离线修正已有 JSON/JSONL，不重新抓 arXiv，不把历史修正变成大规模网络任务。

## 总目标

读取旧知识库输出目录，生成一套前端可直接使用的新格式文件：

```text
papers.jsonl
paper_insights.json
paper_workflows.json
aligned_vocabulary.json
build_report.json
```

如果保留兼容文件，也可以继续输出：

```text
all_papers.jsonl
facet_vocabulary.json
facet_vocabulary.csv
materials_methods_matrix.csv
aligned_matrix.csv
aligned_vocabulary.csv
```

但前端正式消费应优先读取：

```text
papers.jsonl
paper_insights.json
paper_workflows.json
aligned_vocabulary.json
```

## 输入来源优先级

历史自动修正时，按以下优先级读取和合并字段：

1. `capture/papers.jsonl`
   - 最可信的机器事实源。
   - 优先提供 arXiv ID、domain decision、全文路径、hash、categories。

2. `capture/domain_decisions.json`
   - 复用已有领域判定。
   - 不要对 capture 已判定论文再次调用 LLM 做领域判定。

3. 旧知识库输出 `papers.jsonl` / `all_papers.jsonl`
   - 提供已有摘要、作者、URL、source metadata。

4. 旧 `paper_insights.json`
   - 提供材料、方法、关键词、结论、亮点等结构化字段。

5. 旧 `paper_workflows.json`
   - 提供论证流程、主张、证据、开放问题。

6. 旧 `aligned_vocabulary.json` 或 `facet_vocabulary.json`
   - 提供术语索引，但其中的 `evidence_papers` 必须重写为规范 ID。

7. 旧 Daily Markdown 或 Daily JSON
   - 只作为补充来源，用于补 `summary_zh`、`summary_en`、daily 来源链接等。
   - 不应作为唯一事实源。

## 禁止行为

历史修正不应做这些事：

- 不重新访问 arXiv API。
- 不重新下载 PDF/HTML/TeX。
- 不为了补 metadata 大规模联网。
- 不用标题 slug 当长期主键。
- 不把 `2605.20653v1` 作为页面主键。
- 不用旧 Markdown front matter 的 `date` 伪造运行时间。
- 不把 `"Unknown"`、`"本批次暂无数据"` 这类展示文案写进机器字段，除非字段枚举明确允许，例如 `sample_form: "Unknown"`。
- 不让 `aligned_vocabulary[].evidence_papers` 出现 `title:...`。

## ID 规范

### arXiv 论文

任意输入：

```text
2605.20653v1
https://arxiv.org/abs/2605.20653v1
http://arxiv.org/abs/2605.20653v1
https://arxiv.org/pdf/2605.20653v1
```

必须规范成：

```json
{
  "paper_id": "2605.20653",
  "source_ids": {
    "arxiv": "2605.20653v1",
    "doi": null,
    "zotero_key": null
  }
}
```

规则：

- `paper_id` 不带版本号。
- `source_ids.arxiv` 保留带版本号 ID。
- 如果不同来源出现同一 arXiv ID 的多个版本，合并到同一个 `paper_id`。
- `source_ids.arxiv` 保留最高版本或最新记录中出现的版本。

### DOI 论文

如果没有 arXiv ID，但有 DOI：

```json
{
  "paper_id": "doi:10.1038/s41586-xxx",
  "source_ids": {
    "arxiv": null,
    "doi": "10.1038/s41586-xxx",
    "zotero_key": null
  }
}
```

### Zotero 论文

如果没有 arXiv ID 和 DOI，但有 Zotero key：

```json
{
  "paper_id": "zotero:ABCD1234",
  "source_ids": {
    "arxiv": null,
    "doi": null,
    "zotero_key": "ABCD1234"
  }
}
```

只有在完全没有可靠 ID 时，才允许临时保留旧 `title:...`，并必须在 `build_report.warnings` 里记录。

## 合并规则

对所有旧 records 建立 `legacy_id -> canonical_id` 映射。

优先级：

1. 能提取 arXiv ID：canonical ID 为无版本号 arXiv ID。
2. 无 arXiv、有 DOI：canonical ID 为 `doi:<doi>`。
3. 无 arXiv/DOI、有 Zotero key：canonical ID 为 `zotero:<key>`。
4. 仍无法识别：保留旧 ID，但报告 warning。

同一 canonical ID 多条记录合并时：

- `source_types` 合并去重。
- `source_files` 合并去重。
- `authors` 选最长非空数组。
- `affiliations` 合并去重。
- `abstract` 选最长非空文本。
- `summary_zh`、`summary_en` 优先选择非空；capture/daily 优先于 Markdown 反推。
- `full_text_path`、`pdf_path`、`text_sha256`、`pdf_sha256` 以 capture 为最高优先级，不要被 Markdown/Zotero 覆盖。
- `domain_decision` 以 capture 的判定为最高优先级，不要再次 LLM 判定覆盖。
- `source_metadata` 深合并，保留 `categories`、`primary_category`、`full_text_source`、`legacy_ids`。

## `papers.jsonl` 输出规范

每行一篇论文，建议结构：

```json
{
  "schema_version": "1.0",
  "paper_id": "2605.20653",
  "source_ids": {
    "arxiv": "2605.20653v1",
    "doi": null,
    "zotero_key": null
  },
  "title": "Pressure-induced superconductivity in epitaxially-stabilized Pr3Ni2O7 films",
  "title_plain": "Pressure-induced superconductivity in epitaxially-stabilized Pr3Ni2O7 films",
  "title_tex": "Pressure-induced superconductivity in epitaxially-stabilized Pr$_3$Ni$_2$O$_7$ films",
  "title_original": "Pressure-induced superconductivity in epitaxially-stabilized Pr$_3$Ni$_2$O$_7$ films",
  "abstract": "The discovery of...",
  "authors": [
    "Motoki Osada",
    "Chieko Terakura"
  ],
  "affiliations": [],
  "url": "https://arxiv.org/abs/2605.20653v1",
  "pdf_url": "https://arxiv.org/pdf/2605.20653v1",
  "published_at": "2026-05-21T00:00:00Z",
  "source_types": [
    "capture"
  ],
  "source_files": [],
  "source_metadata": {
    "id_type": "arxiv",
    "primary_category": "cond-mat.supr-con",
    "categories": [
      "cond-mat.supr-con"
    ],
    "legacy_ids": [
      "2605.20653v1"
    ]
  },
  "score": 5.5961,
  "summary_zh": "本研究...",
  "summary_en": "This study...",
  "summary_status": {
    "zh": "machine_generated",
    "en": "machine_generated"
  },
  "full_text_path": "data/capture/fulltext/arxiv/2605.20653.txt",
  "pdf_path": "data/capture/fulltext/arxiv/2605.20653.pdf",
  "text_sha256": "...",
  "pdf_sha256": "...",
  "domain_decision": {
    "decision": "accept",
    "confidence": 0.95,
    "reason": "Directly addresses nickelate superconductivity."
  }
}
```

兼容要求：

- 保留旧字段 `title`，但让它等于 `title_plain`。
- 前端优先读 `title_plain`。
- 缺失的机器字段用 `null` 或 `[]`，不要写展示文案。

## 标题清洗规则

输出 `title_plain` 时必须清理 HTML/XML/MathML。

最低要求：

- HTML entity unescape。
- 删除 XML/HTML 标签。
- 删除 MathML 标签。
- 把连续空白压成一个空格。
- `title_plain` 不得包含：
  - `<math`
  - `</math>`
  - `xmlns`
  - `<mrow`
  - `<msub`

`title_tex` 可以保留轻量 TeX，例如：

```text
La$_3$Ni$_2$O$_7$
```

如果无法可靠把 MathML 转成纯文本，至少删除标签并在 `build_report.warnings` 里记录该 paper ID。

## Insights 修正规则

读取旧 `paper_insights.json`，对每条记录：

1. 用 `legacy_id -> canonical_id` 重写 `paper_id`。
2. 同 canonical ID 多条 insight 合并。
3. 数组字段合并去重。
4. 字符串枚举字段优先选择非空值。

输出字段：

```json
{
  "schema_version": "1.0",
  "paper_id": "2605.20653",
  "research_paradigm": "Experimental",
  "sample_form": "Thin Film",
  "materials": [],
  "material_mentions": [],
  "methods": [],
  "keywords": [],
  "highlights": [],
  "conclusions": []
}
```

推荐枚举：

- `research_paradigm`: `Experimental`、`Theoretical`、`Review`、`Mixed`、`Dataset`、`Other`
- `sample_form`: `Single Crystal`、`Thin Film`、`Polycrystal`、`Heterostructure`、`Powder`、`Model`、`Unknown`

如果旧值不在枚举里：

- 可以保留，但写入 `build_report.warnings`。
- 不要直接删除旧信息。

## Workflows 修正规则

读取旧 `paper_workflows.json`，对每条记录：

1. 用 `legacy_id -> canonical_id` 重写 `paper_id`。
2. 同 canonical ID 多条 workflow 合并。
3. `workflow`、`main_claims`、`open_questions` 合并去重。
4. 保留已有 `article_type`，没有则为 `null` 或 `"Unknown"`，按当前站点约定选择。

输出字段：

```json
{
  "schema_version": "1.0",
  "paper_id": "2605.20653",
  "article_type": "Research Article",
  "workflow": [],
  "main_claims": [],
  "open_questions": []
}
```

## Vocabulary 修正规则

读取旧 `aligned_vocabulary.json` 或 `facet_vocabulary.json`。

对每个 entry：

1. 保留 `facet_type`、`canonical_name`、`aliases`。
2. 用 `legacy_id -> canonical_id` 重写所有 `evidence_papers`。
3. 删除无法映射且不存在于新 `papers.jsonl` 的证据 ID。
4. 重新计算 `frequency = len(evidence_papers)`。
5. 合并同 `facet_type + canonical_name` 的重复 entry。

输出文件必须叫：

```text
aligned_vocabulary.json
```

示例：

```json
{
  "schema_version": "1.0",
  "facet_type": "material",
  "canonical_name": "La3Ni2O7",
  "aliases": [
    "La$_3$Ni$_2$O$_7$"
  ],
  "frequency": 2,
  "evidence_papers": [
    "2605.20653",
    "2604.21899"
  ]
}
```

硬性要求：

- `evidence_papers` 不得出现 `title:...`，除非同一个 `title:...` 也存在于新 `papers.jsonl` 且没有更可靠 ID。
- `evidence_papers` 中每个 ID 都必须能在新 `papers.jsonl` 找到。

## Domain Decision 修正规则

如果 capture 或旧 `domain_decisions.json` 已有判定：

- 复用，不重新调用 LLM。
- 用 canonical ID 重写 `paper_id`。
- 同 canonical ID 多条判定时，优先级：
  1. capture accepted decision
  2. old accepted decision
  3. uncertain
  4. reject

如果某论文没有 domain decision：

- 历史修正不要为了补这个字段批量调用 LLM。
- 可以写空对象 `{}` 或 `null`，并在 `source_metadata.domain_decision_status = "missing"` 标记。

## 输出报告

必须写 `build_report.json`，至少包含：

```json
{
  "schema_version": "1.0",
  "mode": "historical_fix",
  "processed_at": "2026-05-23T22:00:00+08:00",
  "input_files": [],
  "output_files": [],
  "paper_count_before": 120,
  "paper_count_after": 95,
  "merged_records": 25,
  "id_rewrites": {
    "2605.20653v1": "2605.20653",
    "title:pressure induced superconductivity": "2605.20653"
  },
  "warnings": [],
  "errors": []
}
```

## 推荐实现步骤

1. 读取所有旧输入文件。
2. 从所有 paper-like records 中提取候选 ID。
3. 建立 `legacy_id -> canonical_id` 映射。
4. 合并 paper records，输出新 `papers.jsonl`。
5. 重写并合并 `domain_decisions.json`。
6. 重写并合并 `paper_insights.json`。
7. 重写并合并 `paper_workflows.json`。
8. 重写并合并 `aligned_vocabulary.json`。
9. 生成 `build_report.json`。
10. 运行 JSON 解析与一致性校验。

## 校验要求

自动修正完成后至少检查：

1. 所有 JSON / JSONL 可被解析。
2. 所有 arXiv `paper_id` 不带版本号。
3. `source_ids.arxiv` 保留带版本号 ID。
4. `title_plain` 不含 `<math`、`</math>`、`xmlns`。
5. `aligned_vocabulary[].evidence_papers` 全部能在 `papers.jsonl` 找到。
6. `aligned_vocabulary[].evidence_papers` 不出现可替换的 `title:...`。
7. `paper_insights[].paper_id` 全部能在 `papers.jsonl` 找到。
8. `paper_workflows[].paper_id` 全部能在 `papers.jsonl` 找到。
9. capture 的 `full_text_path/pdf_path/hash/domain_decision` 没有被 Markdown/Zotero 覆盖。
10. 缺失机器字段使用 `null` 或 `[]`。

## 建议测试

至少增加这些单元测试：

1. `2605.00001v1` 被规范成 `2605.00001`。
2. `https://arxiv.org/abs/2605.00001v2` 被规范成 `paper_id=2605.00001`、`source_ids.arxiv=2605.00001v2`。
3. capture + Markdown + Zotero 同一 arXiv ID 合并为一条记录。
4. capture 的全文路径和 domain decision 不被覆盖。
5. `title:...` insight 被重写到 canonical arXiv ID。
6. `aligned_vocabulary.evidence_papers` 中旧 ID 全部重写。
7. `title_plain` 清除 MathML/XML。
8. 无可靠 ID 的记录保留并写入 warning。

推荐命令：

```bash
uv run pytest
```

如果前端项目需要验证：

```bash
npm run knowledge:demo
npm run build
```

## 最终验收清单

- [ ] `papers.jsonl` 每行都有 `schema_version`、`paper_id`、`title_plain`、`source_ids`。
- [ ] arXiv `paper_id` 全部不带版本号。
- [ ] `source_ids.arxiv` 保留版本号。
- [ ] `paper_insights.json` 使用规范 `paper_id`。
- [ ] `paper_workflows.json` 使用规范 `paper_id`。
- [ ] `aligned_vocabulary.json` 存在并使用规范 `paper_id`。
- [ ] `aligned_vocabulary[].evidence_papers` 不出现可替换的 `title:...`。
- [ ] `title_plain` 不含 HTML/XML/MathML。
- [ ] capture 的全文路径、hash、domain decision 得到保留。
- [ ] `build_report.json` 记录 ID rewrite、merge、warning。
- [ ] 不重新抓 arXiv。
- [ ] 测试通过。

