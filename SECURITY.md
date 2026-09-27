# Security / 安全说明

## Supported use / 支持范围

StudyHub is a single-user, local-first application. Run it on `127.0.0.1` and do not expose the
Flask development server directly to the public internet.

StudyHub 是单用户、本地优先的应用。请仅在 `127.0.0.1` 上运行，不要把 Flask 开发服务器直接暴露到公网。

## Reporting a vulnerability / 报告漏洞

Please avoid opening a public issue for an unpatched vulnerability. Contact the repository owner
privately at [songziyuan929@163.com](mailto:songziyuan929@163.com) with reproduction steps, impact,
and a suggested mitigation when possible.

对于尚未修复的漏洞，请不要直接创建公开 Issue。请通过
[songziyuan929@163.com](mailto:songziyuan929@163.com) 私下联系仓库所有者，并尽可能提供复现步骤、
影响范围和缓解建议。

## Secrets and personal data / 密钥与个人数据

Never commit `.env`, `instance/`, uploaded course material, database backups, or generated local
secret-key files. If a credential is exposed, revoke and rotate it immediately.

禁止提交 `.env`、`instance/`、上传的课程资料、数据库备份或本地生成的密钥文件。如果凭据发生泄露，请立即吊销并轮换。
