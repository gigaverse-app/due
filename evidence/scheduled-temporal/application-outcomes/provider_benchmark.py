"""Sequential local provider diagnostic; no queue-throughput or disk-durability claim."""

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path


def arm(source, size, phase, count):
    spec = importlib.util.spec_from_file_location("provider", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    content = "x" * size
    with tempfile.TemporaryDirectory(prefix="due-provider-bench-") as directory:
        os.environ["DUE_OUTPUT_DIR"] = directory
        if phase == "replay":
            module.write_revision(content, "existing")
        # Warm interpreter and provider outside timing, using a different revision.
        module.write_revision(content, "warm")
        print("MEASURE_START", file=sys.stderr, flush=True)
        samples = []
        for index in range(count):
            key = "existing" if phase == "replay" else f"fresh-{index}"
            start = time.perf_counter_ns()
            result = module.write_revision(content, key)
            samples.append((time.perf_counter_ns() - start) / 1000)
            assert (Path(directory) / result).read_text() == content
        print("MEASURE_END", file=sys.stderr, flush=True)
        return {
            "source": source,
            "size": size,
            "phase": phase,
            "count": count,
            "latency_us": samples,
            "correct_content": True,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline")
    parser.add_argument("--candidate")
    parser.add_argument("--output")
    parser.add_argument("--arm")
    parser.add_argument("--size", type=int)
    parser.add_argument("--phase")
    args = parser.parse_args()
    if args.arm:
        print(json.dumps(arm(args.arm, args.size, args.phase, 30)))
    else:
        started = datetime.now(UTC).isoformat()
        rows = []
        for batch in range(4):
            for size in (8192, 1048576):
                for phase in ("fresh", "replay"):
                    order = ("baseline", "candidate") if batch % 2 == 0 else ("candidate", "baseline")
                    for name in order:
                        result = subprocess.run(
                            [
                                sys.executable,
                                __file__,
                                "--arm",
                                getattr(args, name),
                                "--size",
                                str(size),
                                "--phase",
                                phase,
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        rows.append({"arm": name, "batch": batch, **json.loads(result.stdout)})
        Path(args.output).write_text(
            json.dumps(
                {
                    "started_at": started,
                    "finished_at": datetime.now(UTC).isoformat(),
                    "source_sha256": {
                        name: hashlib.sha256(Path(getattr(args, name)).read_bytes()).hexdigest()
                        for name in ("baseline", "candidate")
                    },
                    "platform": platform.platform(),
                    "python": sys.version,
                    "method": "4 alternating batches, 30 operations each; warm page cache; separate strace invocation counts write syscall bytes, not physical disk writes; output read-back excluded from latency",
                    "rows": rows,
                },
                indent=2,
            )
        )
        for size in (8192, 1048576):
            for phase in ("fresh", "replay"):
                for name in ("baseline", "candidate"):
                    selected = [r for r in rows if (r["size"], r["phase"], r["arm"]) == (size, phase, name)]
                    values = sorted(v for r in selected for v in r["latency_us"])
                    print(
                        size,
                        phase,
                        name,
                        "p50_us",
                        round(statistics.median(values), 1),
                        "p90_us",
                        round(values[int(len(values) * 0.9) - 1], 1),
                    )
