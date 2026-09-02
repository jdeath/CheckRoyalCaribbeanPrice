[Back to README](../README.md)

## Automating
1. Linux: Put in a cron job, if running in linux, I am sure you know how! Be sure to either provide optional argument for the `config.yaml` path or be sure to execute the script from within the directory where the configuration script is present.
1. Home Assistant: Use directions in my [repo](https://github.com/jdeath/homeassistant-addons/tree/main/royalpricecheck)
1. Docker: See directions in [docker section](install-docker.md) above
1. Windows: Use windows task schedular
    1. Type "task schedular" in Windows search bar to bring up program (icon is a clock with 12-3 o'clock shadded)
    1. Create a basic task (Action Menu->Create Basic Task)
    1. Select a daily trigger, suggest a little before you wake up
    1. Action, select "Start a Program"
    1. In "Program/script" Select the CheckRoyalCaribbeanPrice.exe file you download from here. Make sure the config.yaml is in same directory as .exe (if running python script, should be able to put python.exe the full path of this the script location)
    1. In "Start in (optional)" enter the directory of the .exe/.yaml (you can copy the "Program/script" field, paste it, and remove the CheckRoyalCaribbeanPrice.exe)
    1. After clicking finish, you can right click on task, go to triggers, and add more times to trigger the script. Suggest a time right before you get home from work. Twice a day should be sufficient
    1. Ensure apprise notifications are working, because the window will close automatically after run.

## Exit Codes
If your scheduler reacts to the process exit code (e.g. only retrying/alerting on failure), `CheckRoyalCaribbeanPrice.py`/`.exe` exits with one of three codes:
1. `0` - Success. Every account was checked.
1. `1` - Fatal failure (e.g. a bad `config.yaml`, or an unhandled error). This can happen before any account was checked, or partway through a multi-account run after earlier accounts already succeeded and had their price-drop alerts sent - the exit code alone doesn't tell you which. **Do not blindly auto-retry on this code** (that risks re-checking and re-alerting accounts that already succeeded); surface it to a human and check the log first.
1. `2` - Partial failure. The run **completed** and finished checking every account it could, but one or more accounts could not be logged in (or had their profile fail to load) and were skipped - see the log for which account(s) and why. Data for every account that DID succeed was already written and any price-drop alerts for them were already sent, so **do not blindly retry the whole run on exit code 2** - that would re-check and re-alert the accounts that already succeeded. Instead, fix the failing account (credentials, connectivity) and re-run, or just let the next scheduled run pick it up.
