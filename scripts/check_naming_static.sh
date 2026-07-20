#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() {
  printf 'naming static check failed: %s\n' "$1" >&2
  exit 1
}

require_empty() {
  local label="$1"
  local output="$2"
  if [[ -n "$output" ]]; then
    printf '%s\n%s\n' "$label" "$output" >&2
    fail "$label"
  fi
}

for command_name in git rg find awk cut head; do
  command -v "$command_name" >/dev/null 2>&1 || fail "required command is not available: $command_name"
done

git diff --check

tracked_name_offenders="$(
  git ls-files |
    rg '(^|/)(research|trading|trader|adapters)(/|$)|research\.py$|adapter\.py$|service\.py$|models\.py$|base\.py$|utils\.py$|helpers\.py$|manager\.py$|handler\.py$|research_|_research|adapter|trader|trading' || true
)"
require_empty "tracked files contain legacy or generic names" "$tracked_name_offenders"

workspace_name_offenders="$(
  find . \( -path './.git' -o -path './pyenv' -o -path './.venv' -o -path './.pytest_cache' \) -prune -o \
    \( -name 'service.py' -o -name 'models.py' -o -name 'base.py' -o -name 'utils.py' -o -name 'helpers.py' \
    -o -name '*manager*.py' -o -name '*handler*.py' -o -name '*research*' -o -name '*adapter*' \
    -o -name 'adapters' -o -name 'trading' -o -name 'trader' \) -print
)"
require_empty "workspace contains legacy or generic file names" "$workspace_name_offenders"

content_matches="$(
  rg -n 'APPROVED_FOR_RESEARCH|RESEARCH_VALIDATED|ResearchDataClient|ResearchSpec|ResearchService|RunMode\.RESEARCH|research_composition|kairospy\.research|from trading|import trading|name = "trader"|"trader"|"trading"|--adapter|args\.adapter|kairospy\.adapters|from kairospy\.adapters|import kairospy\.adapters' . \
    --glob '!pyenv/**' \
    --glob '!.git/**' \
    --glob '!docs/naming_audit.md' \
    --glob '!tests/test_repository_hygiene.py' \
    --glob '!tests/test_project_init.py' \
    --glob '!scripts/check_naming_static.sh' || true
)"
unexpected_content_matches=""
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  if [[ "$line" == './pyproject.toml:'*'exclude = ["kairospy.research", "kairospy.research.*", "research", "research.*", "studies", "studies.*"]' ]]; then
    continue
  fi
  unexpected_content_matches+="${line}"$'\n'
done <<< "$content_matches"
require_empty "content contains legacy public names" "$unexpected_content_matches"

packaging_file_offenders="$(find . -maxdepth 2 \( -name 'MANIFEST.in' -o -name 'setup.py' -o -name 'setup.cfg' \) -print)"
require_empty "packaging side-channel files are present" "$packaging_file_offenders"

package_data_offenders="$(rg -n 'package-data|include-package-data|data-files' pyproject.toml || true)"
require_empty "setuptools package-data side channels are present" "$package_data_offenders"

readme_duplicate_rows="$(
  awk -F'`' '/^\| `/{count[$2]++} END{for (name in count) if (count[name] > 1 && name != "DataProductDefinition") print name, count[name]}' README.md
)"
require_empty "README core naming table has duplicate rows" "$readme_duplicate_rows"

for marker in \
  '普通用户不需要复制本仓库' \
  'python3 -m pip install kairospy' \
  'mkdir my-kairospy-project' \
  'kairospy init' \
  'python studies/starter.py' \
  '安装包只包含 Kairos 产品库和 CLI，不包含本仓库顶层 `studies/` 源码研究工作区' \
  '如果你是从源码参与开发，再使用 editable 安装' \
  './pyenv/bin/pip install -e'
do
  rg -F "$marker" README.md >/dev/null || fail "README missing user install marker: $marker"
done
readme_user_line="$(rg -n -F 'python3 -m pip install kairospy' README.md | head -n 1 | cut -d: -f1)"
readme_source_line="$(rg -n -F './pyenv/bin/pip install -e' README.md | head -n 1 | cut -d: -f1)"
if [[ -z "$readme_user_line" || -z "$readme_source_line" || "$readme_user_line" -ge "$readme_source_line" ]]; then
  fail "README does not present user pip install before source editable install"
fi
readme_legacy_install="$(
  rg -n 'pip install trader|pip install kairospy$|trader init' README.md || true
)"
require_empty "README contains legacy trader install commands" "$readme_legacy_install"

rg -n 'name = "kairospy"' pyproject.toml >/dev/null || fail "pyproject.toml distribution name is not kairospy"
rg -n 'kairospy = "kairospy.__main__:main"' pyproject.toml >/dev/null || fail "pyproject.toml does not publish kairospy CLI"
[[ -f .github/workflows/release.yml ]] || fail "missing GitHub release workflow"
rg -n 'environment: pypi' .github/workflows/release.yml >/dev/null || fail "release workflow does not use pypi environment"
rg -n 'id-token: write' .github/workflows/release.yml >/dev/null || fail "release workflow does not grant OIDC id-token permission"
rg -n 'pypa/gh-action-pypi-publish@release/v1' .github/workflows/release.yml >/dev/null || fail "release workflow does not use PyPI publish action"
rg -n '"name": "kairospy"' .kairos/project.json >/dev/null || fail ".kairos/project.json does not use kairospy"
rg -n '"root": "\."' .kairos/project.json >/dev/null || fail ".kairos/project.json root is not portable"
rg -n 'name = "kairospy"' kairos.toml >/dev/null || fail "kairos.toml does not use kairospy"
rg -n '^\[study\]' kairos.toml >/dev/null || fail "kairos.toml does not use [study]"

printf 'naming static check passed\n'
