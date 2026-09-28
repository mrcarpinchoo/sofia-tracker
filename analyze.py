"""
SOFIA - Results analysis
------------------------
Reads every results/*.csv written by evaluate.py and produces:

  results/summary.md    Markdown tables (accuracy, latency, FPS, false positives)
  results/confusion.png Confusion matrix heatmap (expected x detected)

    python analyze.py [--dir results]
"""

import argparse
import csv
import glob
import os
from collections import Counter, defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

GESTURES = ["INDEX", "V", "PALM", "SWIPE_LEFT", "SWIPE_RIGHT", "GRAB"]
LIGHT_ORDER = ["bright", "normal", "dim", "backlight"]


def load(directory):
    rows = []
    for path in sorted(glob.glob(os.path.join(directory, "*.csv"))):
        with open(path, newline="") as f:
            rows.extend(csv.DictReader(f))
    return rows


def fnum(row, key):
    try:
        return float(row[key])
    except (KeyError, ValueError):
        return None


def mean(values):
    values = [v for v in values if v is not None]
    return float(np.mean(values)) if values else None


def fmt(v, digits=2, suffix=""):
    return "-" if v is None else f"{v:.{digits}f}{suffix}"


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def sort_keys(keys, order=None):
    if order:
        return sorted(keys, key=lambda k: order.index(k) if k in order else len(order))
    return sorted(keys)


def accuracy_by(trials, key, order=None):
    groups = defaultdict(list)
    for r in trials:
        groups[r[key]].append(int(r["correct"]))
    return [(k, sum(v), len(v), sum(v) / len(v)) for k, v in
            ((k, groups[k]) for k in sort_keys(groups, order))]


def acc_table(trials, key, label, order=None):
    rows = [(k, f"{c}/{n}", f"{a:.1%}") for k, c, n, a in accuracy_by(trials, key, order)]
    return table([label, "Correct", "Accuracy"], rows)


def extra_count(row):
    return len(row["extra_events"].split("|")) if row["extra_events"] else 0


def confusion(trials):
    labels_x = GESTURES
    seen = {r["detected"] for r in trials}
    labels_y = GESTURES + (["NONE"] if "NONE" in seen else []) + sorted(
        seen - set(GESTURES) - {"NONE"})
    m = np.zeros((len(labels_x), len(labels_y)), dtype=int)
    for r in trials:
        if r["expected"] in labels_x and r["detected"] in labels_y:
            m[labels_x.index(r["expected"]), labels_y.index(r["detected"])] += 1
    return labels_x, labels_y, m


def confusion_md(labels_x, labels_y, m):
    rows = [[lx] + [int(v) for v in m[i]] for i, lx in enumerate(labels_x)]
    return table(["expected \\ detected"] + labels_y, rows)


def plot_confusion(trials_by_mode, path):
    modes = list(trials_by_mode)
    fig, axes = plt.subplots(1, len(modes), figsize=(6.5 * len(modes), 5.5),
                             squeeze=False)
    for ax, mode in zip(axes[0], modes):
        lx, ly, m = confusion(trials_by_mode[mode])
        norm = m / np.maximum(m.sum(axis=1, keepdims=True), 1)
        im = ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
        ax.set_xticks(range(len(ly)), ly, rotation=45, ha="right")
        ax.set_yticks(range(len(lx)), lx)
        ax.set_xlabel("detected")
        ax.set_ylabel("expected")
        ax.set_title(f"Confusion matrix ({mode})")
        for i in range(len(lx)):
            for j in range(len(ly)):
                ax.text(j, i, m[i, j], ha="center", va="center",
                        color="white" if norm[i, j] > 0.5 else "black")
        fig.colorbar(im, ax=ax, fraction=0.046, label="row fraction")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def build_summary(rows):
    trials = [r for r in rows if r["expected"] != "IDLE"]
    idle = [r for r in rows if r["expected"] == "IDLE"]
    md = ["# Results summary\n"]

    users = sorted({r["user"] for r in rows})
    md.append(f"- Sessions (CSV rows): {len(rows)} ({len(trials)} trials, {len(idle)} idle)")
    md.append(f"- Users: {', '.join(users)}")
    md.append(f"- Overall accuracy: "
              f"{sum(int(r['correct']) for r in trials)}/{len(trials)} = "
              f"{sum(int(r['correct']) for r in trials) / max(len(trials), 1):.1%}\n")

    # Group by smoothing mode, so on/off can be compared
    modes = sort_keys({r["smoothing"] for r in rows}, ["smooth", "nosmooth"])
    trials_by_mode = {m: [r for r in trials if r["smoothing"] == m] for m in modes}

    md.append("## Accuracy by smoothing\n")
    md.append(acc_table(trials, "smoothing", "Mode", ["smooth", "nosmooth"]))
    md.append("## Accuracy by gesture\n")
    md.append(acc_table(trials, "expected", "Gesture", GESTURES))
    if len(modes) > 1:
        md.append("### Accuracy by gesture and smoothing\n")
        per = {m: {k: a for k, _, _, a in accuracy_by(t, "expected", GESTURES)}
               for m, t in trials_by_mode.items()}
        md.append(table(["Gesture"] + modes, [
            [g] + [f"{per[m][g]:.1%}" if g in per[m] else "-" for m in modes]
            for g in GESTURES]))
    md.append("## Accuracy by user\n")
    md.append(acc_table(trials, "user", "User"))
    md.append("## Accuracy by lighting condition\n")
    md.append(acc_table(trials, "light", "Light", LIGHT_ORDER))

    md.append("## Lighting conditions (measured)\n")
    light_rows = []
    for light in sort_keys({r["light"] for r in rows}, LIGHT_ORDER):
        sel = [r for r in rows if r["light"] == light]
        light_rows.append((light, fmt(mean(fnum(r, "brightness") for r in sel), 1),
                           fmt(100 * mean(fnum(r, "detect_rate") for r in sel), 1, " %")))
    md.append(table(["Light", "Mean brightness (0-255)", "Hand detection rate"],
                    light_rows))

    md.append("## Latency, FPS and processing time\n")
    perf = []
    for m in modes:
        sel = trials_by_mode[m]
        lat = [fnum(r, "latency_s") for r in sel if int(r["correct"])]
        perf.append((m, fmt(mean(lat), 2, " s"), fmt(mean(fnum(r, "fps") for r in sel), 1),
                     fmt(mean(fnum(r, "proc_ms") for r in sel), 1, " ms")))
    md.append(table(["Mode", "Mean latency (correct)", "Mean FPS",
                     "Mean processing time/frame"], perf))
    md.append("### Latency by gesture (correct detections only)\n")
    lat_rows = []
    for g in GESTURES:
        cells = [g]
        for m in modes:
            lat = [fnum(r, "latency_s") for r in trials_by_mode[m]
                   if r["expected"] == g and int(r["correct"])]
            cells.append(fmt(mean(lat), 2, " s"))
        lat_rows.append(cells)
    md.append(table(["Gesture"] + modes, lat_rows))

    md.append("## False positives (idle segment) and spurious events\n")
    fp_rows = []
    for m in modes:
        sel = [r for r in idle if r["smoothing"] == m]
        fps_count = sum((r["detected"] != "NONE") + extra_count(r) for r in sel)
        minutes = sum(fnum(r, "duration_s") or 0 for r in sel) / 60
        extra = sum(extra_count(r) for r in trials_by_mode[m])
        fp_rows.append((m, fps_count, f"{minutes:.1f}",
                        fmt(fps_count / minutes if minutes else None, 1),
                        extra))
    md.append(table(["Mode", "Idle FPs", "Idle minutes", "FPs/min",
                     "Extra events in trials"], fp_rows))

    md.append("## Per-gesture false negatives / false positives\n")
    fnfp = []
    for g in GESTURES:
        fn = sum(1 for r in trials if r["expected"] == g and r["detected"] != g)
        fp = sum(1 for r in trials if r["expected"] != g and r["detected"] == g)
        fnfp.append((g, fn, fp))
    md.append(table(["Gesture", "False negatives", "False positives"], fnfp))

    md.append("## Confusion matrices (expected x detected)\n")
    for m in modes:
        md.append(f"### {m}\n")
        md.append(confusion_md(*confusion(trials_by_mode[m])))
    return "\n".join(md), trials_by_mode


def main():
    ap = argparse.ArgumentParser(description="SOFIA results analysis")
    ap.add_argument("--dir", default="results")
    args = ap.parse_args()

    rows = load(args.dir)
    if not rows:
        raise SystemExit(f"No CSV rows found in {args.dir}/")

    summary, trials_by_mode = build_summary(rows)
    md_path = os.path.join(args.dir, "summary.md")
    png_path = os.path.join(args.dir, "confusion.png")
    with open(md_path, "w") as f:
        f.write(summary)
    plot_confusion(trials_by_mode, png_path)
    print(f"Wrote {md_path} and {png_path}")


if __name__ == "__main__":
    main()
