---
name: implement-spec
description: 在 DSH 中按依赖实施已确认 Spec，逐票复用 implement 流程，在一个 PR 交付。
disable-model-invocation: true
---

# 交付一份 Spec

1. 读取 [协作约定](../../../spec/governance/working-agreement.md)和 Spec 对应的工单图，核对依赖和已有验收结论。
2. 复用 Spec 对应的分支及草稿 PR。从已有分支、PR 和工单回执恢复进度。第一份候选提交后开草稿。每票调用 [implement](../implement/SKILL.md)，使用同一 Spec 分支；红灯与独立验收固定走仓库内 `scripts/codex-tester.py`。需要隔离时，从 Spec 分支建立临时工作树，核对实际汇入候选。
3. 只有具备固定 SHA 的完整验收回执的票才进入待合入状态。依赖票以该票的验收结论为前置。范围变化、调用失败及部分审查按单票规定处理。
4. PR 就绪前核对全量测试和 GitHub 必需检查覆盖最终候选。根据 [OCR 审查约定](../../../docs/agents/open-code-review.md)复用完整回执或复扫变动。受影响的验收条件重验。整份 Spec 只合入一次，PR 正文包含各子 Issue 的关闭引用。
