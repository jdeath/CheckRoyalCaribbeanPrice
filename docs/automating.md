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
