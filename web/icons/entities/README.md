# Upgrade artwork

Clash of Clans artwork belongs to Supercell and is supplied by
[ClashKing Assets](https://github.com/ClashKingInc/ClashKingAssets).
`sources.json` preserves the pinned revision and original URL of every portrait.

The dashboard bundles six WebP sheets, one for each upgrade list, at 96 px per
portrait. `web/upgrade-sprites.js` maps each entity and level to its crop. The
970 source portraits occupy about 3.67 MB as sheets plus mapping, compared with
8.79 MB as individual images. One shared portrait is duplicated so each list
can load its own sheet. Sheet filenames include content hashes.

The Home buildings sheet is about 2.02 MB and 22.08 MB when decoded to RGBA.
Loading one icon downloads its whole sheet. This reduces bundled file count
and overall artwork size; it does not guarantee a smaller cold download for
one village. Portraits load lazily and the service worker caches viewed sheets.
All sheets are bundled for local use without an internet connection.

Refresh pinned artwork and rebuild the sheets with:

```sh
python scripts/sync_upgrade_icons.py
```

The script caches originals in ignored `.coc-auto/artwork/entities`. To rebuild
from that cache without downloading:

```sh
python scripts/build_upgrade_sprites.py
```

The builder uses the existing OpenCV/NumPy dependencies. Generated sheets and
mapping are committed; individual originals are not. It cleans obsolete WebP
files only inside its generated output directory after a successful build.

Export rows use their reported current level. Explicit queue goals use their
target level. Heroes, troops, pets and equipment use standard portraits.
Unsupported artwork uses a category icon, then initials if that also fails.
