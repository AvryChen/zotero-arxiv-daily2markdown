# Zotero-ArXiv-Daily2Markdown

[English](./README.md) | [中文](./README_zh.md)

> **A powerful, bilingual arXiv summarization tool that integrates Zotero and exports directly to Hugo.**
> **一个强大的双语 arXiv 论文总结工具，集成 Zotero 并直接导出为 Hugo 博客格式。**

---

## 🌟 Introduction / 简介

**Zotero-ArXiv-Daily2Markdown** is an enhanced version of the original [zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily). It is designed for researchers who want to keep track of the latest papers in their field, generate high-quality bilingual summaries using LLMs (like DeepSeek or GPT), and automatically publish them to a Hugo-based personal website.

**Zotero-ArXiv-Daily2Markdown** 是基于 [zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) 开发的增强版本。它专为科研人员设计，旨在跟踪特定领域的最新论文，利用大模型（如 DeepSeek 或 GPT）生成高质量的中英双语摘要，并自动发布到基于 Hugo 的个人网站上。

### Key Features / 核心特性
-   **Bilingual TL;DR / 双语摘要**: Automatically generates Chinese and English summaries for each paper. / 自动为每篇论文生成中英文双语 TL;DR。
-   **Hugo Export / Hugo 自动化集成**: Direct export to Hugo-compatible Markdown files with dual-language support (`zh/en`). / 直接导出适配 Hugo 的 Markdown 文件，支持双语目录架构。
-   **Smart Reranking / 智能排序**: Scores papers based on relevance to your local Zotero corpus using embedding models. / 使用 Embedding 模型根据与您 Zotero 本地库的关联度对论文进行打分排序。
-   **Robust Text Extraction / 稳健的文本提取**: Supports HTML (Trafilatura), PDF (PyMuPDF4LLM), and TeX source parsing with hard-timeout protection. / 支持 HTML、PDF 及 TeX 源码解析，具备多进程硬超时保护。
-   **Advanced Rate Limiting / 完善的频率限制**: Specialized logic to handle arXiv's strict API rate limits (HTTP 429). / 专门优化的 arXiv API 抓取逻辑，规避流量限制。

---

## 🚀 Quick Start / 快速开始

### 1. Installation / 安装
This project uses `uv` for lightning-fast dependency management. / 本项目使用 `uv` 进行依赖管理。

```bash
git clone https://github.com/your-username/zotero-arxiv-daily2markdown.git
cd zotero-arxiv-daily2markdown
uv sync
```

### 2. Configuration / 配置
Copy the example environment file and fill in your keys. / 复制环境变量模板并填写您的 Key。

```bash
cp .env.example .env
```

Key settings in `.env`:
-   `ZOTERO_ID` & `ZOTERO_KEY`: Your Zotero user ID and API key.
-   `OPENAI_API_KEY` & `OPENAI_API_BASE`: LLM provider (e.g., DeepSeek, OpenAI).
-   `HUGO_OUTPUT_DIR`: Path to your Hugo site's `content` folder.

### 3. Usage / 使用
Run for the latest papers: / 抓取最新论文：
```bash
uv run python src/zotero_arxiv_daily/main.py
```

Run for a specific date: / 抓取指定日期的论文：
```bash
uv run python src/zotero_arxiv_daily/main.py executor.target_date="2026-05-01"
```

---

## 🛠 Customization / 自定义

You can customize the AI's role and research topic in `config/base.yaml`:
您可以在 `config/base.yaml` 中自定义 AI 的角色和研究主题：

```yaml
prompt:
  topic: "your research field"
  role: "professional academic editor"
  overview_zh: "Custom Chinese prompt template..."
```

---

## 📄 License / 开源协议

This project is licensed under the **AGPL-3.0 License**. It is a fork of the original project by [TideDra](https://github.com/TideDra/zotero-arxiv-daily).

本项目基于 **AGPL-3.0 协议** 开源。本项目是 [TideDra](https://github.com/TideDra/zotero-arxiv-daily) 原始项目的二次开发版本。

---

## 🙏 Acknowledgments / 致谢
Thanks to the original author of `zotero-arxiv-daily` for the great foundation. / 感谢原作者提供的优秀基础架构。
