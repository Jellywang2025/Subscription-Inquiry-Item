# GitHub 工单

项目在 [GitHub Issues](https://github.com/Jellywang2025/Subscription-Inquiry-Item/issues) 管理新工单。Codex 根据已确认 Spec 拆票，维护验收编号、交付行为、范围和依赖。记录仓库中 Spec 文件、版本、提交及实际验证入口。DSH 核对技术依赖并从前置验收通过后的就绪票开始。分诊使用[项目标签](triage-labels.md)。

一份 Spec 的子 Issue 共用一个 PR。子 Issue 留下实现 SHA 与红灯、自测、OCR、独立验收摘要。票验收通过时标记已验收待合入，PR 正文为每张暂未关闭的子 Issue 写 Closes #编号。PR 合入 main 时关闭全部关联票。公开历史摘要见[已交付记录](../../spec/work-items/README.md)。
