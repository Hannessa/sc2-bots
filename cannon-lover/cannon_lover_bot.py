# Inspired by: https://github.com/Dentosal/python-sc2/blob/master/examples/cannon_rush.py
import random, math, asyncio

import sc2
from sc2 import Race, Difficulty
from sc2.constants import *
from sc2.player import Bot, Computer

from base_bot import BaseBot

# TODO: Better micro for first cannon builder
# TODO: Bug, workers hunt enemies too far out
# TODO: Better scouting when no enemy units are known (focus on enemy_start_locations)


class CannonLoverBot(BaseBot):
    cannon_start_distance = 35 # Distance of first pylon/cannon from enemy base (towards natural expansion)
    cannon_advancement_rate = 6 # Distance units to cover per pylon towards enemy base
    cannons_to_pylons_ratio = 2 # How many cannons to build per pylon at cannon_location
    sentry_ratio = 0.15 # Sentry ratio
    stalker_ratio = 0.6 #0.7 # Stalker/Zealot ratio (1 = only stalkers)
    units_to_ignore = [DRONE, SCV, PROBE, EGG, LARVA, OVERLORD, OVERSEER, OBSERVER, BROODLING, INTERCEPTOR, MEDIVAC, CREEPTUMOR, CREEPTUMORBURROWED, CREEPTUMORQUEEN, CREEPTUMORMISSILE]
    army_size_minimum = 20 # Minimum number of army units before attacking.
    enemy_threat_distance = 50 # Enemy min distance from base before going into panic mode.
    max_worker_count = 70 # Max number of workers to build
    max_cannon_count = 15 # Max number of cannons
    gateways_per_nexus = 2 # Number of gateways per nexus

    strategy = "early_game" # Set to "late_game" to skip cannon rush
    cannon_location = None
    start_location = None
    enemy_start_location = None
    #attack_target = None
    has_sent_workers = False
    iteration = 0


    # This is run each game step
    async def on_step(self, iteration):
        # Store iteration
        self.iteration = iteration

        # On first game step, run start logic
        if iteration == 0:
            await self.on_game_start()

        # Remember seen enemy units and previous state of friendly units
        self.remember_enemy_units()
        self.remember_friendly_units()

        # Basic logic
        await self.find_cannon_location() # Find next build location for cannons (and pylons)
        await self.manage_bases() # Manage bases (train workers etc, but also base defense)
        await self.cancel_buildings() # Make sure to cancel buildings under construction that are under attack

        # Change strategy to late game if above 3 minutes or if banking minerals
        if self.strategy == "early_game" and (self.get_game_time() / 60 > 3 or self.minerals > 800):
            await self.chat_send("Changing to late-game strategy")
            self.strategy = "late_game"

        # Run strategy 
        if self.strategy == "early_game":
            await self.early_game_strategy()
        elif self.strategy == "late_game":
            await self.late_game_strategy()
        elif self.strategy == "panic":
            await self.panic_strategy()
        
        # Worker and stalker micro and movement
        await self.move_workers()
        await self.move_army()

        # Execute queued commands
        await self.execute_order_queue()

    # Only run once at game start
    async def on_game_start(self):
        # Say hello!
        await self.chat_send("(probe)(pylon)(cannon)(cannon)(gg)")

        # Save base locations for later
        self.start_location = self.units(NEXUS).first.position
        self.enemy_natural = await self.find_enemy_natural()

        # Perform worker split
        await self.worker_split()

        # If 4-player map, skip cannon rush
        if len(self.enemy_start_locations) > 1:
            await self.chat_send("Not a 2-player map, skipping cannon rush :(")
            self.strategy = "late_game"
        else:
            # 2-player map, so we know enemy start location
            self.enemy_start_location = self.enemy_start_locations[0]

        #self.strategy = "late_game" # Force late-game for testing


    # Find next location for cannons/pylons
    async def find_cannon_location(self):
        if self.units(PHOTONCANNON).amount > self.max_cannon_count:
            # Stop making cannons after we reached self.max_cannon_count
            self.cannon_location = None
            return
        elif self.strategy == "late_game" and (not self.enemy_start_location or not self.units(PYLON).closer_than(self.cannon_start_distance+5, self.enemy_start_location).exists):
            # Also stop making cannons if we're in late-game and still have no pylon near enemy base
            self.cannon_location = None
            return
        elif self.enemy_start_location:
            # Only if enemy start location is known
            target = self.enemy_start_location
            approach_from = self.enemy_natural #self.game_info.map_center
        else:
            # We have no idea where enemy is. Skip cannons rush.
            self.cannon_location = None
            return

        # Find a good distance from enemy base (start further out and slowly close in)
        distance = self.cannon_start_distance-(self.units(PYLON).closer_than(self.cannon_start_distance+5, target).amount*self.cannon_advancement_rate)
        if distance < 0:
            distance = 0

        self.cannon_location = target.towards(approach_from, distance) #random.randrange(distance, distance+5)   #random.randrange(20, 30)


    async def manage_bases(self):
        # Do some logic for each nexus
        for nexus in self.units(NEXUS).ready:
            # Train workers until at nexus max (+4)
            if self.workers.amount < self.max_worker_count and nexus.noqueue: # and nexus.assigned_harvesters < nexus.ideal_harvesters+2 :
                if self.can_afford(PROBE) and self.supply_used < 198:
                    await self.do(nexus.train(PROBE))

            # Always chronoboost when possible
            await self.handle_chronoboost(nexus)

            # Idle workers near nexus should always be mining (we want to allow idle workers near cannons in enemy base)
            if self.workers.idle.closer_than(50, nexus).exists:
                worker = self.workers.idle.closer_than(50, nexus).first
                await self.do(worker.gather(self.state.mineral_field.closest_to(nexus)))

            # Worker defense: If enemy unit is near nexus, attack with a nearby workers
            # TODO: If up to 3 enemies, just attack with workers. If more, escape with workers from home and change mode to defense.
            nearby_enemies = self.known_enemy_units.not_structure.filter(lambda unit: not unit.is_flying).closer_than(30, nexus).prefer_close_to(nexus)
            if nearby_enemies.amount >= 1 and nearby_enemies.amount <= 10 and self.workers.exists:
                #if nearby_enemies.amount <= 4:
                # TODO: Escape if too many enemies

                # We have nearby enemies, so attack them with a worker
                workers = self.workers.prefer_close_to(nearby_enemies.first).take(nearby_enemies.amount*2, False)

                for worker in workers:
                    #if not self.has_order(ATTACK, worker):
                    await self.do(worker.attack(nearby_enemies.closer_than(30, nexus).closest_to(worker)))

                #worker = self.workers.closest_to(nearby_enemies.first)
                #if worker:
                #    await self.do(worker.attack(nearby_enemies.first))
            else:
                # No nearby enemies, so make sure to return all workers to base
                for worker in self.workers.closer_than(50, nexus):
                    if len(worker.orders) == 1 and worker.orders[0].ability.id in [ATTACK]:
                        await self.do(worker.gather(self.state.mineral_field.closest_to(nexus)))


            # Panic mode: Change cannon_location to nexus if we see many enemy units nearby
            if self.strategy == "early_game":
                # TODO: Actually count enemies in early game and detect rush
                num_nearby_enemy_structures = self.remembered_enemy_units.structure.closer_than(self.enemy_threat_distance, nexus).amount
                num_nearby_enemy_units = self.remembered_enemy_units.not_structure.closer_than(self.enemy_threat_distance, nexus).amount
                min_defensive_cannons = num_nearby_enemy_structures + max(num_nearby_enemy_units-1, 0)
                if (num_nearby_enemy_structures > 0 or num_nearby_enemy_units > 2) and self.units(PHOTONCANNON).closer_than(20, nexus).amount < min_defensive_cannons:
                    self.cannon_location = nexus.position.towards(self.get_game_center_random(), random.randrange(5, 15)) #random.randrange(20, 30)
                    self.strategy = "panic"
                    await self.chat_send("That was scary! I'm going into panic mode...")

                elif self.strategy == "panic":
                    self.strategy = "late_game"
                    await self.chat_send("Everything should be fine now. Let's macro!")


    # Chronoboost (CB) management
    async def handle_chronoboost(self, nexus):
        if await self.has_ability(EFFECT_CHRONOBOOSTENERGYCOST, nexus) and nexus.energy >= 50:
            # Always CB Warpgate research first
            if self.units(CYBERNETICSCORE).ready.exists:
                cybernetics = self.units(CYBERNETICSCORE).first
                if not cybernetics.noqueue and not cybernetics.has_buff(CHRONOBOOSTENERGYCOST):
                    await self.do(nexus(EFFECT_CHRONOBOOSTENERGYCOST, cybernetics))
                    return # Don't CB anything else this step

            # Blink is also important
            if self.units(TWILIGHTCOUNCIL).ready.exists:
                twilight = self.units(TWILIGHTCOUNCIL).first
                if not twilight.noqueue and not twilight.has_buff(CHRONOBOOSTENERGYCOST):
                    await self.do(nexus(EFFECT_CHRONOBOOSTENERGYCOST, twilight))
                    return # Don't CB anything else this step

            # Next, focus on Forge
            if self.units(FORGE).ready.exists:
                forge = self.units(FORGE).first
                if not forge.noqueue and not forge.has_buff(CHRONOBOOSTENERGYCOST):
                    await self.do(nexus(EFFECT_CHRONOBOOSTENERGYCOST, forge))
                    return # Don't CB anything else this step

            # Next, prioritize CB on gates
            for gateway in (self.units(GATEWAY).ready | self.units(WARPGATE).ready):
                if not gateway.has_buff(CHRONOBOOSTENERGYCOST):
                    await self.do(nexus(EFFECT_CHRONOBOOSTENERGYCOST, gateway))
                    return # Don't CB anything else this step

            # Otherwise CB nexus
            if not nexus.has_buff(CHRONOBOOSTENERGYCOST):
                await self.do(nexus(EFFECT_CHRONOBOOSTENERGYCOST, nexus))

    # Build cannons (and more pylons) at self.cannon_location (usually in enemy base).
    async def build_cannons(self):
        # If we have no cannon location (e.g. 4-player map), just skip building cannons
        if not self.cannon_location:
            return

        # Don't cancel orders for workers already on their way to build
        if self.has_order([PROTOSSBUILD_PHOTONCANNON, PROTOSSBUILD_PYLON], self.workers): #.closer_than(50, self.cannon_location)
            return

        num_cannons = self.units(PHOTONCANNON).ready.closer_than(15, self.cannon_location).amount + self.already_pending(PHOTONCANNON)
        num_pylons = self.units(PYLON).ready.filter(lambda unit: unit.shield > 0).closer_than(15, self.cannon_location).amount + self.already_pending(PYLON)

        # Keep the ratio between cannons as pylons
        if num_cannons < num_pylons * self.cannons_to_pylons_ratio:
            if self.can_afford(PHOTONCANNON) and self.units(FORGE).ready.exists:
                #await self.build(PHOTONCANNON, near=self.cannon_location)
                pylon = self.units(PYLON).closer_than(10, self.cannon_location).ready.prefer_close_to(self.cannon_location)
                if pylon.exists:
                    await self.build(PHOTONCANNON, near=pylon.first) #, unit=self.select_builder()
        else:
            if self.can_afford(PYLON):
                await self.build(PYLON, near=self.cannon_location) #, unit=self.select_builder()


    # Opening strategy for early game
    async def early_game_strategy(self):
        nexus = self.units(NEXUS).first # We only have one nexus in early game

        # Send a worker to enemy base early on (just once)
        if not self.has_sent_workers:
            await self.do(self.workers.random.move(self.cannon_location))
            self.has_sent_workers = True

        # Build one pylon at home
        elif not self.units(PYLON).closer_than(20, nexus).exists and not self.already_pending(PYLON):
            if self.can_afford(PYLON):
                await self.build(PYLON, near=nexus.position.towards(self.game_info.map_center, 10)) #self.get_game_center_random()

        # Build forge at home
        elif not self.units(FORGE).exists and not self.already_pending(FORGE):
            pylon = self.units(PYLON).ready
            if pylon.exists:
                if self.can_afford(FORGE):
                    await self.build(FORGE, near=pylon.closest_to(nexus))

        # Send an extra worker to front-line
        #elif self.workers.closer_than(50, self.cannon_location).amount < 2 and not self.has_order(MOVE, self.workers):
        #    self.has_sent_workers = False


        # Start building cannons in enemy base (and more pylons)
        else:
            await self.scout_cheese()

            await self.build_cannons()


    # Panic strategy for all-in defense
    async def panic_strategy(self):
        nexus = self.units(NEXUS).first # We likely only have one nexus in early game

        # Make sure we have at least one pylon
        if not self.units(PYLON).exists and not self.already_pending(PYLON):
            if self.can_afford(PYLON):
                await self.build(PYLON, near=self.get_base_build_location(nexus))

        # Make sure forge still exists...
        if not self.units(FORGE).exists and not self.already_pending(FORGE):
            pylon = self.units(PYLON).ready
            if pylon.exists:
                if self.can_afford(FORGE):
                    await self.build(FORGE, near=pylon.closest_to(nexus))

        # Send an extra worker to front-line
        #elif self.workers.closer_than(50, self.cannon_location).amount < 2 and not self.has_order(MOVE, self.workers):
        #    self.has_sent_workers = False

        # Start building cannons in enemy base (and more pylons)
        else:
            await self.build_cannons()


    # Strategy for late game, which prioritizes unit production and upgrades rather than cannons
    async def late_game_strategy(self):
        nexus = self.units(NEXUS).random
        if not nexus:
            return

        gateways = self.units(GATEWAY) | self.units(WARPGATE)

        # We might have multiple bases, so distribute workers between them (not every game step)
        if self.iteration % 10 == 0:
            await self.distribute_workers()

        # If game time is greater than 2 min, make sure to always scout with one worker
        if self.get_game_time() > 120:
            await self.scout()

        # Make sure to expand in late game (every 2.5 minutes)
        expand_every = 2.5 * 60 # Seconds
        prefered_base_count = 1 + int(math.floor(self.get_game_time() / expand_every))
        prefered_base_count = max(prefered_base_count, 2) # Take natural ASAP (i.e. minimum 2 bases)
        current_base_count = self.units(NEXUS).ready.filter(lambda unit: unit.ideal_harvesters >= 10).amount # Only count bases as active if they have at least 10 ideal harvesters (will decrease as it's mined out)

        # Vespene gases per nexus
        if self.enemy_start_location:
            # 2-player map. Just build 1 gas per nexus, as we start with cannon rush so need more minerals.
            prefered_gas_count = round(1 * self.units(NEXUS).amount)
        else:
            # 4-player map. Build 1.5 gas per nexus.
            prefered_gas_count = round(1.5 * self.units(NEXUS).amount)

        # Also add an extra expansion if minerals get too high
        #if self.minerals > 800:
        #    prefered_base_count += 1
        
        # Make sure we have at least one pylon near nexus before expanding
        if not self.units(PYLON).exists and not self.already_pending(PYLON):
            if self.can_afford(PYLON):
                await self.build(PYLON, near=nexus)

        #print(str(self.units(NEXUS).ready.filter(lambda unit: unit.ideal_harvesters >= 10).amount) + " / " + str(prefered_base_count))
        elif current_base_count < prefered_base_count and not self.already_pending(NEXUS) and await self.can_take_expansion():
            if self.can_afford(NEXUS):
                await self.expand_now()

        # Keep building Pylons (until 200 supply cap)
        elif self.supply_left <= 6 and self.already_pending(PYLON) < 2 and self.supply_cap < 200: # 6 / 2
            if self.can_afford(PYLON):
                await self.build(PYLON, near=self.get_base_build_location(nexus, min_distance=5))

        # Make sure forge still exists...
        elif not self.units(FORGE).exists and not self.already_pending(FORGE):
            if self.can_afford(FORGE):
                await self.build(FORGE, near=self.get_base_build_location(self.units(NEXUS).random))

        # Always build a cannon in mineral line for defense
        elif self.units(PHOTONCANNON).closer_than(10, nexus).amount < 1:
            if self.units(PYLON).ready.closer_than(5, nexus).amount < 1:
                if self.can_afford(PYLON) and not self.already_pending(PYLON):
                    await self.build(PYLON, near=nexus)
            else:
                if self.can_afford(PHOTONCANNON) and not self.already_pending(PHOTONCANNON):
                    await self.build(PHOTONCANNON, near=nexus.position.towards(self.game_info.map_center, random.randrange(-10,-1)))

        # Take gases (1 per nexus)
        elif self.units(ASSIMILATOR).amount < prefered_gas_count and not self.already_pending(ASSIMILATOR):
            if self.can_afford(ASSIMILATOR):
                for gas in self.state.vespene_geyser.closer_than(20.0, nexus):
                    if not self.units(ASSIMILATOR).closer_than(1.0, gas).exists and self.can_afford(ASSIMILATOR):
                        worker = self.select_build_worker(gas.position, force=True)
                        await self.do(worker.build(ASSIMILATOR, gas))

        # Build 1 gateway to start with
        elif gateways.ready.amount < 1 and not self.already_pending(GATEWAY):
            if self.can_afford(GATEWAY):
                await self.build(GATEWAY, near=self.get_base_build_location(self.units(NEXUS).first))
        
        # Build a Cybernetics Core (requires Gateway)
        elif not self.units(CYBERNETICSCORE).exists and self.units(GATEWAY).ready.exists and not self.already_pending(CYBERNETICSCORE):
            if self.can_afford(CYBERNETICSCORE):
                await self.build(CYBERNETICSCORE, near=self.get_base_build_location(self.units(NEXUS).first))

        # Keep making more gateways
        elif gateways.amount < self.units(NEXUS).amount * self.gateways_per_nexus and self.already_pending(GATEWAY) < 2:
            if self.can_afford(GATEWAY):
                await self.build(GATEWAY, near=self.get_base_build_location(nexus))

        # For late game, also build Robotics Facility
        elif self.units(CYBERNETICSCORE).ready.exists and not self.units(ROBOTICSFACILITY).exists and not self.already_pending(ROBOTICSFACILITY):
            if self.can_afford(ROBOTICSFACILITY):
                await self.build(ROBOTICSFACILITY, near=self.get_base_build_location(nexus))
            return

        # For even later game, also build Robotics Bay
        elif self.units(ROBOTICSFACILITY).ready.exists and not self.units(ROBOTICSBAY).exists and not self.already_pending(ROBOTICSBAY):
            if self.can_afford(ROBOTICSBAY):
                await self.build(ROBOTICSBAY, near=self.get_base_build_location(nexus))
            return

        else:
            # Make sure to always train army units from gateways/warpgates
            await self.train_army()

            # With the remaining money, go for upgrades
            await self.handle_upgrades()

            # And keep building cannons :)
            await self.build_cannons()

    async def can_take_expansion(self):
        # Must have a valid exp location
        location = await self.get_next_expansion()
        if not location:
            return False

        # Must not have enemies nearby
        if self.remembered_enemy_units.closer_than(10, location).exists:
            return False

        # Must be able to find a valid building position
        if self.can_afford(NEXUS):
            position = await self.find_placement(NEXUS, location.rounded, max_distance=10, random_alternative=False, placement_step=1)
            if not position:
                return False

        return True


    async def scout_cheese(self):
        scout = None
        nexus = self.units(NEXUS).first

        # Check if we already have a scout (a worker with PATROL order)
        for worker in self.workers:
            if self.has_order([PATROL], worker):
                scout = worker

        # If we don't have a scout, select one
        if not scout:
            scout = self.workers.closest_to(nexus)
            await self.order(scout, PATROL, self.find_random_cheese_location())
            return

        # Basic avoidance: If enemy is too close, go back to nexus
        nearby_enemy_units = self.known_enemy_units.filter(lambda unit: unit.type_id not in self.units_to_ignore).closer_than(10, scout)
        if nearby_enemy_units.exists:
            await self.order(scout, PATROL, nexus)
            return

        # When we get close enough to our target location, change target
        target = sc2.position.Point2((scout.orders[0].target.x, scout.orders[0].target.y))
        if scout.distance_to(target) < 6:
            await self.order(scout, PATROL, self.find_random_cheese_location())
            return

    def find_random_cheese_location(self, max_distance=40, min_distance=20):
        location = None
        while not location or location.distance_to(self.start_location) > max_distance or location.distance_to(self.start_location) < min_distance:
            x = random.randrange(0, self.game_info.pathing_grid.width)
            y = random.randrange(0, self.game_info.pathing_grid.height)

            location = sc2.position.Point2((x,y))
        return location

    async def scout(self):
        scout = None

        # Check if we already have a scout (a worker with PATROL order)
        for worker in self.workers:
            if self.has_order([PATROL], worker):
                scout = worker

        # If we don't have a scout, select one, and order it to move to random exp
        if not scout:
            random_exp_location = random.choice(list(self.expansion_locations.keys()))
            scout = self.workers.closest_to(self.start_location)

            if not scout:
                return

            await self.order(scout, PATROL, random_exp_location)
            return

        # Basic avoidance: If enemy is too close, go to map center
        nearby_enemy_units = self.known_enemy_units.filter(lambda unit: unit.type_id not in self.units_to_ignore).closer_than(10, scout)
        if nearby_enemy_units.exists:
            await self.order(scout, PATROL, self.game_info.map_center)
            return

        # We're close enough, so change target
        target = sc2.position.Point2((scout.orders[0].target.x, scout.orders[0].target.y))
        if scout.distance_to(target) < 10:
            random_exp_location = random.choice(list(self.expansion_locations.keys()))
            await self.order(scout, PATROL, random_exp_location)
            return



    # Train/warp-in army units
    async def train_army(self):
        # Start building colossus whenever possible
        for robotics in self.units(ROBOTICSFACILITY).ready.noqueue:
            # Always have one observer out (mainly to gain high ground vision)
            if self.units(OBSERVER).ready.amount < 1:
                await self.train(OBSERVER, robotics)
                return

            # If we can research extended thermal lance, and already have a colossus out, do it
            elif await self.can_upgrade(RESEARCH_EXTENDEDTHERMALLANCE, robotics) and self.units(COLOSSUS).ready.amount >= 1:
                await self.upgrade(RESEARCH_EXTENDEDTHERMALLANCE, robotics)
                return
            
            # Else, just train colossus/immortals
            elif self.units(ROBOTICSBAY).ready.exists:
                enemy_units = self.remembered_enemy_units
                has_mostly_marauders = enemy_units(MARAUDER).amount > 0 and enemy_units(MARAUDER).amount > enemy_units(MARINE).amount
                has_mostly_mech = enemy_units(HELLION).amount > 0 and enemy_units(HELLION).amount > enemy_units(MARINE).amount
                has_mostly_stalkers = enemy_units(STALKER).amount > 0 and enemy_units(STALKER).amount * 1.5 > enemy_units(ZEALOT).amount 
                has_mostly_roaches = enemy_units(ROACH).amount > 0 and enemy_units(ROACH).amount > enemy_units(HYDRALISK).amount and enemy_units(ROACH).amount * 2 > enemy_units(ZERGLING).amount
                has_too_many_flying = enemy_units(VIKINGFIGHTER).amount > 3 or enemy_units(MUTALISK).amount > 3 or enemy_units(VOIDRAY).amount > 3 or enemy_units(PHOENIX).amount > 3

                # Depending on enemy's unit composition, build either immortal or colossus
                if has_mostly_marauders or has_mostly_mech or has_mostly_stalkers or has_mostly_roaches or has_too_many_flying:
                    await self.train(IMMORTAL, robotics)
                else:
                    await self.train(COLOSSUS, robotics)

                # Don't train anything else until robo units are built
                return
        

        rally_location = self.get_rally_location()

        # Train at Gateways
        for gateway in self.units(GATEWAY).ready:
            # Set gateway rally
            #await self.do(gateway(RALLY_BUILDING, rally_location))

            if gateway.noqueue:
                if await self.has_ability(MORPH_WARPGATE, gateway):
                    #if self.can_afford(MORPH_WARPGATE):
                    await self.do(gateway(MORPH_WARPGATE))
                    return
                elif self.supply_used < 198 and self.supply_left >= 2:
                    # Train 75% Stalkers and 25% Zealots
                    if self.can_afford(STALKER) and self.can_afford(ZEALOT) and self.can_afford(SENTRY):
                        rand = random.random()
                        if rand < self.sentry_ratio and self.units(CYBERNETICSCORE).ready.exists:
                            await self.do(gateway.train(SENTRY))
                            return
                        elif rand <= self.stalker_ratio and self.units(CYBERNETICSCORE).ready.exists:
                            await self.do(gateway.train(STALKER))
                            return
                        else:
                            await self.do(gateway.train(ZEALOT))
                            return

        # Warp-in from Warpgates
        for warpgate in self.units(WARPGATE).ready:
            # We check for WARPGATETRAIN_ZEALOT to see if warpgate is ready to warp in
            if await self.has_ability(WARPGATETRAIN_ZEALOT, warpgate) and self.supply_used < 198 and self.supply_left >= 2:
                # Always warp in zealots if banking minerals
                if self.minerals > 400 and self.vespene < 100:
                    await self.warp_in(ZEALOT, rally_location, warpgate)
                # Otherwise, train units depending on the ratio
                elif self.can_afford(STALKER) and self.can_afford(ZEALOT) and self.can_afford(SENTRY):
                    rand = random.random()
                    if rand <= self.sentry_ratio and self.units(CYBERNETICSCORE).ready.exists:
                        await self.warp_in(SENTRY, rally_location, warpgate)
                        return
                    elif rand <= self.stalker_ratio and self.units(CYBERNETICSCORE).ready.exists:
                        await self.warp_in(STALKER, rally_location, warpgate)
                        return
                    else:
                        await self.warp_in(ZEALOT, rally_location, warpgate)
                        return


    # Handle upgrades.
    async def handle_upgrades(self):
        # Prioritize warp-gate research
        if self.units(CYBERNETICSCORE).ready.exists:
            cybernetics = self.units(CYBERNETICSCORE).first
            if cybernetics.noqueue and await self.has_ability(RESEARCH_WARPGATE, cybernetics):
                if self.can_afford(RESEARCH_WARPGATE):
                    await self.do(cybernetics(RESEARCH_WARPGATE))
                return

        # Build Twilight Council (requires Cybernetics Core)
        if not self.units(TWILIGHTCOUNCIL).exists and not self.already_pending(TWILIGHTCOUNCIL):
            if self.can_afford(TWILIGHTCOUNCIL) and self.units(CYBERNETICSCORE).ready.exists:
                await self.build(TWILIGHTCOUNCIL, near=self.get_base_build_location(self.units(NEXUS).first))
            return

        if not self.units(TWILIGHTCOUNCIL).ready.exists:
            return
        twilight = self.units(TWILIGHTCOUNCIL).first

        # Research Blink and Charge at Twilight
        # Temporary bug workaround: Don't go further unless we can afford blink
        if not self.can_afford(RESEARCH_BLINK):
            return

        if twilight.noqueue:
            if await self.has_ability(RESEARCH_BLINK, twilight):
                if self.can_afford(RESEARCH_BLINK):
                    await self.do(twilight(RESEARCH_BLINK))
                return
            elif await self.has_ability(RESEARCH_CHARGE, twilight):
                if self.can_afford(RESEARCH_CHARGE):
                    await self.do(twilight(RESEARCH_CHARGE))
                return
            
        # Must have a forge to continue upgrades
        if not self.units(FORGE).ready.exists:
            return
        forge = self.units(FORGE).first

        # Only if we're not upgrading anything yet
        if forge.noqueue:
            # Go through each weapon, armor and shield upgrade and check if we can research it, and if so, do it
            for upgrade_level in range(1, 4):
                upgrade_weapon_id = getattr(sc2.constants, "FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL" + str(upgrade_level))
                upgrade_armor_id = getattr(sc2.constants, "FORGERESEARCH_PROTOSSGROUNDARMORLEVEL" + str(upgrade_level))
                shield_armor_id = getattr(sc2.constants, "FORGERESEARCH_PROTOSSSHIELDSLEVEL" + str(upgrade_level))
                if await self.has_ability(upgrade_weapon_id, forge):
                    if self.can_afford(upgrade_weapon_id):
                        await self.do(forge(upgrade_weapon_id))
                    return
                elif await self.has_ability(upgrade_armor_id, forge):
                    if self.can_afford(upgrade_armor_id):
                        await self.do(forge(upgrade_armor_id))
                    return
                elif await self.has_ability(shield_armor_id, forge):
                    if self.can_afford(shield_armor_id):
                        await self.do(forge(shield_armor_id))
                    return


    # Micro for workers
    async def move_workers(self):
        # Make workers flee from enemy cannon
        for worker in self.workers:
            if self.known_enemy_units.structure.ready.filter(lambda unit: unit.type_id in [PHOTONCANNON]).closer_than(9, worker):
                if not self.has_order(MOVE, worker):
                    await self.do(worker.move(worker.position.towards(self.start_location, 4)))

        # Make low health cannon builders flee from melee enemies
        if self.cannon_location:
            for worker in self.workers.closer_than(40, self.cannon_location):
                if worker.shield < 10 and self.known_enemy_units.closer_than(4, worker).not_structure.filter(lambda unit: not unit.is_flying).exists:
                    if not self.has_order(MOVE, worker):
                        # We have nearby enemy. Run home!
                        #await self.do(worker.gather(self.state.mineral_field.closest_to(self.units(NEXUS).first))) #Do mineral walk at home base to escape.
                        await self.do(worker.move(worker.position.towards(self.start_location, 4)))



    # Movement and micro for army
    async def move_army(self):
        army_units = self.units(STALKER).ready | self.units(ZEALOT).ready | self.units(OBSERVER).ready | self.units(COLOSSUS).ready | self.units(IMMORTAL).ready | self.units(SENTRY).ready
        army_count = army_units.amount
        home_location = self.start_location
        focus_fire_target = None
        attack_random_exp = False
        attack_location = None

        # Determine attack location
        if army_count < self.army_size_minimum:
            # We have less than self.army_size_minimum army in total. Just gather at rally point
            attack_location = self.get_rally_location()
        elif self.remembered_enemy_units.filter(lambda unit: unit.type_id not in self.units_to_ignore).exists:
            # We have large enough army and have seen an enemy. Attack closest enemy to home
            attack_location = self.remembered_enemy_units.filter(lambda unit: unit.type_id not in self.units_to_ignore).closest_to(home_location).position
        else:
            # We have not seen an enemy
            #if random.random() < 0.8:
                # Try move to random enemy start location 80% of time
            #    attack_location = random.choice(self.enemy_start_locations) #self.enemy_start_locations[0]
            #else:
                # As a last resort, scout different expansions with army units
            attack_random_exp = True


        # Micro for each individual army unit
        for unit in army_units:
            has_blink = False
            has_guardianshield = False
            if unit.type_id == STALKER:
                has_blink = await self.has_ability(EFFECT_BLINK_STALKER, unit) # Do we have blink?
            elif unit.type_id == SENTRY:
                has_guardianshield = await self.has_ability(GUARDIANSHIELD_GUARDIANSHIELD, unit)

            # Find nearby enemy units
            nearby_enemy_units = self.remembered_enemy_units.not_structure.filter(lambda unit: unit.type_id not in self.units_to_ignore).closer_than(15, unit)

            # If we don't have any nearby enemies
            if not nearby_enemy_units.exists:
                # If we don't have an attack order, cast one now
                if not self.has_order(ATTACK, unit) or (self.known_enemy_units.exists and not self.has_target(attack_location, unit)):
                    if attack_random_exp:
                        # If we're attacking a random exp, find one now
                        random_exp_location = random.choice(list(self.expansion_locations.keys()))
                        await self.do(unit.attack(random_exp_location))
                        #print("Attack random exp")
                    elif unit.distance_to(attack_location) > 10:
                        await self.do(unit.attack(attack_location))
                        #print("Attack no enemy nearby")
                
                # Blink towards attack location
                #elif has_blink:
                #    await self.order(unit, EFFECT_BLINK_STALKER, unit.orders[0].target)

                continue # Do no further micro

            # Calculate friendly vs enemy army value
            friendly_army_value = self.friendly_army_value(unit, 10) #20
            enemy_army_value = self.enemy_army_value(nearby_enemy_units.closest_to(unit), 10) #30
            army_advantage = friendly_army_value - enemy_army_value
            #army_advantage = 0

            # If our shield is low, escape a little backwards
            if unit.is_taking_damage and unit.shield < 20 and unit.type_id not in [ZEALOT]:
                escape_location = unit.position.towards(home_location, 4)
                if has_blink:
                    # Stalkers can blink
                    await self.order(unit, EFFECT_BLINK_STALKER, escape_location)
                else:
                    # Others can move normally
                    if not self.has_order(MOVE, unit):
                        await self.do(unit.move(escape_location))

                continue

            # Do we have an army advantage?
            if army_advantage > 0:
                # We have a larger army. Engage enemy
                attack_position = nearby_enemy_units.closest_to(unit).position

                # If not already attacking, attack
                if not self.has_order(ATTACK, unit) or not self.has_target(attack_position, unit):
                    await self.do(unit.attack(attack_position))
                
                # Activate guardian shield for sentries (if enemy army value is big enough)
                if has_guardianshield and enemy_army_value > 200:
                    await self.order(unit, GUARDIANSHIELD_GUARDIANSHIELD)
            else:
                # We have a smaller army, so run back home!
                if has_blink:
                    # Stalkers can blink
                    await self.order(unit, EFFECT_BLINK_STALKER, home_location)
                else:
                    # Others can move normally
                    if not self.has_order(MOVE, unit):
                        await self.do(unit.move(home_location))
                


    def get_rally_location(self):
        if self.units(PYLON).ready.exists:
            if self.cannon_location:
                rally_location = self.units(PYLON).ready.closest_to(self.cannon_location).position
            else:
                rally_location = self.units(PYLON).ready.closest_to(self.game_info.map_center).position
        else:
            rally_location = self.start_location
        return rally_location

    # Approximate army value by adding unit health+shield
    def friendly_army_value(self, position, distance=10):
        value = 0

        for unit in self.units.not_structure.filter(lambda unit: unit.type_id not in self.units_to_ignore).closer_than(distance, position):
            value += unit.health + unit.shield

        # Count nearby cannons
        for unit in self.units(PHOTONCANNON).closer_than(10, position):
            value += unit.health # Skip shield, to not overestimate

        # Count nearby bunkers
        for unit in self.units(BUNKER).ready.closer_than(10, position):
            value += unit.health

        # Count nearby spine crawlers
        for unit in self.units(SPINECRAWLER).ready.closer_than(10, position):
            value += unit.health

        return value

    # Approximate army value by adding unit health+shield
    def enemy_army_value(self, position, distance=10):
        value = 0

        for unit in self.remembered_enemy_units.ready.not_structure.filter(lambda unit: unit.type_id not in self.units_to_ignore).closer_than(distance, position):
            value += unit.health + unit.shield

            # Add extra army value for marine/marauder, to not under-estimate
            if unit.type_id in [MARINE, MARAUDER]:
                value += 20

        # Count nearby cannons
        for unit in self.remembered_enemy_units(PHOTONCANNON).ready.closer_than(10, position):
            value += unit.health # Skip shield, to not overestimate

        # Count nearby bunkers
        for unit in self.remembered_enemy_units(BUNKER).ready.closer_than(10, position):
            value += unit.health

        # Count nearby spine crawlers
        for unit in self.remembered_enemy_units(SPINECRAWLER).ready.closer_than(10, position):
            value += unit.health

        return value

    def get_game_center_random(self, offset_x=50, offset_y=50):
        x = self.game_info.map_center.x
        y = self.game_info.map_center.y

        rand = random.random()
        if rand < 0.2:
            x += offset_x
        elif rand < 0.4:
            x -= offset_x
        elif rand < 0.6:
            y += offset_y
        elif rand < 0.8:
            y -= offset_y

        return sc2.position.Point2((x,y))

    def get_base_build_location(self, base, min_distance=10, max_distance=20):
        return base.position.towards(self.get_game_center_random(), random.randrange(min_distance, max_distance))

