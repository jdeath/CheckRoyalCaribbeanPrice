"""Shared runtime and authentication data models for royal_caribbean."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "AccountInfo",
    "APIAccess",
]


@dataclass
class APIAccess:
    """Authentication session container holding current digital passport tokens.

    Maintains the server-assigned user 'id', OAuth bearer token strings, and the
    persistent network connection session pool context.
    """

    token: str
    id: str
    session: Any
    loyalty_number: Optional[str] = None


@dataclass
class AccountInfo:
    """User credential profile used to initialize authenticated client sessions."""

    username: str
    password: str
    state: Optional[str] = None
    senior: bool = False
    military: bool = False
    fire: bool = False
    police: bool = False
    cruise_line: Optional[str] = "royalcaribbean"
    casino_offers_only: bool = False
    access: Optional[APIAccess] = None
    found_items: Set[str] = field(default_factory=set)
    loyalty_tier: Optional[str] = None
    loyalty_points: Optional[int] = None
    apobj: Optional[Any] = None

    @property
    def is_royal(self) -> bool:
        """True if target brand is Royal Caribbean."""
        return self.cruise_line.lower() in ("royal", "royalcaribbean", "royal caribbean", "r")

    @property
    def is_celebrity(self) -> bool:
        """True if target brand is Celebrity Cruises."""
        return self.cruise_line.lower() in ("celebrity", "celebritycruises", "celebrity cruises", "c")

    @property
    def api_brand(self) -> str:
        """Internal API brand string ('royal' or 'celebrity')."""
        return "celebrity" if self.is_celebrity else "royal"

    @property
    def url_brand(self) -> str:
        """Brand path segment used in web endpoints and OAuth portals."""
        return "celebritycruises" if self.is_celebrity else "royalcaribbean"

    @property
    def friendly_name(self) -> str:
        """Presentation label for logging and user notifications."""
        return "Celebrity Cruises" if self.is_celebrity else "Royal Caribbean"


