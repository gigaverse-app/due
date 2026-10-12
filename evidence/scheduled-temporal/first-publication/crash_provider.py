"""Same partial-write fault against old and new provider, in actual child processes."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CHILD = """
import importlib.util
import os
import sys
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
spec = importlib.util.spec_from_file_location("provider", sys.argv[1])
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)
if sys.argv[2] == "replay":
    provider.write_revision("Complete edition", "revision")
original = Path.open

class CutWrite:
    def __init__(self, file):
        self.file = file
    def write(self, content):
        self.file.write(content[:3])
        self.file.flush()
        os._exit(73)

@contextmanager
def interrupted(path, mode="r", *args, **kwargs):
    with original(path, mode, *args, **kwargs) as file:
        yield CutWrite(file) if any(flag in mode for flag in "wx") else file

with patch.object(Path, "open", interrupted):
    provider.write_revision("Complete edition", "revision")
"""

root = Path(__file__).parent
rows = []
for arm, source in [("baseline", root / "storage_baseline.py"), ("candidate", root / "storage_candidate.py")]:
    for phase in ("first", "replay"):
        with tempfile.TemporaryDirectory() as directory:
            child = subprocess.run(
                [sys.executable, "-c", CHILD, str(source), phase],
                env={**os.environ, "DUE_OUTPUT_DIR": directory},
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert child.returncode in (0, 73), child.stderr
            path = Path(directory) / hashlib.sha256(b"revision").hexdigest()
            content = path.read_text() if path.exists() else None
            row = {"arm": arm, "phase": phase, "exit_code": child.returncode, "published_path_content": content}
            rows.append(row)
            print(row)
assert rows == [
    {"arm": "baseline", "phase": "first", "exit_code": 73, "published_path_content": None},
    {"arm": "baseline", "phase": "replay", "exit_code": 0, "published_path_content": "Complete edition"},
    {"arm": "candidate", "phase": "first", "exit_code": 73, "published_path_content": None},
    {"arm": "candidate", "phase": "replay", "exit_code": 0, "published_path_content": "Complete edition"},
]
(root / "crash-results.json").write_text(json.dumps(rows, indent=2))
