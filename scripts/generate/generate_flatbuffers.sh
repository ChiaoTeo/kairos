#!/usr/bin/env bash
set -euo pipefail

command -v flatc >/dev/null 2>&1 || {
  echo "flatc is required; install FlatBuffers compiler first" >&2
  exit 1
}

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
rustfmt_toolchain="${RUSTFMT_TOOLCHAIN:-nightly-2026-02-01}"
schema_root="$repo_root/schemas"
rust_out="$repo_root/crates/platform/protocol/src/generated"
stage_root="$(mktemp -d)"
rust_stage="$stage_root/rust"
trap 'rmdir "$stage_root" 2>/dev/null || true' EXIT

python3 "$repo_root/scripts/generate/validate_v2_schemas.py"
schemas=()
while IFS= read -r schema; do
  schemas+=("$schema")
done < <(find "$schema_root" -mindepth 2 -type f -name '*.fbs' -path '*/v[0-9]*/*' | sort)
if (( ${#schemas[@]} == 0 )); then
  echo "no versioned FlatBuffers schemas found" >&2
  exit 1
fi

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
  | xargs -0 rustup run "$rustfmt_toolchain" rustfmt \
      --edition 2021 --config-path "$repo_root/rustfmt.toml"

mkdir -p "$rust_out/kairos"
rsync -a --delete "$rust_stage/kairos/" "$rust_out/kairos/"
