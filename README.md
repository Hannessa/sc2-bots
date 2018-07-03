# StarCraft 2 Bots
Various StarCraft 2 bots coded in Python.

## Requirements
* [Python 3.6+](https://www.python.org/downloads/)
* [python-sc2](https://github.com/Dentosal/python-sc2) (```pip install sc2```)

## How to run
1. Install the requirements and download the repository.
2. Open "run.bat" in the bot's folder (Windows only), or type ```python run.py``` in the console (Mac/Linux).

## Bots
* **CannonLover** (*P*) - Cannon rushes natural expansion of opponent, then switches to macro zealot/stalker/sentry/colossus with some army movement and micro.

## Features
### CannonLover
* Cannon rush logic that starts at natural expansion and progresses towards enemy main.
* On 4-player maps or if too long time passes, it switches to macro strategy.
* Macro strategy expands aggressively, upgrades and builds zealots/stalkers/sentries/immortals/colossus/observers.
* Army units compare nearby friendly vs enemy army size before taking an engagement (measured by health + shield).
* Remembers enemy units no longer in sight to know when it can engage, and to avoid dying on ramps.
* Evasive blink stalker micro when stalker is taking damage.
* Counts number of enemy roaches/marauders/stalkers to decide if it should build immortals or colossus.
* Custom BaseBot class with various helper functions.
  * Overridden self.do() that increases performance by queuing up commands which are then executed by self.execute_order_queue() at the end of the on_step() function.
