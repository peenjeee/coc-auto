# Command-line reference

### CLI options — `bot.py`

| Option | Default | Description |
| --- | --- | --- |
| `--config PATH` | `config.json` next to `bot.py` | Calibration file to use; `config.local.json` beside it is read on top. |
| `--mode {home,builder,both}` | `both` | Villages used by `--cycles` runs. |
| `--cycles N` | `1` | Rounds to attack (1–100). |
| `--searches N` | `0` | Opponent searches per Home attack (0–1,000,000); `0` means unlimited. At the limit the base on screen is attacked whatever its loot, so the run never stops for want of a good base. |
| `--until-full` | off | Attack until gold and elixir are full in both villages. |
| `--dark` | off | With `--until-full` (and no `--builder`/`--home`): fill Home Village dark elixir too. |
| `--builder RESOURCES` | – | Fill these Builder Base resources (`gold`, `elixir`, `cart`, or `all`). Implies `--until-full`. |
| `--home RESOURCES` | – | Fill these Home Village resources (`gold`, `elixir`, `dark`, or `all`). Implies `--until-full`. |
| `--first {builder,home}` | `builder` | Which village to work on first when both are chosen. |
| `--walls {home,builder,both}` | – | Spend the stored gold and elixir on walls, then farm both full and repeat until every wall is maxed. Never uses gems. Needs `--live`; not with `--until-full`/`--builder`/`--home`. |
| `--buildings` | off | Upgrade the buildings, Laboratory research, pets, hero equipment, Builder Base buildings and Star Laboratory research picked in the dashboard, first pick first, each up to its target level, leaving `keep_builders` free; attacks in a pick's village when it is short of resources (with the attack options given), waits for builders and slots (with CoC closed when the wait is 15 minutes or more) and stops once every target is reached. Uses gems only for a Goblin switched on in `goblin`. Needs `--live` and a saved pick; not with `--walls`/`--until-full`/`--builder`/`--home`. |
| `--hours N`, `--minutes N` | `0` | Stop `--until-full`/`--builder`/`--home`/`--walls`/`--buildings` after this long; the two add up (`--hours 1 --minutes 30` is 90 minutes; hours 0–1000, minutes 0–60,000). The attack or upgrade in progress is finished first, then the run ends in the village with exit code 0. `0` means no limit. With `--until-full`/`--builder`/`--home` it needs `--live`. |
| `--max-upgrades N` | `0` | `--walls` only: stop in the village after N walls are upgraded (0–100,000). `0` means no limit. |
| `--bulk-walls` | off | `--walls` only: upgrade many walls of the picked level with one Oke through "Tingkatkan Lebih Banyak", as many as the resource pays for, instead of one at a time. Both villages. Needs the `wall_bulk_confirm` screen calibrated (bundled). |
| `--wall-every N` | `0` | `--walls` only: go back to the walls after every N attacks instead of waiting for full storage (0–1000). `0` waits until full. |
| `--wall-resource gold\|elixir\|both` | `both` | `--walls` only: pay for walls with gold only, elixir only, or both (gold first). Farming then fills only that resource. Both villages. |
| `--surrender home\|builder\|both` | off | Give up a battle in these villages that is still running `--surrender-after` seconds into it, counted from the start of the deploy and checked during the deploy too (like `--surrender-at`), so the rest of the army stays home: taps **Menyerah**/**Akhiri Serangan**, then **Oke**. In the Builder Base it counts per stage, so giving up stage 1 skips stage 2. |
| `--surrender-after N` | `60` | Battle seconds, 1–120. On its own (without `--surrender`) it turns surrendering on in both villages. The battle clock runs at normal speed until its last minute (when the 4x button appears), so past 120 there is little left to save. |
| `--surrender-at P` | `50` | Home Village: surrender once the battle's **Total Kerusakan** reads P percent (0–99) twice in a row, checked during the deploy too, so the rest of the army stays home. `0` never surrenders. Works with `--surrender-after`; whichever comes first. A setup without the damage calibration farms on without it (a warning says so) unless the option is given. |
| `--surrender-stars N` | `2` | Builder Base: give up stage 1 once its damage box shows N gold stars (0–3) twice in a row, checked during the deploy too; a stage 2 reached anyway is ended from its own screen without deploying. `0` fights on, stage 2 included. |
| `--trophy-floor N` | `0` (off) | Builder Base: before each attack read the trophy count on the village screen. Under N the battle is fought to the end with no surrender, stage 2 included, to win trophies back; at or over N it surrenders as set again. An unreadable count keeps the setting. 0–10,000; needs the `digits` and `builder.trophies` calibration (bundled). |
| `--stack [home\|builder\|both]` | home | Troops on the one deploy point `D`. Home Village: the default dragon plan. Builder Base: the hero and its ability, then every camp on `D` (full Witches). |
| `--ring [home\|builder\|both]` | builder | Builder Base: every camp in one go, each on the next spread point (the default). Home Village fast attack: every hero dropped at `D` then each ability, the siege machine, the spells (skeletons over the middle of the base, eight at once, one per finger), then each troop card streaming out of eight fingers held at once on ring pins round the map edge, moving one pin on each second until the card greys out. Without a touch device the troops are tapped instead, exactly the `xN` count read from the card. The Home Village ring needs its ring pins (`home_scout.auto.ring`, set in the deploy editor). A village cannot be given to both `--ring` and `--stack`. |
| `--quake` | off | Home Village: the spells go four on each quake pin in turn (four earthquakes break the walls there), one per finger with `--ring`, at most the `xN` count read from the card. Needs the quake pins (`home_scout.auto.quake`, set in the deploy editor). |
| `--max-recoveries N` | `20` | How many recoveries (restarting LDPlayer or CoC, settling a stalled screen) a `--live` run (`--until-full`, `--walls`, `--buildings` or `--cycles`) may make without progress (0–1000). The count starts again after every finished attack, upgraded wall or upgraded building. |
| `--accounts NAME#TAG,...` | – | Run the task on each Supercell ID account listed, in that order, switching inside the one LDPlayer: the account's name on its card in the game's switch list, `#`, then its village's player tag. A card is tapped only when its name matches exactly, the loaded village is confirmed by its tag from the game's own data export, and an account that is not listed is never chosen. `--hours`, `--minutes` and `--max-upgrades` count for each account's turn. With `--buildings` each account upgrades its own picks, and an account whose upgrades keep it waiting 15 minutes or more lets the next one play meanwhile. Needs `--live`. See [Several accounts](farming.md#several-accounts). |
| `--rounds N` | `1` | `--accounts` only: an account whose turn a limit ended (`--hours`, `--minutes`, `--max-upgrades`) gets up to N turns in all, each with fresh limits (0–1000). `0` keeps the accounts taking turns until every task is finished or the bot is stopped. |
| `--live` | off | Allow real taps. Without it the bot only observes. |
| `--agent` | off | Run no task: show the device's API key and wait for **Start** from the web dashboard. |
| `--key` | off | Only show this PC's API key and the dashboard addresses, then exit. Nothing is started. |
| `--no-remote` | off | Run the task without the dashboard link and exit when it ends. |
| `--new-key` | off | Make a new dashboard API key for this PC; the old one stops working. |

Every command runs under the [web dashboard](dashboard.md) link: when its task ends (or is stopped), the bot stays on
standby for the dashboard. Leave with **Ctrl+C** at any time. However the bot stops (Ctrl+C, the dashboard's stop, or
an error), it lifts every virtual finger it may have held, so a hold or zoom cut short does not leave the game reading
your own drags as a pinch. A run also lifts them before its first tap, for a bot that was killed without that chance.
