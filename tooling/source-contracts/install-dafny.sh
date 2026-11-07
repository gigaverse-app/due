#!/usr/bin/env bash
set -euo pipefail
contract_root=$(cd "$(dirname "$0")/../.." && pwd)
contract_tools="$contract_root/.source-contract-tooling"
mkdir -p "$contract_tools"
curl --fail --location --retry 3 \
  https://github.com/dafny-lang/dafny/releases/download/v4.11.0/dafny-4.11.0-x64-ubuntu-22.04.zip \
  --output "$contract_tools/dafny.zip"
printf '%s  %s\n' a46a9ff7cdd720f7955854c78e95df13f4cfe6b80691b05f8654fe19e8267179 "$contract_tools/dafny.zip" | sha256sum --check
python3 - "$contract_tools/dafny.zip" "$contract_tools" <<'PY'
import sys
import zipfile
from pathlib import Path

output = Path(sys.argv[2]).resolve()
with zipfile.ZipFile(sys.argv[1]) as archive:
    for item in archive.infolist():
        target = (output / item.filename).resolve()
        if not target.is_relative_to(output):
            raise RuntimeError("Dafny archive member escapes the install directory")
        archive.extract(item, output)
        mode = (item.external_attr >> 16) & 0o777
        if mode:
            target.chmod(mode)
PY
chmod +x "$contract_tools/dafny/dafny"
"$contract_tools/dafny/dafny" --version
if [[ -n "${GITHUB_PATH:-}" ]]; then
  printf '%s\n' "$contract_tools/dafny" >> "$GITHUB_PATH"
fi
