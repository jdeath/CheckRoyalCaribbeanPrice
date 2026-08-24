[Back to README](../README.md)

## Install (MacOS Option) - Python Not Required!
1. Download [CheckRoyalCaribbeanPrice_MacOS_arm64](https://github.com/jdeath/CheckRoyalCaribbeanPrice/releases/latest/download/CheckRoyalCaribbeanPrice_MacOS_arm64) or [CheckRoyalCaribbeanPrice_MacOS_intel](https://github.com/jdeath/CheckRoyalCaribbeanPrice/releases/latest/download/CheckRoyalCaribbeanPrice_MacOS_intel) depending on your Mac hardware. arm64 = Silicon
1. We must now disable MacOS security for downloaded executables that are not signed.
1. Open a terminal (Command + Spacebar, type Terminal, and press Return)
1. Type `cd Downloads`
1. Type `chmod 755 CheckRoyalCaribbeanPrice_MacOS_intel` or `chmod 755 CheckRoyalCaribbeanPrice_MacOS_arm64` depending on your architecture
1. Open Finder. Go to Downloads
1. Click `CheckRoyalCaribbeanPrice_MacOS_intel` or `CheckRoyalCaribbeanPrice_MacOS_arm64`
1. A malware warning will pop up. Click "Done" (Not "Move to Trash")
1. Go to Settings->Privacy and Security . Under security you will see the Check Script was blocked. Click "Open Anyways". A menu will pop up, Click "Open Anyway" and enter you computer password.
1. Open Finder and click `CheckRoyalCaribbeanPrice_MacOS_intel` or `CheckRoyalCaribbeanPrice_MacOS_arm64`
   - Note: If no config file is found, code will ask to download a simple config file for you and name it correctly. type "y" and hit enter to download
1. The file will be saved in the root of your home directory, not in the current directory
1. Open Finder. Go To Menu Bar and click Go->Home
1. Edit downloaded config.yaml with your user/password. Do not change the spacing before the `-` lines. `#` means comment and everything to the right will be ignored
1. The downloaded configuration file will log the output to "output.txt" in your Home folder, this avoids requiring to keep the output on screen.
1. Click `CheckRoyalCaribbeanPrice_MacOS_intel` or `CheckRoyalCaribbeanPrice_MacOS_arm64`
1. After confirmed working, you can add more options into `config.yaml`. I do not know how to automatically run this every X hours on a Mac, hopefully someone will post directions.
1. If you download a new version, you will need to do the `chmod` and "Click Open Anyway" steps again.
