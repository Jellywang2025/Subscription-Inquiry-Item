#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

required_entries=(
    AGENTS.md
    README.md
    spec/README.md
    spec/governance/working-agreement.md
    spec/governance/spec-template.md
    tests/README.md
    docs/README.md
    docs/agents/issue-tracker.md
    docs/agents/domain.md
    docs/agents/dsh-codex-cli.md
    docs/agents/triage-labels.md
    .agents/skills/README.md
    .agents/skills/implement/SKILL.md
)

for entry in "${required_entries[@]}"; do
    if [[ ! -f "$entry" || ! -s "$entry" ]]; then
        printf '缺少有效的文档入口 %s\n' "$entry" >&2
        exit 1
    fi
done

case "$#" in
    0)
        git diff --check
        git diff --cached --check
        ;;
    2)
        git diff --check "$1" "$2" --
        ;;
    *)
        printf '用法 bash scripts/check-governance.sh [基准 SHA 目标 SHA]\n' >&2
        exit 2
        ;;
esac

printf '文档入口和差异空白检查通过\n'
