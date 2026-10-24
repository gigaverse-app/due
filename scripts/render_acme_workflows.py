"""Render the tutorial's customer goals, external disruptions and recovery paths."""

import argparse
import html
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "documentation/assets/acme"
STAGES = (
    (
        "1. Get an estimate",
        "Willow Cafe wants a quote for 500 brochures.",
        "Upload revision 17; commit its copy and word-count task.",
        "Worker counts four words. Acme can prepare the estimate.",
        "A notification can be lost before rendering starts. At minute 7, a restart can also interrupt unfinished work.",
        "The upload exists, but the customer keeps waiting for an estimate.",
        "Due keeps the committed task in PostgreSQL. After service returns, Beat finds it and a worker finishes it.",
    ),
    (
        "2. Approve the right proof",
        "Willow Cafe changes its opening date from June 12 to June 19.",
        "Render revision 18 and publish its immutable proof.",
        "The customer reviews June 19, the date they actually requested.",
        "Without a restart, the slow old render returns at minute 10. In the restart branch, it dies at minute 7.",
        "An old worker publishes June 12 over the corrected proof.",
        "Separate output keys preserve both versions. Due's settlement fence lets only the current request publish the pointer.",
    ),
    (
        "3. Share the corrected proof",
        "Willow Cafe wants its colleagues to approve June 19 in the hosted portal.",
        "Send revision 18's proof pointer to the approval portal.",
        "Everyone sees the corrected opening date.",
        "A request for an earlier, already published proof is in flight. It survives the minute-7 client restart and lands late.",
        "The portal displays June 12 even though Acme's own database says June 19.",
        "Projection retains the old attempt's repair debt and reapplies revision 18. The remote view converges after recovery.",
    ),
    (
        "4. Order exactly 500 copies",
        "Willow Cafe approves revision 18 and orders 500 brochures.",
        "Printer accepts one order; Acme records its matching receipt.",
        "One purchase is accepted. Printing and shipping are tracked separately.",
        "At minute 7, the client restarts after printer acceptance, losing the reply. No ink before acceptance is a separate case.",
        "Blind retry after a lost reply buys another 500 copies. Treating acceptance as delivery hides an ink delay.",
        "Unknown outcome: observe the receipt before resending. Definite refusal: retry under policy. Ink after acceptance still needs the printer's fulfillment process.",
    ),
    (
        "5. Finish without stranded files",
        "Willow Cafe wants its proof published; Acme needs temporary spool space reclaimed.",
        "Reserve cleanup, render, remove the spool, then commit proof and portal follow-up.",
        "The proof remains available and temporary storage is free for the next customer.",
        "At minute 7, the rendering worker dies with temporary bytes left behind. Its finally block cannot run.",
        "A finally block never runs. Temporary files accumulate; later customers cannot render.",
        "Cleanup was committed before rendering. Recovery deletes disposable bytes and keeps checking until completion evidence rules out later writes.",
    ),
    (
        "6. Keep the promises after changes",
        "Acme wants the next deployment to preserve the customer's proof and single order.",
        "Bind application contracts; generate and run the shared failure histories in CI.",
        "Changes are checked against actual customer outcomes and independently stored provider records.",
        "A deployment changes behavior at the minute-2 correction or minute-7 restart. Success-only tests miss the damage.",
        "The next outage exposes duplicate purchases, stale proofs or abandoned files.",
        "Contract-generated tests exercise declared failure cases. Enrollment checks catch omitted suites; application observations detect the wrong result.",
    ),
)


def text(x, y, value, *, size=28, color="#163449", bold=False, width=37):
    lines = textwrap.wrap(value, width=width)
    weight = "700" if bold else "400"
    return "\n".join(
        f'<text x="{x}" y="{y + index * (size + 9)}" fill="{color}" font-size="{size}" font-weight="{weight}">{html.escape(line)}</text>'
        for index, line in enumerate(lines)
    )


def card(x, y, width, height, label, body, *, fill, stroke, chars=31):
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="16" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
        + text(x + 24, y + 35, label, size=20, color=stroke, bold=True, width=80)
        + text(x + 24, y + 77, body, width=chars)
    )


def render(stage):
    title, goal, happy, result, event, bad, response = stage
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1620" viewBox="0 0 1200 1620">',
        "<style>text{font-family:DejaVu Sans,Arial,sans-serif}</style>",
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#62778a"/></marker></defs>',
        '<rect width="1200" height="1620" fill="#f3f6fa"/>',
        text(32, 46, "ACME PRINTWORKS  /  WILLOW CAFE  /  JOB 418", size=19, color="#137b80", bold=True, width=100),
        text(32, 100, title, size=37, bold=True, width=70),
        card(32, 130, 1136, 145, "CUSTOMER GOAL", goal, fill="#eaf2ff", stroke="#345986", chars=75),
        '<rect x="32" y="300" width="1136" height="380" rx="16" fill="#ffffff" stroke="#94a8bc" stroke-width="2"/>',
        text(56, 338, "THE SAME TEN-MINUTE WINDOW  /  EVENT ORDER, NOT TO SCALE", size=20, bold=True, width=100),
        '<path d="M90 390H1090" stroke="#62778a" stroke-width="4"/>',
        text(56, 588, "Restart branch: the killed render never returns at minute 10.", size=23, bold=True, width=90),
        text(
            56,
            627,
            "Due keeps r18 current; recovery picks up owed work after lease expiry. A new attempt reruns captured inputs; it does not resume a PDF at page 7.",
            size=23,
            width=90,
        ),
        card(32, 720, 548, 245, "HAPPY PATH", happy, fill="#ebf7f1", stroke="#24734d"),
        card(620, 720, 548, 245, "EXTERNAL DISRUPTION", event, fill="#fff5df", stroke="#946321"),
        card(32, 1015, 548, 260, "GOOD OUTCOME", result, fill="#ebf7f1", stroke="#24734d"),
        card(620, 1015, 548, 260, "BAD OUTCOME WITHOUT PROTECTION", bad, fill="#fff0ee", stroke="#aa4439"),
        card(32, 1320, 1136, 270, "DUE RESPONSE AND ITS LIMIT", response, fill="#e9f5f6", stroke="#137b80", chars=70),
    ]
    for x, minute, event_label in (
        (56, "0 MIN", "r17 starts its slow render"),
        (276, "2 MIN", "Customer corrects date + font: r18"),
        (496, "3 MIN", "Fast r18 finishes; publish June 19"),
        (716, "7 MIN", "Restart branch: old worker dies"),
        (936, "10 MIN", "No-restart branch: r17 returns late"),
    ):
        parts.append(f'<circle cx="{x + 34}" cy="390" r="9" fill="#137b80"/>')
        parts.append(text(x, 430, minute, size=23, bold=True, width=20))
        parts.append(text(x, 465, event_label, size=22, width=17))
    for path in ("M306 965V1005", "M894 965V1005", "M894 1275V1310"):
        parts.append(f'<path d="{path}" fill="none" stroke="#62778a" stroke-width="3" marker-end="url(#arrow)"/>')
    parts.append("</svg>\n")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--png", action="store_true")
    args = parser.parse_args()
    ASSETS.mkdir(parents=True, exist_ok=True)
    for index, stage in enumerate(STAGES, 1):
        source = render(stage)
        (ASSETS / f"stage-{index}.svg").write_text(source)
        if args.png:
            import resvg_py  # pyrefly: ignore[missing-import]  # Optional rendering tool: resvg-py==0.2.6.

            (ASSETS / f"stage-{index}.png").write_bytes(resvg_py.svg_to_bytes(svg_string=source))
    print(f"Rendered {len(STAGES)} Acme workflow diagrams")


if __name__ == "__main__":
    main()
