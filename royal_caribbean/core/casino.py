"""Core Club Royale casino offer fetching, processing, and reporting functions.

Handles authentication resolution, paginated API requests to the casino offer endpoints,
and threshold-based expiration reporting/notifications.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from royal_caribbean.api.auth import USER_AGENT_WEB, get_profile, login
from royal_caribbean.config.loaders import AccountInfo
from royal_caribbean.models import APIAccess
from royal_caribbean.utils.constants import EXIT_TOTAL_FAILURE
from royal_caribbean.utils.logging import BLUE, GREEN, RED, RESET, YELLOW, log, log_err


# ==============================================================================
# Global Constants
# ==============================================================================
OFFERS_API: str = "https://www.royalcaribbean.com/api/casino/v2/offers/list"
"""Club Royale casino guest offers endpoint."""

__all__ = [
    "CasinoOffer",
    "build_account",
    "fetch_casino_offers",
    "report_offers",
]


# ==============================================================================
# Configuration Data Classes
# ==============================================================================
@dataclass
class CasinoOffer:
    """A single Club Royale casino offer parsed from the guest offers API.

    Captures the bookable-offer essentials a player tracks: the redemption code,
    the offer type, the reserve-by deadline, and any FreePlay/perk sweeteners.
    """

    offer_code: str
    name: str
    offer_type_code: str
    offer_type_name: str
    reserve_by_date: Optional[str]
    campaign_name: str
    status: str
    perks: List[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> CasinoOffer:
        """Builds a CasinoOffer from one element of the API 'offers' array."""
        offer = raw.get("campaignOffer") or {}
        offer_type = offer.get("offerType") or {}
        perks = [
            p.get("perkName", "")
            for p in (offer.get("perkCodes") or [])
            if p.get("perkName")
        ]
        return cls(
            offer_code=offer.get("offerCode", "?"),
            name=offer.get("name") or raw.get("campaignName", ""),
            offer_type_code=offer_type.get("code", ""),
            offer_type_name=offer_type.get("name", ""),
            reserve_by_date=offer.get("reserveByDate"),
            campaign_name=raw.get("campaignName", ""),
            status=offer.get("status") or raw.get("status", ""),
            perks=perks,
        )

    @property
    def is_complimentary(self) -> bool:
        """True for a Complimentary (COMP) offer."""
        return self.offer_type_code == "COMP"

    def days_until_reserve_by(self) -> Optional[int]:
        """Whole days from now until the offer's reserve-by deadline."""
        if not self.reserve_by_date:
            return None
        try:
            date_str = self.reserve_by_date.replace("Z", "+00:00")
            deadline = datetime.fromisoformat(date_str)
            return (deadline - datetime.now(timezone.utc)).days
        except (ValueError, TypeError):
            return None


# ==============================================================================
# Authentication & Initialization
# ==============================================================================
def build_account(data: Dict[str, Any]) -> AccountInfo:
    """Logs in and resolves the loyalty number for the primary account."""
    account_info_list = data.get("accountInfo") or []
    if not account_info_list:
        log_err("No accountInfo in config; this tracker needs a logged-in account.")
        sys.exit(EXIT_TOTAL_FAILURE)

    account = account_info_list[0]
    account_info = AccountInfo(
        username=account["username"],
        password=account["password"],
        cruise_line=account.get("cruiseLine", "royalcaribbean"),
    )

    account_info.access = login(account_info)
    _state, loyalty_number, _points = get_profile(account_info)
    account_info.access.loyalty_number = loyalty_number
    return account_info

# ==============================================================================
# API Processing Logic
# ==============================================================================
def fetch_casino_offers(account_info: AccountInfo) -> List[CasinoOffer]:
    """Retrieves all active Club Royale offers for the account, following pagination.
    TODO: Should we use _execute_api_request instead?

    Args:
        account_info: Logged-in account with active API access credentials.

    Returns:
        List of parsed CasinoOffer objects (empty on failure or no offers).
    """
    loyalty_id = account_info.access.loyalty_number if account_info.access else None
    if not loyalty_id:
        log(f"{YELLOW}No loyalty ID associated with account '{account_info.username}'. Skipping casino offers lookup.{RESET}")
        return []

    token = account_info.access.token
    headers = {
        "User-Agent": USER_AGENT_WEB,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "country": "USA",
        "Authorization": f"Bearer {token}",
        "x-account-id": account_info.access.id,
        "x-loyalty-id": str(account_info.access.loyalty_number or ""),
    }
    cookies = {"accessToken": token, "country": "USA"}

    offers: List[CasinoOffer] = []
    page, total_pages = 1, 1
    while page <= total_pages:
        params = {
            "sortBy": "offer.reserveByDate",
            "sortDirection": "asc",
            "limit": "100",
            "page": str(page),
            "digitalRedemption": "true",
        }
        try:
            response = account_info.access.session.get(
                OFFERS_API, params=params, headers=headers, cookies=cookies
            )
        except Exception as e:
            log(
                "Can't contact cruise line servers; please try again later\n"
                f"(program exception '{e}')"
            )
            return offers

        if response.status_code != 200:
            log(f"{RED}Casino offers API returned HTTP {response.status_code}{RESET}")
            return offers

        try:
            payload = response.json()
        except Exception as e:
            log(f"{RED}Failed to decode JSON response from Casino API: {e}{RESET}")
            return offers

        offers.extend(
            CasinoOffer.from_api(o) for o in (payload.get("offers") or [])
        )
        total_pages = payload.get("totalPages", 1) or 1
        page += 1

    return offers


# ==============================================================================
# Reporting & Notifications
# ==============================================================================
def report_offers(
    offers: List[CasinoOffer], warn_days: int, apobj: Optional[Any]
) -> None:
    """Prints offer summaries to console and triggers alerts for impending deadlines.

    Args:
        offers: Active offers retrieved for the guest account.
        warn_days: Threshold in days to flag reserve-by expiration warnings.
        apobj: Apprise notification instance (optional).
    """
    if not offers:
        log("No active casino offers found.")
        return

    log(f"\n{BLUE}Club Royale offers: {len(offers)} active{RESET}")

    alerts: List[tuple[int, str]] = []
    for offer in offers:
        days = offer.days_until_reserve_by()
        by_display = (
            offer.reserve_by_date[:10]
            if offer.reserve_by_date
            else "no deadline"
        )
        deadline = f"reserve by {by_display}" + (
            f" ({days} days)" if days is not None else ""
        )

        line = f"  {offer.offer_code}  {offer.name} [{offer.offer_type_name}]"
        if offer.perks:
            line += f"  +{', '.join(offer.perks)}"

        expiring = days is not None and days <= warn_days
        if expiring:
            colour, tag = RED, f"{RED}[EXPIRING]{RESET} "
            alert_text = (
                f"{offer.offer_code} {offer.name} [{offer.offer_type_name}] -"
                f" {deadline}"
                + (f" +{', '.join(offer.perks)}" if offer.perks else "")
            )
            alerts.append((days, alert_text))
        elif offer.is_complimentary:
            colour, tag = YELLOW, f"{YELLOW}[COMP: 2nd guest discounted]{RESET} "
        else:
            colour, tag = GREEN, ""

        log(f"{colour}{line}{RESET}\n      {tag}{deadline}")

    if alerts:
        alerts.sort()
        body = (
            f"{len(alerts)} Club Royale offer(s) expiring within {warn_days} days:\n"
            + "\n".join(f"- {text}" for _, text in alerts)
        )
        log(f"\n{RED}{body}{RESET}")
        if apobj is not None:
            apobj.notify(body=body, title="Club Royale Offer Expiring")
    else:
        log(
            f"\n{GREEN}No offers within {warn_days} days of their reserve-by deadline.{RESET}"
        )

