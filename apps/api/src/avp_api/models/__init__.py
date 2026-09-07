"""SQLAlchemy models for the entities in product-spec.md §5.3.

Importing this package registers every model on `Base.metadata`, which is what
Alembic autogenerate reflects against. A model that is not imported here is
invisible to migrations, so every new model must be added to `__all__`.
"""

from .action_item import (
    ActionItem,
    ActionItemSource,
    ActionItemStatus,
    Effort,
    Priority,
)
from .ai_crawler_access import AiCrawlerAccess
from .alert import Alert, AlertKind
from .base import Base
from .client import ClassificationStatus, Client, ClientKind
from .competitor import (
    Competitor,
    CompetitorSet,
    DetectionSource,
    DetectionStatus,
)
from .engine_result import (
    BrandMention,
    Citation,
    CitationType,
    Engine,
    EngineResult,
    EngineResultStatus,
    Sentiment,
)
from .password_reset import PasswordResetToken
from .prompt import Prompt, PromptIntent, PromptSet
from .prompt_run import (
    PromptRun,
    PromptRunBrand,
    PromptRunCitation,
    PromptRunResult,
    PromptRunStatus,
)
from .scan import Scan, ScanStatus, ScanTrigger
from .score import DEFAULT_WEIGHTS, DIMENSION_KEYS, Score, ScoreStatus
from .technical_audit import AuditStatus, CheckStatus, TechnicalAudit, TechnicalAuditCheck
from .tenancy import Agency, Invitation, User, UserRole, UserStatus

__all__ = [
    "DEFAULT_WEIGHTS",
    "PasswordResetToken",
    "DIMENSION_KEYS",
    "ActionItem",
    "ActionItemSource",
    "ActionItemStatus",
    "Agency",
    "Base",
    "BrandMention",
    "AuditStatus",
    "CheckStatus",
    "ClassificationStatus",
    "Citation",
    "CitationType",
    "Client",
    "ClientKind",
    "Competitor",
    "CompetitorSet",
    "DetectionSource",
    "DetectionStatus",
    "Effort",
    "AiCrawlerAccess",
    "Alert",
    "AlertKind",
    "Engine",
    "EngineResult",
    "EngineResultStatus",
    "Invitation",
    "Priority",
    "Prompt",
    "PromptIntent",
    "PromptSet",
    "PromptRun",
    "PromptRunBrand",
    "PromptRunCitation",
    "PromptRunResult",
    "PromptRunStatus",
    "Scan",
    "ScanStatus",
    "ScanTrigger",
    "Score",
    "ScoreStatus",
    "Sentiment",
    "TechnicalAudit",
    "TechnicalAuditCheck",
    "User",
    "UserRole",
    "UserStatus",
]