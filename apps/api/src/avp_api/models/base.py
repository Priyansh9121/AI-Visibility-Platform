"""Declarative base, shared column types, and mixins.

Conventions applied across every table:

* **Primary keys are prefixed ULIDs** stored as text (see ids.py). Self-
  describing in logs, and lexicographically time-sortable so `ORDER BY id`
  doubles as a cursor.
* **Enumerations are VARCHAR + CHECK, not native Postgres ENUM types.** Native
  enums validate just as well but adding a value means `ALTER TYPE`, which
  interacts badly with migration tooling and long-running transactions.
  Dropping and recreating a CHECK constraint is ordinary, transactional DDL.
* **Timestamps are `TIMESTAMPTZ`**, defaulted server-side. Storing naive local
  timestamps in a product that reports "scanned on" dates to clients in other
  timezones is a defect waiting to happen.
* **Soft delete via `deleted_at`.** An agency that removes a client must not
  cascade-destroy the scan history that its historical reports reference.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit, deterministic constraint naming. Without this, Alembic autogenerate
# emits unnamed constraints that cannot be reliably dropped in a later
# migration, and the names differ between environments.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Length ceiling for a prefixed ULID: 4-char prefix + '_' + 26-char ULID = 31.
# 40 leaves room for a longer prefix without a migration.
ID_LENGTH = 40


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} {pk}>"


def id_column() -> Mapped[str]:
    return mapped_column(String(ID_LENGTH), primary_key=True)


def fk_column(target: str, *, nullable: bool = False, ondelete: str = "CASCADE") -> Any:
    from sqlalchemy import ForeignKey

    return mapped_column(
        String(ID_LENGTH),
        ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
        index=True,
    )


def enum_column(
    py_enum: type[enum.Enum], *, name: str, nullable: bool = False, default: Any = None
) -> Any:
    """VARCHAR-backed enum with a CHECK constraint. See module docstring."""
    return mapped_column(
        Enum(
            py_enum,
            name=name,
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
            create_constraint=True,
        ),
        nullable=nullable,
        default=default,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


def score_column(*, nullable: bool = True) -> Any:
    """A 0-100 score stored as NUMERIC, never float.

    docs/scoring-spec.md determinism rule 3: "Use Decimal, not float, for the
    weighted sum. Binary floats make 0.30 x 33.33 platform-fragile at the
    rounding boundary." NUMERIC(5,2) round-trips to Python `Decimal`, so the
    guarantee holds end to end rather than only inside the scoring function.

    Range enforcement lives in the owning model's `__table_args__` as an
    explicit CheckConstraint — `info=` metadata does not emit DDL, and a range
    check that silently does nothing is worse than no check at all.
    """
    from sqlalchemy import Numeric

    return mapped_column(Numeric(5, 2), nullable=nullable)


def score_range_check(column: str) -> CheckConstraint:
    """CHECK constraint asserting a score column stays within 0-100."""
    return CheckConstraint(
        f"{column} IS NULL OR ({column} >= 0 AND {column} <= 100)",
        name=f"{column}_range",
    )
