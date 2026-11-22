"""Chapter 5 figures with matplotlib, as PNG and SVG (`make results`). Analysis only.

matplotlib is optional (experiments/analysis/requirements.txt): results.py imports this module only
when matplotlib is installed, and otherwise draws F1-F3 with the standard-library svg.py and says in
RESULTS.md which figures are missing. Five figures, one draw each, Agg backend: no interactive
loop, no animation, light on CPU.

Deterministic: the SVG hash salt is fixed and no date or software tag is written, so the same input
gives the same bytes on the same matplotlib version.
"""

from __future__ import annotations

import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update(
    {
        "svg.hashsalt": "nexus-results",
        "svg.fonttype": "none",  # text stays text in the SVG
        "font.size": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "x",
        "grid.color": "#dddddd",
        "grid.linewidth": 0.6,
        "axes.axisbelow": True,
    }
)

# Fixed per series: a phase or an arm has the same colour in every figure.
PALETTE = [
    "#4C78A8",
    "#F58518",
    "#54A24B",
    "#E45756",
    "#72B7B2",
    "#B279A2",
    "#9D755D",
    "#BAB0AC",
]
SVG_META = {"Date": None, "Creator": None}
PNG_META = {"Software": None}
DPI = 150


def _fig(n_rows: int):
    return plt.subplots(figsize=(7.6, max(2.2, 1.3 + 0.32 * max(n_rows, 1))))


def _finish(fig, ax, title: str, banner: str, out: pathlib.Path, fid: str) -> list[str]:
    ax.set_title(title, loc="left", fontsize=10, fontweight="bold")
    if banner:
        fig.text(
            0.99,
            0.01,
            banner,
            ha="right",
            va="bottom",
            color="#B00020",
            fontsize=9,
            fontweight="bold",
        )
    fig.tight_layout()
    fig.savefig(out / f"{fid}.svg", metadata=SVG_META)
    fig.savefig(out / f"{fid}.png", dpi=DPI, metadata=PNG_META)
    plt.close(fig)
    return [f"{fid}.png", f"{fid}.svg"]


def _empty(ax, text="No data"):
    ax.text(
        0.5,
        0.5,
        text,
        ha="center",
        va="center",
        transform=ax.transAxes,
        color="#666666",
    )
    ax.set_yticks([])
    ax.set_xticks([])


def stacked(out, fid, title, rows, series, unit, banner=""):
    """rows: [(label, [value per series])]; horizontal stacked bars."""
    fig, ax = _fig(len(rows))
    if not rows:
        _empty(ax)
    else:
        labels = [r[0] for r in rows][::-1]
        vals = [r[1] for r in rows][::-1]
        left = [0.0] * len(rows)
        for j, name in enumerate(series):
            w = [v[j] for v in vals]
            ax.barh(
                labels,
                w,
                left=left,
                color=PALETTE[j % len(PALETTE)],
                label=name,
                height=0.6,
            )
            left = [a + b for a, b in zip(left, w, strict=True)]
        ax.set_xlabel(unit.strip() or "count")
        ax.legend(
            ncol=min(len(series), 5),
            fontsize=8,
            frameon=False,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.32),  # below the axis label: never over the title
        )
    return _finish(fig, ax, title, banner, out, fid)


def intervals(out, fid, title, groups, banner="", xlabel="proportion"):
    """groups: {series: [(label, p, lo, hi)]}; a point with its Wilson 95 % interval per label.
    Several series are offset vertically so they do not overlap."""
    labels = sorted({row[0] for rows in groups.values() for row in rows})
    fig, ax = _fig(len(labels))
    pts = [(s, r) for s, rows in groups.items() for r in rows if r[1] is not None]
    if not pts:
        _empty(ax)
    else:
        pos = {lab: len(labels) - 1 - i for i, lab in enumerate(labels)}
        n = len(groups)
        for j, (name, rows) in enumerate(groups.items()):
            off = (j - (n - 1) / 2) * 0.22 if n > 1 else 0.0
            rows = [r for r in rows if r[1] is not None]
            y = [pos[r[0]] + off for r in rows]
            x = [r[1] for r in rows]
            # clamp: at k = n the Wilson bound can sit a rounding error below p
            err = [
                [max(0.0, r[1] - r[2]) for r in rows],
                [max(0.0, r[3] - r[1]) for r in rows],
            ]
            ax.errorbar(
                x,
                y,
                xerr=err,
                fmt="o",
                ms=4,
                capsize=3,
                color=PALETTE[j % len(PALETTE)],
                label=name,
            )
        ax.set_yticks(list(pos.values()), list(pos.keys()))
        ax.set_xlim(-0.02, 1.02)
        ax.set_xlabel(xlabel)
        if n > 1:
            ax.legend(
                fontsize=8,
                frameon=False,
                loc="upper center",
                bbox_to_anchor=(0.5, -0.32),
                ncol=n,
            )
    return _finish(fig, ax, title, banner, out, fid)


def medians(out, fid, title, rows, banner="", xlabel="seconds"):
    """rows: [(label, {median, q1, q3}, [values])]; median with its IQR, the values as dots."""
    fig, ax = _fig(len(rows))
    rows = [r for r in rows if r[1]]
    if not rows:
        _empty(ax)
    else:
        for i, (_, d, xs) in enumerate(rows[::-1]):
            ax.plot(
                [d["q1"], d["q3"]],
                [i, i],
                color=PALETTE[0],
                lw=5,
                alpha=0.35,
                solid_capstyle="butt",
            )
            ax.plot([d["median"]], [i], "|", color=PALETTE[0], ms=14, mew=2)
            ax.plot(xs, [i] * len(xs), "o", color="#333333", ms=3)
        ax.set_yticks(range(len(rows)), [r[0] for r in rows[::-1]])
        ax.set_xlabel(xlabel)
    return _finish(fig, ax, title, banner, out, fid)
