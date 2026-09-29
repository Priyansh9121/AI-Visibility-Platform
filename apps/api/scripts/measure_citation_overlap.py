"""Does Citation Strength repeat Mention Rate? Measured, not assumed — 2026-09-29.

Since v4 both dimensions read the same population, awareness answers, and a
brand named in more of them collects more citations almost mechanically. If
the two move together, the composite counts one fact twice, at 30 + 20 = 50%
of its weight. This measures whether they do.

Every scan with a Score row is scored with `score_scan(persist=False)` under
the current `FORMULA_VERSION`. The script collects the subject's (Mention
Rate, Citation Strength) and every rival's from the comparison. It then
reports n, Pearson r and Spearman ρ over all pairs, and again with zeros
excluded (any pair where either value is 0). Zero mentions forcing zero
citations is expected and says nothing about overlap. Rivals only carry a
Citation Strength when the subject's is included, so a scan whose dimension
is excluded contributes no pairs.

It also reports, per scan, which brand SETS THE SCALE: the one holding the
best raw value that everyone is normalised against, and how many awareness
answers name it. It counts the scans whose scale rests on fewer than
`SMALL_SAMPLE` answers. Ties are held jointly, so a scan counts only when
every brand at the top is named in fewer than `SMALL_SAMPLE` answers.

On 2026-09-29, over 26 scored scans, v4 read Spearman ρ = 0.781 with zeros
excluded (n = 83), above the 0.70 threshold fixed beforehand. That produced v5
(scoring-spec.md), which reads 0.177 on the same pairs.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \\
        uv run python scripts/measure_citation_overlap.py --out <dir>

Writes `pairs.json` (every pair, plus each scan's subject scores), and the
scatter as `overlap.svg` and `overlap.png`: no plotting library is a
dependency, so the SVG is written by hand and Playwright, which the audit
already uses, renders it. Reads only: the session is rolled back and nothing
is persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from avp_api.config import Settings  # noqa: E402
from avp_api.models import Client, Scan, Score  # noqa: E402
from avp_api.services import scoring_runner  # noqa: E402
from avp_api.services.domain_authority import load_domain_authority  # noqa: E402
from avp_api.services.scoring import (  # noqa: E402
    FORMULA_VERSION,
    Dimension,
    answer_authority,
    authority_when_named,
    awareness_only,
)

# "Named in fewer than 5 answers": the v6 brief's line for a scale that rests
# on too little evidence.
SMALL_SAMPLE = 5


@dataclass(frozen=True)
class Pair:
    scan_id: str
    client: str
    brand: str
    role: str  # "subject" or "rival"
    mention_rate: float
    citation_strength: float


@dataclass(frozen=True)
class ScaleSetter:
    scan_id: str
    client: str
    brands: list[str]  # every brand holding the best raw value
    named: list[int]  # awareness answers naming each of them
    best: float  # the raw value everyone is normalised against
    small_sample: bool  # every brand at the top is named in < SMALL_SAMPLE answers


@dataclass(frozen=True)
class ScanRow:
    scan_id: str
    client: str
    created_at: str
    status: str
    composite: float | None
    mention_rate: float | None
    citation_strength: float | None
    citation_excluded: str | None


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def correlations(pairs: list[Pair]) -> dict[str, float | int | None]:
    """n, Pearson r and Spearman ρ (ties take their average rank), or None if undefined."""
    xs = [p.mention_rate for p in pairs]
    ys = [p.citation_strength for p in pairs]
    out: dict[str, float | int | None] = {"n": len(pairs), "pearson": None, "spearman": None}
    if len(pairs) >= 3 and len(set(xs)) > 1 and len(set(ys)) > 1:
        out["pearson"] = statistics.correlation(xs, ys)
        out["spearman"] = statistics.correlation(xs, ys, method="ranked")
    return out


def nonzero(pairs: list[Pair]) -> list[Pair]:
    return [p for p in pairs if p.mention_rate != 0 and p.citation_strength != 0]


def widest_gaps(pairs: list[Pair], count: int = 5) -> list[Pair]:
    """The pairs where the two sub-scores differ most, one per brand."""
    seen: set[str] = set()
    out: list[Pair] = []
    ranked = sorted(
        pairs,
        key=lambda p: (-abs(p.mention_rate - p.citation_strength), p.brand, p.scan_id),
    )
    for p in ranked:
        if p.brand.lower() in seen:
            continue
        seen.add(p.brand.lower())
        out.append(p)
        if len(out) == count:
            break
    return out


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, int):
        return str(value)
    return f"{value:.3f}"


def scatter_svg(pairs: list[Pair], every: dict[str, float | int | None],
                zeros_out: dict[str, float | int | None], scans: int) -> str:
    """The scatter, as a self-contained SVG. Hollow marks carry a zero on either axis."""
    left, top, size = 96, 132, 480
    width, height = left + size + 48, top + size + 150

    def x(v: float) -> float:
        return left + v / 100 * size

    def y(v: float) -> float:
        return top + size - v / 100 * size

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="40" font-size="19" font-weight="bold" fill="#111827">'
        f"Does Citation Strength repeat Mention Rate? (formula {FORMULA_VERSION})</text>",
        f'<text x="{left}" y="68" font-size="14" fill="#374151">All pairs: n = {every["n"]}, '
        f'Pearson r = {_fmt(every["pearson"])}, Spearman ρ = {_fmt(every["spearman"])}</text>',
        f'<text x="{left}" y="90" font-size="14" fill="#374151">Zeros excluded: '
        f'n = {zeros_out["n"]}, Pearson r = {_fmt(zeros_out["pearson"])}, '
        f'Spearman ρ = {_fmt(zeros_out["spearman"])}</text>',
        f'<text x="{left}" y="112" font-size="12" fill="#6b7280">{scans} scored scans in '
        "avp_dev, scored with score_scan(persist=False); subject and every rival.</text>",
    ]
    for tick in range(0, 101, 20):
        parts.append(
            f'<line x1="{x(tick)}" y1="{top}" x2="{x(tick)}" y2="{top + size}" '
            'stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<line x1="{left}" y1="{y(tick)}" x2="{left + size}" y2="{y(tick)}" '
            'stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x(tick)}" y="{top + size + 20}" font-size="12" fill="#374151" '
            f'text-anchor="middle">{tick}</text>'
        )
        parts.append(
            f'<text x="{left - 10}" y="{y(tick) + 4}" font-size="12" fill="#374151" '
            f'text-anchor="end">{tick}</text>'
        )
    parts += [
        f'<rect x="{left}" y="{top}" width="{size}" height="{size}" fill="none" '
        'stroke="#9ca3af" stroke-width="1"/>',
        f'<line x1="{x(0)}" y1="{y(0)}" x2="{x(100)}" y2="{y(100)}" stroke="#9ca3af" '
        'stroke-width="1" stroke-dasharray="5 4"/>',
        f'<text x="{left + size / 2}" y="{top + size + 46}" font-size="13" fill="#111827" '
        'text-anchor="middle">Mention Rate: share of awareness answers naming the brand</text>',
        f'<text x="{left - 58}" y="{top + size / 2}" font-size="13" fill="#111827" '
        f'text-anchor="middle" transform="rotate(-90 {left - 58} {top + size / 2})">'
        "Citation Strength: authority against the scan's best</text>",
    ]
    # Rivals first, so the subjects sit on top where they overlap.
    for p in sorted(pairs, key=lambda p: (p.role == "subject", p.scan_id, p.brand)):
        colour = "#0f766e" if p.role == "subject" else "#6b7280"
        radius = 5.5 if p.role == "subject" else 4.5
        zero = p.mention_rate == 0 or p.citation_strength == 0
        fill = "none" if zero else colour
        parts.append(
            f'<circle cx="{x(p.mention_rate):.2f}" cy="{y(p.citation_strength):.2f}" '
            f'r="{radius}" fill="{fill}" fill-opacity="0.55" stroke="{colour}" '
            'stroke-width="1.4"/>'
        )
    legend_y = top + size + 78
    parts += [
        f'<circle cx="{left + 6}" cy="{legend_y}" r="5.5" fill="#0f766e" fill-opacity="0.55" '
        'stroke="#0f766e" stroke-width="1.4"/>',
        f'<text x="{left + 18}" y="{legend_y + 4}" font-size="12" fill="#374151">subject</text>',
        f'<circle cx="{left + 90}" cy="{legend_y}" r="4.5" fill="#6b7280" fill-opacity="0.55" '
        'stroke="#6b7280" stroke-width="1.4"/>',
        f'<text x="{left + 102}" y="{legend_y + 4}" font-size="12" fill="#374151">rival</text>',
        f'<circle cx="{left + 160}" cy="{legend_y}" r="4.5" fill="none" stroke="#6b7280" '
        'stroke-width="1.4"/>',
        f'<text x="{left + 172}" y="{legend_y + 4}" font-size="12" fill="#374151">hollow: '
        "0 on either axis, left out of the zeros-excluded figures</text>",
        f'<text x="{left}" y="{legend_y + 28}" font-size="12" fill="#6b7280">Dashed line: '
        "the two sub-scores equal.</text>",
        "</svg>",
    ]
    return "\n".join(parts)


async def render_png(svg: str, path: Path) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(device_scale_factor=2)
        await page.set_content(
            f'<!doctype html><html><body style="margin:0;background:#fff">{svg}</body></html>'
        )
        await page.locator("svg").screenshot(path=str(path))
        await browser.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True, help="directory for the outputs")
    parser.add_argument("--no-png", action="store_true", help="write the SVG only")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    engine = create_async_engine(os.environ.get("DATABASE_URL", str(settings.database_url)))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    pairs: list[Pair] = []
    scan_rows: list[ScanRow] = []
    setters: list[ScaleSetter] = []
    nobody_backed: list[str] = []
    async with session_factory() as session:
        authority = await load_domain_authority(session)
        scans = (
            await session.execute(
                select(Scan, Client)
                .join(Client, Client.id == Scan.client_id)
                .where(Scan.id.in_(select(Score.scan_id)))
                .order_by(Scan.created_at, Scan.id)
            )
        ).all()
        for scan, client in scans:
            _, computed, comparisons = await scoring_runner.score_scan(
                session, scan, persist=False
            )
            mr = _num(computed.value(Dimension.MENTION_RATE))
            cs = _num(computed.value(Dimension.CITATION_STRENGTH))
            scan_rows.append(
                ScanRow(
                    scan_id=scan.id,
                    client=client.domain,
                    created_at=scan.created_at.isoformat(),
                    status=computed.status,
                    composite=_num(computed.composite),
                    mention_rate=mr,
                    citation_strength=cs,
                    citation_excluded=computed.excluded_dimensions.get(
                        Dimension.CITATION_STRENGTH.value
                    ),
                )
            )
            if mr is not None and cs is not None:
                pairs.append(
                    Pair(scan.id, client.domain, client.brand_name or client.name,
                         "subject", mr, cs)
                )
            for rival in comparisons:
                if rival.citation_strength is None:
                    continue
                pairs.append(
                    Pair(scan.id, client.domain, rival.name, "rival",
                         float(rival.mention_rate), float(rival.citation_strength))
                )
            if cs is None:
                continue
            # Who sets the scale: the raw values `citation_strength` normalises,
            # recomputed from the same facts with the same functions.
            results = await scoring_runner.load_result_facts(session, scan.id)
            competitors, _ = await scoring_runner.load_competitor_facts(session, scan.id)
            aware = awareness_only([r for r in results if r.answered])
            tiers = authority.tiers_for({d for r in aware for d, _ in r.citations})
            subject_raw, rival_raw = authority_when_named(aware, competitors, tiers)
            subject_credits, rival_credits = answer_authority(aware, competitors, tiers)
            names = {c.competitor_id: c.name for c in competitors}
            brands = [(client.brand_name or client.name, subject_raw, len(subject_credits))] + [
                (names[cid], rival_raw[cid], len(rival_credits[cid])) for cid in sorted(rival_raw)
            ]
            best = max(raw for _, raw, _ in brands)
            if best == 0:
                nobody_backed.append(client.domain)
                continue
            top = [(name, n) for name, raw, n in brands if raw == best]
            setters.append(
                ScaleSetter(
                    scan_id=scan.id,
                    client=client.domain,
                    brands=[name for name, _ in top],
                    named=[n for _, n in top],
                    best=float(best),
                    small_sample=all(n < SMALL_SAMPLE for _, n in top),
                )
            )
        await session.rollback()
    await engine.dispose()

    every = correlations(pairs)
    zeros_out = correlations(nonzero(pairs))
    gaps = widest_gaps(pairs)

    (args.out / "pairs.json").write_text(
        json.dumps(
            {
                "formula_version": FORMULA_VERSION,
                "scans": [asdict(r) for r in scan_rows],
                "pairs": [asdict(p) for p in pairs],
                "all_pairs": every,
                "zeros_excluded": zeros_out,
                "widest_gaps": [asdict(p) for p in gaps],
                "scale_setters": [asdict(s) for s in setters],
                "small_sample_setters": sum(s.small_sample for s in setters),
                "nobody_backed": nobody_backed,
            },
            indent=2,
        )
        + "\n"
    )
    svg = scatter_svg(pairs, every, zeros_out, len(scan_rows))
    (args.out / "overlap.svg").write_text(svg + "\n")
    if not args.no_png:
        await render_png(svg, args.out / "overlap.png")

    included = sum(r.citation_strength is not None for r in scan_rows)
    print(f"formula {FORMULA_VERSION}: {len(scan_rows)} scored scans, "
          f"{included} with Citation Strength included")
    subjects = sum(p.role == "subject" for p in pairs)
    print(f"pairs: {len(pairs)} ({subjects} subject, {len(pairs) - subjects} rival)")
    for label, s in (("all pairs", every), ("zeros excluded", zeros_out)):
        print(f"{label}: n={s['n']} pearson={_fmt(s['pearson'])} spearman={_fmt(s['spearman'])}")
    small = sum(s.small_sample for s in setters)
    print(f"scale set by a brand named in fewer than {SMALL_SAMPLE} answers: {small} of "
          f"{len(setters)} scans with a scale ({len(nobody_backed)} more back no brand at all)")
    for st in setters:
        held = ", ".join(f"{b} (n={n})" for b, n in zip(st.brands, st.named, strict=True))
        print(f"  {st.client}: {held}{'  <- small sample' if st.small_sample else ''}")
    print("widest gaps (|MR - CS|, one per brand):")
    for p in gaps:
        print(f"  {p.brand} ({p.role}, scan of {p.client}): MR {p.mention_rate:.2f}, "
              f"CS {p.citation_strength:.2f}, gap {abs(p.mention_rate - p.citation_strength):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
