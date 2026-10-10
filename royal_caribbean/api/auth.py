"""Authentication and account profile retrieval for Royal Caribbean API services."""

from __future__ import annotations

import base64
import json

try:
    import requests as plain_requests
except ImportError:
    import plain_requests  # Fallback fallback

import sys

from typing import Tuple
from urllib.parse import quote

from royal_caribbean.api.client import IMPERSONATE_ARGS, _execute_api_request
from royal_caribbean.models import APIAccess, AccountInfo
from royal_caribbean.utils.constants import (
    APPKEY_WEB,
    EXIT_TOTAL_FAILURE,
    REQUEST_TIMEOUT,
    SHORT_REQUEST_TIMEOUT,
    USER_AGENT_WEB
)
from royal_caribbean.utils.logging import RED, RESET, log, log_err


def login(account_info: AccountInfo) -> APIAccess:
    """
    Performs OAuth2 authentication against corporate cruise line identity endpoints.

    Submits standard encoded payloads to capture bearer authorization access tokens.
    Decodes the resulting middle payload segment via base64 to extract the underlying
    account identifier token ('sub'). Terminates the execution thread if authorization fails.

    MAINTENANCE NOTE: OAuth tokens returned by the cruise system are standard JSON Web Tokens (JWT).
    The server splits these using dots (.). Slicing index [1] isolates the base64-encoded payload string.
    Appending '==' satisfies Python's strict base64 pad requirements to prevent standard padding crashes.

    The 'Basic' Authorization hash is a universal hardcoded client, client-id
    and secret utilized by the cruise line's public mobile app and web infrastructure
    to secure the background OAuth handshake process.
    """
    session = new_api_session()
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Basic ZzlTMDIzdDc0NDczWlVrOTA5Rk42OEYwYjRONjdQU09oOTJvMDR2TDBCUjY1MzdwSTJ5Mmg5NE02QmJVN0Q2SjpXNjY4NDZrUFF2MTc1MDk3NW9vZEg1TTh6QzZUYTdtMzBrSDJRNzhsMldtVTUwRkNncXBQMTN3NzczNzdrN0lC',
        'User-Agent': USER_AGENT_WEB,
    }

    username = account_info.username
    password = account_info.password
    url_safe_password  = quote(password, safe='')
    data = f'grant_type=password&username={username}&password={url_safe_password}&scope=openid+profile+email+vdsid'

    # Attempt the login using the provided variables
    # TODO: Refactor to unified execution engine in a future architecture pass.
    # NOTE: This is left as a direct session call for now to guarantee that the
    # login cookie container and initial OAuth handshakes are preserved perfectly
    # without running into downstream fallback session side-effects.
    try:
        response = session.post(f'https://www.{account_info.url_brand}.com/auth/oauth2/access_token', headers=headers, data=data, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        log(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(EXIT_TOTAL_FAILURE)

    if response.status_code != 200:
        log(f"Login attempt got return code {response.status_code} for user {account_info.username}")

        # The status code alone cannot distinguish a rejected password
        # ("invalid_grant") from a malformed request ("invalid_request") or an
        # edge/WAF block that returns HTML - which makes a login failure very
        # hard to diagnose. Surface the server's own error text; the OAuth
        # error body carries no credentials, and the password is scrubbed
        # defensively in case an echo is ever added.
        detail = (response.text or "").strip()
        if password and password in detail:
            detail = detail.replace(password, "***")
        if detail:
            log(f"   Server said: {detail[:300]}")

        log(f"{account_info.cruise_line} website might be down, username/password incorrect, or have unsupported symbol in password. Quitting.")
        sys.exit(EXIT_TOTAL_FAILURE)

    # Parse out the account's ID and access token
    access_token = response.json().get("access_token")

    try:
        list_of_strings = access_token.split(".")
        if len(list_of_strings) < 2:
            raise ValueError("Token does not contain a valid JWT payload segment.")
        string1 = list_of_strings[1]

        # JWT segments are base64URL: standard b64decode silently drops -/_
        # (validate=False), shifting later bytes and failing the json parse
        # for tokens whose payload contains such a byte
        decoded_bytes = base64.urlsafe_b64decode(string1.replace('+', '-').replace('/', '_') + '==')
        auth_info = json.loads(decoded_bytes.decode('utf-8'))
        account_ID = auth_info["sub"]
    except(IndexError, ValueError, KeyError, AttributeError, TypeError) as parse_err:
        # AttributeError/TypeError: a 200 with no access_token leaves it None
        log(f"Error parsing authentication token structure: {parse_err}")
        sys.exit(EXIT_TOTAL_FAILURE)

    # Attach bearer token to the active session for all subsequent API requests
    session.headers.update({
        "Authorization": f"Bearer {access_token}"
    })

    # Store the server access value in an APIAccess object and return
    return APIAccess(
        token = access_token,
        id = account_ID,
        session = session
    )


def get_profile(account_info: AccountInfo) -> Tuple[Optional[str], Optional[str], int]:
    """
    Retrieves personal profile properties to extract valid residency codes and loyalty tiers.

    Inspects user contact records to locate primary residency states and tracks concurrent
    loyalty modules (Crown & Anchor, Club Royale, Captain's Club, and Blue Chip). Returns
    the active brand tracking index to route downstream web requests correctly. Also stashes
    the active-brand loyalty tier label and individual points directly onto account_info
    (history-layer snapshot fields only - never read by any discount/alert logic) since
    account_info is already in hand here and get_voyages()'s signature must not change.
    """
    url = f"https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/v3/guestAccounts/{account_info.access.id}"
    response = _execute_api_request(account_info, "GET", url)
    if response is None or response.status_code != 200:
        log(f"{YELLOW}Could not retrieve profile after retries; continuing without residency/loyalty discounts{RESET}")
        return None, None, 0

    try:
        payload = response.json().get("payload") or {}
    except Exception as e:
        log_err(f"Failed to parse profile JSON response: {e}")
        return None, None, 0

    state = None
    loyalty_number = None
    c_and_a_shared_points = 0

    address = payload.get("contactInformation", {}).get("address", {})
    if address.get("residencyCountryCode") in ("USA", "CAN"):
        state = address.get("state")

    # Pull the loyalty information from the profile
    loyalty = payload.get("loyaltyInformation") or {}
    captains_club_ID = loyalty.get("captainsClubId")
    c_and_a_number = loyalty.get("crownAndAnchorId")
    c_and_a_level = loyalty.get("crownAndAnchorSocietyLoyaltyTier")

    # "or 0" guards explicit JSON nulls: .get(key, 0) only defaults when the key
    # is absent, and a null value here becomes a TypeError in the > and >=
    # comparisons downstream (including the dp340 eligibility check)
    c_and_a_points = loyalty.get("crownAndAnchorSocietyLoyaltyIndividualPoints", 0) or 0
    c_and_a_shared_points = loyalty.get("crownAndAnchorSocietyLoyaltyRelationshipPoints", 0) or 0

    # Get and display Royal Caribbean (Crown & Anchor and Club Royale) information
    if c_and_a_number and c_and_a_shared_points > 0:
        log(f"\tC&A: {c_and_a_number} {c_and_a_level} - {c_and_a_shared_points} Shared Points ({c_and_a_points} Individual Points)")

        total_nights, total_trips = get_number_of_nights(account_info, c_and_a_number, brand="royal")
        if total_nights > 0:
            log(f"\tTotal Trips on Royal: {total_trips} - Total Nights: {total_nights}")

        # Club Royale tier currently is not part of the loyalty payload; use a helper to compute it
        # but keep the payload check in case it ever comes back (key name may need to change)
        casino_points = loyalty.get("clubRoyaleLoyaltyIndividualPoints",0) or 0
        club_royale_loyalty_tier = loyalty.get("clubRoyaleLoyaltyTier") or get_club_royale_tier(casino_points)
        if club_royale_loyalty_tier:
            log(f"\tCasino Royale Tier: {club_royale_loyalty_tier} - {casino_points} Credits")

    # Get and display Celebrity (Captain's Club and Blue Chip) information
    cc_level = None
    cc_individual = 0
    if captains_club_ID:
        cc_level = loyalty.get("captainsClubLoyaltyTier")
        cc_individual = loyalty.get("captainsClubLoyaltyIndividualPoints", 0)
        cc_shared = loyalty.get("captainsClubLoyaltyRelationshipPoints", 0)
        log(f"\tCaptain's Club Number: {captains_club_ID} {cc_level} TIER ({cc_shared} Shared Points, {cc_individual} Individual Points)")

        total_nights, total_trips = get_number_of_nights(account_info, captains_club_ID, brand="celebrity")
        if total_nights > 0:
            log(f"\tTotal Trips on Celebrity: {total_trips} - Total Nights: {total_nights}")

        celebrity_blue_chip_loyalty_tier = loyalty.get("celebrityBlueChipLoyaltyTier","Unknown")
        if celebrity_blue_chip_loyalty_tier != "Unknown":
            celebrity_blue_chip_loyalty_individual_points = loyalty.get("celebrityBlueChipLoyaltyIndividualPoints",0)
            log(f"\tBlue Chip Tier: {celebrity_blue_chip_loyalty_tier} - {celebrity_blue_chip_loyalty_individual_points} Points")

    # Return the correct loyality number based on the account being used
    loyalty_number_to_use = captains_club_ID if account_info.is_celebrity else c_and_a_number

    # History-layer snapshot only (bookings.loyalty_tier / loyalty_points) - stashed
    # directly on account_info since it's already in hand; not used in any
    # discount/alert calculation and not part of this function's return contract
    account_info.loyalty_tier = cc_level if account_info.is_celebrity else c_and_a_level
    account_info.loyalty_points = cc_individual if account_info.is_celebrity else c_and_a_points

    # Return Royal Crown and Anchor shared points to determine if eligible for dp340
    return state, loyalty_number_to_use, c_and_a_shared_points


def get_club_royale_tier(points: int) -> str | None:
    """Computes Club Royale Tier name based on individual tier credits."""
    if points is None or points <= 0:
        return None
    elif points < 2500:
        return "CHOICE"
    elif points < 25000:
        return "PRIME"
    elif points < 100000:
        return "ICON"
    else:
        return "MASTERS"


def get_number_of_nights(account_info: AccountInfo, loyalty_number: str,
                         brand: Optional[str] = None) -> Tuple[int, int]:
    """
    Queries cumulative night metrics and cruise totals for a specified loyalty profile.

    Queries corporate historical data points. Runs with 'on_failure="retry"' inside the
    request core so historical lookup dropouts won't crash critical root execution pipelines.

    brand must match the loyalty PROGRAM being queried ("royal" for Crown &
    Anchor numbers, "celebrity" for Captain's Club), not the account's login
    brand: the profile shows both programs for either login, and querying a
    number against the other program's endpoint returns HTTP 400 (issue #116).
    Defaults to the account's brand for any caller that queries its own program.
    """
    total_nights, total_trips = -1, -1

    url = f"https://aws-prd.api.rccl.com/en/{brand or account_info.api_brand}/web/v1/guestAccounts/loyalty/history/summary"

    # Request the information from the servers
    response = _execute_api_request(
        account_info, "GET", url,
        params={'loyaltyNumber': loyalty_number},
        timeout=SHORT_REQUEST_TIMEOUT,
        on_failure="retry"
    )

    if response and response.status_code == 200:
        payload = response.json().get("payload", {})
        total_nights = payload.get("totalNights", total_nights)
        total_trips = payload.get("totalTrips", total_trips)

    return total_nights, total_trips


