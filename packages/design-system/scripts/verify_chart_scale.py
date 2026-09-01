#!/usr/bin/env python3
"""A chart's own type must never render larger than the prose beside it.

WHY THIS EXISTS
---------------
Twice now the same defect has shipped and been caught only because somebody
measured a live browser by hand:

  Epic 9.19  The EmptyState ledger figure was given a full-width grid column.
             An SVG at `width: 100%` over a fixed viewBox scales its TYPE with
             its box, so the Ledger's 13px dimension labels rendered at ~30px —
             larger than the page's own headline.

  Epic 9.21  TrendChart shipped without a bound. A 720-unit chart stretched
             across a 1200px Working column ran at 1.6x: 11px axis labels
             rendered at 17.6px against 14px body copy, and a 2.5px stroke came
             out at 4px. The screen read as sparse and oversized.

Both were invisible to the unit suites — over 200 of them — and always will be.
`jsdom` computes no layout, and a static render has no box to be the wrong size.
The property is only true or false once a real browser has laid the page out.

WHAT IT ASSERTS, AND WHY NOT A SCALE RATIO
------------------------------------------
For every `<svg>` with a viewBox, at a range of viewport widths:

    the chart's smallest rendered label  <=  the page's body copy

Scale is the *mechanism*, but scale alone is not a defect. A chart whose type is
authored in viewBox units may be drawn expecting to scale — the Answer Shelf is
exactly that, running at 1.22x on the report with its 11px labels landing at
13.4px against 16px prose, which is correct and must not fail.

What is always wrong is a chart's smallest type rendering LARGER than the prose
beside it. An axis label is the smallest thing on a page by design; when it
becomes the biggest, the page reads as sparse and oversized. That is the symptom
both defects above actually presented as, so it is what this asserts. The scale
is reported for context and never failed on.

Scaling DOWN is fine and must keep working — a narrow viewport has to shrink a
chart, and a fix that pinned a fixed width instead of a max would break exactly
there, which is why the narrow viewports are in the sweep too.

It runs against the STYLE GUIDE rather than the app, because the style guide
renders every chart in the system: a chart added later is covered the day it is
added, without anybody remembering to extend this file.

USAGE
-----
    # the style guide is served by `pnpm dev` at the repo root, on :4100
    apps/api/.venv/bin/python packages/design-system/scripts/verify_chart_scale.py

    # or point it at any page that renders charts
    ... verify_chart_scale.py --url http://localhost:3000/some/page

    # an authenticated page needs a session; the script signs in first
    ... verify_chart_scale.py --url http://localhost:3000/clients/<id>/technical \
          --sign-in-at http://localhost:3000/ --email dev@example --password ...

Exits non-zero and prints every offender.

It uses the Playwright already installed in `apps/api/.venv` rather than adding
a Node browser dependency, which is why it is Python beside a TypeScript
package. Promoting it into CI would mean adding Playwright to the Node side;
until then it is a verification script in the same spirit as
`apps/api/scripts/verify_*.py`.
"""

from __future__ import annotations

import argparse
import asyncio

from playwright.async_api import async_playwright

DEFAULT_URL = "http://localhost:4100/"

# A laptop, two common desktops, a wide monitor and a very wide one. The defect
# lives at the wide end, where the container outgrows the chart; the narrow end
# is here to prove the chart still shrinks rather than being pinned.
VIEWPORTS = (1024, 1280, 1440, 1728, 2560)

# Slack, so a label a hair under the body size is not reported as a failure.
TOLERANCE = 1.02

# `getComputedStyle(body)` is the honest reference: it is what every unstyled
# paragraph inherits and what a reader's eye calibrates against.
#
# `font-size` inside an SVG is in USER UNITS, so it is multiplied by exactly the
# same scale the box is — which is the whole mechanism being guarded against.
PROBE = """() => {
  const bodyPx = parseFloat(getComputedStyle(document.body).fontSize);
  const out = [];
  for (const svg of document.querySelectorAll('svg[viewBox]')) {
    const vb = svg.viewBox.baseVal;
    if (!vb || vb.width === 0) continue;
    const box = svg.getBoundingClientRect();
    if (box.width === 0) continue;
    const texts = [...svg.querySelectorAll('text')]
      .map((t) => parseFloat(getComputedStyle(t).fontSize))
      .filter((n) => Number.isFinite(n) && n > 0);
    if (texts.length === 0) continue;
    const scale = box.width / vb.width;
    const frame = svg.closest('figure') || svg.parentElement;
    out.push({
      cls: svg.getAttribute('class') || '(no class)',
      title: (frame && frame.querySelector('figcaption')?.textContent || '').trim().slice(0, 44),
      viewBoxWidth: vb.width,
      renderedWidth: Math.round(box.width),
      scale: +scale.toFixed(2),
      authoredSmallest: Math.min(...texts),
      onScreenSmallest: +(Math.min(...texts) * scale).toFixed(1),
      bodyPx,
    });
  }
  return out;
}"""


async def main() -> int:
    ap = argparse.ArgumentParser(description="Verify no chart out-types its page.")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"page to check (default {DEFAULT_URL})")
    # Charts inside the product live behind a session. Without these the script
    # would measure the signed-out state and report "no charts found", which
    # reads like a pass and is not one.
    ap.add_argument("--sign-in-at", default=None, help="page carrying the Log in control")
    ap.add_argument("--email", default=None)
    ap.add_argument("--password", default=None)
    args = ap.parse_args()

    failures: list[str] = []
    checked = 0
    empty_sweeps = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await (await browser.new_context()).new_page()

        if args.sign_in_at and args.email and args.password:
            await page.goto(args.sign_in_at, wait_until="domcontentloaded", timeout=20_000)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(900)
            await page.get_by_role("button", name="Log in").first.click(timeout=10_000)
            await page.wait_for_timeout(600)
            await page.get_by_label("Email").fill(args.email)
            await page.get_by_label("Password", exact=True).fill(args.password)
            await page.get_by_role("button", name="Sign in", exact=True).click()
            await page.wait_for_timeout(3000)

        try:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=15_000)
        except Exception as exc:  # noqa: BLE001
            print(f"could not open {args.url}: {exc}")
            print("is the style guide running? `pnpm dev` at the repo root serves it on :4100")
            await browser.close()
            return 2

        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(600)

        for width in VIEWPORTS:
            await page.set_viewport_size({"width": width, "height": 900})
            await page.wait_for_timeout(350)
            charts = await page.evaluate(PROBE)
            if not charts:
                # NOT a pass. A page with no charts on it is either the wrong
                # page or a signed-out one, and silence here would look
                # identical to success.
                print(f"  {width:>5}px  no labelled charts found — wrong page, or not signed in?")
                empty_sweeps += 1
                continue

            over = [c for c in charts if c["onScreenSmallest"] > c["bodyPx"] * TOLERANCE]
            checked += len(charts)
            worst = max(c["onScreenSmallest"] for c in charts)
            body = charts[0]["bodyPx"]
            mark = "FAIL" if over else "ok  "
            print(
                f"  {mark} {width:>5}px viewport   {len(charts):>2} charts   "
                f"largest smallest-label {worst}px   body {body:.0f}px"
            )

            for c in over:
                failures.append(
                    f"{width}px: {c['cls']} {c['title']!r} — smallest label authored at "
                    f"{c['authoredSmallest']:.0f}px renders at {c['onScreenSmallest']}px "
                    f"against {c['bodyPx']:.0f}px body copy "
                    f"({c['viewBoxWidth']:.0f} units drawn, {c['renderedWidth']}px rendered, "
                    f"scale {c['scale']}x)"
                )

        await browser.close()

    print()
    if failures:
        print(f"{len(failures)} chart render(s) whose own type outgrew the page:\n")
        for f in failures:
            print(f"  - {f}")
        print(
            "\nBound the figure at its layout width, the way TrendChart does:"
            "\n    style={{ maxWidth: `${layout.width}px` }}"
            "\nA max-width only — the chart must still scale DOWN on a narrow viewport."
        )
        return 1

    if checked == 0:
        print(
            f"no charts were found at any of the {len(VIEWPORTS)} viewports. "
            "That is not a pass — check the URL, and sign in with --email/--password "
            "if the page is behind a session."
        )
        return 2

    print(
        f"{checked} chart renders across {len(VIEWPORTS)} viewports"
        + (f" ({empty_sweeps} sweep(s) found nothing)" if empty_sweeps else "")
        + ": every chart's smallest label stays at or below body copy."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
