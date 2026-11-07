#!/usr/bin/env bash
set -euo pipefail
contract_root=$(cd "$(dirname "$0")/../.." && pwd)
contract_tools="$contract_root/.source-contract-tooling"
mkdir -p "$contract_tools"
curl --fail --location --retry 3 \
  https://github.com/dafny-lang/dafny/releases/download/v4.11.0/dafny-4.11.0-x64-ubuntu-22.04.zip \
  --output "$contract_tools/dafny.zip"
printf '%s  %s\n' a46a9ff7cdd720f7955854c78e95df13f4cfe6b80691b05f8654fe19e8267179 "$contract_tools/dafny.zip" | sha256sum --check
unzip -oq "$contract_tools/dafny.zip" -d "$contract_tools"
chmod +x "$contract_tools/dafny/dafny"
"$contract_tools/dafny/dafny" --version
if [[ -n "${GITHUB_PATH:-}" ]]; then
  printf '%s\n' "$contract_tools/dafny" >> "$GITHUB_PATH"
fi
