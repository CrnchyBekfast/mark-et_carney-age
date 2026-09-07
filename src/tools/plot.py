#!/usr/bin/env python3
"""
Experiment 7 figures P1 / P2 / P3 from the CSVs produced by exp7_csv.py.

    python3 src/tools/plot.py <csv-prefix> [--outdir DIR] [--slow-sid N]

    P1_pending.png   per-connection userspace backlog vs time
    P2_engine.png    matching-engine latency vs time, onset marked
    P3_queued_sent.png  cumulative queued vs sent for the backpressured sid

Design notes (why it looks the way it does):
  * One y-axis per figure. P2's two series are the same measure (latency) and
    P3's are the same measure (bytes), so neither needs -- or gets -- a second
    scale. A dual-axis chart is the one thing never to draw.
  * Categorical hues assigned in fixed order per entity, never cycled, so a
    connection keeps its colour across all three figures.
  * Every series carries a direct end-of-line label as well as a legend:
    identity is never colour-alone, which also discharges the contrast relief
    rule for the lighter hues on a white page.
  * Grid and axes are recessive; the data is the only saturated ink.
"""

import csv
import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Validated categorical palette, light surface, fixed order.
# node scripts/validate_palette.js "#2a78d6,#eb6834,#1baf7a,#eda100" --mode light
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#d8d7d2"
SURFACE = "#ffffff"

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK_2,
    "axes.titlesize": 11,
    "axes.labelsize": 9,
    "xtick.color": INK_2,
    "ytick.color": INK_2,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "legend.frameon": False,
    "font.size": 9,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
})


def style(ax, title, xlabel, ylabel):
    ax.set_title(title, color=INK, loc="left", pad=10)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", alpha=0.7)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def endlabels(ax, items):
    """
    Direct labels at the right-hand end of each line -- identity without
    relying on colour alone, which also discharges the contrast relief rule
    for the lighter hues on a white page.

    `items` is [(x, y, text, colour)]. Labels are nudged apart vertically when
    series converge, so flat near-zero lines do not stack their labels on top
    of one another.
    """
    rows = [(y[-1], x[-1], text, colour)
            for x, y, text, colour in items if len(x)]
    if not rows:
        return
    rows.sort()
    lo, hi = ax.get_ylim()
    min_gap = (hi - lo) * 0.055
    placed = []
    for yv, xv, text, colour in rows:
        if placed and yv - placed[-1] < min_gap:
            yv = placed[-1] + min_gap
        placed.append(yv)
        ax.annotate(text, xy=(xv, yv), xytext=(5, 0),
                    textcoords="offset points", color=colour,
                    fontsize=8, va="center", fontweight="medium",
                    annotation_clip=False)


def read_conn(prefix):
    series = defaultdict(lambda: {"t": [], "pending": [], "q": [], "s": []})
    with open(prefix + "_conn.csv") as fh:
        for r in csv.DictReader(fh):
            d = series[int(r["sid"])]
            d["t"].append(float(r["t_ms"]) / 1000.0)
            d["pending"].append(int(r["pending"]))
            d["q"].append(int(r["cum_queued"]))
            d["s"].append(int(r["cum_sent"]))
    return series


def read_engine(prefix):
    t, p50, p99 = [], [], []
    with open(prefix + "_engine.csv") as fh:
        for r in csv.DictReader(fh):
            t.append(float(r["t_ms"]) / 1000.0)
            p50.append(int(r["p50_ns"]) / 1000.0)
            p99.append(int(r["p99_ns"]) / 1000.0)
    # The final bucket is partial -- it holds however many calls landed before
    # the run stopped -- so its percentiles are computed over a small sample
    # and dive at the right edge. Drop it rather than ship a tail artifact.
    if len(t) > 2:
        t, p50, p99 = t[:-1], p50[:-1], p99[:-1]
    return t, p50, p99


def read_onset(prefix):
    try:
        with open(prefix + "_events.csv") as fh:
            for r in csv.DictReader(fh):
                if r["ev"] == "send_would_block":
                    return float(r["t_ms"]) / 1000.0, int(r["sid"])
    except OSError:
        pass
    return None, None


def main():
    argv = sys.argv[1:]
    outdir = "."
    slow_sid = None
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--outdir":
            outdir = argv[i + 1]
            i += 2
        elif argv[i] == "--slow-sid":
            slow_sid = int(argv[i + 1])
            i += 2
        elif argv[i].startswith("--"):
            i += 2
        else:
            args.append(argv[i])
            i += 1
    if len(args) != 1:
        sys.stderr.write(__doc__)
        raise SystemExit(2)

    prefix = args[0]
    os.makedirs(outdir, exist_ok=True)

    conn = read_conn(prefix)
    onset_t, onset_sid = read_onset(prefix)
    if slow_sid is None:
        slow_sid = onset_sid
    if slow_sid is None and conn:
        slow_sid = max(conn, key=lambda s: max(conn[s]["pending"] or [0]))

    sids = sorted(conn)
    colour = {sid: SERIES[i % len(SERIES)] for i, sid in enumerate(sids)}

    def label(sid):
        tag = "  <- backpressured" if sid == slow_sid else ""
        return "sid %d%s" % (sid, tag)

    # ---- P1: per-connection userspace backlog --------------------------
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    for sid in sids:
        d = conn[sid]
        ax.plot(d["t"], d["pending"], lw=2, color=colour[sid],
                label="sid %d" % sid, solid_capstyle="round")
    endlabels(ax, [(conn[s]["t"], conn[s]["pending"], "sid %d" % s, colour[s])
                   for s in sids])
    if onset_t is not None:
        ax.axvline(onset_t, color=INK_2, lw=1, ls="--", alpha=0.7)
        ax.annotate("first EWOULDBLOCK", xy=(onset_t, ax.get_ylim()[1]),
                    xytext=(4, -10), textcoords="offset points",
                    fontsize=8, color=INK_2, va="top")
    style(ax, "P1  Per-connection userspace backlog (wbuf pending)",
          "time (s)", "pending bytes")
    ax.legend(loc="upper left", ncols=len(sids))
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "P1_pending.png"), dpi=200)
    plt.close(fig)

    # ---- P2: engine latency, unaffected by the onset -------------------
    t, p50, p99 = read_engine(prefix)
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.plot(t, p99, lw=2, color=SERIES[1], label="p99")
    ax.plot(t, p50, lw=2, color=SERIES[0], label="p50")
    endlabels(ax, [(t, p99, "p99", SERIES[1]), (t, p50, "p50", SERIES[0])])
    if onset_t is not None:
        ax.axvline(onset_t, color=INK_2, lw=1, ls="--", alpha=0.7)
        ax.annotate("first EWOULDBLOCK", xy=(onset_t, ax.get_ylim()[1]),
                    xytext=(4, -10), textcoords="offset points",
                    fontsize=8, color=INK_2, va="top")
    style(ax, "P2  Matching-engine latency is unchanged across the onset",
          "time (s)", "engine.handle() latency (µs)")
    ax.legend(loc="upper left", ncols=2)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "P2_engine.png"), dpi=200)
    plt.close(fig)

    # ---- P3: cumulative queued vs sent for the backpressured sid -------
    if slow_sid in conn:
        d = conn[slow_sid]
        backlog = [q - s for q, s in zip(d["q"], d["s"])]
        # Two stacked panels sharing one x-axis, NOT two y-scales on one plot.
        # The backlog is ~1.5 KB against ~27 MB of cumulative traffic -- four
        # orders of magnitude -- so on a single pair of cumulative lines the
        # gap is invisible and the figure says nothing. The top panel makes
        # the point that delivery keeps up overall; the bottom panel is where
        # the backpressure structure actually lives.
        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(7.2, 5.0), sharex=True,
            gridspec_kw={"height_ratios": [1.15, 1]})

        ax1.plot(d["t"], d["q"], lw=2.6, color=SERIES[1],
                 label="cumulative queued")
        ax1.plot(d["t"], d["s"], lw=1.4, color=SERIES[0],
                 label="cumulative sent")
        endlabels(ax1, [(d["t"], d["q"], "queued", SERIES[1]),
                        (d["t"], d["s"], "sent", SERIES[0])])
        style(ax1, "P3  Cumulative queued vs sent, sid %d" % slow_sid,
              "", "bytes")
        ax1.legend(loc="upper left", ncols=2)

        ax2.fill_between(d["t"], 0, backlog, color=SERIES[1],
                         alpha=0.20, linewidth=0)
        ax2.plot(d["t"], backlog, lw=2, color=SERIES[1])
        endlabels(ax2, [(d["t"], backlog, "backlog", SERIES[1])])
        style(ax2, "queued − sent (the userspace backlog, same x-axis)",
              "time (s)", "bytes")

        for ax in (ax1, ax2):
            if onset_t is not None:
                ax.axvline(onset_t, color=INK_2, lw=1, ls="--", alpha=0.7)
        if onset_t is not None:
            ax2.annotate("first EWOULDBLOCK", xy=(onset_t, ax2.get_ylim()[1]),
                         xytext=(4, -10), textcoords="offset points",
                         fontsize=8, color=INK_2, va="top")
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, "P3_queued_sent.png"), dpi=200)
        plt.close(fig)

    print("wrote P1_pending.png, P2_engine.png, P3_queued_sent.png to %s/"
          % outdir)
    print("slow/backpressured sid = %s   onset = %s s"
          % (slow_sid, ("%.3f" % onset_t) if onset_t is not None else "none"))


if __name__ == "__main__":
    main()
