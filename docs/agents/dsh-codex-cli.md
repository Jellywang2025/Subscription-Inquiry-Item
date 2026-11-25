# DSH 调用 Codex CLI

DSH 主控在一片开始时调用 `codex-tester` 的 `red` 阶段，实现并自测后调用其 `verify` 阶段。两次调用各开一个会话，读取同一仓库中同一版本的 Spec。主控提供当前切片的输入，测试员独立执行并回传实际观察。

| 阶段 | 主控提供 | 测试员交付 |
| --- | --- | --- |
| `red` | Spec 路径、版本与 Spec 提交 SHA，本片 AC、实现前代码基线 SHA、对应工作树、允许写入的测试路径和运行命令 | 从公开入口编写和执行契约测试。回传 `RED_READY`、`ALREADY_PASSES` 或 `BLOCKED`，附实际 HEAD、AC、用例路径、命令、退出码和失败原因 |
| `verify` | 同一 Spec 和 AC、红灯交接提交 SHA 或基线已满足的证据、待验 SHA、从该 SHA 检出的干净工作树、运行命令和可用的 CI 引用 | 核对 HEAD 和验收用例差异，复跑关键用例并从公开入口复检关键场景。回传 `PASS`、`FAIL` 或 `BLOCKED`，附实测 SHA、AC、命令、退出码、观察和未验证项。单独报告检查或 CI 状态 |

- `red` 的写入范围限定在主控指定的契约测试及其夹具。只有因目标行为缺失导致的断言失败算 `RED_READY`。缺入口或缺依赖时先报告阻塞，待最小运行骨架或环境就绪后重跑。用例基线已绿时核实覆盖并保留回归用例，按实际缺口判断是否仍需实现。
- `verify` 在独立工作树只读验收。运行器确需写缓存或临时数据时，写入隔离工作树或专用临时目录，结束后核对跟踪文件差异。修复交给 DSH，验收断言的更正交给测试员单独处理。每次复验注明实际待验提交。
- 主控核对回执及变更范围，固化红灯交接提交，在工单或 PR 引用请求摘要和验收证据。CLI 退出码说明调用是否完成，业务结论以实测回执为准。缺少独立结果时记录阻塞。验收证据引用已核验的提交，更新代码或验收用例后以新的待验提交重新验收。

## 仓库内测试员入口

本仓库用 `scripts/codex-tester.py`（Python 3.12，只依赖标准库）执行 `red` 和 `verify`。脚本保留测试官角色约束与阶段状态词，不修改安装的全局封装。调用前先确认本机 `codex` 可执行文件、认证、工作树和测试命令可用。

```powershell
python scripts/codex-tester.py `
  --ticket 2 `
  --phase red `
  --work-dir <实现前工作树> `
  --expected-sha <实现前基线SHA> `
  --task-file <本机任务文件>

python scripts/codex-tester.py `
  --ticket 2 `
  --phase verify `
  --work-dir <待验SHA的干净工作树> `
  --expected-sha <待验SHA> `
  --task-file <本机任务文件>
```

| 参数 | 说明 |
| --- | --- |
| `--ticket` | 工单编号或标识，写入回执。 |
| `--phase` | `red` 或 `verify`。 |
| `--work-dir` | 已检出到目标提交的工作树。 |
| `--expected-sha` | 启动前要求匹配的 HEAD；实际 HEAD 不一致时不启动 Codex，回执记为 `BLOCKED`。 |
| `--task-file` | UTF-8 任务参数文件，包含 Spec 路径及版本、AC、允许写入范围和验证命令。 |
| `--receipt-root` | 仓库外回执根目录。默认 `%LOCALAPPDATA%\codex-tester\receipts`，也可用环境变量或本机路径覆盖。 |
| `--codex` | 可选的实际 `codex` 可执行文件完整路径；省略时依次查 `CODEX_CLI_PATH`、PATH 的 `codex.exe` 和桌面版 `bin`。不要传全局 `.cmd`/`.ps1` 封装。 |
| `--model` | 默认 `gpt-6.2-sol`。 |
| `--sandbox` | 省略时 `red` 用 `workspace-write`，`verify` 用 `read-only`。 |
| `--effort` | 推理力度，默认 `high`。 |
| `--timeout-seconds` | Codex 运行超时，默认 900 秒。超时会终止进程树并保留已捕获输出。 |

脚本先固定工作树实际 HEAD，再把便携角色约束和任务从标准输入传给 `codex exec`，同时传 `--json` 与 `--output-last-message`。每次调用只在仓库外创建一个带唯一 ID 的运行目录：

```text
<receipt-root>/<UTC时间>-<ticket>-<phase>-<run-id>/
  request.md          角色约束与任务原文
  stdout.jsonl        Codex JSONL 原始标准输出
  stderr.log          Codex 原始标准错误
  last-message.md     --output-last-message 回执原文
  receipt.json        JSON 元数据与业务结论
```

`receipt.json` 关联工单、阶段、实际 SHA、运行 ID、CLI 会话 ID、进程退出码、业务状态、耗时和日志路径。会话 ID 取 `thread.started` 的 `thread_id`；token 取 `turn.completed` 的可见 `usage`，会话或用量缺失时写 `null`。业务状态只取 `last-message.md` 第一条非空行的显式状态词：`red` 接受 `RED_READY`、`ALREADY_PASSES`、`BLOCKED`，`verify` 接受 `PASS`、`FAIL`、`BLOCKED`。正文里的 `PASS`/`FAIL` 不参与判定；没有有效首行时状态为 `BLOCKED`。推进门禁时同时核对实际 SHA、CLI 进程退出码为零、阶段结论及可复现证据。

成功、计划内 `BLOCKED`、Codex 非零退出、启动失败和超时都会写回执与已有日志。脚本自身退出 `0` 表示回执已保存，`2` 表示参数或仓库外目录校验失败，`3` 表示运行目录或回执无法写入。公开工单和 PR 只引用脱敏摘要、公开 SHA 与证据摘要，原始日志留在本机运行目录。

项目约定见[协作约定](../../spec/governance/working-agreement.md)和[测试约定](../../tests/README.md)。
