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
