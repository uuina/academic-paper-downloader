# Academic Paper Downloader 部署与运行指南

本指南介绍如何将 `academic-paper-downloader` 集成到主流 AI Coding Agent，以及如何安全启动用于合法机构订阅访问的 Chrome CDP 调试会话。

---

## 1. 环境准备

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)**（推荐用于隔离执行 `playwright` 依赖）
- **Google Chrome / Chromium**

```bash
uv --version
```

---

## 2. 安装为 Agent Skill / Plugin

将本仓库克隆或复制到对应 Agent 的技能目录：

| Agent CLI | 项目级路径 | 全局路径 |
|---|---|---|
| **Antigravity** | `.agents/skills/academic-paper-downloader/` | `~/.gemini/config/skills/academic-paper-downloader/` |
| **Claude Code** | `.claude/skills/academic-paper-downloader/` | `~/.claude/skills/academic-paper-downloader/` |
| **Codex** | `.agents/skills/academic-paper-downloader/` | `~/.agents/skills/academic-paper-downloader/` |
| **Gemini CLI** | `.gemini/skills/academic-paper-downloader/` | `~/.gemini/skills/academic-paper-downloader/` |
| **OpenCode** | `.opencode/skills/academic-paper-downloader/` | `~/.config/opencode/skills/academic-paper-downloader/` |

如需作为完整插件加载（含 `plugin.json` 与 `rules/AGENTS.md`），可直接克隆整个仓库到插件目录并引用 `skills/academic-paper-downloader/`。

---

## 3. 启动 Chrome CDP 会话

> **重要提示**：必须指定一个独立的 `--user-data-dir`，否则若系统中已存在常规 Chrome 窗口，Chrome 会直接复用旧进程而忽略 `--remote-debugging-port=9222`。

### Windows (PowerShell)

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:USERPROFILE\chrome-cdp-profile"
```

### macOS

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/chrome-cdp-profile"
```

### Linux

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/chrome-cdp-profile"
```

启动后：
1. 在该 Chrome 窗口中连接校园网/机构 VPN 或登录机构统一认证门户；
2. 运行 `check-cdp` 验证端口已就绪：
   ```bash
   uv run --no-project python skills/academic-paper-downloader/scripts/paper_downloader.py check-cdp
   ```

---

## 4. 断点续传与防频控最佳实践

1. **先 `scan` 再 `download`**：始终先执行 `scan` 校验本地已有 PDF 的 `%PDF-` 文件头，避免重复请求。
2. **分出版社小批量执行**：通过 `--publishers ieee --limit 15` 控制单批数量，并保持 `--delay-min 5.0 --delay-max 10.0`（或针对严格风控出版社设为 `8.0–15.0`）。
3. **遇到验证码（Exit Code 2）**：立即在当前 Chrome 窗口手动完成人机验证，随后加上 `--retry-failed` 重新执行同一命令即可从断点继续。
4. **遇到频次限制（Exit Code 3）**：等待数小时或次日冷却后，加上 `--retry-failed` 恢复下载。
