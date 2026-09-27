# Contributing / 参与贡献

Thank you for helping improve StudyHub. This project is an early-stage, local-first learning application; small, focused pull requests are easiest to review.

感谢你帮助改进 StudyHub。本项目仍处于早期阶段，并坚持本地优先；范围清晰、改动集中的 Pull Request 最容易审核。

## Development setup / 开发环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
alembic upgrade head
python scripts\seed_demo.py
```

On macOS/Linux, activate with `source .venv/bin/activate` and use forward slashes.

在 macOS/Linux 上，请使用 `source .venv/bin/activate` 激活环境，并将路径改为正斜杠。

## Before opening a pull request / 提交 PR 前

```powershell
ruff check .
python scripts\check_i18n.py
pytest -m "not e2e" --cov --cov-report=term-missing
pytest -m e2e
```

- Add or update tests when behavior changes. / 行为变化时请增加或更新测试。
- Keep English and Simplified Chinese UI strings in sync. / 保持英文与简体中文界面文案同步。
- Document migrations and avoid destructive changes to local learning history. / 说明数据库迁移，避免破坏本地学习历史。
- Never include `.env`, API keys, databases, backups, uploads, or real course material. / 禁止提交环境文件、API 密钥、数据库、备份、上传文件或真实课程资料。
- Explain the user-visible result and any trade-offs in the PR description. / 在 PR 描述中说明用户可见结果和重要取舍。

## Issues and security / Issue 与安全问题

Use a GitHub issue for reproducible bugs and focused feature proposals. For unpatched vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of publishing exploit details.

可复现 Bug 和范围明确的功能建议请提交 GitHub Issue。尚未修复的漏洞请按照 [SECURITY.md](SECURITY.md) 私下报告，不要公开利用细节。
