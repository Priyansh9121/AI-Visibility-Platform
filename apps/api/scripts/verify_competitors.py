"""Live verification of the Epic 3 acceptance criterion.

§7 Epic 3: "for 10 test URLs across different industries, detected competitors
are manually verified as accurate >=80% of the time."

Runs the real combined system — real SerpApi searches, real model calls — and
prints the ranked set per URL alongside a reference list of known-real rivals,
so accuracy can be judged rather than asserted.

**Measures the combined SERP + co-citation system**, not either signal alone,
because that is what ships. Per-signal columns are printed for diagnosis only.

    uv run python scripts/verify_competitors.py
    uv run python scripts/verify_competitors.py --serp-only   # cheaper, diagnostic

Costs real money: ~6 SerpApi searches and ~4 model calls per URL.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avp_api.config import Settings  # noqa: E402
from avp_api.services import cocitation as cocitation_service  # noqa: E402
from avp_api.services import serp as serp_service  # noqa: E402
from avp_api.services.cocitation import CoCitationResult  # noqa: E402
from avp_api.services.competitors import (  # noqa: E402
    decide_detection,
    merge_candidates,
    score_candidates,
    slugify,
)


@dataclass
class Case:
    domain: str
    brand: str
    industry: str | None
    niche: str | None
    # Known-real competitors, for scoring precision. Matched loosely on slug, so
    # "Zendesk" matches zendesk.com. Not exhaustive — a detected rival absent
    # from this list is judged by eye, not auto-failed.
    known: list[str]


# Ten businesses across deliberately different industries. Chosen so the
# competitor set is checkable by a non-specialist.
CASES: list[Case] = [
    Case("helpscout.com", "Help Scout", "customer support software", None,
         ["zendesk", "intercom", "freshdesk", "front", "gorgias", "kayako", "hubspot", "zoho",
          "crisp", "helpspace", "happyfox", "tidio", "liveagent", "groove"]),
    Case("basecamp.com", "Basecamp", "project management software", None,
         ["asana", "trello", "monday", "clickup", "notion", "wrike", "teamwork", "jira",
          "smartsheet", "linear", "height", "podio"]),
    Case("stripe.com", "Stripe", "payment processing software", None,
         ["adyen", "braintree", "paypal", "square", "checkout", "mollie", "worldpay",
          "authorizenet", "razorpay", "paddle", "2checkout", "gocardless"]),
    Case("allbirds.com", "Allbirds", "footwear brand", None,
         ["rothys", "vejastore", "veja", "toms", "nike", "adidas", "onrunning", "on", "hoka",
          "brooks", "newbalance", "cariuma", "atoms", "vessi", "tropicfeel"]),
    Case("ooni.com", "Ooni", "pizza oven manufacturer", None,
         ["gozney", "solostove", "bertello", "roccbox", "camp chef", "campchef", "everdure",
          "weber", "blackstone", "halo", "cuisinart", "breville"]),
    Case("savvycal.com", "SavvyCal", "meeting scheduling software", None,
         ["calendly", "cal", "acuityscheduling", "acuity", "doodle", "youcanbookme", "hubspot",
          "chilipiper", "motion", "reclaim", "zcal", "tidycal"]),
    Case("roto-rooter.com", "Roto-Rooter", "plumbing and drain services", None,
         ["mrrooter", "benjaminfranklinplumbing", "arsrescuerooter", "ars", "rescuerooter",
          "zoomdrain", "michaelandson", "servicetitan", "plumbingtoday", "rotorooter",
          "drainmaster", "wind river", "leonardsplumbing"]),
    Case("patagonia.com", "Patagonia", "outdoor apparel", None,
         ["thenorthface", "northface", "arcteryx", "columbia", "rei", "cotopaxi",
          "mountainhardwear", "marmot", "outdoorresearch", "fjallraven", "kuhl", "prana",
          "blackdiamond"]),
    Case("wise.com", "Wise", "international money transfer", None,
         ["revolut", "remitly", "westernunion", "payoneer", "xe", "ofx", "currencyfair",
          "worldremit", "moneygram", "n26", "monzo", "atlanticmoney", "paypal"]),
    Case("ghost.org", "Ghost", "publishing platform", None,
         ["substack", "wordpress", "medium", "beehiiv", "buttondown", "convertkit", "kit",
          "webflow", "squarespace", "wix", "typepad", "hashnode", "write", "mailerlite"]),
]


def matches_known(name: str, domain: str | None, known: list[str]) -> bool:
    candidates = {slugify(name)}
    if domain:
        candidates.add(slugify(domain.split(".")[0]))
    for k in known:
        ks = slugify(k)
        for c in candidates:
            if c and ks and (c == ks or c.startswith(ks) or ks.startswith(c)):
                return True
    return False


async def run_case(case: Case, settings: Settings, serp_only: bool) -> dict:
    queries = serp_service.build_queries(
        brand_name=case.brand, domain=case.domain,
        industry=case.industry, niche=case.niche,
    )
    prompts = cocitation_service.build_seed_prompts(
        brand_name=case.brand, domain=case.domain,
        industry=case.industry, niche=case.niche,
    )

    serp_results = await serp_service.search_many(queries, settings=settings)
    if serp_only:
        co_results: list[CoCitationResult] = []
    else:
        co_results = await cocitation_service.run_seed_prompts(
            prompts, subject_brand=case.brand, settings=settings
        )

    candidates = score_candidates(
        merge_candidates(
            serp_results, co_results,
            subject_domain=case.domain, subject_name=case.brand,
        )
    )
    outcome = decide_detection(
        candidates,
        serp_ok=sum(1 for r in serp_results if r.ok),
        co_citation_ok=sum(1 for r in co_results if r.ok),
        used_industry_seed=bool(case.industry or case.niche),
    )

    rows = []
    for rank, c in enumerate(outcome.candidates, 1):
        rows.append({
            "rank": rank,
            "name": c.resolved_name(),
            "domain": c.domain,
            "source": c.source.value,
            "corroborated": c.corroborated,
            "known": matches_known(c.resolved_name(), c.domain, case.known),
        })
    hits = sum(1 for r in rows if r["known"])
    return {
        "case": case,
        "rows": rows,
        "status": outcome.status.value,
        "confidence": outcome.detection_confidence,
        "considered": outcome.candidates_considered,
        "hits": hits,
        "accuracy": (hits / len(rows)) if rows else 0.0,
    }


async def main() -> int:
    serp_only = "--serp-only" in sys.argv
    settings = Settings()
    if serp_only:
        print("SERP ONLY — diagnostic. Does NOT verify the acceptance criterion.\n")

    results = []
    for case in CASES:
        result = await run_case(case, settings, serp_only)
        results.append(result)

        conf = result["confidence"]
        print(f"\n{case.domain}  ({case.industry})")
        print(f"  status={result['status']}  confidence={conf}  "
              f"considered={result['considered']}  accuracy={result['accuracy']:.0%}")
        for r in result["rows"]:
            mark = "OK " if r["known"] else " ? "
            corr = "both" if r["corroborated"] else r["source"]
            print(
                f"    [{mark}] {r['rank']}. {r['name'][:26]:26} "
                f"{str(r['domain'])[:26]:26} {corr}"
            )

    print("\n" + "=" * 96)
    print(f"{'site':22} {'status':14} {'conf':7} {'known/returned':16} accuracy")
    print("=" * 96)
    total_hits = total_rows = 0
    for r in results:
        total_hits += r["hits"]
        total_rows += len(r["rows"])
        ratio = f"{r['hits']}/{len(r['rows'])}"
        print(
            f"{r['case'].domain:22} {r['status']:14} "
            f"{str(r['confidence'] or '-'):7} {ratio:16} {r['accuracy']:.0%}"
        )
    print("=" * 96)

    overall = (total_hits / total_rows) if total_rows else 0.0
    per_url_pass = sum(1 for r in results if r["accuracy"] >= 0.8)
    print(f"\noverall precision : {total_hits}/{total_rows} = {overall:.0%}")
    print(f"URLs at >=80%     : {per_url_pass}/{len(CASES)}")
    print("\n'?' rows are not necessarily wrong — the known-competitor lists are")
    print("not exhaustive. Judge them by eye before calling them failures.")

    if serp_only:
        print("\nSERP ONLY — acceptance criterion NOT verified.")
        return 0
    print("\nRESULT:", "PASS" if overall >= 0.8 else "BELOW BAR — needs review")
    return 0 if overall >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
