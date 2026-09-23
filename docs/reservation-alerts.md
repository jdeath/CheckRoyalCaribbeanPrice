# Dining and entertainment reservation-release alerts

Optionally watch a booked Royal Caribbean sailing for dining or show reservations
to open. This checks **dated offerings with reported inventory**, rather than
whether a product is visible in Cruise Planner or has a price. Free shows can
therefore trigger an alert too.

The feature is disabled unless `reservationAlerts` is configured. It runs alongside
normal price checks, using the existing account login, schedule, request retries,
and per-account/global Apprise settings. It does not reserve anything or create a
cart. Celebrity is not supported.

## Configuration

Add this block to your existing configuration. Each entry identifies a booked
reservation and the categories to discover automatically. The reservation must be
linked to one of your configured Royal Caribbean accounts.

```yaml
reservationAlerts:
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
looks correct. The message-specific notification checks below explain when
`overflow=split` is needed.

| Setting | Behavior |
| --- | --- |
| `reservation` | Required booking number. Add one entry per cruise you want to monitor. |
| `dining: true` | Discover dining products for the sailing and check each matching `pt_dining` product for dated inventory. Defaults to `false`. |
| `dining: {products: [...]}` | Check only the listed dining product IDs. The catalog is still read first so a configured product that disappears from a complete catalog can be treated as unavailable. |
| `shows: true` | Discover show products for the sailing and check each matching `pt_show` product for dated inventory. Defaults to `false`. |
| `shows: {products: [...]}` | Check only selected show product IDs, reducing eligibility requests. |
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

## Reducing requests: select products and stop checks you no longer need

**Monitoring only selected restaurants or shows uses markedly fewer requests than
monitoring the entire category.** Use `dining: {products: ["UT_RAILDINNER"]}` when
Royal Railway is the only restaurant you still need. The example above shows the
expanded YAML form. Product IDs come from the sailing's Cruise Planner catalog;
use the returned `id`, not the restaurant's display name.

The catalog is still paginated for selected-product monitoring, but eligibility
is requested only for selected matching products. For a catalog containing 20
dining products across two pages:

| Dining selection | Catalog requests | Eligibility requests | Total per check |
| --- | ---: | ---: | ---: |
| All 20 restaurants | 2 | 20 | 22 |
| One selected restaurant | 2 | 1 | 3 |

The example reduces dining requests by about 86%. Actual counts depend on the
catalog, its mixed product types, pagination, and retries. A reservation with 20
dining products and 10 shows across three total catalog pages costs 33 additional
requests per run. Three such reservations checked hourly cost approximately
2,376 requests daily, before retries and ordinary price checks.

The checker leaves a minimum one-second gap after an availability request
finishes before starting the next request for that account, including catalog
pages. Existing retry backoff remains in place. A 33-request sequence adds about
32 seconds of waiting plus network time. This reduces bursts, not total request
volume, and does not guarantee protection from Royal's rate limits.

**Once all the dining reservations you want have opened and you have booked them,
disable dining monitoring for that reservation.** Restaurants may release at
different times; an alert for one restaurant does not establish availability at
all others. While waiting, narrow monitoring to the remaining selected products.

```yaml
reservationAlerts:
  reservations:
    - reservation: "1234567"
      dining: false
      shows: true
```

This abbreviated example shows the category change; retain your existing
`dryRun` and `stateFile` settings. Receiving a one-time alert suppresses repeats
but does **not** stop polling. Disabling dining also stops discovery of new
restaurants and detection of reopenings. Remove a reservation entry when neither
category needs monitoring. Remove the whole block when no reservations remain.
Keep the state file if you might re-enable monitoring and want to preserve prior
acknowledgements.

## Notifications and state

Console output is grouped as account → sailing → category → product. The sailing heading uses the configured date format and the full ship name when Royal includes it in the booking payload, falling back to the ship code without making an extra display-only request. Apprise transport-success messages are suppressed during availability notification delivery; warnings and failures remain visible.

Newly available products are grouped into one alert per reservation category. Each product
previews up to six times, grouped by date using `dateDisplayFormat`, with a link
to the sailing's Cruise Planner category. The console also previews up to six
times per product, with the total number of available times and days when more
exist. Full inventory is retained internally; only the display is shortened.
Times preserve Royal's wall-clock values; no timezone conversion is performed.
Before sending an aggregate, the checker validates its final title and body for
**every effective destination**, using Apprise's formatting and overflow preview.
Per-account Apprise settings override the global fallback.

- A message that fits without losing content uses the destination's existing
  overflow setting. Short Pushover alerts and email within its larger limit do
  not require a URL change.
- If the message needs multiple parts, that destination must explicitly use
  `overflow=split`. The checker never rewrites notification settings.
- Formatting expansion, combined title/body limits, title truncation and line
  limits are checked too. Apprise can truncate at a line limit before splitting;
  `split` alone cannot repair that configuration.
- If validation cannot establish safe delivery, nothing in the aggregate is sent
  or acknowledged. Diagnostics name the service and corrective action without
  exposing notification URLs or credentials. Pending alerts retry after correction.

For an oversized Pushover alert, configure:

```yaml
apprise:
  - url: "pover://APP_TOKEN@USER_KEY/?overflow=split"
```

If a URL already has query options, append `&overflow=split`; replace an existing
`overflow` value rather than adding it twice. Setting splitting on a shared URL
also affects oversized price notifications. No change is required to other URLs
whose current message fits. Future notifications are validated again because their
size may differ. A missing or empty notifier is a configuration failure for live
alerts even when no message is currently pending.

Dry runs validate a preview of the currently available products, warn about
incompatible destinations, and do not read or write state. Because they do not
read acknowledgements, that preview can be larger than the pending live alert.
Live delivery/configuration failures preserve pending alerts, finish normal price
outputs, and report partial failure. The formatting preview uses Apprise internals
behind one validation helper. Installs and packaged builds require
**Apprise >= 1.13.1**, the minimum tested version, with no upper bound. CI tests
both that minimum and the latest release; both jobs must pass. Newer versions
are not rejected at runtime: reservation alerts send when formatting validation
succeeds. The minimum does not guarantee that future private APIs stay compatible.
An incompatible preview API leaves alerts pending and reports partial failure
in live mode. The diagnostic includes the installed version and an optional
recovery command to restore the tested baseline:
`python -m pip install 'Apprise==1.13.1'`. That recovery command is not an
installation pin. Docker/standalone users should use a compatible release or
report the error. If the problem persists on that version, report the version and affected service without
including notification URLs or credentials. There is no unchecked-send fallback.
This checks Apprise's declared formatting limits,
not end-to-end receipt by a person's device.

All parts must succeed before the aggregate is acknowledged. If a part fails, the
aggregate retries on a later run, so parts that already arrived may repeat.

Keep the state file on persistent storage. In Docker, mount a writable directory
at `/app/data`, or configure an absolute `stateFile` path in an existing persistent
mount. The file is separate from cabin-alert JSON and optional price-history
SQLite storage; no additional database is required. Do not point these features
at the same file. Deleting the reservation state or changing a reservation/category selection
can repeat previously delivered alerts.

State version 2 uses readable scope keys: a JSON-encoded list of account email,
ship code, sail date, reservation number and category. It also stores product IDs,
last confirmed availability and notification acknowledgements. It does not store
credentials, guest names or raw API responses. Treat the file as personal booking
information and do not commit it to a public repository.

To reset just one product, stop the checker, back up the file, locate its readable
scope and product ID, and set that product's `notified` value to `false`. Leave
other entries intact and restart the checker. The product can alert again when
available.

For users of earlier pre-release versions: rename the top-level `availability`
configuration block to `reservationAlerts`. The old key is explicitly rejected.
Version-1 state with hashed keys is also rejected rather than silently reset.
Stop the checker and move that reservation state file to a backup before starting
with a fresh version-2 file. This intentional reset may repeat prior alerts. No
state conversion or automatic deletion is performed; cabin-alert state is unaffected.
 Narrowing a category from automatic discovery
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
can generate alerts. Count mismatches and isolated unknown products produce a
coverage warning when the category has usable results. The final summary names
incomplete coverage rather than claiming every check succeeded; optional price
history records the warning summary with the otherwise successful run.

Only a successful, complete catalog can establish that a product disappeared.
`CommerceProductNotFound` is uncertain evidence, not a complete empty catalog.
Out-of-sailing offerings are ignored when valid in-range offerings remain. If
all offerings are outside the sailing, the product remains unknown rather than
being re-armed as unavailable. Products absent
from an incomplete catalog retain their previous state and are never re-armed
based on that absence. An independent, valid eligibility response for a returned
product can still confirm availability or closure. Previously saved state is
retained for unknown products. A category with no usable results, transport or
authentication failures, missing reservations, invalid configuration, state errors,
and unconfirmed notifications still cause partial failure after normal price
outputs finish. A complete, successful empty catalog is a valid result.

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

### Catalogs that are not available yet

A page-zero response containing only `CommerceProductNotFound` means no catalog
is currently available for that category. The checker prints an informational
line, preserves all saved state, and does not count this as a failed check. This
applies to automatic discovery and selected products. It does not distinguish
between a ship without reservable shows and a catalog that has not opened yet.
Other categories continue normally, and the catalog is checked again next run.

A not-found response on a later page, mixed exceptions, malformed responses, and
request failures still follow the incomplete-catalog/failure rules. A successful
empty catalog remains distinct from this no-catalog response.
