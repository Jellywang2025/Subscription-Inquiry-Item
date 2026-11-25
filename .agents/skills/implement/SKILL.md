---
name: implement
description: 在 DSH 中交付已确认 Spec 的业务切片。调用 codex-tester 验证实现前的红灯和实现后的独立验收，完成实现、自测与审查。
disable-model-invocation: true
---

# 实现一个业务切片

由 DSH 执行。读取 [协作约定](../../../spec/governance/working-agreement.md)、[工单约定](../../../docs/agents/issue-tracker.md)、[测试约定](../../../tests/README.md)和 [codex-tester 调用约定](../../../docs/agents/dsh-codex-cli.md)。

0. 根据 Spec 路径和版本查找同一 Spec 分支和 PR。有分支时接续，首票在第一个候选提交就绪后建立共用草稿 PR。票验收通过时留下 SHA 和回执，整份 Spec 合入前保持工单打开。
1. 读取就绪工单并确认标题、Spec 路径、版本及提交 SHA、本片 AC、依赖和修改范围。根据 Spec 核对公开入口和关键用例。若业务规则或验收预期需要改动，将对应条目交回需求会话确认。
2. **实现前调用 `codex-tester red`。** 按调用约定使用仓库内的 `scripts/codex-tester.py`，启动独立的 Codex CLI 会话。传入实现前工作树和基线 SHA、已确认的 Spec 和本片 AC、验证命令、允许写入的测试路径。入口缺失时先准备最小可运行骨架。核对回执和实测基线，`RED_READY` 须有目标行为缺失导致的失败证据。可复用同一 Spec、AC、验收用例和基线上的有效红灯回执。测试官返回 `BLOCKED` 或缺少有效回执时，处理阻塞后再开始产品实现。
3. **DSH 实现和自测。** 固化测试官交付的契约用例，记录交接 SHA，从它建立实现工作树。用 [TDD](../tdd/SKILL.md) 在已确认的入口补实现侧用例，循环运行切片检查，提交候选代码。将交接 SHA 和 Spec 路径交给 [code-review](../code-review/SKILL.md) 按项目约定审查实现，再对待验提交运行项目检查。若 `ALREADY_PASSES` 且本片行为已被覆盖，保留关键回归用例后直接验收。
4. **OCR 原生审查。** 完成自测后按[审查约定](../../../docs/agents/open-code-review.md)审查红灯交接 SHA 到候选 SHA 的 diff，处置发现并检查实际覆盖，再进行独立验收。
5. **提交后调用 `codex-tester verify`。** 从待验 SHA 创建独立的干净工作树，通过 `scripts/codex-tester.py --phase verify` 开启新的 Codex CLI 会话，传入 Spec、AC、交接 SHA、待验 SHA、验证命令和已有 CI 证据。以该提交的 `PASS` 和实测回执作为独立验收结果。若返回 `FAIL`，依据证据修复后对新提交复验。需纠正契约用例时单独交测试官处理。
6. 将红灯、独立验收和项目检查的提交 SHA、命令与结果记入工单或 PR。缺少测试官、回执无法核验或环境阻塞时标记 `BLOCKED`，处理阻塞后再运行对应阶段。CLI 调用成功只代表收到回执，按回执里的实测结论推进合入门禁。
