import json
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).parent
rows = []
for size in (8192, 1048576):
    for phase in ("fresh", "replay"):
        for arm, source in [("baseline", root / "storage_baseline.py"), ("candidate", root / "storage_candidate.py")]:
            trace = root / f"trace-{arm}-{size}-{phase}.txt"
            subprocess.run(
                [
                    "strace",
                    "-yy",
                    "-e",
                    "trace=write,pwrite64,mkdir,rmdir,unlink,unlinkat,link,linkat,getdents64",
                    "-o",
                    str(trace),
                    sys.executable,
                    str(root / "provider_benchmark.py"),
                    "--arm",
                    str(source),
                    "--size",
                    str(size),
                    "--phase",
                    phase,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            active = False
            sizes = []
            calls = {}
            for line in trace.read_text().splitlines():
                if "MEASURE_START" in line:
                    active = True
                elif "MEASURE_END" in line:
                    active = False
                elif active and "/tmp/due-provider-bench-" in line:
                    syscall = line.split("(", 1)[0]
                    calls[syscall] = calls.get(syscall, 0) + 1
                    if syscall in ("write", "pwrite64"):
                        match = re.search(r"= (\d+)$", line)
                        assert match, line
                        sizes.append(int(match.group(1)))
            row = {
                "arm": arm,
                "size": size,
                "phase": phase,
                "operations": 30,
                "syscall_counts": calls,
                "write_syscalls": len(sizes),
                "bytes_written": sum(sizes),
            }
            rows.append(row)
            print(row)
(root / "write-counts.json").write_text(json.dumps(rows, indent=2))
