# 01 · 管理员登录并进入空总览

- 产品版本 `0.2.0`
- 规格基线 [个人订阅管理系统统一 Spec](../../../spec/implement/subscription-management.md)，提交 `934c18379055de0a75c04a194f6d723381629aee`
- 验收编号 AC-01、AC-04（首次无配置）
- 直接依赖 无
- 初始状态 `ready-for-agent`

## 可观察结果

管理员从服务器网址登录到自己的总览。尚无订阅时可以开始添加第一条。

## 验证入口

使用未登录的浏览器分别访问首页、配置管理和带搜索条件的受保护网址，完成登录，再从空总览点击「添加第一条订阅」。

## 通过条件

- [ ] 受保护入口先要求管理员登录。普通首页登录后显示全部已配置订阅，空库时显示首次使用空态。
- [ ] 从受保护深链接登录后回到原页面并保留原筛选参数。实际匹配结果由 07 覆盖。
- [ ] 空总览可进入添加订阅流程，页面不预置任何示例账号。

## 交付回执

- **实现前基线**：`044bd54`（Spec 分支交接提交，工作树干净）。
- **独立红灯**：`RED_READY`（会话 `01a2170e-f9f2-7643-a25b-01f129a4f643`）。测试官新增 `tests/contract/test_subscription_admin_entry.py` 4 条浏览器契约用例（AC-01 首页 / 配置管理 / 筛选深链接回跳、AC-04 首次无配置），基线上全部因目标行为缺失失败（直接显示旧看板、无口令框、四张旧卡片、无「添加第一条订阅」）。统一入口实测治理 / pyright / node-check 通过，pytest 114 通过 4 失败（均为新增用例）。搜索词 `青岚 & A+B` 覆盖 URL 编码往返。
- **测试交接 SHA**：`df174a9`。实现暴露正路径探测缺陷（DOM 元素经 CDP `returnByValue` 序列化为空对象）后，由测试官会话单独修正一处条件为 `Boolean(password)`（回执 `FIXED`，4 用例复跑通过），修正并入实现候选。
- **实现候选 SHA**：`a4ef05b`（`df174a9` 红灯 → `1211a1f` 实现 → `346de50` OCR 处置 → `a4ef05b` OCR 复扫处置）。
- **实现要点**：`QUOTA_ADMIN_PASSWORD` 口令恒定时间校验 + HttpOnly 内存会话；除 `/login` 与静态资源外 GET/POST/HEAD 全路由（含 API）要求会话，未登录 302 `/login?next=<原URL>`，登录回跳保留查询参数；`next` 拒绝控制字符且 Location 头重新百分号编码；0.2.0 壳（侧栏双导航、`?view=/platform/q` 路由、筛选恢复）；空库「还没有订阅」空态 +「添加第一条订阅」进入三平台可选表单；`GET /api/accounts` 凭据不外泄（仅 `key_tail`）；`POST /api/accounts` 暂回 501（保存属 02 票）、`POST /api/refresh` 空操作（查询属后续票）。
- **自测**：统一入口 `pwsh -NoProfile -File scripts/test.ps1` 全绿——治理 / pyright / node-check 通过，pytest **122 passed, 36 skipped**（36 条为 v0.1 数据契约按票标记 skip：02/03/04/06/08 重建；`test_startup_dashboard.py` 已更新为 0.2.0 登录门禁口径）。实现侧新增 `test_admin_auth.py`（23 条）、`test_route_protection.py`（21 条）。
- **OCR 覆盖及处置**：首扫 `df174a9→1211a1f` 20 条发现——13 条修复（`safe_next` 控制字符与 Location 编码、深链接数据到达重渲染、会话过期 API 转登录、登录失败保留 `next`、`data-remove` 转义、双重转义、providers 守卫、Codex `hasKey`、do_HEAD 门禁、progressbar ARIA、tone 映射、补凭据过滤测试、授权视图占位简化）；6 条记录到后续票（DELETE 路由与金额格式属 02 票、保存后跳授权属 04 票、id 类型随 02 票、过滤快照重构延后）；1 条按核准视觉基线保留（`.setting-row`/`.code-box` 等样式为 04/08 票基线资产）。复扫 `1211a1f→346de50` 4 条发现全部修复（`346de50→a4ef05b`）。
- **独立验收**：`PASS`（会话 `01a2174c-2c89-75c1-ad61-bf715c44d979`，干净工作树实测 HEAD `a4ef05b`）。Chrome 公开入口复检：AC-01 三入口未登录均 302 登录页，错误口令提示，正确口令回原页面且查询串与筛选控件值恢复；预置三条测试记录时普通首页全部显示。AC-04 空库显示「还没有订阅」与 `0 个账号`，无预置卡片与 ChatGPT 入口，「添加第一条订阅」进入三平台可选表单。契约 4 条复跑通过；分步全量 122 通过 36 跳过；验收前后 `git status` 干净、`git diff --exit-code HEAD` 为 0。首次 verify 因 read-only 沙箱拒绝写临时目录 BLOCKED，按约定放宽为 `workspace-write` 后完成（回执含沙箱说明）。
- **待复检项**：真实平台账号、保存与授权流程、筛选匹配结果及 AC-04 其余状态由 02+ 票覆盖；该 SHA 尚无 Actions 运行（草稿 PR 建立后由 CI 覆盖）。
