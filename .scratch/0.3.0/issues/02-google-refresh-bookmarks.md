# 02 · 逐项刷新、失败恢复与书签重开

- 产品版本 `0.3.0`
- 规格基线 [个人订阅管理系统统一 Spec](../../../spec/implement/subscription-management.md)，提交 `b2e203199f671ca1e83c9bece7248ad6f714b0a3`
- 验收编号 AC-11（部分成功、真实零值与恢复），复用 AC-01、AC-03、AC-04、AC-05（改名保留连接）和 AC-08（立即刷新）
- 直接依赖 [01 添加一个 Google 账号并查看三项额度](01-google-account-overview.md) 的固定候选 SHA 独立验收通过
- 初始状态 等待前置票 01 验收
- 交付状态 `verified-for-merge`（2026-11-28 固定候选 `3144f9d` 独立验收 PASS，前置 01 已由本票覆盖）

## 可观察结果与范围

管理员在总览点击「立即刷新」后，可分别看清 Google 账号三项额度的数值、时间以及本次刷新结果。再次打开总览或配置管理的收藏链接，仍以原平台和搜索条件查看最新可用结果。

## 验证入口

从浏览器打开「订阅总览」，让可控来源先返回空回执、真实零值，再返回一次成功、一轮部分失败和一次恢复。逐轮点击「立即刷新」，对照每项的数值、成功时间和提示。在「订阅总览」「配置管理」分别按 Google 平台和账号名筛选，收藏网址，刷新数据或改名后重开，另测登录过期。

## 通过条件

- [x] 全部订阅为空时引导添加首条；有账号但条件无匹配时提示清除条件。卡片内首次查询无值时给出原因；仅能人工查询的项提供官方入口和步骤，成功时间留空。官方明确返回零时显示对应单位的零值（如「0 积分」）。
- [x] 同轮某项刷新失败，该项保留上次值和原成功时间，注明失败原因。其他项正常更新；数值恰好未变的成功项仍有新的成功时间。故障恢复后，该项更新时间和值并清除失败提示。
- [x] 在两个页面重开书签，恢复原页面、Google 平台和账号搜索词，显示刷新后的最新可用结果。重新登录后返回原视图；账号改名后原搜索无匹配，清除条件可见新名称，仍能使用原连接刷新该账号。直接打开首页可见全部订阅。

## 交付回执

- **红灯**：基线 `bd7d2f5`，`RED_READY`（2 项预期失败：单项刷新失败时未就地显示原因；3 项通过：首次空回执、官方零值、书签场景）。契约测试 `tests/contract/test_google_refresh_bookmarks.py`（5 项用例）与夹具扩展 `google_source.py::set_cli_replies`（逐轮切换空回执/零值/单项失败/恢复）由独立测试官交付，交接 SHA `841dc1f`。
- **实现候选 SHA**：`3144f9d`（`841dc1f` → `4b14db8` 实现 → `3144f9d` OCR 处置）。
- **实现要点**：`app.js` 逐项失败提示（`googleItemFailure`：失败项就地显示原因、保留旧值与原成功时间，恢复后清除；共享 `failureNote` 文案 helper）与 `styles.css` 告警色（`.metric small.issue`）。逐项保留旧值/成功时间/恢复逻辑已在 01 票 `google_items` 状态结构中就位，本票补齐展示层并经契约固化。
- **自测**：统一入口 `scripts/test.ps1 -TempRoot .tmp`（`4b14db8`）：209 passed、36 skipped、1 failed（既有 flaky `test_ac08_restart_recovers...[changed]`，DeepSeek 重启时间竞争，单独复跑通过，与本票无关）。
- **OCR 覆盖及处置**：首扫 `841dc1f→4b14db8` 1 条 medium（`.issue` class 无样式规则、文案与账号级提示重复）已处置；复扫 `4b14db8→3144f9d` 零发现。工具 open-code-review v1.14.4 + deepseek-flash，报告留存本机 `ocr-reports/02-google-*.json`。
- **CI**：`3144f9d`（run 46092976136）governance、windows-tests 均通过（210 passed、36 skipped）。
- **独立验收**：固定候选 `3144f9d` 在干净工作树（`.agents/worktrees/verify-02`，workspace-write 沙箱）以独立 Codex CLI 会话 verify，结论 **PASS**（实测 HEAD 一致；AC-11 五项契约逐项复跑：空回执原因、官方 0 积分含成功时间、单项超时保留旧值/原时间并显示原因、同轮成功项新时间、恢复清除提示、双页书签/重登录/改名清除/沿用原授权；统一入口全量 210 passed；验收结束跟踪文件无差异）。回执留档本机 codex-tester receipts（run `…-verify-273168e6…`）。
- **遗留到后续票**：双账号错配、重新授权/删除隔离、后台刷新与实号关页对照归 [03](03-google-isolation-background-refresh.md)。
