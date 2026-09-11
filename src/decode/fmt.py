"""Shared output formatting: money, percents, and plain-text tables.

Deliberately not `rich`: this output is redirected into cron logs and parsed by
eye weeks later, so it must be stable, ANSI-free and fixed-width. The whole
renderer is 40 lines; a dependency would buy colour we cannot use in a log file.
"""
from __future__ import annotations

from typing import Iterable, Sequence


def money(x: float | None, dp: int | None = None) -> str:
    """$1.24B / $340M / $45.2K / $812 -- one consistent scale everywhere."""
    if x is None:
        return "-"
    a = abs(x)
    sign = "-" if x < 0 else ""
    if a >= 1e9:
        return f"{sign}${a/1e9:,.{2 if dp is None else dp}f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:,.{0 if dp is None else dp}f}M"
    if a >= 1e3:
        return f"{sign}${a/1e3:,.{1 if dp is None else dp}f}K"
    return f"{sign}${a:,.0f}"


def price(x: float | None) -> str:
    return "-" if x is None else f"${x:,.0f}"


def pct(x: float | None, dp: int = 1, sign: bool = True) -> str:
    if x is None:
        return "-"
    return f"{x:{'+' if sign else ''}.{dp}f}%"


def band(lo: float, hi: float) -> str:
    return price(lo) if lo == hi else f"{price(lo)} – {price(hi)}"


def table(columns: Sequence[tuple[str, str]], rows: Iterable[Sequence[str]],
          indent: int = 0, rule_after: int | None = None) -> str:
    """columns: (header, align) where align is '<' or '>'. Cells are pre-formatted."""
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    heads = [h for h, _ in columns]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(heads)]
    pad = " " * indent

    def line(cells: Sequence[str]) -> str:
        return pad + "  ".join(f"{c:{a}{w}}" for c, (_, a), w in zip(cells, columns, widths))

    out = [line(heads), pad + "─" * (sum(widths) + 2 * (len(widths) - 1))]
    for i, r in enumerate(rows):
        out.append(line(r))
        if rule_after is not None and i == rule_after:
            out.append(pad + "·" * (sum(widths) + 2 * (len(widths) - 1)))
    return "\n".join(out)


def heading(text: str, char: str = "═") -> str:
    return f"\n{text}\n{char * len(text)}"


def kv(pairs: Sequence[tuple[str, str]], indent: int = 0) -> str:
    """Aligned key: value block for scalar facts that are not a table."""
    w = max((len(k) for k, _ in pairs), default=0)
    return "\n".join(f"{' ' * indent}{k:<{w}}   {v}" for k, v in pairs)
