"""Apprise notification initialization and dispatching helpers for royal_caribbean."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from royal_caribbean.utils.logging import RESET, YELLOW, log

# Optional apprise dependency handling
try:
    from apprise import Apprise, NotifyFormat, __version__ as apprise_version
except ImportError:
    Apprise = None
    NotifyFormat = None
    apprise_version = "not installed"

# ==============================================================================
# Notification Utilities
# ==============================================================================
def build_apprise(urls: List[Dict[str, Any]] | List[str]) -> Optional[Any]:
    """Constructs and configures an Apprise notification instance.

    Args:
        urls: List of URL strings or dictionary items containing a 'url' key.

    Returns:
        Configured Apprise instance or None if not installed or unconfigured.
    """
    if Apprise is None:
        if urls:
            log(YELLOW + "Apprise configuration found, but 'apprise' module is not installed." + RESET)
        return None

    if not urls:
        return None

    apobj = Apprise()
    for item in urls:
        url_str = item["url"] if isinstance(item, dict) and "url" in item else item
        if isinstance(url_str, str) and url_str.strip():
            apobj.add(url_str)

    return apobj if len(apobj) > 0 else None