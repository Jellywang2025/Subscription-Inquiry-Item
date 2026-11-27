# 01 · 添加一个 Google 账号并查看三项额度

- 产品版本 `0.3.0`
- 规格基线 [个人订阅管理系统统一 Spec](../../../spec/implement/subscription-management.md)，提交 `b2e203199f671ca1e83c9bece7248ad6f714b0a3`
- 验收编号 AC-10（单账号三项额度），复用 AC-02、AC-05（配置回读）和 AC-09
- 直接依赖 无
- 初始状态 `ready-for-agent`（拆票已确认，DSH 待开工）
- 交付状态 `verified-for-merge`（2026-11-28 固定候选 `4ba22ba` 独立验收 PASS）

## 可观察结果与范围

管理员通过「添加订阅」为一个自定义名称的 Google AI（Google One）账号完成 Google 官方授权。登录后的全部订阅总览出现该账号的一张卡。家庭共享账号同样归看板管理员负责。卡片直接显示三项额度，展开可查看套餐、费用和续费信息。

## 验证入口

独立测试员从网页「添加订阅 → Google AI → 官方授权 → 订阅总览」操作，以可控身份及额度回执核对卡片，再从「配置管理」重开已保存的账号。用规格约定的横屏、竖屏和 390 × 844 窄屏复验。真实账号与官方入口的逐项对照归入 [03](03-google-isolation-background-refresh.md)。

## 通过条件

- [x] 只配置一个账号即可使用，自定义名称、连接状态及已填写的付费信息可回读。折叠卡片仍能看到三项额度或各自的状态，窄屏可逐项阅读，无页面级横向滚动。
- [x] Gemini Apps 仅使用该账号在应用中的五小时及每周窗口。实号自动读取核实前显示「请到 Gemini 查看」，入口及「设置 → 使用限额」指引可用。
- [x] Antigravity 按官方只读查询 `agy -p /usage --output-format json` 返回的模型组、窗口及原单位展示。同组多个模型只计一次，名称含 Gemini 的模型仍归 Antigravity。
- [x] Google One AI 积分按同账号活动页的当前可用余额核对 `agy -p /credits --output-format json`。核对一致后展示，家庭共享积分在该卡显示一次。每个已取值项目显示自己的最近成功时间，并展示官方提供的重置时间。
- [x] 仅将与所选授权身份匹配的数据写入卡片；需要官方人工核对的项保留查看入口。两账号的错配与独立性在 03 复验。

## 交付回执

- **红灯**：基线 `8779871`，`RED_READY`（平台下拉缺 Google AI 导致预期断言失败；统一入口复现同一首错）。契约测试 `tests/contract/test_google_account_overview.py` 与夹具 `tests/contract/google_source.py`（回环 OAuth + Antigravity CLI 替身）由独立测试官交付，交接 SHA `944b2f7`。
- **实现候选 SHA**：`4ba22ba`（`944b2f7` → `9e58cc5` 实现 → `3d158aa` OCR 首扫处置 → `ec63a4c` OCR 复扫处置 → `c729604`/`4ba22ba` CDP 超时调整）。
- **实现要点**：新增 `quota_dashboard/sources/google.py`（OAuth PKCE、userinfo 身份核对、`agy -p /usage|/credits --output-format json` 子进程查询与解析、按账号隔离 HOME 的 CLI 凭据）、`quota_dashboard/google_auth.py`（授权会话：授权 URL 展示、授权码提交、state 校验、身份绑定）、accounts/api/server 的 google 平台接线（逐项 `google_items` 状态、公开字段投影、`/api/accounts/{id}/google-auth[/start|/submit]` 路由）与前端 Google AI 平台（表单、授权页、三项额度卡、配置管理入口）。Google OAuth 客户端凭据经 `QUOTA_GOOGLE_CLIENT_ID/SECRET` 部署时注入，不写入源码（仓库推送保护拦截凭据形态字符串）。
- **自测**：统一入口 `pwsh -NoProfile -File scripts/test.ps1 -TempRoot .tmp`：201 passed、36 skipped（实现提交 `9e58cc5`）；另有 2 个既有 Chrome 时序 flaky（DeepSeek 删除确认、后台刷新重启回读）单独复跑通过，红灯基线全量同样出现，与本票无关。实现侧测试 `tests/impl/test_google_source.py` 12 项（解析、PKCE、替身 CLI、账号生命周期、逐项失败保留旧值、state 校验）。
- **OCR 覆盖及处置**：首扫 `944b2f7→9e58cc5` 19 条发现，18 条修复（state/CSRF 校验、授权会话失败状态、磁盘凭据清理与删除窗口、凭据落盘失败拒绝回退真实 HOME、轮询停止与非破坏性失败、`_read_json_body` 异常兜底、status 端点过期守卫、嵌套三元、占位文案、死代码），1 条记录到后续票（Google 授权页与 Codex 设备码流程的结构性重复，02/03 票演进时统一重构）；复扫 `9e58cc5→3d158aa` 6 条全部修复（含两类 GoogleAuthError 混用导致的会话状态缺失）；收敛复扫 `3d158aa→ec63a4c` 零发现；CDP 超时调整复扫 1 条（常量提取）已处置。工具 open-code-review v1.14.4（试点锁定 v1.14.3，补丁级偏差已记录），模型 deepseek-flash，报告留存本机 `ocr-reports/01-google-*.json`。
- **CI**：`9e58cc5` 与最终候选 `4ba22ba` 的 governance、windows-tests 均通过（PR #7，run `46041600130`、`46048649351`，后者 205 passed、36 skipped）；`3d158aa`/`ec63a4c` 两次 CI 因 Chrome CDP 5 秒命令超时在既有用例上偶发失败（runner 性能波动），故 `c729604` 将共享 CDP 超时放宽至 20 秒。
- **独立验收**：固定候选 `4ba22ba` 在干净工作树（`.agents/worktrees/verify-01`）以独立 Codex CLI 会话 verify，结论 **PASS**（实测 HEAD 一致；AC-10 授权与三项额度逐项复跑：Gemini Apps 指引无成功时间、Antigravity 模型组只计一次且含重置时间、家庭积分显示一次 1347、各取值项成功时间；AC-02/AC-05/AC-09 适用点与既有平台回归 8 项通过；验收结束工作树无差异）。首次 verify 在 read-only 沙箱因无法写临时目录 BLOCKED，按约定以 workspace-write 沙箱在隔离工作树复验并通过，结束后核对跟踪文件无差异。回执留档本机 codex-tester receipts（run `…-verify-cc77a98f…`）。
- **遗留到后续票**：Google 授权页与 Codex 设备码授权页的轮询/渲染结构性重复（OCR 首扫 #9）；真实 Google 登录、官方 `agy` 取数、活动页/Gemini 实号对照、双账号错配与关页后台刷新归 [03](03-google-isolation-background-refresh.md)。
