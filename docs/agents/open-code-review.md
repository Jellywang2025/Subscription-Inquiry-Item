# Open Code Review

DSH 在每票自测通过后、调用独立验收前，用 OCR 原生模式检查从红灯交接提交到候选提交的差异。确认交接提交是候选的祖先，使用干净候选工作树。先以 OCR 预览核对文件范围，然后对同一提交范围运行审查。

```powershell
ocr review --from <红灯交接SHA> --to <候选SHA> --preview --format json
$env:OCR_LLM_URL = 'https://api.deepseek.com'
$env:OCR_LLM_TOKEN = $env:DEEPSEEK_API_KEY
$env:OCR_LLM_MODEL = 'deepseek-flash'
$env:OCR_LLM_PROTOCOL = 'openai'
ocr review --from <红灯交接SHA> --to <候选SHA> --format json --output <本机报告路径>
```

Windows 试点锁定 OCR v1.14.3。在独立进程中使用环境变量提供 DeepSeek 配置。CLI 可能优先采用既有用户配置，核对实际模型与端点再开始。记录工具版本、模型、基线 SHA、候选 SHA、规则及过滤范围、文件覆盖、结论和处置。原始 JSON 会包含源码及会话明细，保留在仓库外。

命令完成后检查覆盖及警告。失败、超时、预算耗尽及范围不完整时标明阻塞。确认的阻断发现修复后重跑相应测试，审查新增差异再交给 codex-tester verify。最终候选与已审内容、基线和规则一致时，复用回执。返修、合并或范围变化时复扫实际差异，并重新验收受影响行为。[OCR CLI](https://github.com/alibaba/open-code-review/blob/v1.14.3/pages/src/content/docs/zh/cli-reference.md)
