# 规格与工单

每个业务目标维护一份 Spec。同一个版本可以交接多个切片。本地工单引用对应版本和验收编号。

## 个人订阅额度看板 v0.1

规格已归档，9 张本地工单已交付。参见[已确认规格](archived/personal-quota-dashboard.md)、[工单及验收索引](work-items/README.md)。

## 目录

| 目录 | 用途 | 进入条件 |
| --- | --- | --- |
| `governance/` | [协作约定](governance/working-agreement.md)与[规格模板](governance/spec-template.md) | 长期使用的协作规则或模板 |
| `planned/` | 规划中的业务规格 | 已明确要讨论的业务目标 |
| `implement/` | 已确认、正在推进的业务规格 | 老板确认当前版本 |
| `archived/` | 已完成的业务规格与验收索引 | 所有相关切片已验收并合入 |
| `work-items/` | [本地工单与验收证据](work-items/README.md) | 已确认规格可拆成独立验收的切片 |

有业务规格时再创建相应目录，将同一文件随状态移动。历史工单用提交 SHA 和文件路径引用当时所依据的版本。
