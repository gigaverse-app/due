import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt

root = Path(__file__).parent
rows = json.loads((root / "provider-results-final.json").read_text())["rows"]
fig, ax = plt.subplots(figsize=(10, 5))
groups = [(8192, "fresh"), (8192, "replay"), (1048576, "fresh"), (1048576, "replay")]
for offset, arm, color in [(-0.2, "baseline", "#68778b"), (0.2, "candidate", "#087f8c")]:
    medians = [
        statistics.median(v for r in rows if (r["size"], r["phase"], r["arm"]) == (*g, arm) for v in r["latency_us"])
        for g in groups
    ]
    positions = [i + offset for i in range(4)]
    bars = ax.bar(positions, medians, width=0.36, color=color, label=arm)
    for position, median, group in zip(positions, medians, groups, strict=True):
        ax.text(position, median * 1.18, f"{median:.1f}", ha="center", fontsize=9)
        batch = [statistics.median(r["latency_us"]) for r in rows if (r["size"], r["phase"], r["arm"]) == (*group, arm)]
        ax.scatter([position] * len(batch), batch, marker="_", color="black", alpha=0.65, zorder=3)
ax.set_yscale("log")
ax.set_ylim(3, 6000)
ax.set_ylabel("Provider call microseconds (log scale)")
ax.set_xticks(
    range(4), ["8 KiB\nfirst publication", "8 KiB\nequal replay", "1 MiB\nfirst publication", "1 MiB\nequal replay"]
)
ax.set_title("Same atomic publication, less staging overhead")
ax.legend()
ax.spines[["top", "right"]].set_visible(False)
fig.text(
    0.08,
    0.02,
    "120 sequential calls per arm/case; bars = pooled p50, marks = four batch medians. Local WSL; no engine-throughput claim.",
    fontsize=9,
)
fig.tight_layout(rect=(0, 0.05, 1, 1))
fig.savefig(root / "provider-cost.png", dpi=160)
