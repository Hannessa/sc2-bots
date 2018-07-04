
import random, math, time

import sc2
from sc2 import Race, Difficulty
from sc2.constants import *
from sc2.player import Bot, Computer


# This fix is required for the queued order system to work correctly (self.execute_order_queue())
import itertools
FLOAT_DIGITS = 8
EPSILON = 10**(-FLOAT_DIGITS)
def eq(self, other):
    #assert isinstance(other, tuple)
    if not isinstance(other, tuple):
        return False
    return all(abs(a - b) < EPSILON for a, b in itertools.zip_longest(self, other, fillvalue=0))
sc2.position.Pointlike.__eq__ = eq


class BaseBot(sc2.BotAI):
    under_construction = {}
    timer = None
    order_queue = []

    remembered_enemy_units = []
    remembered_enemy_units_by_tag = {}
    remembered_friendly_units_by_tag = {}

    def reset_timer(self):
        self.timer = time.time()


    def get_timer(self):
        if self.timer:
            return "%s" % (time.time() - self.timer)
        else:
            return "Timer not started"

    # Cancel buildings in construction that are under attack
    async def cancel_buildings(self):
        # Loop through all buildings that are under construction
        for building in self.units.structure.not_ready:
            # If we're not tracking this building, make sure to track it
            if building.tag not in self.under_construction:
                self.under_construction[building.tag] = {}
                self.under_construction[building.tag]["last_health"] = building.health

            # If health is low, and has dropped since last frame, cancel it!
            if building.health < 100 and building.health < self.under_construction[building.tag]["last_health"]:
                await self.do(building(CANCEL))
            else:
                self.under_construction[building.tag]["last_health"] = building.health

    # Returns current game time in seconds. Source: https://github.com/Dentosal/python-sc2/issues/29#issuecomment-365874073
    def get_game_time(self):
        return self.state.game_loop*0.725*(1/16)

    # Find enemy natural expansion location
    async def find_enemy_natural(self):
        closest = None
        distance = math.inf
        for el in self.expansion_locations:
            def is_near_to_expansion(t):
                return t.position.distance_to(el) < self.EXPANSION_GAP_THRESHOLD

            if is_near_to_expansion(sc2.position.Point2(self.enemy_start_locations[0])):
                continue

            #if any(map(is_near_to_expansion, )):
                # already taken
            #    continue

            d = await self._client.query_pathing(self.enemy_start_locations[0], el)
            if d is None:
                continue

            if d < distance:
                distance = d
                closest = el

        return closest


    # Custom select_worker() to also check if path is blocked
    async def select_worker(self, pos, force=False):
        if not self.workers.exists:
            return None

        # Find worker closest to pos, but make sure to only choose a worker that is gathering (to not cancel orders)
        worker = None
        for unit in self.workers.prefer_close_to(pos):
            if unit.is_idle or self.has_order([HARVEST_GATHER, HARVEST_RETURN, MOVE], unit):
                worker = unit
                break

        if worker is None:
            return None

        # Check if path is blocked
        distance = await self._client.query_pathing(worker.position, pos)
        if distance is None:
            # Path is blocked, so return random worker
            return self.workers.random
        else:
            # Path not blocked
            return worker
    
    # Custom overridden build() to use select_worker() instead, and also try a random alternative if failing
    async def build(self, building, near, max_distance=20, unit=None, random_alternative=False, placement_step=2):
        """Build a building."""

        if isinstance(near, sc2.unit.Unit):
            near = near.position.to2
        elif near is not None:
            near = near.to2

        is_valid_location = False
        p = None

        p = await self.find_placement(building, near.rounded, max_distance, random_alternative, placement_step)
        if p is None:
            p = await self.find_placement(building, near.rounded, max_distance, True, placement_step)
            if p is None:
                return sc2.data.ActionResult.CantFindPlacementLocation

        unit = unit or await self.select_worker(p)

        if unit is None:
            return sc2.data.ActionResult.Error

        return await self.do(unit.build(building, p))

    # Give an order to unit(s)
    async def order(self, units, order, target=None, silent=True):
        if type(units) != list:
            unit = units
            await self.do(unit(order, target=target))
        else:
            for unit in units:
                await self.do(unit(order, target=target))

    async def do(self, action):
        #print("Custom do")
        #assert self.can_afford(action)
        #if not self.can_afford(action):
        #    return

        self.order_queue.append(action) #await self._client.actions(action, game_data=self._game_data)

        #cost = self._game_data.calculate_ability_cost(action.ability)
        #self.minerals -= cost.minerals
        #self.vespene -= cost.vespene
        #print("Custom do done")

    # Warp-in a unit nearby location from warpgate
    async def warp_in(self, unit, location, warpgate):
        if isinstance(location, sc2.unit.Unit):
            location = location.position.to2
        elif location is not None:
            location = location.to2

        x = random.randrange(-8,8)
        y = random.randrange(-8,8)

        placement = sc2.position.Point2((location.x+x,location.y+y))

        action = warpgate.warp_in(unit, placement)
        error = await self._client.actions(action, game_data=self._game_data)

        if not error:
            cost = self._game_data.calculate_ability_cost(action.ability)
            self.minerals -= cost.minerals
            self.vespene -= cost.vespene
            return None
        else:
            return error

    # Execute all orders in self.order_queue and reset it
    async def execute_order_queue(self):
        await self._client.actions(self.order_queue, game_data=self._game_data)
        self.order_queue = [] # Reset order queue
        

    async def train(self, unit_type, building):
        if self.can_afford(unit_type): #and await self.has_ability(unit_type, building):
            await self.do(building.train(unit_type))

    async def can_train(self, unit_type, building):
        return await self.has_ability(unit_type, building)

    async def upgrade(self, upgrade_type, building):
        if self.can_afford(upgrade_type) and await self.has_ability(upgrade_type, building):
            await self.do(building(upgrade_type))

    async def can_upgrade(self, upgrade_type, building):
        return await self.has_ability(upgrade_type, building)

    # Check if a unit has an ability available (also checks upgrade costs??)
    async def has_ability(self, ability, unit):
        abilities = await self.get_available_abilities(unit)
        if ability in abilities:
            return True
        else:
            return False

    # Check if a unit has a specific order. Supports multiple units/targets. Returns unit count.
    def has_order(self, orders, units):
        if type(orders) != list:
            orders = [orders]

        count = 0

        if type(units) == sc2.unit.Unit:
            unit = units
            if len(unit.orders) >= 1 and unit.orders[0].ability.id in orders:
                count += 1
        else:
            for unit in units:
                if len(unit.orders) >= 1 and unit.orders[0].ability.id in orders:
                  count += 1

        return count

    # Check if a unit has a specific target. Supports multiple units/targets. Returns unit count.
    def has_target(self, targets, units):
        if type(targets) != list:
            targets = [targets]

        count = 0

        if type(units) == sc2.unit.Unit:
            unit = units
            if len(unit.orders) == 1 and unit.orders[0].target in targets:
                count += 1
        else:
            for unit in units:
                if len(unit.orders) == 1 and unit.orders[0].target in targets:
                  count += 1

        return count

    # Custom distribute_workers() to not touch idle workers (otherwise cannon builders will be affected)
    async def distribute_workers(self):
        """Distributes workers across all the bases taken."""

        expansion_locations = self.expansion_locations
        owned_expansions = self.owned_expansions
        worker_pool = []

        for location, townhall in owned_expansions.items():
            workers = self.workers.closer_than(20, location)
            actual = townhall.assigned_harvesters
            ideal = townhall.ideal_harvesters
            excess = actual-ideal
            if actual > ideal:
                worker_pool.extend(workers.random_group_of(min(excess, len(workers))))
                continue
        for g in self.geysers:
            workers = self.workers.closer_than(5, g)
            actual = g.assigned_harvesters
            ideal = g.ideal_harvesters
            excess = actual - ideal
            if actual > ideal:
                worker_pool.extend(workers.random_group_of(min(excess, len(workers))))
                continue

        for g in self.geysers:
            actual = g.assigned_harvesters
            ideal = g.ideal_harvesters
            deficit = ideal - actual

            for x in range(0, deficit):
                if worker_pool:
                    w = worker_pool.pop()
                    if len(w.orders) == 1 and w.orders[0].ability.id in [AbilityId.HARVEST_RETURN]:
                        await self.do(w.move(g))
                        await self.do(w.return_resource(queue=True))
                    elif len(w.orders) == 1 and w.orders[0].ability.id in [AbilityId.HARVEST_GATHER]:
                        await self.do(w.gather(g))

        for location, townhall in owned_expansions.items():
            actual = townhall.assigned_harvesters
            ideal = townhall.ideal_harvesters

            deficit = ideal - actual
            for x in range(0, deficit):
                if worker_pool:
                    w = worker_pool.pop()
                    mf = self.state.mineral_field.closest_to(townhall)
                    if len(w.orders) == 1 and w.orders[0].ability.id in [AbilityId.HARVEST_RETURN]:
                        await self.do(w.move(townhall))
                        await self.do(w.return_resource(queue=True))
                        await self.do(w.gather(mf, queue=True))
                    elif len(w.orders) == 1 and w.orders[0].ability.id in [AbilityId.HARVEST_GATHER]:
                        await self.do(w.gather(mf))

    async def worker_split(self):
        for worker in self.workers:
            closest_mineral_patch = self.state.mineral_field.closest_to(worker)
            await self.do(worker.gather(closest_mineral_patch))
            #await self.order(worker, HARVEST_GATHER, closest_mineral_patch)


    # Remember enemy units' last position, even though they're not seen anymore
    def remember_enemy_units(self):
        # Every 60 seconds, clear all remembered units (to clear out killed units)
        #if round(self.get_game_time() % 60) == 0:
        #    self.remembered_enemy_units_by_tag = {}

        # Look through all currently seen units and add them to list of remembered units (override existing)
        for unit in self.known_enemy_units:
            unit.is_known_this_step = True
            self.remembered_enemy_units_by_tag[unit.tag] = unit

        # Convert to an sc2 Units object and place it in self.remembered_enemy_units
        self.remembered_enemy_units = sc2.units.Units([], self._game_data)
        for tag, unit in list(self.remembered_enemy_units_by_tag.items()):
            # Make unit.is_seen = unit.is_visible 
            if unit.is_known_this_step:
                unit.is_seen = unit.is_visible # There are known structures that are not visible
                unit.is_known_this_step = False # Set to false for next step
            else:
                unit.is_seen = False

            # Units that are not visible while we have friendly units nearby likely don't exist anymore, so delete them
            if not unit.is_seen and self.units.closer_than(7, unit).exists:
                del self.remembered_enemy_units_by_tag[tag]
                continue

            self.remembered_enemy_units.append(unit)

    # Remember friendly units' previous state, so we can see if they're taking damage
    def remember_friendly_units(self):
        for unit in self.units:
            unit.is_taking_damage = False

            # If we already remember this friendly unit
            if unit.tag in self.remembered_friendly_units_by_tag:
                health_old = self.remembered_friendly_units_by_tag[unit.tag].health
                shield_old = self.remembered_friendly_units_by_tag[unit.tag].shield

                # Compare its health/shield since last step, to find out if it has taken any damage
                if unit.health < health_old or unit.shield < shield_old:
                    unit.is_taking_damage = True
                
            self.remembered_friendly_units_by_tag[unit.tag] = unit

        