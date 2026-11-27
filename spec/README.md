# 规格与工单

每个业务目标维护一份 Spec。同一个版本可以交接多个切片。本地工单引用对应版本和验收编号。

## 个人订阅管理 0.3.0

当前版本在个人订阅管理中加入 Google AI（Google One）。每张 Google 账号卡分别呈现 Gemini Apps 用量窗口、Antigravity 模型组额度和 Google One AI 积分余额。[统一 Spec](implement/subscription-management.md)沿用 AC-01 至 AC-09，并用 AC-10 至 AC-12 验证三项额度的来源与单位、逐项刷新与恢复、账号隔离和实号后台刷新。整版交付时，至少一项须在关页后成功自动更新，并在目标服务器用同一账号与 Google 官方结果核对。

## 个人订阅管理 0.2.0

需求、视觉及工单粒度已确认。提交 `934c183` 固定了本版的业务行为、八张核准基准图和 AC-01 至 AC-09。[0.2.0 统一 Spec](https://github.com/Jellywang2025/Subscription-Inquiry-Item/blob/934c18379055de0a75c04a194f6d723381629aee/spec/implement/subscription-management.md)

以下为 `0.2.0` 本地实施工单，初始仅 01 就绪。前置票按固定候选 SHA 独立验收通过后，DSH 可领取依赖票。

| 本地工单 | 直接依赖 |
| --- | --- |
| [01 管理员登录并进入空总览](../.scratch/subscription-management/issues/01-admin-entry.md) | 无 |
| [02 管理第一个 DeepSeek 账号并查看余额](../.scratch/subscription-management/issues/02-deepseek-account.md) | 01 |
| [03 管理 MiMo SK 并核对可动用余额](../.scratch/subscription-management/issues/03-mimo-account.md) | 02 |
| [04 连接一个 Codex 账号并查看额度](../.scratch/subscription-management/issues/04-codex-device-login.md) | 01 |
| [05 按需添加第二个 Codex 账号并隔离连接](../.scratch/subscription-management/issues/05-additional-codex-account.md) | 04 |
| [06 立即刷新与单账号查询失败恢复](../.scratch/subscription-management/issues/06-refresh-recovery.md) | 02 |
| [07 搜索自定义名称并恢复筛选书签](../.scratch/subscription-management/issues/07-search-bookmarks.md) | 06 |
| [08 关页后台刷新、修改间隔与重启恢复](../.scratch/subscription-management/issues/08-background-refresh.md) | 06 |

各票在交付相应页面时验证 AC-09。四个场景齐备后，以核准截图中的示例数据统一对照横屏 1920 × 1080、竖屏 1080 × 1920 的八张图，再检查 390 × 844 单列、页面横向滚动和缩放时保留输入。上线实号及后台刷新情况在目标服务器复检，按实际环境记录证据。

## 个人订阅额度看板 v0.1

规格已归档，9 张本地工单已交付。参见[已确认规格](archived/personal-quota-dashboard.md)、[工单及验收索引](work-items/README.md)。

## 当前协作试点

[协作试点规范](implement/collaboration-pilot.md)覆盖共享 PR 的测试入口与 CLI 留档。进度与结果见 [试点 PR](https://github.com/Jellywang2025/Subscription-Inquiry-Item/pull/3)。

## 目录

| 目录 | 用途 | 进入条件 |
| --- | --- | --- |
| `governance/` | [协作约定](governance/working-agreement.md)与[规格模板](governance/spec-template.md) | 长期使用的协作规则或模板 |
| `planned/` | 规划中的业务规格 | 已明确要讨论的业务目标 |
| `implement/` | 已确认、正在推进的业务规格 | 老板确认当前版本 |
| `archived/` | 已完成的业务规格与验收索引 | 所有相关切片已验收并合入 |
| `work-items/` | [本地工单与验收证据](work-items/README.md) | 已确认规格可拆成独立验收的切片 |

有业务规格时再创建相应目录，将同一文件随状态移动。历史工单用提交 SHA 和文件路径引用当时所依据的版本。
