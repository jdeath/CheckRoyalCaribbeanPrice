[Back to README](../README.md)

## Notification Emails/Pushbullet/etc via Apprise (Optional)
1. Review documentation for apprise at: https://github.com/caronc/apprise
1. 99% of people probably have gmail, so you can use this line in your config.yaml (see [Edit Config File](docs/config.md)):
```
apprise:
  - url: "mailto://user:password@gmail.com"
```
3. This will send you an email only if there is a price drop
1. Change username to your gmail username
1. Change password to your gmail password. If you use 2-factor authentication, you need to generate an app password. You cannot use use normal password
   - Documentation to generate an app password for gmail is here: https://security.google.com/settings/security/apppasswords
1. You can also add more lines for an additional gmail accounts.
1. To test apprise, add a key in your config.yaml that says `apprise_test: true` . This will send a notification, then quit and not run the price check. This key goes above the `apprise:` keys not inside it (see [Edit Config File](config.md)). Once you know apprise is working, remove the line or set value to `false`
1. To be alerted if the script fails to run, set `notifyOnError: true` in your `config.yaml`. This sends a short Apprise notification and the script exits with a non-zero code.

### Per-Account Apprise Notifications
If multiple people share one config file, each `accountInfo` entry can carry its own `apprise:`
list, in the same shape as the top-level one. Alerts produced while checking that account (price
drops, watch-list hits, add-on/order price alerts) go only to that account's URLs. An account
with no `apprise:` of its own falls back to the top-level `apprise:` list.
```yaml
accountInfo:
  - username: "chris@example.com"
    password: "pa$$word"
    apprise:
      - url: "ntfy://chris-topic"
  - username: "friend@example.com"
    password: "pa$$word"
    apprise:
      - url: "ntfy://friend-topic"
apprise: # Optional global fallback for accounts without their own apprise: list
  - url: "mailto://user:password@gmail.com"
```
The `apprise_test` self-test and the `notifyOnError` script-failure notification always use the
top-level `apprise:` list (plus, during `apprise_test`, each configured account's own list gets a
test message too, so a bad per-account URL is caught early).
