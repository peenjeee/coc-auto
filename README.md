# CoC Auto

A Clash of Clans farming and upgrade bot with a browser dashboard.

![CoC Auto dashboard](docs/dashboard-desktop.jpg)

## Download for Windows

**[Download CoC Auto from Releases](https://github.com/peenjeee/coc-auto/releases/latest)**

Choose the **Windows ZIP** under **Assets**, rather than GitHub's source-code downloads.
Packaged downloads are available once a maintainer publishes the first Windows release.

## Get started

1. Install [LDPlayer](https://www.ldplayer.net/) and open Clash of Clans.
2. Download the Windows ZIP, right-click it, and choose **Extract All**.
3. Open the extracted folder and double-click **CoC Auto.exe**.

Your browser opens the dashboard. Go to **Device settings**, follow the checklist,
and check your village before starting a run. Keep the launcher window open.

You do **not** need Python, Git, or a separate Tesseract installation for the Windows download.

### Requirements

- Windows with a 64-bit LDPlayer instance.
- Clash of Clans set to **English** or **Bahasa Indonesia** in the game's own settings.
- **1280×720, DPI 240**. The dashboard can apply this and enable local ADB; LDPlayer restarts.
- Builder Base unlocked for Builder Base tasks; Dark Elixir Storage for dark elixir tasks.

**Already using CoC Auto?** Close it, open **Import existing setup.cmd** from the new
download, and select your old installation folder. Existing destination files are
backed up; your old installation stays untouched.

**Updating?** Close the app and extract the newer release into a new folder.
Your settings and statistics stay in `%LOCALAPPDATA%\CoC Auto`.

## What it does

- Farms Home Village and Builder Base resources separately.
- Upgrades walls and queued buildings, research, pets, and equipment.
- Shows the live device screen, ongoing upgrades, and farming statistics.
- Supports Discord notifications and an English/Indonesian dashboard.

## Help and guides

- [Installation and first run](docs/user/installation.md)
- [Troubleshooting](docs/user/troubleshooting.md)
- [Farming](docs/user/farming.md) · [Upgrades](docs/user/upgrades.md)
- [Dashboard and remote access](docs/user/dashboard.md)
- [Command-line reference](docs/user/cli.md)
- [Calibration](docs/user/calibration.md) · [Recovery](docs/user/recovery.md)

Apple Silicon Mac users can use the existing
[BlueStacks Air setup](scripts/README.md). A packaged Mac download is not available yet.

## Contributing

Building from source? Start with [CONTRIBUTING.md](CONTRIBUTING.md).
The [architecture guide](docs/developer/architecture.md) explains the folder layout.

[Report a bug](https://github.com/peenjeee/coc-auto/issues) with your app version,
what happened, and relevant logs. Remove webhook URLs and device keys before sharing logs.

## License

[PolyForm Noncommercial 1.0.0](LICENSE). You may use, fork, change, and share this
project for noncommercial purposes. Selling it or using it in paid work is not
allowed. Copies must include the LICENSE file and its Required Notice.

The license covers this project's code. Game artwork, screenshots, and videos
belong to Supercell. This project is not affiliated with, endorsed, or sponsored
by Supercell.
