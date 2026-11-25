# DSH 调用 Codex CLI

DSH 主控在一片开始时调用 `codex-tester` 的 `red` 阶段，实现并自测后调用其 `verify` 阶段。两次调用各开一个会话，读取同一仓库中同一版本的 Spec。主控提供当前切片的输入，测试员独立执行并回传实际观察。

| 阶段 | 主控提供 | 测试员交付 |
| --- | --- | --- |
| `red` | Spec 路径、版本与 Spec 提交 SHA，本片 AC、实现前代码基线 SHA、对应工作树、允许写入的测试路径和运行命令 | 从公开入口编写和执行契约测试。回传 `RED_READY`、`ALREADY_PASSES` 或 `BLOCKED`，附实际 HEAD、AC、用例路径、命令、退出码和失败原因 |
| `verify` | 同一 Spec 和 AC、红灯交接提交 SHA 或基线已满足的证据、待验 SHA、从该 SHA 检出的干净工作树、运行命令和可用的 CI 引用 | 核对 HEAD 和验收用例差异，复跑关键用例并从公开入口复检关键场景。回传 `PASS`、`FAIL` 或 `BLOCKED`，附实测 SHA、AC、命令、退出码、观察和未验证项。单独报告检查或 CI 状态 |

- `red` 的写入范围限定在主控指定的契约测试及其夹具。只有因目标行为缺失导致的断言失败算 `RED_READY`。缺入口或缺依赖时先报告阻塞，待最小运行骨架或环境就绪后重跑。用例基线已绿时核实覆盖并保留回归用例，按实际缺口判断是否仍需实现。
- `verify` 在独立工作树只读验收。运行器确需写缓存或临时数据时，写入隔离工作树或专用临时目录，结束后核对跟踪文件差异。修复交给 DSH，验收断言的更正交给测试员单独处理。每次复验注明实际待验提交。
- 主控核对回执及变更范围，固化红灯交接提交，在工单或 PR 引用请求摘要和验收证据。CLI 退出码说明调用是否完成，业务结论以实测回执为准。缺少独立结果时记录阻塞。验收证据引用已核验的提交，更新代码或验收用例后以新的待验提交重新验收。

## 复用已有的 `codex-tester`

归档时记录封装运行 ID、CLI session ID、阶段、实际验收提交、CLI 退出码、耗时和可见 token 用量，并链接实测回执。当前封装的 CLI 文本日志可从 stderr 核对 session ID 和总 token。缺少用量明细时记为「未记录」。仓库内保存上述元数据和实测结论，原始请求和输出保存在本机。

优先使用 DSH 已配置的 `codex-tester`。将本仓库的路径、版本、SHA 和允许写入范围作为任务参数传入。首次调用前确认本机可用的 Codex CLI、认证、工作树和测试命令。若本机封装引用了旧项目的位置，接入时核对其实际读取的是当前任务的仓库与 Spec。

现有 PowerShell 封装支持 `-Agent tester`、`-Phase red|verify`、`-WorkDir`、`-Task` 和可选的 `-ArchiveFile` 时，可由 DSH 按如下方式传参。变量由主控根据当前切片传入。

```powershell
& $testerScript -Agent tester -Phase red -WorkDir $redWorktree -Task $redTask
& $testerScript -Agent tester -Phase verify -WorkDir $verifyWorktree -Task $verifyTask
```

其他封装可使用相同输入和回执约定。直接接入 Codex CLI 时，可用 `codex exec --cd <工作树> --sandbox <模式> --ephemeral --output-last-message <回执文件> -` 从标准输入传角色约束与任务。通常 `red` 用 `workspace-write`，`verify` 用 `read-only`。两条命令由主控在各自阶段执行。

项目约定见[协作约定](../../spec/governance/working-agreement.md)和[测试约定](../../tests/README.md)。
