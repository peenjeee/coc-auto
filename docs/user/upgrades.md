# Wall and building upgrades

### Upgrading walls

```bash
python bot.py --walls home --live
```
```bash
python bot.py --walls both --first home --live
```
```bash
python bot.py --walls home --hours 3 --live
```
```bash
python bot.py --walls home --max-upgrades 20 --minutes 45 --live
```
```bash
python bot.py --walls both --bulk-walls --live
```
```bash
python bot.py --walls home --wall-resource elixir --wall-every 5 --live
```

`--walls` takes `home`, `builder` or `both`. For each village in turn it first spends the gold and elixir already
in storage: it opens the builder list, finds a "Tembok" row ("Wall" in an English game; the row template follows the
list's own language, so the Grand Warden's "Wali Besar" is never taken for a wall), and upgrades one wall at a time
with whichever of gold or elixir is still affordable. It always takes the lowest wall level first, and moves on to the next level only when that one is used up. Each wall level costs more than the one below, so it reads the price on every wall row and picks the cheapest. Position in the list says nothing about level: the game puts affordable rows above ones the storage cannot pay for. When neither resource is affordable any more, it
farms until gold and elixir are both full, spends them again, and repeats. It stops once no "Tembok" row is left, which means
every wall is at the maximum level for the current Town Hall or Builder Hall. In the Builder Base it also claims the
Elixir Cart first; its lower walls cost gold only, higher ones gold or elixir (gold first, like the Home Village), and only the upgrade buttons a wall actually shows are used. Builder Base walls also need a free builder: with every builder busy the game offers to free one for gems, and the bot closes that offer with X and skips the Builder Base walls for that run. If storage is full and still no wall can be upgraded, the run stops instead of looping. `--first` picks the village to finish first when `both` is chosen.
`--wall-resource gold` or `elixir` pays for walls with that resource only and farms only it full; a wall that the chosen
resource cannot pay for (such as a low Builder Base wall, gold only, with `elixir`) is never upgraded, and once the
storage is full with no wall that resource can pay for, the run stops. `--wall-every N`
goes back to the walls after N attacks, or sooner once the storage is full, so walls get the loot without waiting for a
full storage; a short round that leaves nothing affordable just farms on.
The bot never spends gems. It taps an upgrade only when its price is shown in white, and taps Konfirmasi only when
the dialog is a wall upgrade, its price is white, and its icon is the chosen gold or elixir. During a sale such as
Hammer Jam the Home Village dialog changes: the button carries a "Konfirmasi" label, the new price is green and the old
one is struck through in white below it. The bot knows this layout by the label (`walls.home.confirm.sale`) and then
counts only the green price, so a sale price the storage cannot pay (shown in red) is never confirmed even though the
struck-through price is white; the icon must still be gold or elixir. The gold reserve is checked against that green
price too, so gold keeps paying for walls during a sale.

With `--bulk-walls` the selected wall's "Tingkatkan Lebih Banyak" ("Upgrade More") button upgrades many walls of its
level in one go (`walls.<village>.bulk`). The button is found by its label, since the wall drawn on it is the selected
wall's level. The bot adds ten walls at a time while the price stays white, then takes one off at a time until it is
white again (one at a time when +10 is greyed out, or gone: with only a few walls of that level left the bar shows -1
and +1 only), never beyond `--max-upgrades` or, for gold, the gold reserve. The dialog that follows ("Yakin ingin
meningkatkan tembok yang dipilih dengan … Emas?", in English "… for … Gold?") gets Oke only when it names the chosen
resource (Emas/Gold, Eliksir/Elixir, or their "Tukang" Builder Base forms), so a gem price is never confirmed; anything
else is cancelled with Batal. A wall without the button, or a
resource that pays for one wall only, takes the one-wall upgrade. When the button lights up more walls but the -1/+10/+1
bar never comes (the plain wall panel stays, as with the last few walls left to max), the run stops using it and goes
on one wall per upgrade. The bulk selection outlives the builder list, so the
bot clears it by opening and closing the list. With wall rings (both villages) the ring adds a button, and beside the
open builder list the buttons wrap into two rows: "Tingkatkan Lebih Banyak" ends the top row, and in bulk mode -1,
+10 and +1 sit above the upgrade buttons instead of left of them; the bot reads both layouts. A bulk bar it cannot
place them in is left untouched and the rest of that run goes one wall at a time. Wall rings are never used. It reads the gem count
before and after every upgrade and keeps the last trusted count across recoveries. A count that drops with the same
number of digits stops the run for good, without any recovery. A count with other digits or a higher one is treated
as a misread: nothing is tapped and recovery reads it again. In the Home Village it also keeps a gold reserve (`walls.home.gold_reserve`, 200,000) for opponent searches, which cost gold: a gold upgrade that would dip below it is closed and that session continues with elixir. A village needs a
`walls.<village>` calibration before `--walls` accepts it.

### Upgrading buildings

```bash
python bot.py --buildings --live
```
```bash
python bot.py --buildings --hours 8 --live
```

`--buildings` upgrades the rows picked on the dashboard's **Upgrade** page: in the Home Village buildings (saved as
`buildings` in `config.local.json`, with `keep_builders`), Laboratory research (`lab`), pets (`pets`) and hero
equipment (`gear`), in the Builder Base its buildings (`builder`) and Star Laboratory research (`star`), each the
targets in priority order, `{"id", "name", "level"}` with `level` `null` for Max. With accounts on the dashboard's
**Accounts** page, every village keeps its own picks (`account_picks` in `config.local.json`, by player tag), and the
Upgrade page names the account it shows; see [Several accounts](farming.md#several-accounts). Each round looks at every list with
targets in turn, Home Village buildings first. For each it reads the gold, elixir and dark elixir on
hand from the resource bars (`capacity.home`) and the free builders from the village's "1/6" counter
(`building_upgrade.free_builders`). A matching Goblin portrait excludes the paid Goblin slot from that count;
Goblin use still needs its separate opt-in and gem checks. A confirmed full queue waits for its running timer,
bounded at six hours; a refusal learned only from a dialog keeps a short recheck. It opens the builder list on that list's tab and reads its actionable rows. It
takes the first target, in the order picked, with a row that is not upgrading now, whose next level does not pass the
target level (any level for Max) and whose cost is covered by the resource on hand (for equipment: every ore cost
read), as long as the list has room: more builders free than `keep_builders` for buildings (for example 1 to leave a
builder for walls), a free slot on the Laboratory or pet house counter at the bottom of its tab ("0/1"), and for
equipment always, as ore is its only limit. A row is matched by its catalog id, or by its name as read for one the
catalog does not know. A picked row that is upgrading now is waited for and then upgraded on. The row is tapped only
while the list's own close button shows undimmed, and opens the game's upgrade dialog: `building_confirm` (the same
dialog as a wall's) for buildings, research and pets, the Blacksmith's `gear_confirm` for equipment; any other screen
after the tap stops the try and nothing on it is tapped. The building dialog's confirm button is tapped only when its
price equals the row's cost and that price is white (green during a sale) beside that row's gold, elixir or dark
elixir icon; the Blacksmith's only when it asks exactly the row's ore costs, all white (a short ore shows red). This is
checked again in the very frame the tap follows. A gem price never matches, and the grey magic item button beside
the confirm ("Hammer of Building", "Hammer of Fighting") is never tapped. Anything else closes the dialog with X, logs
why, and lets the next target go first until the next wait. The list's builders tab is shown again before the list is
closed. Each upgrade logs `Bangunan di-upgrade`, `Riset di-upgrade`, `Hewan di-upgrade` or `Peralatan di-upgrade:
<name> ke level <n>` ("Building / Research / Pet / Equipment upgraded: …"), which the Stats page and the Discord
progress report count as upgrades. The gem count is read before and after each upgrade, as for walls. When nothing can
be done in any list and a target's first row is only short of the resource it costs, with a builder or slot free for
it, the bot attacks in that target's village (Home Village or Builder Base, with the task's attack settings) 3 times or
until that resource is full, then looks again; already full, it waits instead. Otherwise (no builder or slot to spare)
it waits until the soonest running upgrade in a list with something left is done (the timer read in either language:
Indonesian `j` is hours, `h` days and `d` seconds): a wait of 15 minutes or more is spent with CoC closed (6 hours at
most, then it looks again) and the game opened again afterwards; a shorter one keeps the game open, 10 minutes at
most. `--hours`/`--minutes` stop it, in the village. Once every target has passed its level (or left its list) and
none is upgrading, it stops with exit code 0. The gem offer and "all builders busy" popups are closed with X, as for
walls.

**Builder Base** picks (`builder`, `star`) are read from the Builder Base's resource bars (`capacity.builder`) and the
free builders or Star Laboratory slot on the list's tab ("0/3", "1/1"). Their rows open an upgrade dialog
(`builder_upgrade_confirm`, told by its red X and the corner of its green price button, no text) whose layout differs
from one upgrade to the next: a building's button is centred and larger, a hero's or a Star Laboratory upgrade's sits
at the bottom right. The button is found as the largest green patch at the bottom of the dialog, its price read white
beside the icon, and the icon told by its colour (Builder Gold yellow, Builder Elixir purple; a gem's green is
neither). It is tapped, where it was found in the very frame the tap follows, only for the row's own price and
resource. A locked Builder Base row opens the same `locked_info` dialog as the Home Village's ("Power P.E.K.K.A
(Locked)", "Level 10 Barracks Required"): it needs a free Builder Base builder on the tab bar, its "Go" selects the
building on the Builder Base, whose hammer button price is read and checked against the Builder Gold or Builder
Elixir on hand, and the dialog that opens is confirmed like a row's. No gems are paid on the Builder Base.

A picked **locked** row (the game's padlock, "Terkunci"/"Locked") is worked toward the way a player does, in any list
(the Builder Base's as described above): in the Home Village it needs a builder like a building (more free than
`keep_builders`), whatever its list. The row is
tapped with the list in front and must open its info dialog (`locked_info`: "Meriam Pantul (Terkunci)" / "Ricochet
Cannon (Locked)", "Tingkatkan Meriam ke level 21 untuk membuka!"); its green "Ayo"/"Go" button closes the list and
selects the building it waits for on the village, where the one hammer button beside it ("Tingkatkan"/"Upgrade") is
found by the wall button template (`walls.home.button`), its price read (white, gold or elixir) and checked against the
resource on hand. The hammer opens that building's `building_confirm`, confirmed only when it shows that same price
and resource, checked again in the very frame the tap follows, as for a row. No hammer (maxed or upgrading), a red or
unread price, more than one button, a dark elixir price or any other dialog upgrades nothing and leaves the target for
the next wait. It logs `Bangunan di-upgrade untuk membuka <name>` ("Building upgraded to unlock …"). The target stays
picked until the game lists the row unlocked; from then on it is a normal row. A building's own upgrade dialog that
points at another building first (the Town Hall's red "Perhatian!"/"Note!" bar with "Ayo"/"Go", told by the bar's red
and the button's green, no text) is never confirmed: its green button is followed the same way. The Town Hall's
"Peningkatan Terkunci"/"Upgrade Locked" popup (`upgrade_locked`) has its first required building's blue tile tapped;
the game opens the builder list at that building's row, marked with a white border, and that row, when it is a locked
one, is followed like a picked one. Rows opened from another tab leave the list on that tab, so it is opened again and
its builders tab shown before it is closed.

**Goblins** are the only thing the bot ever pays gems for, each only with its switch on (`goblin` in
`config.local.json`, set on the dashboard's Upgrade page: `{"builder": false, "researcher": false, "max_gems": 0}`,
`max_gems` 0–1000 per hire, 0 never). The Goblin Researcher shows as a second Laboratory slot ("1/2"): switched off,
only the house's own slot counts (free minus the extra ones), so research waits for the Laboratory; switched on, any
free slot is used and the confirm brings up the Goblin's offer. The Goblin Builder: switched on, a building is tapped
even with no builder free. The offer (`goblin_offer`: one button for the Goblin Researcher, two for the builders,
"Tukang Goblin"/"Goblin Builder" and "Bebaskan Tukang"/"Free a Builder") is paid at its cheapest gem button, only when
every button's price reads cleanly and that one is at most `max_gems`, read again in the very frame the tap follows; it
logs `Goblin disewa: <gems> permata untuk <name>` ("Goblin hired: …"). The gem watch then allows exactly that drop once
and still stops the run on any other change. A builder freed for gems brings the upgrade dialog back, which is checked
and confirmed again as before. Switched off, or over the limit, the offer is closed with X and the target waits.
