#!/usr/bin/env bash
set -euo pipefail

command -v flatc >/dev/null 2>&1 || {
  echo "flatc is required; install FlatBuffers compiler first" >&2
  exit 1
}

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
schema_root="$repo_root/schemas"
python_out="$repo_root/kairospy/infrastructure/transport/generated"
rust_out="$repo_root/crates/platform/protocol/src/generated"
stage_root="$(mktemp -d)"
python_stage="$stage_root/python"
rust_stage="$stage_root/rust"
trap 'rmdir "$stage_root" 2>/dev/null || true' EXIT

python3 "$repo_root/scripts/generate/validate_v2_schemas.py"
schemas=()
while IFS= read -r schema; do
  schemas+=("$schema")
done < <(find "$schema_root/v2" -type f -name '*.fbs' | sort)
if (( ${#schemas[@]} == 0 )); then
  echo "no v2 FlatBuffers schemas found" >&2
  exit 1
fi

flatc -I "$schema_root" --python -o "$python_stage" "${schemas[@]}"

for index in "${!schemas[@]}"; do
  schema="${schemas[$index]}"
  output="$stage_root/rust-$index"
  flatc -I "$schema_root" --rust --rust-module-root-file -o "$output" "$schema"
  while IFS= read -r generated; do
    relative="${generated#"$output/"}"
    destination="$rust_stage/$(dirname "$relative")"
    mkdir -p "$destination"
    cp "$generated" "$destination/"
  done < <(find "$output" -type f -name '*.rs' -not -name mod.rs)
done

find "$rust_stage" -type f -name '*.rs' -print0 \
  | xargs -0 rustfmt --edition 2021

mkdir -p "$python_out/kairos" "$rust_out/kairos"
rsync -a --delete "$python_stage/kairos/" "$python_out/kairos/"
rsync -a --delete "$rust_stage/kairos/" "$rust_out/kairos/"
