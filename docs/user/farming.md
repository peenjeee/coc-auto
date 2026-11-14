# Farming

## Recommended armies

The bundled deploy plans were tuned for these armies. Other compositions work too, but deploy points and card order may need recalibration (see [Changing the army and deploy plan](#changing-the-army-and-deploy-plan)). The Home Village has two ways to attack:

| Village | Troops | Spells | Run with | How it attacks |
| --- | --- | --- | --- | --- |
| **Home Village** stack (default) | Full **Dragons** and/or **Electro Dragons** | Full **Rage** or full **Freeze** | nothing, or `--stack home` | Troops held at one edge point (`D`) until the card is empty, then the siege machine and heroes with their abilities, then the spells along the troops' path. |
| **Home Village** ring | **Valkyries / Super Valkyries**, **Barbarians / Super Barbarians**, **Super Minions** or full **Miners** | **Earthquake** (`--quake`) or **Skeleton** | `--ring home`, plus `--quake` for earthquakes | Heroes with their abilities first, the siege machine, then the spells (earthquakes four per wall pin, skeletons over the middle of the base), then the troops streaming out of eight fingers round the map edge. |
| **Builder Base** ring (default) | Full **Baby Dragons** or full **Raged Barbarians** + Battle Machine/Copter | — | nothing, or `--ring builder` | The hero and its ability, then every camp in one go, each on the next spread point round the base (a Raged Barbarian camp gets one tap per unit, `4x` = four). |
| **Builder Base** stack | Full **Witches** + Battle Machine/Copter | — | `--stack builder` | The hero and its ability, then every camp in one go on the deploy point `D`; in stage 2 on that stage's first spread point instead. |

`--ring` and `--stack` take `home`, `builder` or `both` (alone, `both`), so one `--until-full` run can attack each village its own way, e.g. `--ring home --stack builder` for Valkyries and Witches. By default every Home Village attack gives up at 50% damage (`--surrender-at 0` fights to the end) and every Builder Base attack gives up stage 1 at 2 stars, so stage 2 is never played (`--surrender-stars 0` fights on).

- **Rage and Freeze go with the default plan.** The ring plan drops the spells before the troops, so earthquakes open the walls and skeletons pull the defences before the troops walk in; Rage and Freeze need troops already on the field.
- **Valkyries + earthquakes are the fastest farm** in live tests: 37 Valkyries out in about 2 seconds and 50% reached about 17 seconds after the deploy began. 300 Barbarians + skeletons took 22–39 seconds, depending on the base. Super Valkyries, Super Barbarians, Super Minions and Miners use the same ring plan but have not been tested live yet.
- **Miners dig under the walls**, so they need no earthquakes: pair them with skeletons, or no spells.
- **Builder Base:** the Witch stack, the Baby Dragon ring and the Raged Barbarian ring were tested live, stage 2
  included. Camps that live through stage 1 spread again in stage 2. The hero's ability fires once more whenever the
  three steps of its meter are charged, as it is weaker before. `--trophy-floor 2000` fights every battle to the end
  while trophies are under 2000 and goes back to the 2-star surrender above it.
- The loot minimum is `0` by default, the fastest farm: every opponent is attacked as it comes, without reading its loot.
- Heroes and a siege machine are optional in both plans; the default plan's sweep step deploys any card left in the bar.
  Troops and spells are told apart by how the game groups the card bar (troops, siege machines, heroes, spells, with a
  wider gap between groups), not by card colour, so any troop type works, with or without heroes and a siege machine.

## Quick start

The repository ships with the calibration that was used to build and test the bot (`config.json` + `calibration/`).
If your setup matches (see [First-time setup](installation.md)), you can start right away:

```bash
python bot.py --until-full --live
```

The fastest farm, with a Valkyrie + earthquake army (see [Recommended armies](farming.md)): heroes and
earthquakes first, Valkyries round the base, and give up at 50% damage:

```bash
python bot.py --until-full --ring --quake --surrender-at 50 --live
```

With skeletons instead of earthquakes, leave out `--quake`. On the web dashboard, the **army template** list fills in
these options for each recommended army (Valkyries + earthquakes, Barbarians + skeletons, dragons, Miners +
skeletons, Super Minions), and the **Builder Base army template** list does the same there (Baby Dragons spread,
Witches on one point, Raged Barbarians spread); they can still be changed by hand afterwards.

| Command | Purpose |
| --- | --- |
| `python bot.py` | Dry run: detect the current screen and, on a scout screen, read loot. No input. |
| `python bot.py --mode builder --cycles 1 --live` | One Builder Base attack. |
| `python bot.py --mode home --cycles 1 --live` | One Home Village attack. |
| `python bot.py --mode both --cycles 3 --live` | Alternate villages, three rounds. |
| `python bot.py --until-full --live` | Farm until both villages are full, with self-recovery. |

### Choosing what to fill

`--builder` and `--home` pick the village and the resources to fill. Each takes a comma-separated list or `all`,
and either one alone farms only that village. Without both, `--until-full` fills Builder Base gold, elixir and the
Elixir Cart, then Home Village gold and elixir. Add `--dark` to fill Home Village dark elixir as well:

```bash
python bot.py --until-full --dark --live
```

| Village | Resources | `all` means |
| --- | --- | --- |
| `--builder` | `gold`, `elixir`, `cart` (Elixir Cart) | `gold,elixir,cart` |
| `--home` | `gold`, `elixir`, `dark` (dark elixir) | `gold,elixir,dark` |

```bash
python bot.py --builder gold --live
```
```bash
python bot.py --builder elixir,cart --live
```
```bash
python bot.py --home gold,dark --live
```
```bash
python bot.py --builder all --home all --first home --live
```

The first command fills Builder Base gold only, the second Builder Base elixir and the Elixir Cart, and the third
Home Village gold and dark elixir. The last fills everything in both villages, Home Village first. Use
`--home gold,elixir` instead of `--home all` to leave dark elixir out. A resource must be calibrated before it can
be chosen (dark elixir needs a `capacity.home.dark` entry); the bot refuses to start otherwise. If the Elixir Cart
cannot be found on 5 Builder Base visits in a row, its fill cannot be read, so `cart` counts as done for the rest of
the run instead of keeping the bot attacking forever.

## Several accounts

The bot can work through several Supercell ID accounts in turn inside the one LDPlayer instance: farming, walls,
attacks and building upgrades alike. It opens the game's own switch-account list (**Settings**, the button beside
the Supercell ID **Open** button), loads the account, runs the task there, and moves on to the next account when
that account's task ends.

**On the dashboard**, the **Accounts** page keeps this PC's accounts, one card each:

- **Add an account** with its Supercell ID name and its village's player tag (see below).
- **Takes turns** switches an account in or out of the turns; **↑ / ↓** set the order.
- **Load account** loads it in the game now (the bot must be stopped), checked the same way a run checks it. The
  Upgrade page then shows that village and its own upgrade picks.
- A card shows whether that village is the one **loaded**, which account a running bot is **playing now**, and how
  many upgrade targets were picked for it.

Then switch on **Run limits → Accounts take turns** in the run setup. The accounts that take turns are listed under
it, and the command preview shows them as `--accounts`.

**On the command line**, each account is written as `Name#TAG`, separated by commas:

```bash
python bot.py --until-full --accounts "MainVillage#PPPPPPPP,SecondOne#QQQQQQQQ" --live
```

- **Name** is the Supercell ID name on the account's card in the switch list (its bold first line), not the village
  name under it. Spaces and capitals do not matter; every letter does.
- **TAG** is that village's player tag: the `#` code under the village name in the Supercell ID bar of **Settings**
  and on **My Profile**.

How the bot stays on the right account:

- A card is tapped only when its name reads exactly as an account you listed and no other card reads the same, and
  only if the list still looks as it did when the names were read. An account you did not list is never chosen.
  When the name is not found, the list is closed without choosing and the run stops.
- Which village is loaded is told by its tag in the game's own data export (the export the Upgrade page syncs),
  never by reading the screen: before a switch, so an account already loaded is left alone, and after it. If another
  village comes up, the run stops there. After a recovery the account is confirmed again before the task goes on.

When an account's turn ends:

- The task finished (storages full, every wall maxed, the attacks played, every upgrade target reached): the account
  is done.
- A limit ended it (`--hours`, `--minutes`, `--max-upgrades`, which count for each turn): the next account plays.
  `--rounds N` (**account rounds** on the dashboard) gives such an account up to N turns in all, each with fresh
  limits; `0` keeps the accounts taking turns until every task is finished or the bot is stopped. The default, 1,
  plays each account once.
- Its building upgrades are running and nothing else can be done for 15 minutes or more: the next account plays
  meanwhile, and this one comes back once its builders or laboratory are free, its time limit still running. When
  every account is waiting, CoC is closed until the first of them is due.

**Building upgrades per account.** With accounts on the Accounts page, each village keeps its own upgrade picks:
load the account (Accounts page), pick its upgrades on the Upgrade page (it names the account at the top) and save.
Picks made before there were accounts belong to the village that was synced when the accounts were added. A
`--buildings` run then upgrades, on each account, that account's own picks; an account without picks is passed
over. Without accounts taking turns, `--buildings` upgrades the village shown on the Upgrade page, once the game
shows that this village is the one loaded.

Good to know:

- It needs `--live`, the 1280x720 screen, Tesseract (bundled in the Windows download) and LDPlayer clipboard
  synchronization, like the Upgrade page's sync. Every check copies the export, so it replaces what is on the
  Windows clipboard and takes about 10 seconds; a switch takes about 40.
- The accounts must already be signed in on this LDPlayer. When an account is further down the switch list than the
  screen shows, the bot scrolls the list with a finger on the empty strip left of the cards, never on a card, and
  reads the cards again, until the account or the **Log in to Supercell ID** card at the end of the list shows. If
  the list does not move, nothing is chosen and the run stops. Switching was tested live with three accounts in the
  list, which fit on the screen; scrolling a longer list is covered by the tests only.
- A card's name is read with English OCR, so a Supercell ID name needs Latin letters, digits, spaces, `.`, `_` or
  `-`. A name that cannot be read is never chosen: the list is closed and the run stops with the names it read.
- After the last turn the bot stops with the last account loaded.
- One calibration, loot minimum, army and deploy plan serve every account.
- Nothing in the switch is read in the game's language: the list is found by the Supercell ID logo and the switch
  icon, and the cards by their shape. It was tested live with the game in English and in Indonesian.
- CoC sometimes closes itself while it loads another account. The bot opens it again and checks which village came
  up; if the game came back on the account it had, the switch is made again.
