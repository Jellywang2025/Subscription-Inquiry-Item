# <版本或主题>

按[统一 Spec <版本>](spec/implement/<文件名>.md)（提交 `<sha>`）交付第 <N> 票。每票经 codex-tester red → DSH 实现自测 → OCR 审查 → 干净工作树 verify 独立验收，回执回填至[工单](.scratch/<版本>/issues/)。

## 工单进度

| 工单 | AC | 结论 | 候选 SHA |
| --- | --- | --- | --- |
| [01 <票名>](.scratch/<版本>/issues/01-<文件名>.md) | AC-01、AC-04 | ✅ verify PASS | `<sha>` |

## 验收证据

- 统一入口 `scripts/test.ps1`：<通过数> passed，<跳过数> skipped。
- 最终 CI（`<sha>`）：governance 与 windows-tests 结论。
- verify 期间发现并修复的缺陷：<数量与要点>。
- 真机或人工复检：<结果>。

## 未完成、已知风险或需要人工确认的事项

- <没有就写「无」>
