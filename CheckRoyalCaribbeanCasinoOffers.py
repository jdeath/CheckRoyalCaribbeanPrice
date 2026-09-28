from __future__ import annotations

import argparse
import sys

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

import CheckRoyalCaribbeanPrice as crc
from CheckRoyalCaribbeanPrice import (
    BLUE,
    GREEN,
    RED,
    RESET,
    USER_AGENT_WEB,
    YELLOW,
    AccountInfo,
    build_apprise,
    expand_env_vars,
    get_profile,
    log,
    log_err,
    log_warn,
    login,
    setup_hybrid_logging,
)

##################################
# Global Constants & Variables
##################################
# Club Royale casino guest offers endpoint. Auth requires the account bearer token
# plus the x-account-id and x-loyalty-id identity headers and a USA country header.
OFFERS_API = "https://www.royalcaribbean.com/api/casino/v2/offers/list"


##################################
# Data Classes
##################################
@dataclass
class CasinoOffer:
    """A single Club Royale casino offer parsed from the guest offers API.

    Captures the bookable-offer essentials a player tracks: the redemption code,
    the offer type, the reserve-by deadline, and any FreePlay/perk
    sweeteners.

    On offer type: the primary guest's fare is comped in both COMP and GOBO
    offers. The difference is the second guest - COMP discounts (or comps) the
    companion fare, while GOBO ("Get One, Buy One") charges the full going rate
    - so a COMP is generally the more valuable of the two. The API's description
    text does NOT reliably distinguish them (it is templated), so key on
    offer_type_code, not the description.
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
        """Builds a CasinoOffer from one element of the API 'offers' array.

        Args:
            raw (Dict[str, Any]): A single offer record, including its nested
              'campaignOffer' pricing/terms object.

        Returns:
            CasinoOffer: The flattened, typed offer.
        """
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
        """True for a Complimentary (COMP) offer, where the second guest fare
        is discounted or comped rather than full price - generally more valuable
        than a GOBO."""
        return self.offer_type_code == "COMP"

    def days_until_reserve_by(self) -> Optional[int]:
        """Whole days from now until the offer's reserve-by deadline.

        Returns:
            Optional[int]: Days remaining, or None if there is no parseable date.
        """
        if not self.reserve_by_date:
            return None
        try:
            date_str = self.reserve_by_date.replace("Z", "+00:00")
            deadline = datetime.fromisoformat(date_str)
            return (deadline - datetime.now(timezone.utc)).days
        except (ValueError, TypeError):
            return None


##################################
# Config & Authentication
##################################
def load_config_file(config_path: str) -> Dict[str, Any]:
    """Loads and parses the YAML configuration file with UTF-8 primary decoding
    and system locale fallback for maximum cross-platform compatibility."""
    try:
        with open(config_path, "r", encoding="utf-8") as file:
            raw_data = yaml.safe_load(file)
    except UnicodeDecodeError:
        # Fallback for legacy non-UTF-8 files saved on Windows (e.g., CP1252/ANSI)
        with open(config_path, "r") as file:
            raw_data = yaml.safe_load(file)

    # Resolve ${VAR} placeholders (e.g. passwords kept out of config.yaml)
    return expand_env_vars(raw_data or {})


def build_account(data: Dict[str, Any]) -> AccountInfo:
    """Logs in and resolves the loyalty number, mirroring the main script's flow.

    Args:
        data (Dict[str, Any]): The parsed configuration mapping (first
          accountInfo used).

    Returns:
        AccountInfo: A logged-in account with its access session and loyalty number.
    """
    account_info_list = data.get("accountInfo") or []
    if not account_info_list:
        log_err(
            "No accountInfo in config; this tracker needs a logged-in account."
        )
        sys.exit(1)

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


##################################
# Casino Offers API
##################################
def fetch_casino_offers(account_info: AccountInfo) -> List[CasinoOffer]:
    """Retrieves all active Club Royale offers for the account, following
    pagination.

    Args:
        account_info (AccountInfo): A logged-in account with an active access session.

    Returns:
        List[CasinoOffer]: The parsed active offers (empty on failure).
    """
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
                "Can't contact cruise line servers; please try again"
                f" later\n(program exception '{e}')"
            )
            return offers

        if response.status_code != 200:
            log(
                f"{RED}Casino offers API returned HTTP"
                f" {response.status_code}{RESET}"
            )
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


##################################
# Reporting
##################################
def report_offers(
    offers: List[CasinoOffer], warn_days: int, apobj: Optional[Any]
) -> None:
    """Prints every offer and alerts on those whose reserve-by deadline is near.

    Complimentary (COMP) offers are highlighted, since their second-guest fare is
    discounted or comped rather than full price. Offers within warn_days of their
    reserve-by date are flagged and, if apprise is configured, sent as a
    notification.

    Args:
        offers (List[CasinoOffer]): The active offers to report.
        warn_days (int): Alert when an offer's reserve-by date is within this
          many days.
        apobj (Optional[Apprise]): Notifier for alerts, or None for console
          only.
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
            f"{len(alerts)} Club Royale offer(s) expiring within"
            f" {warn_days} days:\n"
            + "\n".join(f"- {text}" for _, text in alerts)
        )
        log(f"\n{RED}{body}{RESET}")
        if apobj is not None:
            apobj.notify(body=body, title="Club Royale Offer Expiring")
    else:
        log(
            f"\n{GREEN}No offers within {warn_days} days of their reserve-by"
            f" deadline.{RESET}"
        )


##################################
# Main execution path
##################################
def main() -> None:
    """Loads config, authenticates, fetches Club Royale offers, and reports deadlines."""
    parser = argparse.ArgumentParser(
        description="Check Royal Caribbean Casino Offers"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--warn-days",
        type=int,
        default=14,
        help=(
            "Alert when an offer's reserve-by date is within this many days"
            " (default: 14)"
        ),
    )
    args = parser.parse_args()

    data = load_config_file(args.config)
    setup_hybrid_logging(data.get("logFile"))

    # setup_hybrid_logging() only rebinds the loggers inside CheckRoyalCaribbeanPrice;
    # the names imported above are still its pre-setup None placeholders.
    global log, log_warn, log_err
    log, log_warn, log_err = crc.log, crc.log_warn, crc.log_err

    apobj = build_apprise(data.get("apprise") or [])
    account_info = build_account(data)
    offers = fetch_casino_offers(account_info)
    report_offers(offers, args.warn_days, apobj)


if __name__ == "__main__":
    main()
