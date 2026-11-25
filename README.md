# Codex × DSH 项目协作

Codex 会话和老板确认需求、验收条件与关键用例，将 Spec 写入两端共用的仓库。Codex 从已确认的 Spec 拆票，DSH 负责实现、自测和交付，并调用 `codex-tester` 按业务切片验证实现前的红灯和实现后的结果。

- [协作入口](AGENTS.md)是两边开始工作的入口。
- [规格与工单](spec/README.md)包含规格、切片状态和验收证据入口。
- [已交付记录](spec/work-items/README.md)列出 v0.1 已完成的九项业务行为。
- [测试约定](tests/README.md)包含红灯证据、测试职责和本地全量检查。
- [文档索引](docs/README.md)指向工单约定、测试智能体调用规则和验收报告。

使用 Git Bash 从仓库根目录运行 `bash scripts/check-governance.sh`，检查协作入口和待提交改动的空白格式。[GitHub Actions](.github/workflows/ci.yml)配置了治理检查及 Windows 静态检查和全量测试，触发条件为提交进入 `main` 或提交面向 `main` 的 PR。

## 个人订阅额度看板

Windows 本机双击 `start-dashboard.bat` 打开看板；服务已在运行时再次双击，直接打开同一看板。命令行等价入口为 `python -m quota_dashboard [--port N]`（默认端口 8765）。

技术栈：Python 3.12+ 标准库（无运行时第三方依赖），页面为原生 HTML/CSS/JS。本地开发与检查命令（PowerShell，仓库根目录）：

```powershell
uv venv --python 3.12 .venv
uv sync --locked  # 按 uv.lock 安装 dev 依赖（pytest、pyright）

uv run --locked pyright
node --check quota_dashboard/static/app.js
uv run --locked pytest --basetemp=.tmp-run -p no:cacheprovider
```

在受限沙箱（如 `codex-tester`）中运行时，`.venv\Scripts\python.exe` 可能不可用，改用 uv 下载的裸解释器（按 uv 实际安装版本解析，不绑死补丁号）并借 `PYTHONPATH` 取用 `.venv` 中已装的依赖：

```powershell
$py = Get-ChildItem "$env:APPDATA\uv\python\cpython-3.12.*-windows-x86_64-none\python.exe" |
  Select-Object -First 1 -ExpandProperty FullName
$env:PYTHONPATH = "$PWD\.venv\Lib\site-packages"
& $py -m pyright
& $py -m pytest --basetemp=.tmp-run -p no:cacheprovider
```

浏览器契约用例需要 Chrome 和 Node.js 22+。契约用例位于 `tests/contract/`，从启动入口与本机服务验证 Spec 的 AC。实现侧用例位于 `tests/impl/`。
