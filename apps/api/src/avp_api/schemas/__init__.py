"""Pydantic request/response models.

All response models emit camelCase per docs/api-contracts.md. The alias
generator is applied once on a shared base so no schema has to remember.
"""

from .action_item import ActionItemListOut, ActionItemOut
from .audit import AuditCheckOut, TechnicalAuditOut
from .auth import (
    AgencyOut,
    LoginRequest,
    MeOut,
    SeatUsageOut,
    SignUpRequest,
    UserOut,
)
from .client import (
    ClientDetailOut,
    ClientOut,
    CrawlSummaryOut,
    CreateClientRequest,
)
from .common import ApiModel, Page
from .competitor import (
    CompetitorInput,
    CompetitorOut,
    CompetitorSetOut,
    ReplaceCompetitorsRequest,
)
from .dashboard import DashboardOut, ScanSummaryOut
from .scan import (
    BrandMentionOut,
    CitationOut,
    EngineResultOut,
    PromptOut,
    PromptSetOut,
    RunScanRequest,
    ScanDetailOut,
    ScanOut,
)
from .score import CompetitorScoreOut, ScoreDetailOut, ScoreOut

__all__ = [
    "ActionItemListOut",
    "ActionItemOut",
    "AgencyOut",
    "ApiModel",
    "AuditCheckOut",
    "TechnicalAuditOut",
    "CompetitorScoreOut",
    "ScoreDetailOut",
    "ScoreOut",
    "BrandMentionOut",
    "CitationOut",
    "EngineResultOut",
    "PromptOut",
    "PromptSetOut",
    "RunScanRequest",
    "ScanDetailOut",
    "ScanOut",
    "CompetitorInput",
    "CompetitorOut",
    "CompetitorSetOut",
    "ReplaceCompetitorsRequest",
    "ClientDetailOut",
    "ClientOut",
    "CrawlSummaryOut",
    "CreateClientRequest",
    "DashboardOut",
    "LoginRequest",
    "MeOut",
    "Page",
    "ScanSummaryOut",
    "SeatUsageOut",
    "SignUpRequest",
    "UserOut",
]
