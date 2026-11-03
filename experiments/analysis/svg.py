"""Minimal SVG charts, standard library only, deterministic (no clock, no random, fixed precision).

Two chart kinds are enough for Chapter 5: stacked horizontal bars (MTTR decomposition, terminal
counts) and interval plots (a proportion with its Wilson interval). Colours are fixed per series so a
figure reads the same in the report and in a browser; text is escaped.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

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
W, LEFT, RIGHT, ROW, TOP = 760, 190, 30, 26, 46


def _f(x: float) -> str:
    return f"{x:.1f}"


def _frame(title: str, height: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 0 {W} {height}" '
        f'font-family="sans-serif" font-size="12">'
        f'<rect width="{W}" height="{height}" fill="#ffffff"/>'
        f'<text x="{LEFT}" y="22" font-size="14" font-weight="bold">{escape(title)}</text>{body}</svg>\n'
    )


def stacked_bars(
    title: str, rows: list[tuple[str, list[float]]], series: list[str], unit: str = ""
) -> str:
    """rows: (label, [value per series]); one bar per row, drawn in order."""
    top = max((sum(v) for _, v in rows), default=0) or 1
    scale = (W - LEFT - RIGHT) / top
    body = []
    for i, name in enumerate(series):  # legend
        x = LEFT + i * 110
        body.append(
            f'<rect x="{x}" y="30" width="10" height="10" fill="{PALETTE[i % len(PALETTE)]}"/>'
        )
        body.append(f'<text x="{x + 14}" y="39">{escape(name)}</text>')
    for r, (label, vals) in enumerate(rows):
        y = TOP + 10 + r * ROW
        body.append(
            f'<text x="{LEFT - 8}" y="{y + 14}" text-anchor="end">{escape(label)}</text>'
        )
        x = float(LEFT)
        for i, v in enumerate(vals):
            w = v * scale
            if w > 0:
                body.append(
                    f'<rect x="{_f(x)}" y="{y}" width="{_f(w)}" height="18" fill="{PALETTE[i % len(PALETTE)]}"/>'
                )
            x += w
        body.append(
            f'<text x="{_f(x + 6)}" y="{y + 14}">{_f(sum(vals))}{escape(unit)}</text>'
        )
    return _frame(title, TOP + 20 + len(rows) * ROW, "".join(body))


def interval_plot(
    title: str, rows: list[tuple[str, float | None, float | None, float | None]]
) -> str:
    """rows: (label, p, lo, hi) on 0..1; a row with no data (n = 0) is drawn as 'no data'."""
    span = W - LEFT - RIGHT
    body = [
        f'<line x1="{LEFT}" y1="{TOP - 4}" x2="{LEFT}" y2="{TOP + 8 + len(rows) * ROW}" stroke="#999"/>'
    ]
    for t in (0, 0.25, 0.5, 0.75, 1):
        x = LEFT + t * span
        body.append(
            f'<line x1="{_f(x)}" y1="{TOP - 4}" x2="{_f(x)}" y2="{TOP + 4 + len(rows) * ROW}" stroke="#ddd"/>'
        )
        body.append(
            f'<text x="{_f(x)}" y="{TOP - 8}" text-anchor="middle">{t:g}</text>'
        )
    for r, (label, p, lo, hi) in enumerate(rows):
        y = TOP + 12 + r * ROW
        body.append(
            f'<text x="{LEFT - 8}" y="{y + 4}" text-anchor="end">{escape(label)}</text>'
        )
        if p is None:
            body.append(f'<text x="{LEFT + 8}" y="{y + 4}" fill="#888">no data</text>')
            continue
        x0, x1, xp = LEFT + lo * span, LEFT + hi * span, LEFT + p * span
        body.append(
            f'<line x1="{_f(x0)}" y1="{y}" x2="{_f(x1)}" y2="{y}" stroke="#4C78A8" stroke-width="2"/>'
        )
        body.append(f'<circle cx="{_f(xp)}" cy="{y}" r="4" fill="#4C78A8"/>')
    return _frame(title, TOP + 24 + len(rows) * ROW, "".join(body))
