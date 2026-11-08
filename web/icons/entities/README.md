# Upgrade portraits

Clash of Clans game artwork belongs to Supercell. Images are supplied by
[ClashKing Assets](https://github.com/ClashKingInc/ClashKingAssets).
`sources.json` records the pinned revision and original URL of every image.

Building and trap artwork follows the destination level reported by the scan.
Queue artwork follows an explicit level goal, or the next scanned level for Max.
Unknown levels retain a representative portrait; known but unavailable levels use initials. Heroes, troops, pets, and equipment
use their standard portraits. These images do not claim to show a current level. Assets load lazily and are cached by the existing
service worker as viewed. Unsupported names use initials.

Refresh the catalog subset from the repository root with:

```sh
python3 scripts/sync_upgrade_icons.py
```
