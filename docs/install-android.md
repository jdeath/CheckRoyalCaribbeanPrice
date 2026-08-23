[Back to README](../README.md)

## Install (Android)
Android users have option of running a stripped down vibe-coded native app, or stripped down python or full version python (python is not vibe-coded).  Stripped down python version only supports excursion/drink packages etc. You need to edit the python file directly (directions below) because it does not use the config.yaml file. Full version supports everything. Both options allow you to check prices on the go. Works on the ship even *without* the internet package!

### Native Android App (Stripped Down & Vibe Coded with Claude to convert from python code)
1. Install the [APK](https://github.com/jdeath/CheckRoyalCaribbeanPrice/releases/download/3.3.1/CheckRoyalCaribbeanPrice.apk) on your Android phone or Fire Tablet.
1. Start it, allow notifications.
1. Click "Go to Settings" and enter you Royal Caribbean account credentials.
1. Back out of settings, then click "Run Check" button at bottom right.
1. As stripped down, it does not handle Apprise notifcations or automatic check cabin prices. It will notify via Android and do manual cabin price checks from a URL.
1. If you get the cruise URL on your phone, when you change an addon (like gratituies, insurance, refundable deposit) do a reload of page from the menu to pull in new URL or code may only find the price without addons.
1. May have problems with password with special characters (most of debugging was to fix this!)
1. As vibe-coded, it will not be updated much
1. The "Enable Auto Check" feature has not been tested.
1. If the app crashes after upgrading, delete the data and cache in your android settings menu for the app.
   
### Stripped Down Python Version

1. Install Python on your Phone
   - Android:  Get pydroid 3 : `https://play.google.com/store/apps/details?id=ru.iiec.pydroid3`
      - Open Python App, and install required library
      - Click menu, click PIP, type `requests`, click install.
3. Download phone version `https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/PhonePriceCheck.py` from the repo to your computer
   -   Use a text editor to add your username and password between the "" a few lines down.
   -   If you are are using a Celebrity account, remove `#` before `#cruiseLineName = "celebritycruises"`
   -   Ignore the [Edit Config File](config.md) section below, that only pretains to computer installations
4. Email yourself the edited `PhonePriceCheck.py`
   -   On your phone, save the emailed `PhonePriceCheck.py` to your Downloads section. 
5. Open Python App 
   -    Tap the Folder icon in the top right
   -    Tap Open and Navigate to downloaded file ("Storage Access Framework might be easiest")
   -    Select the PhonePriceCheck.py file you downloaded
   -    To run: tap the yellow arrow icon at bottom right of screen
6. Look for any price drops in the output

### Full Python Version

1. Install Python on your Phone
   - Android:  Get pydroid 3 : `https://play.google.com/store/apps/details?id=ru.iiec.pydroid3`
      - Open Python App, and install required library
      - Click menu, click PIP, type `requests`, click install. Repeat for `PyYAML` and `Apprise`
3. Download full version `https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/CheckRoyalCaribbeanPrice.py` from the repo to your computer or direct to phone
4. If downloaded to computer, email yourself the edited `CheckRoyalCaribbeanPrice`
   -   On your phone, save the emailed `CheckRoyalCaribbeanPrice.py` to your Downloads section. 
5. Open Python App 
   -    Tap the Folder icon in the top right
   -    Tap Open and Navigate to downloaded file ("Storage Access Framework might be easiest")
   -    Select the CheckRoyalCaribbeanPrice.py file you downloaded
   -    To run: tap the yellow arrow icon at bottom right of screen
   -    Select Y to download a config file
   -    The config.yaml file will probably be downloaded to root of your internal (not SD card) storage
   -    Edit the [config.yaml](config.md) (see below directions) or replace with the one you use on your computer
   -    Go back to Pydroid, Open CheckRoyalCaribbeanPrice.py, tap the yellow arrow icon at bottom right of screen
