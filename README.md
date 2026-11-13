# Academic Paper Downloader — 合规机构授权文献批量下载技能与 CLI

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-brightgreen.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-supported-blueviolet.svg)](https://docs.astral.sh/uv/)

面向科研人员与 AI Coding Agent（Antigravity、Claude Code、Codex、OpenCode 等）设计的**多出版社学术文献解析、本地库存核验与 Chrome CDP 受控批量下载工具**。

> [!IMPORTANT]
> **合规与使用声明（Compliance Notice）**
> 本工具**仅限用于个人合法学术研究**，严格依赖用户自身在本地浏览器中已合法登录的**机构/校园网订阅权限或开放获取（Open Access）资源**。
> 本工具通过 Chrome DevTools Protocol (CDP) 复用用户现有的已认证浏览器会话，**绝不**包含任何绕过 CAPTCHA 验证码、破解付费墙（Paywall）或规避 DRM 的逻辑。遇到人机验证或无订阅权限条目时，工具将自动暂停、保存断点状态（`missing_papers.json`）并提示用户人工处理。

---

## ✨ 核心特性

1. **多格式自适应解析与跨源去重 (`parse`)**
   - 原生支持 **Markdown 文献综述表/编号列表**、**纯 DOI/URL 文本列表**、**BibTeX (`.bib`)** 与 **RIS (`.ris`)**；
   - 按归一化 DOI、规范化 URL 及归一化题名三层去重，自动保留别名编号并智能识别所属出版社。
2. **本地文献库扫描与 `%PDF-` 真伪校验 (`scan`)**
   - 扫描本地 PDF 目录，按编号前缀或题名自动匹配已入库文献；
   - 严格校验文件头 `%PDF-` 魔数与最小文件体积，剔除伪装成 `.pdf` 的 HTML 登录页/拦截页，生成可断点续传的 `missing_papers.json`。
3. **多出版社 Chrome CDP 受控抓取 (`download`)**
   - 支持 **IEEE Xplore**、**ScienceDirect / Elsevier**、**IOPscience**、**Wiley / IET**、**ACS Publications**、**CNKI（知网）** 及通用 `citation_pdf_url` 适配器；
   - 内置随机化防频控延时（默认 `5.0–10.0s`）、逐篇即时落盘状态、人机验证暂停提示（Exit Code `2`）与限流冷却恢复（Exit Code `3`）。

---

## 📂 目录结构

```text
academic-paper-downloader/
├── plugin.json                         # Agent 插件元信息
├── pyproject.toml                      # uv / pytest 项目配置
├── README.md                           # 项目说明文档
├── DEPLOY.md                           # 多 Agent 部署与 Chrome CDP 启动指南
├── LICENSE                             # Apache 2.0 开源协议
├── rules/
│   └── AGENTS.md                       # 合规与工作流行为准则
├── examples/
│   ├── sample_review.md                # 示例 Markdown 文献清单（占位 DOI）
│   ├── sample_dois.txt                 # 示例纯 DOI/URL 列表
│   └── sample_refs.bib                 # 示例 BibTeX 导出
├── skills/
│   └── academic-paper-downloader/
│       ├── SKILL.md                    # Agent 技能主指令
│       ├── references/
│       │   └── publishers.md           # 各出版社提取策略与防频控参考
│       └── scripts/
│           ├── paper_downloader.py     # 主 CLI（parse/scan/check-cdp/download/status）
│           ├── parsers.py              # 多格式解析与去重模块
│           └── publishers.py           # 多出版社 CDP 适配器与 %PDF- 校验
└── tests/
    ├── test_parsers.py                 # 解析与去重单元测试
    ├── test_scanner_and_state.py       # 本地扫描、%PDF- 校验与断点状态测试
    └── test_publishers_and_cli.py      # 出版社路由、拦截检测与 CLI 冒烟测试
```

---

## 🚀 快速开始

### 1. 扫描本地文献库并生成待下载清单

`parse`、`scan`、`status`、`check-cdp` 仅依赖 Python 标准库：

```bash
uv run --no-project python skills/academic-paper-downloader/scripts/paper_downloader.py scan \
  --input examples/sample_review.md \
  --pdf-dir ./papers \
  --state missing_papers.json
```

### 2. 启动带 CDP 调试端口的 Chrome 并登录机构权限

使用独立的 `--user-data-dir` 启动 Chrome（避免与日常浏览器进程冲突），并在打开的窗口中登录您的校园网/机构图书馆门户：

```powershell
# Windows (PowerShell)
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:USERPROFILE\chrome-cdp-profile"
```

校验端口连通性：

```bash
uv run --no-project python skills/academic-paper-downloader/scripts/paper_downloader.py check-cdp \
  --cdp-url http://127.0.0.1:9222
```

### 3. 执行分批受控下载

```bash
uv run --with playwright python skills/academic-paper-downloader/scripts/paper_downloader.py download \
  --state missing_papers.json \
  --pdf-dir ./papers \
  --delay-min 5.0 \
  --delay-max 10.0 \
  --limit 20
```

随时查看汇总进度：

```bash
uv run --no-project python skills/academic-paper-downloader/scripts/paper_downloader.py status \
  --state missing_papers.json
```

更多参数、多平台部署与出版社细节请参见 [DEPLOY.md](DEPLOY.md) 与 [skills/academic-paper-downloader/references/publishers.md](skills/academic-paper-downloader/references/publishers.md)。

---

## 🧪 运行测试

```bash
uv run --extra dev pytest
```

---

## 📄 开源许可

本项目遵循 [Apache-2.0 License](LICENSE)。
