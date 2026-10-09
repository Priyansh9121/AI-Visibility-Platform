"""Rival suggestions — schemas (F2, 2026-10-09).

Facts only: a domain, the name to add it with (made from the domain's own label), and two counts.
Nothing here can carry an answer's or a page's words.
"""

from __future__ import annotations

from .common import ApiModel


class RivalSuggestionOut(ApiModel):
    """A domain the latest scan's answers named, offered to the agency as a rival.

    A suggestion, not a finding: it changes nothing until the agency adds it through
    `PUT /clients/{clientId}/competitors`, and asking about it writes nothing.
    """

    # The registrable domain, as the scan cited it.
    domain: str
    # The name to add it with: its label, in capitals when it is two to four letters ("UNSW"),
    # else capitalised ("Deakin"). The agency can rename the rival in the competitor editor.
    name: str
    # How many times the scan's answered results cited it (the count the report's list uses).
    citations: int
    # How many of the scan's answers named a brand whose name matches the domain's label.
    answers_naming: int


class RivalSuggestionsOut(ApiModel):
    """The suggestions for one client, most cited first, then by domain. Empty if there are none."""

    suggestions: list[RivalSuggestionOut]
