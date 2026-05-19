# Zotero-ArXiv-Daily2Markdown (中文文档)

> **一个强大的双语 arXiv 论文总结工具，集成 Zotero 并直接导出为 Hugo 博客格式。**

本项目是 [zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) 的增强版，旨在为科研人员提供自动化的论文跟踪、双语总结及个人学术博客同步功能。

---

## ✨ 核心改进

相比于原版，本项目进行了以下重大升级：

1.  **全面双语支持**：
    *   自动生成中英文双语 TL;DR（摘要）。
    *   AI 自动撰写每日研究亮点概览（Overview），并提供双语版本。
2.  **Hugo 博客自动化集成**：
    *   直接导出适配 Hugo 博客架构的 Markdown 文件。
    *   支持双语内容存放（`zh/posts/` 和 `en/posts/`）。
    *   包含完整的 Front Matter（标题、日期、标签、评分等）。
3.  **更可靠的 arXiv 抓取校验**：
    *   对 arXiv API 的 `totalResults`、分页结果数量和元数据 ID 覆盖做完整性审计。
    *   可选接入 dailyarxiv 交叉验证，用于发现日期窗口或抓取数量异常。
4.  **更强大的文本抓取引擎**：
    *   **三级降级提取**：HTML -> PDF (PyMuPDF4LLM) -> TeX 源码。
    *   **硬超时保护**：使用多进程隔离提取任务，防止复杂 PDF 导致程序挂起。
    *   **频率限制优化**：针对 arXiv API 的 HTTP 429 限制，使用失败重试与退避逻辑。
5.  **配置通用化**：
    *   支持在配置文件中自定义研究领域（Topic）和 AI 提示词（Prompt）。
    *   自动处理环境变量中的布尔值解析。

---

## 🚀 快速开始

### 1. 安装
使用 `uv` 管理依赖：
```bash
git clone https://github.com/your-username/zotero-arxiv-daily2markdown.git
cd zotero-arxiv-daily2markdown
uv sync
```

### 2. 配置
参考 `.env.example` 创建您的 `.env` 文件，填写 Zotero 和 LLM 的 API Key。

### 3. 运行
```bash
# 抓取最新论文
uv run python src/zotero_arxiv_daily2markdown/main.py

# 抓取特定日期的论文
uv run python src/zotero_arxiv_daily2markdown/main.py executor.target_date="2026-05-01"

# 抓取特定日期并启用 dailyarxiv 交叉验证
uv run python src/zotero_arxiv_daily2markdown/main.py executor.target_date="2026-05-01" executor.cross_validate_dailyarxiv=true
```

---

## 📄 开源协议

本项目遵循 **AGPL-3.0** 开源协议。它是对 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) 项目的二次开发版本，请在使用时遵守相关协议并尊重原作者。
