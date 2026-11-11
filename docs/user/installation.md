# Installation and first run

## Windows download

1. Install [LDPlayer](https://www.ldplayer.net/), then install CoC inside it and sign in.
2. Open [CoC Auto Releases](https://github.com/peenjeee/coc-auto/releases/latest).
   Expand **Assets** and download `coc-auto-<version>-windows-x64.zip`.
   The first packaged release must be published by a maintainer before this asset appears.
3. Right-click the ZIP → **Extract All**. Open the extracted **CoC Auto** folder.
4. Double-click **CoC Auto.exe**. Leave its window open while using the dashboard.

No Python, Git, or OCR installation is needed.

## Connect your emulator

In **Device settings**:

1. Click **Auto-detect** with LDPlayer running. If several emulators appear, choose yours.
2. Save the detected device settings.
3. If the display differs, click **Fit screen**. This enables local ADB and sets
   1280×720 at DPI 240; LDPlayer restarts. Wait for it to finish, then detect and save again.
4. Open your village. In CoC's own language settings, choose **English** or
   **Bahasa Indonesia**. Changing Android's language does not change CoC.
5. Click **Check village**, then **Continue to dashboard** when it is recognized.

Read and acknowledge the requirements banner. It remains until acknowledged.
Use a dry run before your first live task; a dry run recognizes the screen without tapping.

Advanced device paths and serial numbers are available under **Advanced device settings**.
Most users can leave them alone.

## Import an existing installation

Close both CoC Auto installations first. In the new download, double-click
**Import existing setup.cmd** and choose the old installation folder containing
`config.local.json`. The app then opens with the imported setup.

The importer copies settings, available statistics, upgrade history, and saved scans.
It also retains pairing/display state when present and preserves custom calibration.
Existing destination files are backed up under `%LOCALAPPDATA%\CoC Auto\backups`.
The old installation is never edited or removed. Retest detection if you moved to another PC.
Browser-only preferences, such as a dismissed banner, remain in the browser rather than the archive.

## Update or quit

Close the launcher window to quit. To update, extract the newer ZIP into a new
folder and open its executable. Settings and statistics live separately in
`%LOCALAPPDATA%\CoC Auto`; replacing the app folder does not replace them.

## Other installation paths

- [Apple Silicon Mac / BlueStacks Air](../../scripts/README.md)
- [Run from source](../developer/development.md)
- [Advanced device setup](advanced-setup.md)
- [Troubleshooting](troubleshooting.md)
