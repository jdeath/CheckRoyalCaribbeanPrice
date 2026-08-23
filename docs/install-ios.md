[Back to README](../README.md)

## Install (iOS / iPhone)
iOS can run a stripped down version or an almost full version that supports everything except apprise! You can also run the [GreaseMonkey version](install-greasemonkey.md). Stripped down version is a little easier to setup, but setup must be repeated if code needs an upgrade. Full version is harder to setup, but much simpler to upgrade.

### Stripped down version
This will run a stripped down version to work on the free Python iPhone app. It is a little easier to setup, but need to repeat setup if a new version comes out.
As stripped down, it only supports excursion/drink packages etc. It does not support cruise fare price checks. It does not support apprise notifications, so you will have to watch the log to see any price drops. You need to edit the python file directly (directions below) because it does not use the config.yaml file. But allows you to check prices on the go. Works on the ship even *without* the internet package!

1. Install Python on your Phone
   - iOS: Get Python From Appstore. `https://apps.apple.com/us/app/python-coding-editor-ide-app/id6444399635`
      - Free version is fine, no need to make inapp purchases.
3. Download `https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/PhonePriceCheck.py` from the repo to your computer
   -   Use a text editor to add your username and password between the "" a few lines down.
   -   If you are are using a Celebrity account, remove `#` before `#cruiseLineName = "celebritycruises"`
   -   Ignore the [Edit Config File](config.md) section below, that only pretains to computer installations
4. Email yourself the edited `PhonePriceCheck.py`
   -   On your iPhone, save the emailed `PhonePriceCheck.py` to your files section. This can be done by clicking the attachment, select share, then select saved files
5. Open Python App (these are iOS instructions, need to modify for Android)
   -    Tap the **blue** hamburger icon on the top right side of the screen, just below the adverstisement
   -    Tap "Load from File"
   -    Select the PhonePriceCheck.py file you downloaded
   -    To run: tap the arrow icon at top right of screen (between a bug icon and a `...` icon)
6. Look for any price drops in the output

### Full Version (almost!)
This will run the standard python code. It does not support apprise notifications. You will have to watch the log to see any price drops or look at the log file. Allows you to check prices on the go. Works on the ship even *without* the internet package!

1. Install Python on your Phone
   - iOS: Get Python From Appstore. `https://apps.apple.com/us/app/python-coding-editor-ide-app/id6444399635`
      - Free version is fine, no need to make inapp purchases.
3. Download `https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/CheckRoyalCaribbeanPrice.py` from the repo to your computer or direct to your phone
4. If download on computer, email yourself the edited `CheckRoyalCaribbeanPrice.py`
   -   On your iPhone, save the emailed `CheckRoyalCaribbeanPrice.py` to your files section in Download Folder. This can be done by clicking the attachment, select share, then select saved files
5. Open Python App (these are iOS instructions, need to modify for Android)
   -    Tap the **blue** hamburger icon on the top right side of the screen, just below the adverstisement
   -    Tap "Load from File"
   -    Select the CheckRoyalCaribbeanPrice.py file you downloaded
   -    To run: tap the arrow icon at top right of screen (between a bug icon and a `...` icon)
   -    Type `y` to download the baseline config, it will to "On My Iphone/Python CodePad"
   -    Click blue hamburger icon, "Load From File" and navigate to "On My Iphone/Python CodePad" and config.yaml
   -    Edit this file with your username/password and any other settings besides `apprise:` (code will crash if you have `apprise` in your config)
   -    Click Save. You can also probably move your normal config.yaml via iTunes if you wish.
   -    Here is tricky part, the above step saved config.yaml as config.py
   -    Open iPhone Files App. Navigate to "On My Iphone/Python CodePad". Delete config.yaml. Click config.py and rename to config.yaml. Confirm you want to change name to .yaml
   -    Go back to Python App, Open CheckRoyalCaribbeanPrice.py, and run
   -    You can use the file app to open output.txt if you want to review what happened.
   -    The config file never needs to be touched again.
6. Look for any price drops in the output
   