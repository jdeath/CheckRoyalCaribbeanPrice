"""HTTP request orchestration and session management for Royal Caribbean API endpoints."""

from __future__ import annotations

try:
    import requests as plain_requests
except ImportError:
    import plain_requests  # Fallback fallback

import logging
import re
import sys
import time

# Optional curl_cffi dependency handling
try:
    from curl_cffi import requests
    IMPERSONATE_ARGS = {"impersonate": "chrome"}
except ImportError:
    import requests as plain_requests
    requests = plain_requests
    IMPERSONATE_ARGS = {}

from typing import Any, Dict, Optional, Union

# Import session setup & user agent from auth
from royal_caribbean.models import AccountInfo
from royal_caribbean.utils.constants import (
    APPKEY_WEB,
    DEFAULT_ON_FAILURE,
    EXIT_TOTAL_FAILURE,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF_BASE,
    USER_AGENT_WEB
)
from royal_caribbean.utils.logging import RED, RESET, log


def new_api_session(use_impersonation: bool = True) -> plain_requests.Session:
    """
    Creates a network session that impersonates a real browser's TLS fingerprint
    when curl_cffi is available and requested, falling back to standard requests.
    """
    if use_impersonation and IMPERSONATE_ARGS:
        return requests.Session(**IMPERSONATE_ARGS)
    return plain_requests.Session()


def _execute_api_request(
    account_info: Optional[AccountInfo] = None,
    method: str = "GET",
    url: str = "",
    params: Optional[dict] = None,
    data: Optional[Union[str, dict]] = None,
    json_data: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: Optional[int] = None,
    on_failure: str = DEFAULT_ON_FAILURE,
    exit_on_fail: Optional[bool] = None,
    max_retries: int = MAX_RETRIES,
    use_impersonation: bool = True
) -> Optional[plain_requests.Response]:
    """
    Unified API execution engine for all cruise line network interactions.

    Centralizes tracking parameters, developer keys, and connect timeouts.
    If an active session profile exists, it automatically injects 'Access-Token'
    and account tracking headers into the request context.

    Supported strategies for on_failure:
    - "retry": Automatically retries transient errors with exponential backoff.
    - "skip" : Logs the warning and returns None on failure.
    - "exit" : Logs the error and terminates the script entirely on failure.
    """
    # Backwards compatibility helper for existing exit_on_fail parameter callers
    if exit_on_fail is not None:
        on_failure = "exit" if exit_on_fail else "skip"

    # Resolve effective timeout: explicit override -> config setting -> default baseline
    if timeout is None:
        timeout = REQUEST_TIMEOUT

    # Start with caller override headers or an empty dictionary
    final_headers = headers.copy() if headers else {}

    # Inject corporate authentication layers if a live session exists
    if account_info and getattr(account_info, "access", None):
        if "Access-Token" not in final_headers and account_info.access.token:
            final_headers["Access-Token"] = account_info.access.token
        if "vds-id" not in final_headers and account_info.access.id:
            final_headers["vds-id"] = account_info.access.id
        if "account-id" not in final_headers and account_info.access.id:
            final_headers["account-id"] = account_info.access.id

    # Always include baseline developer web key
    if "AppKey" not in final_headers and "appkey" not in final_headers:
        final_headers["AppKey"] = APPKEY_WEB

    # Target session selection: existing session token or new engine session
    if account_info and getattr(account_info, "access", None) and account_info.access.session:
        session_context = account_info.access.session
    else:
        session_context = new_api_session(use_impersonation=use_impersonation)

    def _handle_terminal_failure(error: Exception) -> Optional[plain_requests.Response]:
        error_msg = f"Can't contact cruise line servers; please try again later\n(program exception '{error}')"
        if on_failure == "exit":
            log(error_msg)
            sys.exit(EXIT_TOTAL_FAILURE)
        else:
            logging.warning(f"Non-critical API interaction skipped (exception: {error})")
            return None

    # --- STRATEGY A: RESILIENT RETRY LOOP ---
    if on_failure == "retry":
        for attempt in range(1, max_retries + 1):
            try:
                response = session_context.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    data=data,
                    json=json_data,
                    headers=final_headers,
                    timeout=timeout
                )

                # Treat 5xx server errors as transient retriable errors
                if response.status_code >= 500:
                    raise plain_requests.exceptions.HTTPError(
                        f"Server Error {response.status_code}", response=response
                    )

                response.raise_for_status()
                return response  # Success!

            except Exception as e:
                # Terminal 4xx client errors (e.g. 401, 403, 404) fail fast without retrying
                resp_obj = getattr(e, "response", None)
                status_code = getattr(resp_obj, "status_code", None)
                # Fallback: curl_cffi's HTTPError does not always attach .response -
                # parse the status out of the exception text ("404 Client Error")
                # so a definitive client error is never misread as transient and
                # retried. Match only HTTP-status phrasing: a bare \b[45]\d\d\b
                # also matched the "port 443" in every HTTPS connection-failure
                # message, misclassifying transient network errors as terminal
                # 4xx and skipping every retry.
                if status_code is None:
                    match = re.search(
                        r"\b([45]\d\d)\s+(?:client|server)\s+error\b"
                        r"|\bhttp(?:\s+error)?\s*:?\s*([45]\d\d)\b"
                        r"|\bstatus(?:\s+code)?\s*:?\s*([45]\d\d)\b",
                        str(e), re.IGNORECASE)
                    if match:
                        status_code = int(next(g for g in match.groups() if g))
                if status_code and 400 <= status_code < 500:
                    return _handle_terminal_failure(e)

                if attempt < max_retries:
                    backoff_time = RETRY_BACKOFF_BASE ** attempt
                    logging.warning(f"Attempt {attempt}/{max_retries} failed for {url}: {e}. Retrying in {backoff_time}s...")
                    time.sleep(backoff_time)
                else:
                    logging.warning(f"All {max_retries} retry attempts exhausted for {url}.")
                    return _handle_terminal_failure(e)

    # --- STRATEGY B: STATIC SINGLE-SHOT ACTIONS ("skip" or "exit") ---
    try:
        response = session_context.request(
            method=method.upper(),
            url=url,
            params=params,
            data=data,
            json=json_data,
            headers=final_headers,
            timeout=timeout
        )
        response.raise_for_status()
        return response
    except Exception as e:
        return _handle_terminal_failure(e)
