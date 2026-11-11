Loot HUD crops from the user-supplied `j8yroj.zip` diagnostic package. Each image
stacks the original 150×26 gold and elixir regions vertically, without rescaling
or changing their pixels. `sources.json` records the original frame hashes and
crop coordinates. Only the resource digits and their immediate background are
included; the full diagnostic logs and screenshots are not part of the patch.

`stable-a` and `stable-b` come from two real scout frames with unchanged loot but
animated scenery. `different-opponent` supplies the prior opponent. The noise
pair records the one-pixel signature difference that made an earlier comparator
mistake the same opponent for a new one.
