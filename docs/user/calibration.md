## Calibration

| Files | UI version |
| --- | --- |
| `config.json` + `calibration/` | Current game UI (after the latest update). |
| `config.legacy.json` + `calibration-legacy/` | Previous game UI, kept for reference. Run it with `python bot.py --config config.legacy.json`. |

Calibration is specific to resolution, language, UI scale, and army composition. After a game update, or on a
different setup, recalibrate the screens that are no longer recognised. Calibration only works on screenshots and
never taps the game.

#### `calibrate.py` options

| Option | Meaning |
| --- | --- |
| `--config PATH` | Config to write (default `config.json` next to `calibrate.py`; only this shared file is written, never `config.local.json`). |
| `--init` | Create a new config; never overwrites one that exists. Needs `--serial`. |
| `--serial SERIAL` | ADB serial for `--init`, e.g. `emulator-5554` (see `adb devices`). |
| `--adb PATH` | `adb.exe` for `--init` (default `C:\LDPlayer\LDPlayer14\adb.exe`). |
| `--tesseract PATH` | Tesseract for `--init`; only needed when no digit templates are used. |
| `--gold N`, `--elixir N`, `--dark N` | Loot minimums written by `--init` (`0` = no filter). |
| `--capture PNG` | Save the current LDPlayer screen to a new PNG (never overwrites). |
| `--screen NAME` | Calibrate one screen (names below), from the live LDPlayer screen or from `--image`. |
| `--image PNG` | Calibrate from a saved screenshot instead of the live screen. |
| `--replace` | Recalibrate a screen that already exists: new anchors and tap points, everything else kept. |
| `--counts N …` | Units per troop card, left to right; needed the first time a deploy screen is calibrated. |
| `--learn-digits PNG:GOLD:ELIXIR …` | Build loot digit templates from scout screenshots labelled with the loot they show. |
| `--digits-dir DIR` | New folder for `--learn-digits` (default `calibration/digits`; it must not exist yet). |

#### General commands

Start a new config (first time on a new setup):

```bash
python calibrate.py --init --serial emulator-5554 --gold 500000 --elixir 500000 --dark 0
```

Save the screen LDPlayer shows now, to calibrate from later:

```bash
python calibrate.py --capture shot.png
```

Calibrate a screen from the live LDPlayer screen, from a saved screenshot, or again after a game update:

```bash
python calibrate.py --screen home
```
```bash
python calibrate.py --screen home --image shot.png
```
```bash
python calibrate.py --screen home --image shot.png --replace
```

Build digit templates from two or more labelled scout screenshots (gold and elixir as shown on screen):

```bash
python calibrate.py --learn-digits scout1.png:1234567:987654 scout2.png:450210:399870 --digits-dir calibration/digits-new
```

Use the legacy calibration with any command by adding `--config config.legacy.json`.

For each screen, the tool asks for two anchors (small, fixed pieces of UI that only that screen shows; not numbers,
timers or the base), then each tap point the screen needs. Deploy screens also ask for the deploy point and one slot
per `--counts` value, and `home_scout` asks for the gold, elixir and dark elixir loot numbers. An existing screen
needs `--replace`, which keeps everything else it has (`targets`, `auto`, `sweep`, `overrides`, `alt_anchors`,
`deploy`, `troops`, `loot`); tap points that a target already finds are not asked again.

#### Every screen

Open the screen in CoC, then run its command (add `--replace` if it is already calibrated, or `--image shot.png` to
use a saved screenshot). "Taps" are the points the tool asks for.

**Villages and attacks**

| Command | Screen | Taps |
| --- | --- | --- |
| `python calibrate.py --screen home` | Home Village | attack, switch (boat) |
| `python calibrate.py --screen home_menu` | Home attack menu ("Pertempuran") | find, close |
| `python calibrate.py --screen home_army` | Army screen before searching | attack, close |
| `python calibrate.py --screen home_scout --counts 1 1` | Opponent shown, before deploying | next; deploy point, card slots, loot numbers |
| `python calibrate.py --screen home_battle` | Home battle in progress | surrender (optional, for `--surrender`) |
| `python calibrate.py --screen home_result` | Home battle result | return |
| `python calibrate.py --screen builder` | Builder Base | attack, switch (boat), cart |
| `python calibrate.py --screen builder_menu` | Builder Base attack menu | find, close |
| `python calibrate.py --screen builder_scout --counts 1 1` | Builder Base stage 1, before deploying | deploy point, card slots |
| `python calibrate.py --screen builder_battle` | Builder Base stage 1 in progress | surrender (optional, for `--surrender`) |
| `python calibrate.py --screen builder_stage2 --counts 1 1` | Builder Base stage 2, before deploying | deploy point, card slots |
| `python calibrate.py --screen builder_battle2` | Builder Base stage 2 in progress | surrender (optional, for `--surrender`) |
| `python calibrate.py --screen builder_result` | Builder Base battle result | return |
| `python calibrate.py --screen builder_cart` | Elixir Cart window | claim, close |
| `python calibrate.py --screen loading` | Game loading screen | – |

**Popups**

| Command | Screen | Taps |
| --- | --- | --- |
| `python calibrate.py --screen star_bonus` | Home star bonus | ok |
| `python calibrate.py --screen builder_star_bonus` | Builder Base star bonus | ok |
| `python calibrate.py --screen reward_chest` | Reward chest | open |
| `python calibrate.py --screen reward_card` | Reward card | continue |
| `python calibrate.py --screen layout_blocked` | "Defence layout blocked" warning | ok |
| `python calibrate.py --screen league` | League popup | close |
| `python calibrate.py --screen welcome` | Welcome back popup | ok |
| `python calibrate.py --screen idle` | "Ada orang di sana?" (inactive) | reload |
| `python calibrate.py --screen update_offer` | "Pembaruan Tersedia!" (update offer) | update (the game's update button) |
| `python calibrate.py --screen disconnected` | "Sambungan terputus" / "Kesalahan sambungan" | retry |
| `python calibrate.py --screen search_failed` | "Tidak dapat menyerang desa! Coba lagi nanti." | close |
| `python calibrate.py --screen gem_offer` | "Perlu lebih banyak …" (resources for gems) | close (the X, never the gem button) |
| `python calibrate.py --screen other_device` | "Perangkat lain tersambung ke desa ini" | reload |
| `python calibrate.py --screen surrender_confirm` | "Akhiri Pertempuran?" (Batal / Oke) | confirm (the Oke button) |
| `python calibrate.py --screen builders_busy` | "Semua tukang sibuk!" | close (the X) |
| `python calibrate.py --screen gold_pass` | "Get Gold Pass!" offer (any language) | close (the red X) |
| `python calibrate.py --screen maintenance` | Maintenance break notice | – (CoC is closed and the run stops; start it again after the break) |

**Walls**

| Command | Screen | Taps |
| --- | --- | --- |
| `python calibrate.py --screen build_list` | Builder list ("Tembok" rows) | close |
| `python calibrate.py --screen wall_selected` | A wall selected, upgrade buttons shown | close, build |
| `python calibrate.py --screen wall_confirm` | Home Village wall upgrade dialog | confirm, close |
| `python calibrate.py --screen builder_wall_confirm` | Builder Base wall upgrade dialog | confirm, close |
| `python calibrate.py --screen wall_bulk_confirm` | Bulk upgrade dialog ("Yakin ingin meningkatkan tembok yang dipilih …"), needed by `--bulk-walls` | confirm (Oke), close (Batal) |
| `python calibrate.py --screen building_confirm` | A building's upgrade dialog, needed by `--buildings` (anchors: its X and the "Upgrade time" label; the wall dialog overrides it) | confirm, close |
| `python calibrate.py --screen locked_info` | A locked row's info dialog ("Meriam Pantul (Terkunci)" / "Ricochet Cannon (Locked)"), used by `--buildings` (anchors: its X and both ends of the green "Ayo"/"Go" button, no text) | go, close |
| `python calibrate.py --screen upgrade_locked` | "Peningkatan Terkunci" / "Upgrade Locked" (the Town Hall waits for buildings), used by `--buildings` (anchors: its X and both ends of the green "Oke"/"Okay" button) | close || `python calibrate.py --screen goblin_offer` | The Goblin Researcher's one-button offer or the Goblin Builder's two-button one ("Hai, Chief!" / "Hey Chief!"), used by `--buildings` (anchors: its X and the gem on a green button; the two layouts are two anchor groups) | close (paid only by `--buildings` with the switch on, at the cheapest gem button found on screen) |

The `--counts` values are the units on each troop card, left to right (for example `--counts 11 4` for 11
dragons and 4 rage spells); with an `auto` card-bar plan they are only used for the first calibration. The wall
details beyond these screens (price band, row template, gem count areas, confirm checks), storage bars, digit
folder and zoom settings are keys in `config.json`; see the configuration reference below.

### Changing the army and deploy plan

The army and how it is deployed live in `config.json`; no code changes are needed.

| Village | Key |
| --- | --- |
| Home Village | `screens.home_scout.auto` (reads the card bar; takes precedence over `troops`), plus `screens.home_scout.sweep` |
| Builder Base, stage 1 | `screens.builder_scout.auto` (reads the card bar; takes precedence over `troops`) |
| Builder Base, stage 2 | `screens.builder_stage2.auto` (reads the card bar; takes precedence over `troops`) |

A deploy screen with an `auto` entry builds its plan from the cards in the bar on every attack, once two frames in a
row show the same cards, so a new army needs no slot changes; its `troops` list is then ignored. In the Home Village
`auto` has three fields:

| Field | Meaning |
| --- | --- |
| `bar` | `[x, y, width, height]` of the card bar. |
| `hold` | Seconds each troop card is held at the screen's `deploy` point; it stops early once the card is grey. |
| `spells` | Spell drop points, listed as rounds of front, left and right, each round further into the base. |

In the Builder Base `auto` has `bar` and `points` instead. Troop cards carry a magenta header, or a dark blue one for
the camps that lived through stage 1, and a white slot number top left; hero cards (Battle Machine, Battle Copter)
have neither. Each hero is dropped at the screen's `deploy` point and then tapped again for its
ability. One tap deploys a whole troop camp, so each troop card is tapped once, at the next point of `points` in
turn; cards greyed out in stage 1 are skipped. A camp of several units (Raged Barbarians) shows an `Nx` label top
right instead and gets N taps on its point, one per unit. Five seconds after the last camp lands every troop card is tapped once
more, which fires the troop abilities, in stage 1 and stage 2 alike. Through the battle the hero's ability meter,
three steps above its card, is watched: once every step is charged (magenta) the hero card is tapped again, as the
ability fired earlier is weaker and empties the meter. There are no spells.

| Field | Meaning |
| --- | --- |
| `bar` | `[x, y, width, height]` of the card bar, header included (the troop headers sit higher than in the Home Village). |
| `points` | Spread drop points around the base, used one per troop card. |

Without `auto`, `troops` is an ordered list. Each item is one card in the troop bar, used in list order:

| Field | Meaning |
| --- | --- |
| `slot` | `[x, y]` of the card in the troop bar. Required. |
| `count` | How many taps to drop from this card. Required. |
| `deploy` | One drop point `[x, y]` for this card. Defaults to the screen's own `deploy` point. |
| `points` | Several drop points; taps cycle through them (e.g. spells along the troop path). Overrides `deploy`. |
| `hold` | Seconds to keep a finger down on the first drop point instead of tapping (fast for dragons). |
| `until_empty` | Stop this card as soon as it is greyed out (empty). |
| `tap_only` | Only tap the card itself, e.g. a second tap on a hero to fire its ability. |
| `wait` | Seconds to wait before this card. |

Example of a fixed `troops` plan:

```json
{"slot": [130, 650], "count": 1, "hold": 6, "until_empty": true},
{"slot": [545, 650], "count": 1},
{"slot": [545, 650], "count": 1, "tap_only": true},
{"slot": [845, 650], "count": 4, "until_empty": true, "points": [[230, 345], [320, 342], [410, 338], [500, 335]]}
```

This holds the first card until it is empty, drops the hero in slot 545, taps it again for its ability, then throws four spells along a line.

After the list, the Home Village `sweep` taps every card in `sweep.slots` that still has troops left at `sweep.deploy`, so nothing is wasted when the army differs from the plan.

How to change it:

1. Card positions depend on army order, so with fixed `troops` re-check every `slot` after changing the army.
2. The bot zooms out (`zoom_out`) before deploying, so drop points must be read from a **zoomed-out** battle screen, not a normal scout screenshot. Start an attack, zoom out like the bot does, then save a screenshot with `python calibrate.py --capture shot.png` and read pixel positions in any image viewer.
3. Edit your `config.local.json` (or `config.json`), then check it without input: `python bot.py`. Deploy points
   can also be moved in the [web dashboard](dashboard.md), over a real attack screen.
4. Test one attack: `python bot.py --mode home --cycles 1 --live` (or `--mode builder`).

<details>
<summary><b>Configuration reference</b></summary>

| Key | Meaning |
| --- | --- |
| `adb`, `serial`, `resolution` | ADB executable, device serial, and screen size. |
| `ldconsole`, `ld_index` | LDPlayer console and instance index, used to start a stopped emulator. |
| `touch_device` | Raw touch input device, e.g. `/dev/input/event4`. |
| `language` | Terminal, CLI help and calibration instruction language: `id` (default) or `en`. The dashboard's ID/EN switch saves it in `config.local.json`; supervised logs switch immediately without restarting the task. Each dashboard renders recent history in its own selected language. Game recognition is unchanged. |
| `tesseract` | Tesseract executable; reads upgrade-action rows, and loot only when `digits` is not set. Village sync uses game JSON. |
| `minimum` | Loot filter for Home Village opponents (`0` disables a resource). |
| `remote` | Web dashboard: the online relay's address and token (`relay_url`, `relay_key`), local `port` (8765), and the `dashboard` address printed in the terminal. |
| `screens.<name>.anchors` | Two or more UI crops that must all match to identify a screen; optional `margin` for small UI shifts. |
| `screens.<name>.alt_anchors` | Alternative anchor sets for screens with two layouts. |
| `screens.<name>.overrides` | Screens this one wins over when both match (e.g. stage 2 over battle). |
| `screens.<name>.actions` | Fixed tap points (attack, find, next, return, …). Menus may add an optional `close`. |
| `screens.<name>.targets` | Tap points found by image search (boat, Elixir Cart) with `pan`, `scales`, `avoid`, `alt_files`. |
| `screens.<deploy>.troops` | Ordered cards: `slot`, `count`, `hold`, `until_empty`, `points`, `tap_only`, `wait`. |
| `screens.<deploy>.auto` | Build the deploy order from the card bar instead of `troops`. Home Village: `bar` region, troop `hold` seconds, and `spells` points listed as rounds of front, left, right. Builder Base: `bar` region and spread `points`, one per troop card. |
| `capacity.<village>.<resource>` | Storage bar crop, tooltip tap point, `max` crop, and `label` ("Maks:") template. Optional `maximum` forces a fixed value. Needs `digits`. |
| `storage.<village>.<resource>` | Fallback fill check by bar colour (`row`, `left`, `right`, `hue`, `min_saturation`), used when a village has no `capacity`. |
| `cart_text` | Crop of the Elixir Cart "current / max" text, read with `digits`. |
| `cart_bar` | Elixir Cart fill bar read by colour, used when there is no `cart_text`. Either key makes `cart` a goal. |
| `walls.<village>` | `--walls` calibration: builder list tap point, "Tembok" row template and its price band (`row.price` = `[x, dy, w, h]`, `dy` from the row title's centre; `row.scales` `[1.0, 0.95]` also finds the row of the selected building, which the game draws smaller), scroll swipe, gem count areas, upgrade button template and price offsets, the confirm dialog checks (with `confirm.sale` for the sale dialog's label and green price), the HUD gold areas (`hud_gold`) and the Home Village `gold_reserve`, and `bulk` for `--bulk-walls`: the "Tingkatkan Lebih Banyak" label (`more`), the double-hammer upgrade button (`button`), and the dialog's resource words (`words`, read in `text`). |
| `dpi` | Optional: the DPI the calibration was made at (default 240), used when the LDPlayer screen is fitted. |
| `digits` | Folder of digit templates (`0.png` … `9.png`, plus variants like `9-ui2.png`). |
| `zoom_out` | Pinch settings used before switching villages or attacking. |
| `speed_button` | Template of the battle "1x" button, tapped once to switch to 4x. |

</details>
