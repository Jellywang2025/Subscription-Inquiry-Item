# 工单分诊标签

| 标签 | 触发条件 |
| --- | --- |
| `needs-triage` | 等待维护者判断范围和处理方式 |
| `needs-info` | 缺少规格、场景或可复现步骤 |
| `ready-for-agent` | 规格及必要前置已明确，可交给执行者 |
| `ready-for-human` | 需要老板或维护者作决定或完成保留给人的操作 |
| `wontfix` | 已决定暂不处理，在工单中记录原因 |

标签沿用 [Matt Pocock 技能的默认分诊角色](../../.agents/skills/setup-matt-pocock-skills/triage-labels.md)。本地工单把当前状态写在票头 `初始状态` 字段，DSH 按需改为已验收待合入、已合入等后续状态；GitHub Issues 上的票沿用同名标签。
