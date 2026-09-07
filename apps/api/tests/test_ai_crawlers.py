"""robots.txt AI-crawler policy parsing — Epic F.

The assertions that matter here are the ones a plausible-looking parser gets
wrong while still passing a happy-path test: falling through to the `*` group
after a named group already matched, merging two groups because a blank line
was treated as a separator, and letting a path containing regex metacharacters
change what a rule means.

Every fixture in `TestRealRobotsFiles` is the shape of a robots.txt actually
served by a client domain in `avp_dev`, transcribed at the time Epic F was
built. They are the regression net for the semantics, not decoration.
"""

from __future__ import annotations

from avp_api.services.ai_crawlers import (
    AGENTS,
    AccessVerdict,
    AgentPurpose,
    RuleSource,
    evaluate_robots,
    unknown_access,
)


def verdict_for(body: str, token: str) -> AccessVerdict:
    for row in evaluate_robots(body):
        if row.agent.token == token:
            return row.verdict
    raise AssertionError(f"{token} is not in the roster")


def row_for(body: str, token: str):
    for row in evaluate_robots(body):
        if row.agent.token == token:
            return row
    raise AssertionError(f"{token} is not in the roster")


class TestGroupSelection:
    """RFC 9309 §2.2.1 — the most specific group applies, and ONLY that one."""

    def test_a_named_group_suppresses_the_wildcard_group_entirely(self) -> None:
        # The classic misreading is to apply `*` as well, which would report
        # GPTBot as blocked here. It is named, so only its own group counts.
        body = "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /\n"
        assert verdict_for(body, "GPTBot") is AccessVerdict.ALLOWED
        # ...while an agent with no group of its own does inherit the block.
        assert verdict_for(body, "ClaudeBot") is AccessVerdict.BLOCKED

    def test_the_source_records_which_group_decided(self) -> None:
        body = "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /\n"
        assert row_for(body, "GPTBot").source is RuleSource.EXPLICIT
        assert row_for(body, "ClaudeBot").source is RuleSource.WILDCARD

    def test_agent_matching_is_case_insensitive(self) -> None:
        body = "user-agent: gptbot\nDisallow: /\n"
        assert verdict_for(body, "GPTBot") is AccessVerdict.BLOCKED

    def test_consecutive_user_agent_lines_share_one_group(self) -> None:
        # Two tokens, one rule set. If the parser opened a new group on the
        # second line, ClaudeBot would come out UNSPECIFIED instead.
        body = "User-agent: GPTBot\nUser-agent: ClaudeBot\nDisallow: /\n"
        assert verdict_for(body, "GPTBot") is AccessVerdict.BLOCKED
        assert verdict_for(body, "ClaudeBot") is AccessVerdict.BLOCKED

    def test_a_user_agent_line_after_a_rule_starts_a_new_group(self) -> None:
        # No blank line between them. The boundary is the rule, not the
        # whitespace, and a parser keying on blank lines merges these two.
        body = "User-agent: GPTBot\nDisallow: /\nUser-agent: ClaudeBot\nAllow: /\n"
        assert verdict_for(body, "GPTBot") is AccessVerdict.BLOCKED
        assert verdict_for(body, "ClaudeBot") is AccessVerdict.ALLOWED

    def test_a_non_rule_field_does_not_end_a_group(self) -> None:
        # Sitemap/Crawl-delay sit inside groups in the wild. Treating one as a
        # rule would make the following user-agent line open a new group.
        body = (
            "User-agent: GPTBot\n"
            "User-agent: ClaudeBot\n"
            "Crawl-delay: 10\n"
            "Disallow: /\n"
        )
        assert verdict_for(body, "ClaudeBot") is AccessVerdict.BLOCKED


class TestPathRules:
    """RFC 9309 §2.2.2 — longest match wins, Allow breaks ties."""

    def test_the_longest_matching_rule_decides(self) -> None:
        body = "User-agent: *\nDisallow: /\nAllow: /blog\n"
        # Root itself is still disallowed: `/` is the longer match for `/`.
        assert verdict_for(body, "GPTBot") is AccessVerdict.BLOCKED

    def test_allow_wins_an_equal_length_tie(self) -> None:
        body = "User-agent: *\nDisallow: /\nAllow: /\n"
        assert verdict_for(body, "GPTBot") is AccessVerdict.ALLOWED

    def test_an_empty_disallow_forbids_nothing(self) -> None:
        # `Disallow:` with no value is the documented way to say "allow all".
        # Treating the empty string as a prefix match would block the site.
        body = "User-agent: *\nDisallow:\n"
        assert verdict_for(body, "GPTBot") is AccessVerdict.ALLOWED
        assert row_for(body, "GPTBot").disallow_rules == 0

    def test_a_path_with_regex_metacharacters_is_matched_literally(self) -> None:
        # `re.compile(path)` would read `(` as a group and raise, or worse,
        # silently match something else. Only `*` and a trailing `$` are
        # wildcards in robots.txt.
        body = "User-agent: *\nDisallow: /a+b(c)?\n"
        assert verdict_for(body, "GPTBot") is AccessVerdict.ALLOWED

    def test_the_dollar_anchor_is_honoured(self) -> None:
        body = "User-agent: *\nDisallow: /*.md$\n"
        # It anchors to end-of-path, so the root is untouched.
        assert verdict_for(body, "GPTBot") is AccessVerdict.ALLOWED

    def test_a_star_wildcard_can_block_the_root(self) -> None:
        body = "User-agent: *\nDisallow: /*\n"
        assert verdict_for(body, "GPTBot") is AccessVerdict.BLOCKED

    def test_comments_are_stripped_including_trailing_ones(self) -> None:
        body = "User-agent: *  # everyone\nDisallow: /  # the whole site\n"
        assert verdict_for(body, "GPTBot") is AccessVerdict.BLOCKED

    def test_rules_before_any_user_agent_line_are_ignored(self) -> None:
        # They belong to no group. Attributing them to `*` would invent a
        # policy the file does not state.
        body = "Disallow: /\n\nUser-agent: *\nAllow: /\n"
        assert verdict_for(body, "GPTBot") is AccessVerdict.ALLOWED


class TestSilenceIsAnAnswerButAnUnreadableFileIsNot:
    """The distinction the module exists to keep — see its docstring."""

    def test_an_agent_named_nowhere_with_no_wildcard_is_unspecified(self) -> None:
        body = "User-agent: Googlebot\nDisallow: /private/\n"
        row = row_for(body, "GPTBot")
        assert row.verdict is AccessVerdict.UNSPECIFIED
        assert row.source is RuleSource.NONE

    def test_an_empty_file_is_a_valid_allow_all_not_an_unknown(self) -> None:
        assert verdict_for("", "GPTBot") is AccessVerdict.UNSPECIFIED

    def test_an_unreadable_file_is_unknown_and_never_an_allow(self) -> None:
        rows = unknown_access()
        assert {row.verdict for row in rows} == {AccessVerdict.UNKNOWN}
        assert {row.source for row in rows} == {RuleSource.UNREADABLE}

    def test_unknown_is_not_reachable_by_parsing_any_body(self) -> None:
        # The only way to get UNKNOWN is `unknown_access()`. If a parse could
        # produce it, a malformed file would be indistinguishable from a
        # network failure — which is the conflation the split prevents.
        for body in ("", "garbage\n", "User-agent: *\nDisallow: /\n", ":::\n"):
            assert all(
                row.verdict is not AccessVerdict.UNKNOWN for row in evaluate_robots(body)
            )


class TestTheRoster:
    def test_every_agent_is_covered_exactly_once_per_evaluation(self) -> None:
        rows = evaluate_robots("User-agent: *\nAllow: /\n")
        assert len(rows) == len(AGENTS)
        assert len({row.agent.token for row in rows}) == len(AGENTS)

    def test_tokens_are_unique_case_insensitively(self) -> None:
        # Two entries differing only in case would both match the same group
        # and double-count in every tally the screen shows.
        lowered = [agent.token.lower() for agent in AGENTS]
        assert len(set(lowered)) == len(lowered)

    def test_search_and_training_agents_both_exist_from_one_vendor(self) -> None:
        # The purpose split is only meaningful if it cuts across vendors —
        # otherwise it is just a vendor list wearing a second name.
        openai = {a.purpose for a in AGENTS if a.vendor == "OpenAI"}
        assert AgentPurpose.TRAINING in openai
        assert AgentPurpose.SEARCH in openai

    def test_google_extended_is_classified_as_training_not_search(self) -> None:
        # It gates Gemini training and grounding, NOT Googlebot's index.
        # Filing it under SEARCH would tell a client that blocking it costs
        # them classic search traffic, which is false.
        agent = next(a for a in AGENTS if a.token == "Google-Extended")
        assert agent.purpose is AgentPurpose.TRAINING


class TestRealRobotsFiles:
    """Transcribed from the live client domains in `avp_dev`, Epic F.

    These are the files that killed the first draft's `PARTIAL` verdict: seven
    of nine carry housekeeping disallows, so a verdict keyed on "has any
    Disallow" fired almost always and said nothing.
    """

    NOTION = (
        "# Notion is hiring!\n"
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /invite/\n"
        "Disallow: /*/invite/\n"
        "Disallow: /embed/*\n"
        "\n"
        "User-agent: BLEXBot\n"
        "Disallow: /\n"
        "\n"
        "User-agent: Amazonbot\n"
        "Disallow: /\n"
        "\n"
        "User-agent: SemrushBot\n"
        "Disallow: /\n"
    )

    # Only named search-engine groups, no `*` group at all.
    POSTHOG = (
        "User-agent: Googlebot\nDisallow: /*.md$\n\n"
        "User-agent: Bingbot\nDisallow: /*.md$\n\n"
        "Sitemap: https://posthog.com/sitemap/sitemap-index.xml\n"
    )

    # A single Sitemap line and nothing else.
    PLAUSIBLE = "Sitemap: https://plausible.io/sitemap.xml\n"

    LINEAR = (
        "User-Agent: *\nDisallow: /api/\nDisallow: /cdn-cgi/\nAllow: /api/og/\n\n"
        "Sitemap: https://linear.app/sitemap.xml\n"
    )

    def test_notion_blocks_amazonbot_explicitly_and_allows_every_other(self) -> None:
        # The real finding on real data: five SEO and commerce crawlers are
        # fenced out by name while every AI crawler walks in through `*`.
        rows = {row.agent.token: row for row in evaluate_robots(self.NOTION)}
        assert rows["Amazonbot"].verdict is AccessVerdict.BLOCKED
        assert rows["Amazonbot"].source is RuleSource.EXPLICIT
        assert rows["GPTBot"].verdict is AccessVerdict.ALLOWED
        assert rows["ClaudeBot"].verdict is AccessVerdict.ALLOWED
        assert rows["PerplexityBot"].verdict is AccessVerdict.ALLOWED
        blocked = [t for t, r in rows.items() if r.verdict is AccessVerdict.BLOCKED]
        assert blocked == ["Amazonbot"]

    def test_posthog_names_search_engines_and_no_ai_crawler(self) -> None:
        # No `*` group, so every AI agent is UNSPECIFIED rather than allowed
        # by inheritance. A parser that defaulted a missing `*` to the last
        # group seen would report them all as blocked from `.md` files.
        rows = evaluate_robots(self.POSTHOG)
        assert {row.verdict for row in rows} == {AccessVerdict.UNSPECIFIED}

    def test_plausible_has_no_rules_at_all(self) -> None:
        rows = evaluate_robots(self.PLAUSIBLE)
        assert {row.verdict for row in rows} == {AccessVerdict.UNSPECIFIED}

    def test_housekeeping_disallows_do_not_downgrade_the_verdict(self) -> None:
        # `/api/` and `/cdn-cgi/` say nothing about AI visibility. The verdict
        # stays ALLOWED and the count is carried separately so an operator can
        # still see there is a rule set worth reading.
        rows = {row.agent.token: row for row in evaluate_robots(self.LINEAR)}
        assert rows["GPTBot"].verdict is AccessVerdict.ALLOWED
        assert rows["GPTBot"].disallow_rules == 2
