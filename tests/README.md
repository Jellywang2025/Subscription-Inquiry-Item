# 测试约定

Spec 写定验收条件和关键用例。DSH 调用独立的 [`codex-tester`](../docs/agents/dsh-codex-cli.md) 编写契约测试和验收，DSH 负责实现侧测试、自测和返修。

- 契约用例关联 `AC-xx`，从 Spec 约定的公开入口验证输出、错误和状态变化。入口相同时，复用参数化数据、共享夹具和运行器。人工复检按 Spec 执行。
- 每个就绪切片从实现前基线执行 `red`。记录本片 AC、基线 SHA、用例命令、退出码和实际结果。目标行为缺失对应 `RED_READY`，行为已有对应 `ALREADY_PASSES`，环境、入口或规格妨碍验证对应 `BLOCKED`。
- 实现完成后，`codex-tester` 在待验 SHA 的干净工作树用新会话执行 `verify`，独立复跑关键契约用例，记录 `PASS`、`FAIL` 或 `BLOCKED` 和实测证据。验收期间保持产品代码和契约测试原样。
- 测试员入口 `scripts/codex-tester.py` 的归档回归用例在 `tests/impl/test_codex_tester.py`，覆盖退出 0 的 `PASS`、退出 0 的 `BLOCKED`、非零退出带证据、缺失 token、启动失败与超时。
- 在待合入提交上先跑静态检查，再跑全量测试。首片确定技术栈后接入实际命令，Python 项目采用 `pyright` → `pytest` 的顺序。具体门禁和复验规则见[协作约定](../spec/governance/working-agreement.md)。

## 统一入口

本地、测试员与 CI 从仓库根目录执行同一个 Windows PowerShell 入口：

```powershell
pwsh -NoProfile -File scripts/test.ps1
# 受限环境系统临时目录不可写时，使用仓库内统一忽略目录
pwsh -NoProfile -File scripts/test.ps1 -TempRoot .tmp
```

入口在系统临时目录（或 `-TempRoot`）下创建唯一运行子目录，把 `QUOTA_STATE_FILE` 和 Codex、DeepSeek、MiMo 的服务地址隔离到本机不可达端点，按治理检查 → `pyright` → `node --check` → `pytest` 执行。失败时保留运行目录，打印日志路径和退出码；成功后只用 .NET 清理本次创建的目录。Windows Git Bash 从 `GIT_BASH` 解析，本机默认 `Git for Windows 自带的 bash.exe`，CI 使用 `C:\Program Files\Git\bin\bash.exe`。

`scripts/check-governance.sh` 的根目录临时项检查会拒绝 `.tmp-gov-red-*` 等根目录临时文件；`.tmp/` 是受限环境统一使用的忽略目录。
