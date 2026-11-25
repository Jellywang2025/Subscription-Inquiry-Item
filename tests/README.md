# 测试约定

Spec 写定验收条件和关键用例。DSH 调用独立的 [`codex-tester`](../docs/agents/dsh-codex-cli.md) 编写契约测试和验收，DSH 负责实现侧测试、自测和返修。

- 契约用例关联 `AC-xx`，从 Spec 约定的公开入口验证输出、错误和状态变化。入口相同时，复用参数化数据、共享夹具和运行器。人工复检按 Spec 执行。
- 每个就绪切片从实现前基线执行 `red`。记录本片 AC、基线 SHA、用例命令、退出码和实际结果。目标行为缺失对应 `RED_READY`，行为已有对应 `ALREADY_PASSES`，环境、入口或规格妨碍验证对应 `BLOCKED`。
- 实现完成后，`codex-tester` 在待验 SHA 的干净工作树用新会话执行 `verify`，独立复跑关键契约用例，记录 `PASS`、`FAIL` 或 `BLOCKED` 和实测证据。验收期间保持产品代码和契约测试原样。
- 在待合入提交上先跑静态检查，再跑全量测试。首片确定技术栈后接入实际命令，Python 项目采用 `pyright` → `pytest` 的顺序。具体门禁和复验规则见[协作约定](../spec/governance/working-agreement.md)。
