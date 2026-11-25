# 项目协作入口

老板确认需求、公开行为和验收口径。Codex 负责需求澄清、规格及拆票，DSH 领取就绪工单，负责实现、诊断、修复和自测，调用 OCR 及独立 Codex CLI 验收。需求或验收口径有变时交回 Codex。

- [协作约定](spec/governance/working-agreement.md)定义交接和交付。
- [工单约定](docs/agents/issue-tracker.md)和[分诊标签](docs/agents/triage-labels.md)定义状态与交付入口。
- [技能索引](.agents/skills/README.md)指向项目 implement 与 implement-spec。必须显式读取本检出的文件。
- [测试约定](tests/README.md)、[测试员调用](docs/agents/dsh-codex-cli.md)和[OCR 审查](docs/agents/open-code-review.md)提供验收入口。
