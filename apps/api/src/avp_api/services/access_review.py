"""The one-time "Check who has access" review — hardening A6 part 4, launch checklist 3.123.

WHY IT EXISTS
-------------
A Google sign-in whose verified address already has a password account LINKS to it, and the link
removes the password and ends the sessions (`google_oauth`: "A link takes the old credentials with
it"). Password sign-up never verified the address, so whoever registered it first may not have held
the inbox, and whatever they set up while they held the account stays: the seats they added, the
invitations they had seen accepted. The link takes the earlier holder's way in; it cannot know which
of the people they let in the real holder wants. So the real holder is asked, once. The founder's
decision (2026-10-11): Settings > Team shows the holder the people who already had access, each with
Remove, and one "Keep these people" button that closes the list.

WHAT IS STORED, AND WHEN
------------------------
Two nullable timestamps on `users` and nothing else (no list is stored; the people are read from the
roster when the page is):

* `google_linked_at`, written by `google_oauth._link` in the link's own transaction, and ONLY when
  the link replaced a password. A Google-first account (created through Google) and an invited seat
  that joined through Google had no password to replace, so they have nothing to review. An account
  linked before this existed has no value and gets no review: the time of that link was never
  recorded.
* `access_review_done_at`, written by `keep`. There is no un-keep.

Both are cleared when `invitations.invite_seat` gives a seat a new life (revives a removed seat or
re-issues an invited one), as `google_sub` is: whoever accepts then is not the person the link was.

WHO SEES IT, AND WHO IS LISTED
------------------------------
* **The holder alone, and only an owner or an admin.** The review is read from the REQUESTER's own
  row, so another owner or admin of the same agency never gets the holder's (they get their own, if
  their own link replaced a password). It rides the seat list (`GET /agencies/{agencyId}/seats`),
  which is owner-or-admin only; a member holder has nothing of theirs to review (only an owner's or
  an admin's account could have set up access, because invitations are admin-only), and
  `people_to_review` says so itself rather than relying on that route's gate. `keep` says it too, in
  its UPDATE: a member's call matches nothing, so a member whose link replaced a password still has
  their review if they are promoted later.
* **The other seat holders the roster already shows** (not removed, any status), **whose row was
  created before `google_linked_at`**, and never the holder. A person who accepted an invitation
  holds one of those rows, so accepted invitations are covered by them; a removed person has no
  access and is not listed. A seat that was removed before the link and invited again after it is
  still listed, because its row is older than the link (no column records when a seat's access
  began), and so is an invitee whose invitation the link revoked: the holder can Remove either, as
  they could from the roster.
* **Only ids leave this module.** The names and addresses are the roster's own, in the same
  response.

When nobody is left to list (everyone was removed, or there was nobody) the review has no one to
show and the response carries none; the stored timestamps are not touched by that.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User, UserRole

# The roles that can have a review: the ones that manage seats, `deps.ADMIN_ROLES`. This module
# cannot import `deps` (which imports the services), so `tests/test_access_review.py` pins that the
# two are the same.
REVIEWING_ROLES: frozenset[UserRole] = frozenset({UserRole.OWNER, UserRole.ADMIN})


def people_to_review(holder: User, seat_holders: Sequence[User]) -> list[str]:
    """The ids of the people `holder` should look at, in the roster's order; empty for no review.

    `holder` is the requester, read from their own row: there is no way to ask for someone else's.
    `seat_holders` is the roster (`invitations.seat_holders`). Reads and writes nothing.
    """
    if holder.role not in REVIEWING_ROLES:
        return []
    linked = holder.google_linked_at
    if linked is None or holder.access_review_done_at is not None:
        return []
    return [
        person.id
        for person in seat_holders
        if person.id != holder.id and person.deleted_at is None and person.created_at < linked
    ]


async def lock_holder(session: AsyncSession, user_id: str) -> None:
    """Take the caller's own row `FOR NO KEY UPDATE`, so that "Keep these people" waits for a link
    that is in flight and then reads what it committed.

    The link's UPDATE of the row (`google_oauth._link`: the Google account, the link time, no
    password) holds the row until the link commits, with the credential cutoff that ends the
    earlier holder's sessions. A Keep request that was let in on one of those sessions before the
    commit would otherwise find the link time set when its UPDATE ran after it, and close the
    review the real holder has not seen. Waiting here, the route then asks again whether its
    session still stands (`deps.reconfirm_principal`), as the removal and invitation routes do
    under the agency's lock; the agency's lock is not the one that serialises with a link, because
    the link does not take it, and this row is.

    `FOR NO KEY UPDATE`, not `FOR UPDATE`, for the reason `seats.lock_agency` gives: a row that
    names this person as its author (a scan, a prompt run, an invitation) takes `FOR KEY SHARE` on
    their row until its own transaction commits, and `FOR UPDATE` would wait behind it.
    `FOR NO KEY UPDATE` does not conflict with that, and does conflict with the link's UPDATE.
    Holds only this one row, so it cannot be one half of a deadlock. Released with the transaction.
    """
    await session.execute(select(User.id).where(User.id == user_id).with_for_update(key_share=True))


async def keep(session: AsyncSession, *, user_id: str, agency_id: str) -> bool:
    """Close the holder's review ("Keep these people"). True when this call closed it.

    One conditional UPDATE of the caller's own row, so it is atomic and idempotent: the first call
    is the fact and a second (a stale page, a double click) matches nothing and changes nothing. It
    matches only the row of an owner or an admin whose link replaced a password and whose review is
    still open. A member, a caller whose link replaced no password (the usual one) and a caller who
    has already answered are left exactly as they were. An owner or admin whose link replaced a
    password and who has nobody to list is recorded, harmlessly: there was nothing to answer.
    The caller commits, having called `lock_holder` and asked again whether its session stands.
    """
    closed = (
        await session.execute(
            update(User)
            .where(
                User.id == user_id,
                User.agency_id == agency_id,
                User.role.in_(list(REVIEWING_ROLES)),
                User.google_linked_at.is_not(None),
                User.access_review_done_at.is_(None),
            )
            .values(access_review_done_at=datetime.now(UTC))
            .returning(User.id)
            .execution_options(synchronize_session=False)
        )
    ).first()
    return closed is not None
