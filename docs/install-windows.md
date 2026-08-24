[Back to README](../README.md)

## Install (Windows 10/11 Option) - Python Not Required!
1. Download [CheckRoyalCaribbeanPrice.exe](https://github.com/jdeath/CheckRoyalCaribbeanPrice/releases/latest/download/CheckRoyalCaribbeanPrice.exe) .  Link points to the latest release
1. Move CheckRoyalCaribbeanPrice.exe file to a folder (optional) , or just leave it in Downloads.
1. Click `CheckRoyalCaribbeanPrice.exe` file on your computer
   - Note: If no config file is found, code will ask to download a simple config file for you and name it correctly. type "y" and hit enter to download
1. Edit downloaded config.yaml (using NotePad) with your user/password. Do not change the spacing before the `-` lines. `#` means comment and everything to the right will be ignored
1. The downloaded configuration file will log the output to "output.txt", this avoids requiring to keep the output on screen.
1. Click `CheckRoyalCaribbeanPrice.exe` again and watch the magic!
1. After confirmed working, you can add more options into `config.yaml` and review the [automation/notification section](automating.md) below if you want to run it automatically a couple times a day!
1. To keep output on screen, go to folder you put `CheckRoyalCaribbeanPrice.exe`, type `cmd` and hit enter in the location field. A dos prompt window should open up. Type `CheckRoyalCaribbeanPrice.exe` in the dos prompt:
 

<img src="https://github.com/jdeath/CheckRoyalCaribbeanPrice/blob/main/images/Screenshot%202026-03-16%20071344.png" height="120"> <img src="https://github.com/jdeath/CheckRoyalCaribbeanPrice/blob/main/images/Screenshot%202026-03-16%20071642.png" height="120">

7. Optional: For advanced users, you can compile the .exe yourself (because you do not trust files from the internet) with: `pyinstaller -F --collect-all apprise CheckRoyalCaribbeanPrice.py` 
