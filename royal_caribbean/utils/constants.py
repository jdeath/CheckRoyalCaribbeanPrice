"""Application-wide constants, status codes, and error literals."""

from __future__ import annotations

# Process exit code contract for main() - a supervising scheduler can rely on
# these three outcomes meaning exactly this and nothing else:
#   EXIT_SUCCESS         - full success: every account was checked
#   EXIT_TOTAL_FAILURE   - fatal/total failure: an unhandled exception, or a
#                          module-level setup failure. This can happen before
#                          any pricing ran, or partway through a multi-account
#                          run after earlier accounts already succeeded and
#                          had their price-drop alerts sent - the exit code
#                          alone doesn't say how far the run got. Don't
#                          blindly auto-retry on this code (that risks
#                          re-alerting accounts that already succeeded);
#                          surface it to a human and check the log first.
#   EXIT_PARTIAL_FAILURE - the run COMPLETED, but one or more accounts were
#                          SKIPPED after a login or post-login profile-fetch
#                          failure. Data already written for the accounts
#                          that DID succeed (price points committed,
#                          price-drop alerts sent, the watchlist JSON) is
#                          real and must not be redone.
#                          A scheduler that treats this the same as exit 1
#                          and blindly retries the whole run will re-price
#                          already-priced accounts and can send duplicate
#                          price-drop notifications to real users - so this
#                          code is deliberately distinct from the fatal (1)
#                          and success (0) cases.
EXIT_SUCCESS = 0
EXIT_TOTAL_FAILURE = 1
EXIT_PARTIAL_FAILURE = 2

# API timeout / retry behavior
# Seconds before giving up on an API call so a stalled connection cannot hang the run
# forever. Override with requestTimeout in config.yaml if the API is slow for you.
REQUEST_TIMEOUT = 30

# Shorter timeout for quick auxiliary endpoints (check-in status, loyalty summary,
# sample-config download) where a long wait is not worth it
SHORT_REQUEST_TIMEOUT = 10

# How API failures are handled when a call site does not choose explicitly:
# "retry" (back off and try again), "skip" (log and move on), "exit" (stop the run)
DEFAULT_ON_FAILURE = "retry"

# Retry attempts and exponential backoff base for on_failure="retry" calls
# (sleep = RETRY_BACKOFF_BASE ** attempt seconds between attempts: 2s, 4s)
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2

# Cool-down between accounts when checking more than one, to avoid hammering the API
ACCOUNT_COOLDOWN_SECONDS = 5
RESERVATION_REQUEST_INTERVAL_SECONDS = 1.0

APPKEY_WEB = 'hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm'
USER_AGENT_WEB = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0'

APPKEY_MOBILE_GRAPH = '5pWJwSTvu30Dkw5GHLVQ5PsmoKRE1arh'
USER_AGENT_MOBILE_GRAPH = 'okhttp/4.12.0'

DATE_DISPLAY_FORMAT = "%x"

