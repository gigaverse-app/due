import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

root = Path(__file__).parent
names = ["baseline-seq", "candidate-seq", "baseline-spread", "candidate-spread"]
runs = {name: json.loads((root / name / "results.json").read_text()) for name in names}
fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), sharey=True)
for ax, phase, title in zip(axes, ["seq", "spread"], ["Sequential arrivals", "Varied arrival phase"], strict=True):
    for x, (label, arm, color) in enumerate(
        [
            ("baseline", "due", "#bc5d32"),
            ("candidate", "due", "#197f68"),
            ("baseline", "temporal", "#707bad"),
            ("candidate", "temporal", "#a48ec5"),
        ]
    ):
        samples = [s["lateness_ms"] for s in runs[f"{label}-{phase}"]["samples"] if s["arm"] == arm]
        offsets = [(i % 5 - 2) * 0.045 for i in range(len(samples))]
        ax.scatter([x + v for v in offsets], samples, color=color, s=32, alpha=0.8)
        ax.plot([x - 0.3, x + 0.3], [statistics.median(samples)] * 2, color="black", linewidth=2)
    ax.set_xticks(range(4), ["Due\nbefore", "Due\nafter", "Temporal\nbaseline run", "Temporal\ncandidate run"])
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.22)
    ax.set_yscale("log")
axes[0].set_ylabel("Observed deadline lateness (ms, log scale)")
fig.suptitle("Transient deadline hints: all 120 local samples, with medians")
fig.text(
    0.5,
    0.01,
    "15 samples per group • Due verifies file + product state; Temporal completes a timer • shared host; not capacity evidence",
    ha="center",
    fontsize=8,
)
fig.tight_layout(rect=[0, 0.04, 1, 0.95])
fig.savefig(root / "lateness.png", dpi=170)
