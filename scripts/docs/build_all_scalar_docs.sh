#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
output_dir="${repo_root}/target/docs/api"
openapi_bundler_version="${OPENAPI_BUNDLER_VERSION:-2.46.1}"
check_only=0

if [[ "${1:-}" == "--check" ]]; then
  check_only=1
elif [[ $# -gt 0 ]]; then
  echo "usage: $0 [--check]" >&2
  exit 2
fi

specs=()
while IFS= read -r spec; do
  specs+=("${spec}")
done < <(find "${repo_root}/schemas/v2" -type f -name 'control.openapi.yaml' | sort)
if [[ ${#specs[@]} -eq 0 ]]; then
  echo "No v2 OpenAPI control schemas found; skipping Scalar API docs."
  if [[ ${check_only} -eq 0 ]]; then
    mkdir -p "${output_dir}"
    node "${repo_root}/scripts/docs/render_scalar_index.mjs" \
      "${output_dir}/index.html"
    echo "Generated ${output_dir}/index.html"
  fi
  exit 0
fi

if [[ ${check_only} -eq 0 ]]; then
  mkdir -p "${output_dir}"
fi

temp_dir="$(mktemp -d -t kairos-openapi.XXXXXX)"
trap 'rm -rf "${temp_dir}"' EXIT

index_entries=()
for spec in "${specs[@]}"; do
  relative="${spec#"${repo_root}/schemas/v2/"}"
  module="${relative%%/*}"
  bundle_file="${temp_dir}/${module}.json"

  # Redocly CLI is used only as an OpenAPI reference resolver.  The generated
  # documentation UI is Scalar; no Redoc page is produced.
  npx --yes "@redocly/cli@${openapi_bundler_version}" bundle \
    "${spec}" \
    --output "${bundle_file}"

  node -e 'JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));' "${bundle_file}"

  if [[ ${check_only} -eq 0 ]]; then
    output_file="${output_dir}/${module}.html"
    node "${repo_root}/scripts/docs/render_scalar_api_docs.mjs" \
      "${bundle_file}" \
      "${output_file}" \
      "${module}"
    index_entries+=("${module}.html")
    echo "Generated ${output_file}"
  else
    echo "Validated ${spec}"
  fi
done

if [[ ${check_only} -eq 0 ]]; then
  node "${repo_root}/scripts/docs/render_scalar_index.mjs" \
    "${output_dir}/index.html" \
    "${index_entries[@]}"
  echo "Generated ${output_dir}/index.html"
fi
