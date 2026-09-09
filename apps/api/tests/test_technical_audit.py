"""Technical audit normalisation — pure functions over signals.

No network. The live audit against real sites is scripts/verify_audit.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from avp_api.services.technical_audit import (
    COMPONENT_WEIGHTS,
    AuditSignals,
    build_checks,
    compute_components,
    score_audit,
)


def sig(**kw) -> AuditSignals:  # noqa: ANN003
    base = {
        "url": "https://example.com", "domain": "example.com", "http_status": 200,
        "ok": True, "is_indexable": True, "robots_allows_crawl": True,
    }
    base.update(kw)
    return AuditSignals(**base)


def verdict(signals: AuditSignals, key: str) -> str:
    return next(c.status for c in build_checks(signals) if c.key == key)


def detail(signals: AuditSignals, key: str) -> str | None:
    return next(c.detail_code for c in build_checks(signals) if c.key == key)


class TestComponentWeights:
    def test_weights_sum_to_one_hundred(self) -> None:
        assert sum(COMPONENT_WEIGHTS.values()) == Decimal("100")

    def test_indexation_is_the_heaviest_component(self) -> None:
        """It is a precondition: a site blocking crawlers has no foundation."""
        assert COMPONENT_WEIGHTS["indexation"] == max(COMPONENT_WEIGHTS.values())

    def test_core_web_vitals_carry_no_weight(self) -> None:
        """Measured and reported per §7, but a noisy lab number must not move
        a client-facing score."""
        assert not any("cwv" in k or "vital" in k for k in COMPONENT_WEIGHTS)


class TestIndexation:
    def test_perfect_indexation(self) -> None:
        components, _ = compute_components(
            sig(is_indexable=True, has_sitemap=True, canonical_present=True)
        )
        assert components["indexation"] == Decimal("100")

    def test_noindex_dominates_the_component(self) -> None:
        components, _ = compute_components(
            sig(is_indexable=False, meta_robots_noindex=True,
                has_sitemap=True, canonical_present=True)
        )
        assert components["indexation"] == Decimal("40"), "loses the 60-point core"

    def test_robots_disallow_is_reported_distinctly_from_noindex(self) -> None:
        """The fix differs, so the detail code must differ."""
        assert detail(sig(is_indexable=False, meta_robots_noindex=True), "indexable") == (
            "META_ROBOTS_NOINDEX"
        )
        assert detail(sig(is_indexable=False, robots_allows_crawl=False), "indexable") == (
            "ROBOTS_TXT_DISALLOW"
        )


class TestSchema:
    def test_no_schema_scores_zero(self) -> None:
        components, _ = compute_components(sig(schema_types=[]))
        assert components["schema_presence"] == Decimal("0")
        assert verdict(sig(schema_types=[]), "schema_present") == "fail"

    def test_presence_is_graduated_not_binary(self) -> None:
        """One stray WebSite type is not a marked-up site; a flag would say it is."""
        one, _ = compute_components(sig(schema_types=["WebSite"]))
        three, _ = compute_components(sig(schema_types=["WebSite", "Organization", "FAQPage"]))
        assert one["schema_presence"] == Decimal("60")
        assert three["schema_presence"] == Decimal("100")

    def test_structured_data_rewards_the_right_types(self) -> None:
        components, _ = compute_components(sig(
            schema_types=["Organization", "FAQPage", "Product"],
            has_organization_schema=True, has_faq_schema=True, has_product_schema=True,
        ))
        assert components["structured_data"] == Decimal("100")

    def test_business_entity_is_half_the_structured_data_component(self) -> None:
        components, _ = compute_components(
            sig(schema_types=["Organization"], has_organization_schema=True)
        )
        assert components["structured_data"] == Decimal("50")

    def test_localbusiness_counts_as_a_business_entity(self) -> None:
        components, _ = compute_components(
            sig(schema_types=["Dentist"], has_localbusiness_schema=True)
        )
        assert components["structured_data"] == Decimal("50")


class TestFreshness:
    @pytest.mark.parametrize(
        ("age", "expected"),
        [(0, "100"), (90, "100"), (91, "75"), (180, "75"),
         (181, "50"), (365, "50"), (366, "25"), (730, "25"), (731, "0")],
    )
    def test_bands(self, age: int, expected: str) -> None:
        components, _ = compute_components(sig(content_age_days=age))
        assert components["content_freshness"] == Decimal(expected)

    def test_no_date_signal_excludes_rather_than_zeroes(self) -> None:
        """Not advertising Last-Modified is a publishing convention, not a
        visibility problem. Same discipline as sentiment with no population."""
        components, excluded = compute_components(sig(content_age_days=None))
        assert "content_freshness" not in components
        assert excluded["content_freshness"] == "NO_DATE_SIGNAL_AVAILABLE"
        assert verdict(sig(content_age_days=None), "content_freshness") == "not_applicable"


class TestCoreWebVitals:
    def test_inp_is_always_not_applicable(self) -> None:
        """INP is a FIELD metric. A crawler never interacts, so there is
        nothing to measure and a lab proxy would be fabricated."""
        signals = sig(lcp_ms=1200, cls=Decimal("0.01"))
        assert verdict(signals, "cwv_inp") == "not_applicable"
        assert detail(signals, "cwv_inp") == "FIELD_METRIC_REQUIRES_REAL_USER_DATA"

    @pytest.mark.parametrize(
        ("lcp", "expected"), [(1200, "pass"), (2500, "pass"), (3000, "warn"), (5000, "fail")]
    )
    def test_lcp_thresholds(self, lcp: int, expected: str) -> None:
        assert verdict(sig(lcp_ms=lcp), "cwv_lcp") == expected

    def test_lcp_and_cls_are_labelled_as_lab_measurements(self) -> None:
        """A report must not present a single cold load as field data."""
        assert detail(sig(lcp_ms=1200), "cwv_lcp") == "LAB_MEASUREMENT_NOT_FIELD_DATA"
        assert detail(sig(cls=Decimal("0.01")), "cwv_cls") == "LAB_MEASUREMENT_NOT_FIELD_DATA"

    def test_vitals_do_not_change_the_score(self) -> None:
        """The property that matters: measured, reported, not scored."""
        fast = score_audit(sig(schema_types=["Organization"], has_organization_schema=True,
                               content_age_days=10, lcp_ms=400, cls=Decimal("0.0")))
        slow = score_audit(sig(schema_types=["Organization"], has_organization_schema=True,
                               content_age_days=10, lcp_ms=9000, cls=Decimal("0.9")))
        assert fast.score == slow.score


class TestScoreAudit:
    def test_unreachable_site_scores_null_not_zero(self) -> None:
        """A site we could not read is not a site with a bad foundation."""
        outcome = score_audit(sig(ok=False, error_code="FETCH_FAILED"))
        assert outcome.score is None
        assert outcome.scored is False
        assert outcome.checks[0].key == "site_reachable"
        assert outcome.checks[0].status == "error"

    def test_perfect_site_scores_one_hundred(self) -> None:
        outcome = score_audit(sig(
            schema_types=["Organization", "FAQPage", "Product"],
            has_organization_schema=True, has_faq_schema=True, has_product_schema=True,
            is_indexable=True, has_sitemap=True, canonical_present=True,
            content_age_days=5,
        ))
        assert outcome.score == Decimal("100.00")

    def test_bare_site_scores_low_but_not_null(self) -> None:
        outcome = score_audit(sig(schema_types=[], content_age_days=1000,
                                  is_indexable=True, has_sitemap=False,
                                  canonical_present=False))
        assert outcome.score is not None
        assert outcome.score < Decimal("30")

    def test_excluded_component_redistributes_weight(self) -> None:
        """A site with no date signal is scored on what could be measured."""
        outcome = score_audit(sig(
            schema_types=["Organization", "FAQPage", "Product"],
            has_organization_schema=True, has_faq_schema=True, has_product_schema=True,
            is_indexable=True, has_sitemap=True, canonical_present=True,
            content_age_days=None,
        ))
        assert outcome.excluded_components == {"content_freshness": "NO_DATE_SIGNAL_AVAILABLE"}
        assert outcome.score == Decimal("100.00"), "remaining components were perfect"

    def test_score_is_in_range_and_two_decimal_places(self) -> None:
        outcome = score_audit(sig(schema_types=["WebSite"], content_age_days=200,
                                  has_sitemap=True))
        assert Decimal("0") <= outcome.score <= Decimal("100")
        assert outcome.score == outcome.score.quantize(Decimal("0.01"))


class TestDeterminism:
    """The output feeds scoring.py, so sloppy arithmetic here undermines
    Epic 5's guarantees just as much as float inside scoring.py would."""

    def _signals(self) -> AuditSignals:
        return sig(
            schema_types=["Organization", "WebSite", "FAQPage"],
            has_organization_schema=True, has_faq_schema=True,
            is_indexable=True, has_sitemap=True, canonical_present=True,
            content_age_days=137, lcp_ms=1834, cls=Decimal("0.041"),
            h1_count=1, word_count=912,
        )

    def test_same_signals_produce_an_identical_score(self) -> None:
        first = score_audit(self._signals()).score
        for _ in range(25):
            assert score_audit(self._signals()).score == first

    def test_every_value_is_a_decimal(self) -> None:
        outcome = score_audit(self._signals())
        assert isinstance(outcome.score, Decimal)
        for value in outcome.components.values():
            assert isinstance(value, Decimal)
            assert not isinstance(value, float)

    def test_module_uses_no_float_arithmetic_in_normalisation(self) -> None:
        """Rule 3 discipline, extended to this module.

        The Playwright bridge legitimately receives floats from the browser
        (timings are JS numbers); those are converted at the boundary. The
        NORMALISATION functions must not contain any.
        """
        import inspect

        from avp_api.services import technical_audit as module

        for fn in (compute_components, score_audit, build_checks):
            source = inspect.getsource(fn)
            assert "float(" not in source, f"{fn.__name__} uses float()"
            assert "/ 100.0" not in source, f"{fn.__name__} uses float division"
        assert module.COMPONENT_WEIGHTS

    def test_check_ordering_is_stable(self) -> None:
        signals = self._signals()
        first = [c.key for c in build_checks(signals)]
        for _ in range(10):
            assert [c.key for c in build_checks(signals)] == first


class TestFactsOnly:
    """The facts-only rule — the client's own site, but the same discipline."""

    def test_signals_carry_no_page_content(self) -> None:
        import dataclasses

        names = {f.name for f in dataclasses.fields(AuditSignals)}
        for forbidden in (
            "html", "body", "text", "title", "description", "meta_description",
            "og_title", "og_description", "content", "snippet", "excerpt",
        ):
            assert forbidden not in names, f"AuditSignals.{forbidden} would hold page copy"
        # Presence flags are fine; the values behind them are not.
        assert "has_title" in names
        assert "has_meta_description" in names
        assert "open_graph_tag_count" in names

    def test_redacted_view_is_narrow(self) -> None:
        view = sig(schema_types=["Organization"]).redacted()
        assert set(view) == {
            "url", "ok", "error_code", "http_status", "schema_types", "indexable", "lcp_ms"
        }
        assert view["schema_types"] == 1, "a count, not the names"

    def test_detail_codes_are_machine_readable_not_prose(self) -> None:
        for check in build_checks(sig(schema_types=[], content_age_days=None)):
            if check.detail_code:
                assert check.detail_code.isupper() or "_" in check.detail_code
                assert " " not in check.detail_code, "detail codes must not be sentences"


class TestAToolFailureIsNotAFindingAboutTheSite:
    """The pilot dry run's blocking bug — 2026-09-08.

    A real site that answers in 1.13 seconds timed out in this module once
    mid-scan, and the report told its owner to *"fix the crawl failure on
    helpwise.io so the site returns rendered HTML to automated visitors"*. The
    same site then passed this audit six times out of six, standalone, median
    11.9s against a 25s ceiling — so the timeout was never evidence about the
    site at all.

    The rule: what the server says about itself is a finding; what this module
    says about its own attempt is not.
    """

    @pytest.mark.parametrize("code", ["BROWSER_ERROR", "TIMEOUT", "FETCH_FAILED"])
    def test_our_own_failure_stays_an_error(self, code: str) -> None:
        (check,) = build_checks(sig(ok=False, error_code=code))

        assert check.key == "site_reachable"
        assert check.status == "error", "a failed attempt must not read as a failed site"
        assert check.detail_code == code

    @pytest.mark.parametrize("code", ["HTTP_404", "HTTP_500", "HTTP_503"])
    def test_the_servers_own_answer_is_a_real_finding(self, code: str) -> None:
        """A 4xx or 5xx is the site describing itself, and a client can act on it."""
        (check,) = build_checks(sig(ok=False, error_code=code))

        assert check.status == "fail"
        assert check.detail_code == code

    def test_technical_foundation_is_excluded_either_way(self) -> None:
        """Neither kind may silently score a client down for an unmeasured scan."""
        for code in ("BROWSER_ERROR", "HTTP_500"):
            out = score_audit(sig(ok=False, error_code=code))
            assert out.score is None
            assert out.scored is False


class TestTheFallbackKeepsTheChecksThatDoNotNeedLoad:
    """`load` timing out used to cost seventeen checks to save one.

    The fallback re-navigates on `domcontentloaded`, which is what `crawl.py`
    has always used. What it must NOT do is report web vitals measured against
    a page it stopped waiting for.
    """

    def test_vitals_measured_after_a_partial_load_are_not_reported(self) -> None:
        """Measured while building this: 988ms on the fallback against 2,952ms
        on a full load of the same page — a third of the real figure, enough to
        flip `cwv_lcp` from `warn` to `pass`. A fallback that quietly flatters
        every slow site it rescues is worse than the outage it survives."""
        partial = sig(load_event_reached=False, lcp_ms=None, cls=None)

        assert verdict(partial, "cwv_lcp") == "not_applicable"
        detail = next(c.detail_code for c in build_checks(partial) if c.key == "cwv_lcp")
        assert detail == "LCP_NOT_OBSERVED"

    def test_the_structural_checks_survive_a_partial_load(self) -> None:
        """Schema, indexability and the sitemap never needed the load event."""
        partial = sig(
            load_event_reached=False, lcp_ms=None,
            schema_types=["Organization", "FAQPage"],
            has_organization_schema=True, has_faq_schema=True,
            has_sitemap=True, is_indexable=True,
        )

        checks = build_checks(partial)
        assert len(checks) > 1, "a partial load must not collapse to one error"
        assert verdict(partial, "site_reachable") == "pass"
        assert verdict(partial, "indexable") == "pass"
        assert score_audit(partial).score is not None

    def test_a_full_load_is_still_the_default(self) -> None:
        assert sig().load_event_reached is True
