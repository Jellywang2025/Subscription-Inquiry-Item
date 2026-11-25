# 工程技能版本

Codex 和 DSH 按任务读取本目录的 `SKILL.md` 及其引用文件。上游技能和许可固定在 [mattpocock/skills 的同一提交](https://github.com/mattpocock/skills/tree/abeeaf95660ba9e8b7c753eb38f441ca707a8823)。本仓库的 [implement](implement/SKILL.md) 和 [implement-spec](implement-spec/SKILL.md) 是接入 OCR 与 codex-tester 的项目定制入口，项目协作细节以 [仓库规则](../../spec/governance/working-agreement.md) 为准。

| 环节 | 执行者 | 技能 |
| --- | --- | --- |
| 初始化 | Codex | [setup-matt-pocock-skills](setup-matt-pocock-skills/SKILL.md) |
| 需求、术语与规格 | Codex | [grill-with-docs](grill-with-docs/SKILL.md)、[grilling](grilling/SKILL.md)、[domain-modeling](domain-modeling/SKILL.md)、[to-spec](to-spec/SKILL.md) |
| 拆票 | Codex | [to-tickets](to-tickets/SKILL.md) |
| 实现与诊断 | DSH | [implement](implement/SKILL.md)、[implement-spec](implement-spec/SKILL.md)、[diagnosing-bugs](diagnosing-bugs/SKILL.md) |
| 实现侧测试与自测 | DSH | [tdd](tdd/SKILL.md) |
| 契约测试、红灯与独立验收 | DSH 调用 `codex-tester` | [测试智能体调用约定](../../docs/agents/dsh-codex-cli.md) |
| 工程审查 | 按项目协作约定 | [code-review](code-review/SKILL.md) |
| 工单分诊 | DSH 编排 | [triage](triage/SKILL.md) |

`to-spec` 将确认稿写入仓库的 Spec，工单引用 Spec 的路径、版本和提交 SHA。`to-tickets` 依据已确认 Spec 自主拆分业务切片，粒度和依赖由 Codex 与需求方核对。影响业务或公开契约的歧义回到 Codex 会话，工单位置和发布权限按[项目工单约定](../../docs/agents/issue-tracker.md)执行。

`grill-with-docs` 引用的 `grilling` 和 `domain-modeling` 都随仓库提供。需要升级时，两边在同一变更中更新技能原文、来源和[上游许可](LICENSE.mattpocock)。
