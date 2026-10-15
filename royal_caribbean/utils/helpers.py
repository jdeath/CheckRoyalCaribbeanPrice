"""utils/helpers.py: General-purpose utility functions for string sanitation, date math, and JSON extraction."""

from __future__ import annotations

import json
import locale
import logging
import re

from datetime import date
from typing import Any, List, Optional

from royal_caribbean.utils.logging import has_terminal_issues

def _safe_json(response: Any) -> Optional[dict]:
    """response.json() that returns None on a non-JSON body instead of raising."""
    try:
        return response.json()
    except ValueError:
        return None


def _extract_json_array(text: str, key: str) -> Optional[List[Any]]:
    """Finds and extracts a specific JSON array buried inside raw text chunks."""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text)
    if not m:
        return None

    start = m.end() - 1
    depth, i = 0, start
    in_string, escape = False, False

    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
        elif ch == "\\" and in_string:
            escape = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return None
        i += 1
    return None


def print_response(response: Union[Dict[str, Any], List[Any], str, requests.Response]) -> None:
    """
    Debug utility to format and display raw API responses.

    Transforms nested API response JSON payloads or dictionary objects into standard,
    indented strings for readable terminal diagnosis during live testing.
    """
    json_resp = json.dumps(response, indent=2)
    log("API returned output:")
    log(json_resp)


def days_between(sail_date: str, activity_date: str) -> str:
    d0 = date(int(sail_date[0:4]), int(sail_date[4:6]), int(sail_date[6:8]))
    d1 = date(int(activity_date[0:4]), int(activity_date[4:6]), int(activity_date[6:8]))
    delta = d1 - d0
    return str(delta.days + 1)


def get_system_currency() -> str:
    try:
        locale.setlocale(locale.LC_ALL, '')
    except locale.Error as e:
        logging.warning(f"Warning: Could not set locale. Using default 'C' locale. Error: {e}")
        locale.setlocale(locale.LC_ALL, 'C')

    conventions = locale.localeconv()
    international_symbol = conventions.get('int_curr_symbol', '').strip()
    if not international_symbol:
        logging.warning("No currency in the system locale; defaulting to USD (use -c to override)")
        return "USD"
    return international_symbol

