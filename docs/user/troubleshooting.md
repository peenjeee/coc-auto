# Troubleshooting

| What you see | What to do |
| --- | --- |
| No Windows ZIP under Assets | A maintainer must publish the first packaged release. Source setup is in the developer guide. |
| App will not open from the ZIP | Right-click the ZIP and choose Extract All, then run the extracted executable. Keep its supporting files with it. |
| LDPlayer not found | Install and start LDPlayer, then click Auto-detect. Use Advanced only for an unusual install folder. |
| Several emulators | Choose the intended instance, detect again, and save. |
| ADB disconnected | Enable local ADB through Fit screen, wait for LDPlayer to restart, then detect and save again. |
| Display does not match | Use Fit screen; wait for the emulator restart before checking the village. |
| Village not recognized | Open the village, close overlays, and check the in-game language and display settings. |
| Dashboard closed | Double-click CoC Auto.exe again to reopen the running dashboard. |
| Port already in use | Close the other CoC Auto launcher, then try again. A different service on port 8765 must be closed or the configured port changed. |
| Import is refused | Close CoC Auto first and select the old installation folder containing config.local.json. |

Windows app logs are in `%LOCALAPPDATA%\CoC Auto\application.log`.
Source-checkout state is under `.coc-auto` in the repository.
Keep device keys and webhook URLs private when sharing diagnostic information.
For recognition problems, see [Calibration](calibration.md); for stalls, see [Recovery](recovery.md).
