# Codex × DSH 项目协作

Codex 会话和老板确认需求、验收条件与关键用例，将 Spec 写入两端共用的仓库。Codex 从已确认的 Spec 拆票，DSH 负责实现、自测和交付，并调用 `codex-tester` 按业务切片验证实现前的红灯和实现后的结果。

- [协作入口](AGENTS.md)是两边开始工作的入口。
- [规格与工单](spec/README.md)包含规格、切片状态和验收证据入口。
- [已交付记录](spec/work-items/README.md)列出 v0.1 已完成的九项业务行为。
- [测试约定](tests/README.md)包含红灯证据、测试职责和本地全量检查。
- [文档索引](docs/README.md)指向工单约定、测试智能体调用规则和验收报告。

本地、测试员与 CI 共用 Windows PowerShell 测试入口（仓库根目录）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test.ps1
# 受限环境系统临时目录不可写时，改用仓库内统一忽略目录
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test.ps1 -TempRoot .tmp
```

入口在系统临时目录（或 `-TempRoot`）下建立唯一运行子目录，把 `QUOTA_STATE_FILE` 和 Codex、DeepSeek、MiMo 的服务地址隔离到本机不可达端点，依次运行治理检查、`pyright`、`node --check` 和 `pytest`。失败时保留运行目录并打印日志路径与失败码；成功后只用 .NET 清理本次创建的目录。治理检查通过 `GIT_BASH` 调用 Git Bash，本机默认 `D:\Git\bin\bash.exe`，CI 使用 `C:\Program Files\Git\bin\bash.exe`。

`scripts/check-governance.sh` 保留既有 Git 差异空白检查，并检查关键文档本地链接、规格归档状态和仓库根目录临时项。[GitHub Actions](.github/workflows/ci.yml)的治理作业调用同一治理脚本，Windows 作业调用同一测试入口，触发条件为提交进入 `main` 或提交面向 `main` 的 PR。

## 个人订阅额度看板

Windows 本机双击 `start-dashboard.bat` 打开看板；服务已在运行时再次双击，直接打开同一看板。命令行等价入口为 `python -m quota_dashboard [--port N]`（默认端口 8765）。

技术栈：Python 3.12+ 标准库（无运行时第三方依赖），页面为原生 HTML/CSS/JS。本地开发先安装锁定的开发依赖，再从上面的统一入口执行检查：

```powershell
uv venv --python 3.12 .venv
uv sync --locked  # 按 uv.lock 安装 dev 依赖（pytest、pyright）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test.ps1
```

入口优先使用 `.venv\Scripts\python.exe`；受限沙箱（如 `codex-tester`）不允许执行该解释器时，改用它从 uv 下载的 Python 3.12 裸解释器（按实际安装版本解析，不绑死补丁号），并通过 `PYTHONPATH` 复用 `.venv\Lib\site-packages` 中已装的依赖。

浏览器契约用例需要 Chrome 和 Node.js 22+。契约用例位于 `tests/contract/`，从启动入口与本机服务验证 Spec 的 AC。实现侧用例位于 `tests/impl/`。
