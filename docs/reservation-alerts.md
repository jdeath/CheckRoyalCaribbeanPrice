# Dining and entertainment reservation-release alerts

Optionally watch a booked Royal Caribbean sailing for dining or show reservations
to open. This checks **dated offerings with reported inventory**, rather than
whether a product is visible in Cruise Planner or has a price. Free shows can
therefore trigger an alert too.

The feature is disabled unless `availability` is configured. It runs alongside
normal price checks, using the existing account login, schedule, request retries,
and per-account/global Apprise settings. It does not reserve anything or create a
cart. Celebrity is not supported.

## Configuration

Add this block to your existing configuration. Each entry identifies a booked
reservation and the categories to discover automatically. The reservation must be
linked to one of your configured Royal Caribbean accounts.

```yaml
availability:
  dryRun: true
  stateFile: "data/reservation-availability.json"
  reservations:
    - reservation: "1234567"
      dining: true
      shows: true
    - reservation: "7654321"
      dining:
        products:
          - "UT_RAILDINNER"
      shows: false
      notifyOnReopen: false
```

Run your usual check and compare the reported times with Cruise Planner. With
`dryRun: true` (the default), availability checks use live read requests but do
not send availability notifications or read/write notification state. This is
**not a global dry run**: existing price alerts and `appriseTest` still work as
configured. Set `dryRun: false` to enable release notifications once the output
looks correct, and configure Apprise with `overflow=split` as described below.

| Setting | Behavior |
| --- | --- |
| `reservation` | Required booking number. Add one entry per cruise you want to monitor. |
| `dining: true` | Discover dining products for the sailing and check each matching `pt_dining` product for dated inventory. Defaults to `false`. |
| `dining: {products: [...]}` | Check only the listed dining product IDs. The catalog is still read first so a configured product that disappears from a complete catalog can be treated as unavailable. |
| `shows: true` | Discover show products for the sailing and check each matching `pt_show` product for dated inventory. Defaults to `false`. |
| `shows: {products: [...]}` | Optional advanced form to restrict show checks to selected product IDs. |
| `notifyOnReopen: false` | Default: alert once per discovered product, account, booking and category. |
| `notifyOnReopen: true` | Also alert after a confirmed closure and subsequent reopening. Failed or uncertain checks do not re-arm an alert. |
| `stateFile` | JSON file used to suppress repeats across runs and restarts. Defaults to `data/reservation-availability.json`. |

At least one of `dining` or `shows` must be enabled for each reservation. Product
codes are not required for normal use. The tracker reads the sailing's category
catalog, filters to products whose returned type matches the requested category,
and then checks dated offering inventory for each matching product. Royal's Dining
catalog can also contain packages and onboard activities; products whose returned
type is not `pt_dining` are silently ignored. A selected product that is present with
an unexpected type is treated as unknown rather than closed.

The first live check alerts for products that are already available. An alert is
about a product's release, not every additional session or change in its times.
Existing reservations, exhausted guest allowances and personal scheduling
conflicts do not hide a release. You must confirm that an offering is suitable
and book it yourself. Dining stock is not a guarantee of a table for your party;
not every dining product exposes usable dated offerings through this endpoint.

## Notifications and state

Console output is grouped as account → sailing → category → product. The sailing heading uses the configured date format and the full ship name when Royal includes it in the booking payload, falling back to the ship code without making an extra display-only request. Apprise transport-success messages are suppressed during availability notification delivery; warnings and failures remain visible.

Newly available products are grouped into one alert per reservation category. Each product
previews up to six times, grouped by date using `dateDisplayFormat`, with a link
to the sailing's Cruise Planner category. The console shows all returned times.
Times preserve Royal's wall-clock values; no timezone conversion is performed.
Every Apprise destination used for live availability alerts must explicitly use
`overflow=split`. Apprise then splits long plain-text alerts according to each
service's message limit. For example:

```yaml
apprise:
  - url: "pover://APP_TOKEN@USER_KEY/?overflow=split"
```

If a URL already has query options, append `&overflow=split`; replace an existing
`overflow` value rather than adding it twice. Set this on each per-account URL if
that account overrides the global notifier, otherwise on each global URL.
The checker validates the parsed destinations, without rewriting your URLs or
logging credentials. The default `upstream` mode and `truncate` are not accepted
for availability delivery, even when the current message is short: later releases
can produce larger alerts. A missing or empty notifier is also a configuration
failure for live alerts.

Dry runs warn about incompatible notification settings while still checking
inventory, without reading or writing state. Live checks continue reporting
inventory, but do not send or acknowledge pending alerts until the configuration
is corrected. They finish normal price outputs and report a partial failure.
Splitting a shared Apprise URL also affects oversized price notifications; short
notifications remain single messages. The checker does not alter price-notification
settings automatically.

All parts must succeed before the aggregate is acknowledged. If a part fails, the
aggregate retries on a later run, so parts that already arrived may repeat.

Keep the state file on persistent storage. In Docker, mount a writable directory
at `/app/data`, or configure an absolute `stateFile` path in an existing persistent
mount. The file is separate from cabin-alert JSON and optional price-history
SQLite storage; no additional database is required. Do not point these features
at the same file. Deleting the reservation state or changing a reservation/category selection
can repeat previously delivered alerts.

State stores a hashed account/booking/category scope, product IDs, last confirmed
availability and notification acknowledgements. It does not store credentials,
guest names or raw API responses. Narrowing a category from automatic discovery
to selected products prunes unrelated saved products instead of marking them
unavailable. Notification links contain booking context; handle them as personal
information.

A sidecar lock covers reading state, deciding/sending notifications and atomically
replacing the JSON file. Invalid state or lock contention is reported without
resetting saved acknowledgements. Apprise must confirm success before an alert
is acknowledged. Failed delivery retries on a later run. A crash or disk failure
after delivery but before saving can produce a duplicate alert; delivery and
local persistence cannot be one atomic operation.

`unknown` means failed, incomplete or unrecognized evidence, not sold out.
Returned products are still checked if a later catalog page fails or the declared
count does not match the returned products. This also applies to explicitly
selected restaurants or shows that were returned. Successfully checked products
can generate alerts, but the run still reports a partial failure so the missing
coverage remains visible.

Only a complete catalog can establish that a product disappeared. Products absent
from an incomplete catalog retain their previous state and are never re-armed
based on that absence. An independent, valid eligibility response for a returned
product can still confirm availability or closure. Previously saved state is
retained for unknown products. Missing reservations, failed checks,
state errors or unconfirmed notifications are reported after normal price
outputs, using the existing partial-failure exit status so scheduled failures
remain visible.

## Validation

Tests use synthetic response fixtures and mocked transports, including inventory
interpretation, recovery from incomplete catalogs without false disappearances,
per-account notification validation, failed split-message retries, JSON validation,
concurrent processes, atomic-save failures and normal price-report completion. They do not contact Royal or send real notifications.
Entertainment release behavior has been exercised in a running fork. Dining
discovery and eligibility behavior were also validated against sanitized live
captures from Icon of the Seas and Utopia of the Seas. Those captures included
normal restaurant reservations, My Time Dining, dining packages, onboard dining
activities, and Royal Railway — Utopia Station. Royal Railway used `pt_dining`
and returned in-stock dated offerings even when `active` was false or the guest
had scheduling conflicts, so those fields are not used to suppress release alerts.
