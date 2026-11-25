#!/usr/bin/env bash
set -euo pipefail

shopt -s nullglob dotglob

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

# 受限环境统一使用的忽略目录；其余根目录临时项一律视为污染。
root_temp=()
for entry in ./*; do
    name=${entry#./}
    if [[ "$name" == ".tmp" ]]; then
        if [[ -d "$entry" ]]; then
            continue
        fi
        root_temp+=("$entry")
        continue
    fi
    case "$name" in
        .tmp-*|tmp-*|*.tmp)
            root_temp+=("$entry")
            ;;
    esac
done
shopt -u dotglob

if ((${#root_temp[@]})); then
    printf '仓库根目录存在临时项：\n' >&2
    printf '  %s\n' "${root_temp[@]}" >&2
    printf '请删除或移入系统临时目录；受限环境统一使用 .tmp/。\n' >&2
    exit 1
fi

required_entries=(
    AGENTS.md
    README.md
    spec/README.md
    spec/governance/working-agreement.md
    spec/governance/spec-template.md
    spec/archived/personal-quota-dashboard.md
    tests/README.md
    docs/README.md
    docs/agents/issue-tracker.md
    docs/agents/domain.md
    docs/agents/dsh-codex-cli.md
    docs/agents/triage-labels.md
    docs/agents/open-code-review.md
    .agents/skills/README.md
    .agents/skills/implement/SKILL.md
    scripts/check-governance.sh
    scripts/test.ps1
)

for entry in "${required_entries[@]}"; do
    if [[ ! -f "$entry" || ! -s "$entry" ]]; then
        printf '缺少有效的入口 %s\n' "$entry" >&2
        exit 1
    fi
done

link_docs=(
    AGENTS.md
    README.md
    docs/README.md
    docs/agents/open-code-review.md
    spec/README.md
    spec/governance/working-agreement.md
    tests/README.md
    spec/implement/*.md
    spec/archived/*.md
)

for doc in "${link_docs[@]}"; do
    doc_dir=$(dirname -- "$doc")
    while IFS= read -r raw; do
        target=${raw#']('}
        target=${target%')'}
        target=${target%% *}
        target=${target#<}
        target=${target%>}
        case "$target" in
            ''|\#*|http://*|https://*|mailto:*)
                continue
                ;;
        esac
        target=${target%%#*}
        [[ -z "$target" ]] && continue
        if [[ "$target" == /* ]]; then
            resolved=".$target"
        else
            resolved="$doc_dir/$target"
        fi
        if [[ ! -e "$resolved" ]]; then
            printf '文档链接失效：%s -> %s（解析为 %s）\n' "$doc" "$target" "$resolved" >&2
            exit 1
        fi
    done < <(grep -oE '\]\([^)]*\)' "$doc" || true)
done

for archive_spec in spec/archived/*.md; do
    if ! grep -qE '^[[:space:]]*-[[:space:]]+状态[[:space:]]+`archived`' "$archive_spec"; then
        printf '归档规格缺少“状态 archived”：%s\n' "$archive_spec" >&2
        exit 1
    fi
done
for active_spec in spec/implement/*.md; do
    if grep -qE '^[[:space:]]*-[[:space:]]+状态[[:space:]]+`archived`' "$active_spec"; then
        printf '实施中规格不应标记 archived：%s\n' "$active_spec" >&2
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

printf '文档入口、本地链接、规格归档和根目录临时项检查通过\n'
