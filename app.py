# https://discord.com/api/oauth2/authorize?client_id=1190002809685430437&permissions=139586776128&scope=bot
import os
import asyncio
import random
import math
import datetime
from copy import deepcopy
import json
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks

load_dotenv()
discord.utils.setup_logging()

client = commands.Bot(command_prefix="/",
                      intents=discord.Intents.all())


@tasks.loop(time=[datetime.time(hour=12, minute=0, tzinfo=datetime.timezone.utc)])
#@tasks.loop(hours=1)
async def dailyReset():
    print('Daily reset occurring')
    with open("./data/bot_status.txt", "r") as file:
        randomresponses = file.readlines()
        response = random.choice(randomresponses)
    await client.change_presence(activity=discord.CustomActivity(name=response, emoji='🦆'))
    # Requires that you do the following for this to work: pip install discord.py>=2.3.2

    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    for userId, user in user_info.items():
        if userId == "default":
            continue

        # Collect the income from each land
        for land_id in user["land_ids"]:
            land = lands[str(land_id)]

            species = await get_species(land["species"])

            income = land["quality"] + int(species["bonusIncomePerQuality"] * land["quality"])

            # Give the user extra income according to the support they gave/lands this user has
            if user["support"] > 0:
                support_used = int(
                    round(user["support"] / len(user["land_ids"])))
                user["support"] -= support_used
                income += income * support_used * \
                    global_info["supportIncomeBoostPercent"]

            # Adjust income if they have too many lands
            if len(user["land_ids"]) > global_info["landLimit"]:
                income -= income * \
                    global_info["landIncomePenaltyPercentPerLand"] * \
                    (len(user["land_ids"]) - global_info["landLimit"])
                income = max(0, int(income))

            # Adjust income if the land is being sieged by a superior foe
            if await is_surrounded(land):
                income -= income * species["incomePenaltyPercentInSiege"]
                income = max(0, int(income))

            # Add the income to the user
            user["quackerinos"] += income

            # Roll for increase land quality if the user quacked or if there is a bonus this season
            if bool(user["quackedToday"]):
                if land["quality"] < land["maxQuality"]:
                    qualityImprovementProbability = global_info["qualityImprovementProbability"]

                    for building_id in land["buildings"]:
                        building = await get_building(building_id)
                        qualityImprovementProbability += building["qualityIncreaseChanceBonus"]

                    if random.random() < qualityImprovementProbability:
                        land["quality"] += 1
                    
                    land["quality"] += species["landQualityIncreasePerTurn"]
                
                land["quality"] = min(land["maxQuality"], land["quality"])

            else:
                # Roll for decrease land quality of the user didn't quack
                if land["quality"] > 0 and random.random() < global_info["qualityDecayProbability"]:
                    land["quality"] -= 1

        # Pay liege lord according to the tax rate set by them
        if user["liege_id"] != 0:
            liege = user_info[str(user["liege_id"])]
            tax = liege["taxPerVassalLand"] * len(user["land_ids"])

            if tax > user["quackerinos"]:
                liege["quackerinos"] += user["quackerinos"]
                user["quackerinos"] = 0
            else:
                liege["quackerinos"] += tax
                user["quackerinos"] -= tax

        # Reset streak counter if the streak is broken
        if not bool(user["quackedToday"]):
            user["quackStreak"] = 0
        else:
            user["spins"] += 1

        user["quackedToday"] = False
        user["mischief"] = False

        target_rank = await get_quack_rank(user["quacks"])

        if target_rank != user["quackRank"]:
            user["quackRank"] = target_rank

        # Reset support
        user["support"] = 0
        user["supportee_id"] = 0

        # Reduce safety counter
        if user["safety_count"] > 0 and user["homeland_id"] > 0:
            user["safety_count"] -= 1

    # Attempt to pay all the soldiers in each land
    for land_id, land in lands.items():
        if land_id == "default":
            continue

        garrison_disband_list = []
        siege_camp_disband_list = []

        # Pay the garrison
        for unit in land["garrison"]:
            user = user_info[str(unit["user_id"])]
            troop = await get_troop(unit["troop_name"])
            species = await get_species(troop["species"])

            cost = unit["amount"] * troop["upkeep"]
            cost -= cost * species["upkeepDiscountPerTroop"]

            # If the user doesn't have enough money left then disband all, otherwise reduce the user's qq balance
            if user["quackerinos"] < cost:
                # Add unit to the list to be disbanded
                garrison_disband_list.append(unit)

                # DM user that units have been disbanded
                await dm(unit["user_id"], f'{unit["amount"]} {unit["troop_name"]} have been disbanded at {land["name"]} because you didn\'t have enough money to pay them.')
            else:
                user["quackerinos"] -= cost

        # Disband units from the garrison
        for unit in garrison_disband_list:
            land["garrison"].remove(unit)

        # Pay the siegecamp
        for unit in land["siegeCamp"]:
            user = user_info[str(unit["user_id"])]
            troop = await get_troop(unit["troop_name"])
            species = await get_species(troop["species"])

            cost = unit["amount"] * troop["upkeep"]
            cost -= cost * species["upkeepDiscountPerTroop"]
            cost += unit["amount"] * species["upkeepExtraPerTroopInOffensiveSiege"]

            # If the user doesn't have enough money left then disband all, otherwise reduce the user's qq balance
            if user["quackerinos"] < cost:
                # Add unit to the list to be disbanded
                siege_camp_disband_list.append(unit)

                # DM user that units have been disbanded
                await dm(unit["user_id"], f'{unit["amount"]} {unit["troop_name"]} have been disbanded at {land["name"]} because you didn\' have enough money to pay them.')
            else:
                user["quackerinos"] -= cost

        # Disband units from the siege camp
        for unit in siege_camp_disband_list:
            land["siegeCamp"].remove(unit)

    # Execute the task queue
    index = 0

    # Execute all the siege commands first
    while index < len(global_info["task_queue"]):
        task = global_info["task_queue"][index]

        if task["task"] == "siege":
            user = user_info[str(task["user_id"])]
            land = lands.get(str(task["location_id"]), "")
            target_land = lands.get(str(task["target_land_id"]), "")
            unit = await get_unit(land["siegeCamp"], task["item"], task["user_id"])
            army = land["siegeCamp"]

            # Fail if that troop isn't in that land or if there aren't as many as specified
            if unit == "" or unit["amount"] < task["amount"]:
                unit = await get_unit(land["garrison"], task["item"], task["user_id"])
                army = land["garrison"]
                if unit == "" or unit["amount"] < task["amount"]:
                    await dm(task["user_id"], f'You don\'t have enough of {task["item"]} from {land["name"]} to send to the siege camp of {target_land["name"]}.')
                    global_info["task_queue"].pop(index)  # Remove this task
                    continue

            # Fail if the target land is yours
            if target_land["owner_id"] == task["user_id"]:
                await dm(task["user_id"], 'You can\'t siege yourself.')
                global_info["task_queue"].pop(index)  # Remove this task
                continue

            allies = await get_allies(task["user_id"])

            # Fail if the target is the liege or vassal of your liege or your vassal
            if str(target_land["owner_id"]) in allies:
                await dm(task["user_id"], f'You can\'t siege {client.get_user(int(target_land["owner_id"]))}\'s settlement {target_land["name"]} for one of the following reasons: they are your liege, fellow vassal, your vassal, your ally, or a vassal of your ally.')
                global_info["task_queue"].pop(index)  # Remove this task
                continue

            # Fail if the your land is already surrounded
            if await is_surrounded(land):
                await dm(task["user_id"], f'You cannot move {task["item"]} out of {land["name"]} because it is fully surrounded.')
                global_info["task_queue"].pop(index)  # Remove this task
                continue

            # Remove the troops from the original land
            moved_unit = await remove_unit(army, unit, task["amount"])

            # Add them to the siege camp on the target land
            await add_unit(target_land["siegeCamp"], moved_unit)

            await dm(task["user_id"],
                     f'{task["amount"]} {task["item"]}s were sent to siege {target_land["name"]}.')
            global_info["task_queue"].pop(index)  # Remove this task
        else:
            index += 1

    index = 0

    # Execute each siege battle, including attack commands and garrison. Then also include the siege camp if there are any defend commands.
    while index < len(global_info["task_queue"]):
        task = global_info["task_queue"][index]

        if task["task"] == "attack":
            include_siege_camp = False
            user_ids = []
            attacker_army = []
            defender_army = []

            defend_index = 0
            target_land = lands.get(str(task["target_land_id"]), "")

            # Check for all other defend commands done to this target place and put them into an array
            while defend_index < len(global_info["task_queue"]):
                action = global_info["task_queue"][defend_index]
                if action["target_land_id"] == task["target_land_id"] and action["task"] == "defend":
                    user = user_info[str(action["user_id"])]
                    land = lands.get(str(action["location_id"]), "")

                    unit = await get_unit(land["siegeCamp"], action["item"], action["user_id"])

                    # Fail if that troop isn't in that land or if there aren't as many as specified
                    if unit == "" or unit["amount"] < action["amount"]:
                        unit = await get_unit(land["garrison"], action["item"], action["user_id"])
                        if unit == "" or unit["amount"] < action["amount"]:
                            await dm(action["user_id"], f'You don\'t have enough {action["item"]} from {land["name"]} to send on an attack against {target_land["name"]}.')
                            global_info["task_queue"].pop(
                                defend_index)  # Remove this task
                            continue

                    # Fail if they are both the same land
                    if target_land["owner_id"] == action["user_id"]:
                        await dm(action["user_id"], 'You don\'t need to use the defend command for troops in the garrison of a land being attacked.')
                        global_info["task_queue"].pop(
                            defend_index)  # Remove this task
                        continue

                    # Fail if the your land is already surrounded
                    if await is_surrounded(land):
                        await dm(task["user_id"], f'You cannot move {action["item"]} out of {land["name"]} because it is fully surrounded.')
                        global_info["task_queue"].pop(
                            defend_index)  # Remove this task
                        continue

                    # Add the troops to the defender army
                    defender_army.append(
                        {"unit": unit, "amount": action["amount"]})

                    # user_ids.append(action["user_id"])

                    global_info["task_queue"].pop(
                        defend_index)  # Remove this task

                    include_siege_camp = True
                else:
                    defend_index += 1

            # Add all garrison to the defend army
            for unit in target_land["garrison"]:
                defender_army.append({"unit": unit, "amount": unit["amount"]})

            if include_siege_camp:
                # Add all siege camp to the attack army
                for unit in target_land["siegeCamp"]:
                    attacker_army.append(
                        {"unit": unit, "amount": unit["amount"]})

            attack_index = 0

            # Check for all other attack commands done to this target place and put them into an array
            while attack_index < len(global_info["task_queue"]):
                action = global_info["task_queue"][attack_index]

                if action["target_land_id"] == task["target_land_id"] and action["task"] == "attack":
                    user = user_info[str(action["user_id"])]
                    land = lands.get(str(action["location_id"]), "")
                    # target_land = lands.get(str(action["target_land_id"]), "")

                    unit = await get_unit(land["siegeCamp"], action["item"], action["user_id"])

                    # Fail if that troop isn't in that land or if there aren't as many as specified
                    if unit == "" or unit["amount"] < action["amount"]:
                        unit = await get_unit(land["garrison"], action["item"], action["user_id"])
                        if unit == "" or unit["amount"] < action["amount"]:
                            await dm(action["user_id"], f'You don\'t have enough {action["item"]} from {land["name"]} to send on an attack against {target_land["name"]}.')
                            global_info["task_queue"].pop(
                                attack_index)  # Remove this task
                            continue
                    # Fail if the siege camp has already been included in the battle
                    elif include_siege_camp and action["location_id"] == action["target_land_id"]:
                        global_info["task_queue"].pop(
                            attack_index)  # Remove this task
                        continue

                    # Fail if the target land is yours
                    if target_land["owner_id"] == action["user_id"]:
                        await dm(action["user_id"], 'You can\'t attack yourself.')
                        global_info["task_queue"].pop(
                            attack_index)  # Remove this task
                        continue

                    allies = await get_allies(action["user_id"])

                    # Fail if the target is the liege or vassal of your liege or your vassal
                    if str(target_land["owner_id"]) in allies:
                        await dm(action["user_id"], f'You can\'t attack {client.get_user(int(target_land["owner_id"]))} for one of the following reasons: they are your liege, fellow vassal, your vassal, your ally, or a vassal of your ally.')
                        global_info["task_queue"].pop(
                            attack_index)  # Remove this task
                        continue

                    # Fail if the your land is already surrounded
                    if await is_surrounded(land) and land != target_land:
                        await dm(task["user_id"], f'You cannot move {task["item"]} out of {land["name"]} because it is fully surrounded.')
                        global_info["task_queue"].pop(
                            attack_index)  # Remove this task
                        continue

                    # Add the troops to the attacker army
                    attacker_army.append(
                        {"unit": unit, "amount": action["amount"]})

                    # user_ids.append(action["user_id"])

                    global_info["task_queue"].pop(
                        attack_index)  # Remove this task

                else:
                    attack_index += 1

            # Get the list of people to alert
            for unit in attacker_army:
                user_ids.append(unit["unit"]["user_id"])
            for unit in defender_army:
                user_ids.append(unit["unit"]["user_id"])

            total_defenders = await get_total_troops(defender_army)
            total_attackers = await get_total_troops(attacker_army)
            # Only resolve combat if there are defenders and attackers
            if total_defenders > 0 and total_attackers > 0:
                # Resolve the combat
                message = await resolve_battle(attacker_army, defender_army, target_land)
            else:
                message = f'There were not enough troops for a battle at {target_land["name"]}.'

            # If the defender army is empty then transfer the land to the attacking side (and add this to the message)
            total_defenders = await get_total_troops(defender_army)
            total_attackers = await get_total_troops(attacker_army)

            if total_defenders <= 0 and total_attackers > 0:
                troops_by_user = {}
                highest_user_id = 0

                # Find the person with the most troops currently left in the attacking army
                for unit in attacker_army:
                    troops_by_user[unit["unit"]["user_id"]] = troops_by_user.get(
                        unit["unit"]["user_id"], 0) + unit["unit"]["amount"]

                for user_id, number in troops_by_user.items():
                    if troops_by_user.get(highest_user_id, 0) < number:
                        highest_user_id = user_id

                # Destroy buildings accordingly
                total_destroy_percent = 0
                total_troops = 0

                # Get the average percent building destruction
                for company in attacker_army:
                    troop = await get_troop(company["unit"]["troop_name"])
                    species = await get_species(troop["species"])

                    total_destroy_percent += species["percentBuildingsDestroyedOnConquest"] * company["amount"]
                    total_troops += company["amount"]

                total_buildings_destroyed = int(
                    round(len(target_land["buildings"]) * (total_destroy_percent/total_troops)))

                for x in range(total_buildings_destroyed):

                    building_name = target_land["buildings"].pop(random.randint(
                        0, len(target_land["buildings"])-1))

                    building = await get_building(building_name)

                    # Add the lower tier building if necessary
                    if building["demolishedTo"] != "":
                        target_land["buildings"].append(
                            building["demolishedTo"])

                # Change the land owner
                if highest_user_id != 0:
                    user_info[str(target_land["owner_id"])]["land_ids"].remove(
                        task["target_land_id"])
                    target_land["owner_id"] = int(highest_user_id)
                    user_info[str(target_land["owner_id"])]["land_ids"].append(
                        task["target_land_id"])
                    message += f'\n\n{target_land["name"]} has been taken by {client.get_user(int(target_land["owner_id"]))}.'

                message += f'\n{total_buildings_destroyed} buildings were burned.'

                # Move the siege camp troops into the garrison
                target_land["garrison"] = deepcopy(target_land["siegeCamp"])
                target_land["siegeCamp"] = []

            # DM the results to all the combatants
            for user_id in user_ids:
                await dm(user_id, message)
        else:
            index += 1

    index = 0

    # Execute each field battle, including sallyout commands and siege camp.
    while index < len(global_info["task_queue"]):
        task = global_info["task_queue"][index]

        if task["task"] == "sallyout":
            user_ids = []
            attacker_army = []
            defender_army = []

            attack_index = 0
            
            target_land = lands.get(str(task["target_land_id"]), "")

            while attack_index < len(global_info["task_queue"]):
                action = global_info["task_queue"][attack_index]
                if action["target_land_id"] == task["target_land_id"] and action["task"] == "sallyout":
                    land = lands.get(str(action["location_id"]), "")

                    unit = await get_unit(land["siegeCamp"], action["item"], action["user_id"])

                    # Fail if that troop isn't in that land or if there aren't as many as specified
                    if unit == "" or unit["amount"] < action["amount"]:
                        unit = await get_unit(land["garrison"], action["item"], action["user_id"])
                        if unit == "" or unit["amount"] < action["amount"]:
                            await dm(action["user_id"], f'You don\'t have enough {action["item"]} from {land["name"]} to send on an attack at {target_land["name"]}.')
                            global_info["task_queue"].pop(
                                attack_index)  # Remove this task
                            continue

                    # Fail if the your land is already surrounded
                    if await is_surrounded(land) and action["location_id"] != action["target_land_id"]:
                        await dm(task["user_id"], f'You cannot move {action["item"]} out of {land["name"]} because it is fully surrounded.')
                        global_info["task_queue"].pop(
                            attack_index)  # Remove this task
                        continue

                    # Add the troops to the defender army
                    attacker_army.append(
                        {"unit": unit, "amount": action["amount"]})

                    user_ids.append(action["user_id"])

                    global_info["task_queue"].pop(
                        attack_index)  # Remove this task
                else:
                    attack_index += 1

            # Add all siege camp to the attack army
            for unit in target_land["siegeCamp"]:
                defender_army.append(
                    {"unit": unit, "amount": unit["amount"]})

            total_defenders = await get_total_troops(defender_army)
            total_attackers = await get_total_troops(attacker_army)

            # Only resolve combat if there are defenders and attackers
            if total_defenders > 0 and total_attackers > 0:
                # Resolve the combat
                message = await resolve_battle(attacker_army, defender_army)
            else:
                message = f'There were not enough troops for a battle at {target_land["name"]}.'

            # DM the results to all the combatants
            for user_id in user_ids:
                await dm(user_id, message)
        else:
            index += 1

    index = 0

    # Execute all move commands.
    while index < len(global_info["task_queue"]):
        task = global_info["task_queue"][index]

        if task["task"] == "move":
            user = user_info[str(task["user_id"])]
            land = lands.get(str(task["location_id"]), "")
            target_land = lands.get(str(task["target_land_id"]), "")
            unit = await get_unit(land["siegeCamp"], task["item"], task["user_id"])
            army = land["siegeCamp"]

            # Fail if that troop isn't in that land or if there aren't as many as specified
            if unit == "" or unit["amount"] < task["amount"]:
                unit = await get_unit(land["garrison"], task["item"], task["user_id"])
                army = land["garrison"]
                if unit == "" or unit["amount"] < task["amount"]:
                    await dm(task["user_id"], f'You don\'t have enough {task["item"]} from {land["name"]} to send to the garrison of {target_land["name"]}.')
                    global_info["task_queue"].pop(index)  # Remove this task
                    continue
                # Fail if they are both the same land
                elif task["location_id"] == task["target_land_id"]:
                    await dm(task["user_id"], 'The developers stopped you from taking a useless action.')
                    global_info["task_queue"].pop(index)  # Remove this task
                    continue

            allies = await get_allies(task["user_id"])

            # Fail if the target land isn't yours or one of your allies
            if target_land["owner_id"] != task["user_id"] and str(target_land["owner_id"]) not in allies:
                await dm(task["user_id"], f'You can\'t move {task["item"]} into {client.get_user(int(target_land["owner_id"]))}\'s settlement {target_land["name"]} for one of the following reasons: they are not your liege, fellow vassal, your vassal, your ally, or a vassal of your ally.')
                global_info["task_queue"].pop(index)  # Remove this task
                continue

            # Fail if the your land is already surrounded
            if await is_surrounded(land):
                await dm(task["user_id"], f'You cannot move {task["item"]} out of {land["name"]} because it is fully surrounded.')
                global_info["task_queue"].pop(index)  # Remove this task
                continue

            # Fail if the target land is already surrounded unless taking troops out of the siege camp
            if await is_surrounded(target_land) and army != land["siegeCamp"]:
                await dm(task["user_id"], f'You cannot move {task["item"]} into the garrison of {target_land["name"]} because it is fully surrounded.')
                global_info["task_queue"].pop(index)  # Remove this task
                continue

            # Remove the troops from the original land
            moved_unit = await remove_unit(army, unit, task["amount"])

            # Add them to the garrison on the target land
            await add_unit(target_land["garrison"], moved_unit)

            # DM the results to the player
            await dm(task["user_id"], f'{task["amount"]} {task["item"]}s were sent to {target_land["name"]}\'s garrison.')

            global_info["task_queue"].pop(index)  # Remove this task
        else:
            index += 1

    index = 0

    # Execute all upgrade commands in the following order: Tier 4 upgrades → Tier 3 upgrades → Tier 2 upgrades → Hire upgrades
    while index < len(global_info["task_queue"]):
        task = global_info["task_queue"][index]

        if task["task"] == "upgrade":
            top_tier = 1

            # Get the top tier upgrade troop
            for action in global_info["task_queue"]:
                if action["task"] == "upgrade":
                    troop = await get_troop(action["item"])

                    if troop["tier"] > top_tier:
                        top_tier = troop["tier"]

            # Execute all the upgrade commands going from top to bottom tiers
            while top_tier > 0:
                troop_index = 0
                while troop_index < len(global_info["task_queue"]):
                    action = global_info["task_queue"][troop_index]
                    troop = await get_troop(action["item"])

                    if action["task"] == "upgrade" and troop["tier"] == top_tier:
                        user = user_info[str(action["user_id"])]
                        land = lands.get(str(action["location_id"]), "")
                        # Fail if the specified land doesn't belong to that player
                        if action["location_id"] not in user["land_ids"]:
                            await dm(action["user_id"], f'You cannot upgrade {action["item"]} at {land["name"]} because that land doesn\'t belong to you.')
                            global_info["task_queue"].pop(
                                troop_index)  # Remove this task
                            continue

                        unit = await get_unit(land["garrison"], action["item"], action["user_id"])

                        # Fail if that troop isn't in that land or if there aren't as many as specified
                        if unit == "" or unit["amount"] < action["amount"]:
                            await dm(action["user_id"], f'You don\'t have enough {action["item"]} to upgrade {action["amount"]} of them.')
                            global_info["task_queue"].pop(
                                troop_index)  # Remove this task
                            continue

                        new_troop = await get_troop(troop["upgradesTo"])
                        cost = new_troop["cost"] * action["amount"]

                        # Fail if not enough money
                        if int(user["quackerinos"]) < cost:
                            await dm(action["user_id"], f'You don\'t have enough quackerinos to upgrade {action["amount"]} {action["item"]}s.')
                            global_info["task_queue"].pop(
                                troop_index)  # Remove this task
                            continue

                        # Remove the money
                        user["quackerinos"] -= cost

                        # Remove the troops from the garrison
                        await remove_unit(land["garrison"], unit, action["amount"])

                        # Add the upgraded troop to the garrison
                        new_unit = {"troop_name": troop["upgradesTo"], "amount": action["amount"], "user_id": action["user_id"]}
                        await add_unit(land["garrison"], new_unit)

                        # DM the results to the player
                        await dm(action["user_id"], f'{action["amount"]} {action["item"]}s were upgraded to {troop["upgradesTo"]}s at {land["name"]}\'s garrison.')

                        global_info["task_queue"].pop(
                            troop_index)  # Remove this task

                    else:
                        troop_index += 1
                top_tier -= 1
        else:
            index += 1

    index = 0

    # Execute all hire commands
    while index < len(global_info["task_queue"]):
        task = global_info["task_queue"][index]

        if task["task"] == "hire":
            user = user_info[str(task["user_id"])]
            troop = await get_troop(task["item"])
            land = lands.get(str(task["location_id"]), "")
            species = await get_species(troop["species"])

            # Fail if the specified land doesn't belong to that player
            if task["location_id"] not in user["land_ids"]:
                await dm(task["user_id"], f'You cannot hire {action["item"]} at {land["name"]} because that land doesn\'t belong to you.')
                global_info["task_queue"].pop(index)  # Remove this task
                continue

            # Get the amount that the land quality decreases by
            troop_counter = 0
            land_quality_penalty = 0
            quality_penalty_probability = species["qualityPenaltyProbabilityPerTroop"]

            while troop_counter < task["amount"] and quality_penalty_probability > 0:
                if not bool(troop["requiresSpeciesMatch"]):
                    break

                if random.random() < quality_penalty_probability:
                    land_quality_penalty += 1

                if land_quality_penalty >= land["quality"]:
                    task["amount"] = troop_counter
                    break

                troop_counter += 1

            cost = troop["cost"] * task["amount"]

            # Fail if not enough money
            if int(user["quackerinos"]) < cost:
                await dm(task["user_id"], f'You don\'t have enough quackerinos to upgrade {action["amount"]} {action["item"]}s.')
                global_info["task_queue"].pop(index)  # Remove this task
                continue

            # Remove the money
            user["quackerinos"] -= cost

            # Remove the land quality
            land["quality"] -= land_quality_penalty

            # Add the troops to the garrison
            new_unit = {"troop_name": task["item"], "amount": task["amount"], "user_id": task["user_id"]}
            await add_unit(land["garrison"], new_unit)

            # DM the results to the player
            await dm(task["user_id"], f'You hired {task["amount"]} {task["item"]}s at {land["name"]}\'s garrison.')

            global_info["task_queue"].pop(index)  # Remove this task
        else:
            index += 1

    index = 0

    # Execute all build commands
    while index < len(global_info["task_queue"]):
        task = global_info["task_queue"][index]

        if task["task"] == "build":
            user = user_info[str(task["user_id"])]
            land = lands.get(str(task["location_id"]), "")
            building = await get_building(task["item"])

            # Fail if the specified land doesn't belong to that player
            if task["location_id"] not in user["land_ids"]:
                await dm(task["user_id"], f'You cannot build {task["item"]} because {land["name"]} doesn\'t belong to you.')
                global_info["task_queue"].pop(index)  # Remove this task
                continue

            # Fail if the building has already been built on that land
            if task["item"] in land["buildings"]:
                await dm(task["user_id"], f'{task["item"]} has already been built at {land["name"]}.')
                global_info["task_queue"].pop(index)  # Remove this task
                continue

            # Fail if the building has to be upgraded to and is missing their requirement
            if bool(building["fromUpgradeOnly"]):
                requirement = False
                for building_x_name in land["buildings"]:
                    building_x = await get_building(building_x_name)
                    if building_x["upgradesTo"] == task["item"]:
                        requirement = True
                        break
                if not requirement:
                    await dm(task["user_id"], f'{task["item"]} needs to be built at {land["name"]} by upgrading a lower tier one.')
                    global_info["task_queue"].pop(index)  # Remove this task
                    continue

            # Fail if there is an upper tier building of this already
            upgradesTo = deepcopy(building["upgradesTo"])
            skip = False
            while upgradesTo != "":
                if upgradesTo in land["buildings"]:
                    await dm(task["user_id"], f'There is already an upper tier equivalent of {task["item"]} at {land["name"]}.')
                    global_info["task_queue"].pop(index)  # Remove this task
                    skip = True
                    break
                else:
                    next_building = await get_building(upgradesTo)
                    upgradesTo = deepcopy(next_building["upgradesTo"])
            if skip:
                continue

            # Only make the user pay at the beginning of the construction
            if task["time"] == building["constructionTime"]:
                cost = building["cost"]

                # Fail if not enough money
                if int(user["quackerinos"]) < cost:
                    await dm(task["user_id"], f'You don\'t have enough quackerinos to build {task["item"]} at {land["name"]}.')
                    global_info["task_queue"].pop(index)  # Remove this task
                    continue

                # Remove the money
                user["quackerinos"] -= cost

                await dm(task["user_id"], f'The labourers have started building {task["item"]} at {land["name"]}, costing {cost}')

            task["time"] -= 1

            if task["time"] <= 0:
                # Build the new building
                land["buildings"].append(task["item"])

                # Destroy the lower tier one if applicable
                if bool(building["fromUpgradeOnly"]):
                    for building_x_name in land["buildings"]:
                        building_x = await get_building(building_x_name)
                        if building_x["upgradesTo"] == task["item"]:
                            land["buildings"].remove(building_x_name)
                            break

                await dm(task["user_id"], f'{task["item"]} has been built at {land["name"]}.')
                global_info["task_queue"].pop(index)  # Remove this task
            else:
                index += 1
        else:
            index += 1

    index = 0
    
    # Remove all stale commands that aren't build commands
    while index < len(global_info["task_queue"]):
        if task["task"] != "build":
            global_info["task_queue"].pop(index)
        else:
            index += 1


    # Update the quality of all the lands
    for land_id, land in lands.items():
        if land_id == "default":
            continue

        maxQuality = lands["default"]["maxQuality"]

        for building_name in land["buildings"]:
            building = await get_building(building_name)
            maxQuality += building["maxQualityBonus"]

        land["maxQuality"] = maxQuality

    # Randomize the q-qq exchange rate
    global_info["qqExchangeRate"] = random.randint(int(
        global_info["qqExchangeRateRange"][0]), int(global_info["qqExchangeRateRange"][1]))

    # Add to the day counter and cycle the season accordingly
    global_info["day_counter"] += 1
    global_info["current_season"] = await get_season(global_info["day_counter"])

    global_info["first_attack"] = False

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    with open("./data/global_info.json", "w") as file:
        json.dump(global_info, file, indent=4)

    with open("./data/lands.json", "w") as file:
        json.dump(lands, file, indent=4)

    newday_message = f'A new day has arrived and the ducks feel refreshed from their slumber. The current season is: {global_info["current_season"]}'
    
    # Tell all users with daily reminder on about the update
    for user_id, user in user_info.items():
        if bool(user["daily_reminder"]):
            await dm(user_id, newday_message)
    
    # Tell all specified channels about the update
            
    with open("./data/server_info.json", "r") as file:
        server_info = json.load(file)
    
    for server_id, server in server_info.items():
        for channel_id in server["daily_channels"]:
            try:
                await client.get_channel(channel_id).send(newday_message)
            except:
                #print(f'Error trying to execute the new day to channel {channel_id}.')
                print()


@client.event
async def on_ready():
    await client.tree.sync()
    print("Bot is connected to Discord")
    dailyReset.start()


@client.tree.command(name="quack", description="Get your quack in for today.")
async def quack(interaction: discord.Interaction):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    user_id = interaction.user.id
    username = client.get_user(user_id)

    try:
        user = user_info[str(user_id)]

        if not bool(user["quackedToday"]):
            user["quackedToday"] = True
            user["quacks"] += 1
            user["quackStreak"] += 1

            if user["species"] == "penguin":
                message = f'{username}: noot noot!'
            # elif user_id == 712336169270116403:
            #     message = f'{username} did not deserve to quack today.'
            else:
                message = f'{username} quacked loudly.'

            if user["quackStreak"] >= global_info["maxQuackStreakLength"]:
                user["quackStreak"] -= global_info["maxQuackStreakLength"]
                user["quacks"] += global_info["quackStreakReward"]
                message += f'\n{username} finished a streak and got an extra {global_info["quackStreakReward"]} quacks.'
        else:
            message = f'{username} tried to quack but their throat is too sore today.'
    except:
        new_user = deepcopy(user_info["default"])
        user_info[user_id] = new_user
        message = f'{username} quacked for the first time!'

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, message)


@client.tree.command(name="pay", description="Give a player some quackerinos.")
async def pay(interaction: discord.Interaction, target_user_id: str, number: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the target player exists in user_info
    try:
        target = user_info[target_user_id]
        if user == target:
            await reply(interaction, "You can't give quackerinos to yourself.")
            return
        elif target_user_id == "default":
            await reply(interaction, "You can't give quackerinos to the default user.")
            return

    except:
        await reply(interaction, "Target has not quacked yet.")
        return

    # Make sure the player can't give negative quackerinos
    if number < 1:
        await reply(interaction, "Nice try.")
        return

    # Make sure the player can't give more quackerinos than they have
    try:
        if int(user["quackerinos"]) < number:
            await reply(interaction, "You don't have enough quackerinos for that.")
            return
    except:
        await reply(interaction, "You don't have enough quackerinos for that.")
        return

    # Give the other player quackerinos, but check if they have the quackerinos attribute yet
    target["quackerinos"] = target.get("quackerinos", 0) + number
    user["quackerinos"] -= number

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, f'You transferred {number} quackerinos to {client.get_user(int(target_user_id))}. They now have {target["quackerinos"]} qq and you now have {user["quackerinos"]} qq.')


@client.tree.command(name="buyqq", description="Trade in some of your quacks for quackerinos.")
async def buy_qq(interaction: discord.Interaction, quacks: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the player has enough quacks
    if int(user["quacks"]) - int(user["spentQuacks"]) < quacks:
        await reply(interaction, "You don't have enough quacks for that.")
        return

    user["spentQuacks"] += quacks
    result = int(global_info["qqExchangeRate"]) * quacks
    user["quackerinos"] = user.get("quackerinos", 0) + result

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    message = f'You bought {result} quackerinos using {quacks} quacks. You now have {user["quackerinos"]} qq and {user["quacks"]-user["spentQuacks"]} unspent quacks.'

    await reply(interaction, message)


@client.tree.command(name="qqrate", description="Check the current quacks-quackerino exchange rate.")
async def qq_rate(interaction: discord.Interaction):
    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    await reply(interaction, f'Currently 1 quack can buy {global_info["qqExchangeRate"]} quackerinos.')


@client.tree.command(name="quackery", description="Check out who are the top quackers.")
async def quackery(interaction: discord.Interaction, number: int = 10):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    top_list = "__**Top Quackers (:ballot_box_with_check: = quacked today)**__"

    for x in range(number):
        user_id = await get_max_quacks(user_info)

        if user_id == 0:
            break

        top_list += f'\n{client.get_user(int(user_id))} ({user_id}) --- {user_info[str(user_id)]["quacks"]}'

        if bool(user_info[str(user_id)]["quackedToday"]):
            top_list += f' :ballot_box_with_check:'
        
        user_info.pop(str(user_id))

    await reply(interaction, top_list)


# Return user id of the user with the most quacks
async def get_max_quacks(users):
    quacks = 0
    top_user_id = 0

    # Find the userId with the max quacks
    for userId, user in users.items():
        if userId == "default":
            continue

        if int(user["quacks"]) > quacks:
            quacks = int(user["quacks"])
            top_user_id = int(userId)

    return top_user_id


@client.tree.command(name="quackinfo", description="Check out the quack info of a user.")
async def quack_info(interaction: discord.Interaction, user_id: str = ""):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    if user_id == "":
        user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "That user has not quacked yet.")
        return

    try:
        message = f'{client.get_user(int(user_id))}'
        if user["quackRank"] != "":
            message += f' the {user["quackRank"]}'

        message += f' has quacked {user["quacks"]} times and is on a {user["quackStreak"]} day streak. '

        next_rank = await get_next_quack_rank(user["quackRank"])

        if next_rank != "":
            quacks = int(user["quacks"])
            next_quacks = int(global_info["quackRank"][next_rank])

            message += f'They are {next_quacks - quacks} quacks away from the next rank of {next_rank}. '

        message += f'They have spent {user.get("spentQuacks", 0)} quacks and have {user.get("quackerinos", 0)} quackerinos. '

        if user["homeland_id"] > 0:
            if user["homeland_id"] in user["land_ids"]:
                message += f'This user is in control of their homeland. '
            else:
                message += f'This user is not in control of their homeland. '

        if user["safety_count"] > 0:
            message += f'This user has {user["safety_count"]} turns left of safety protection. '

        if user["liege_id"] != 0:
            message += f'\nLiege: {client.get_user(int(user["liege_id"]))} (ID:{user["liege_id"]})'

        if len(user["ally_ids"]) > 0:
            message += f'\nAllies: '
            for ally_id in user["ally_ids"]:
                message += f'{client.get_user(int(ally_id))}, '
            message = message.rstrip()
            message = message.rstrip(",")

        has_vassals = False

        if user["taxPerVassalLand"] > 0:
            message += f'\nTax per vassal land: {user["taxPerVassalLand"]}'

        for target_id, target in user_info.items():
            if target["liege_id"] == user_id:
                if not has_vassals:
                    has_vassals = True
                    message += f'\nVassals: '
                message += f'{client.get_user(int(target_id))}, '
        message = message.rstrip()
        message = message.rstrip(",")

        if len(user["vassal_waitlist_ids"]) > 0:
            message += f'\nVassal waitlist: '
            for target_id in user["vassal_waitlist_ids"]:
                message += f'{client.get_user(int(target_id))}, '
        message = message.rstrip()
        message = message.rstrip(",")
        
        if user["spins"] > 0:
            message += f'\n\nSlot machine spins available: {user["spins"]}'

        for land_id in user["land_ids"]:
            land = await get_land(land_id)

            message += f'\n\n**{land["name"]} (ID:{land_id}) - {land["species"]}**'
            message += f'\nQuality: {land["quality"]}/{land["maxQuality"]}'
            message += f'\nBuildings: {land["buildings"]}'
            message += f'\nGarrison: '
            for unit in land["garrison"]:
                message += f'\n• {unit["amount"]} {unit["troop_name"]} ({client.get_user(int(unit["user_id"]))})'

            if land["siegeCamp"] != []:
                message += f'\nSiege camp: '
                for unit in land["siegeCamp"]:
                    message += f'\n• {unit["amount"]} {unit["troop_name"]} ({client.get_user(int(unit["user_id"]))})'

    except:
        message = 'Error while fetching user information.'

    await reply(interaction, message)


@client.tree.command(name="rawquackinfo", description="Check out the raw quack info of a user.")
async def raw_quack_info(interaction: discord.Interaction, user_id: str = ""):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    if user_id == "":
        user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "That user has not quacked yet.")
        return

    try:
        message = f'{user}'
    except:
        message = 'Error while fetching user information.'

    await reply(interaction, message)


@client.tree.command(name="landinfo", description="Check out the info on a certain land.")
async def land_info(interaction: discord.Interaction, land_id: int = 0, land_name: str = ""):
    # Fail if both fields are empty
    if land_id == 0 and land_name == "":
        await reply(interaction, "You need to put in either a land id or land name.")
        return

    land = await get_land(land_id)

    # Fail if land id is wrong/empty and land name is wrong/empty
    if land == "":
        land = await get_land_by_name(land_name)
        if land == "":
            await reply(interaction, "Land not found.")
            return

    land_id = await get_land_id(land)

    # Display the land info
    message = f'**{land["name"]} (ID: {land_id}) - {land["species"]}**'
    message += f'\nOwner: {client.get_user(int(land["owner_id"]))} (ID: {land["owner_id"]})'
    message += f'\nQuality: {land["quality"]}/{land["maxQuality"]}'
    message += f'\nBuildings: {land["buildings"]}'
    message += f'\nGarrison: '
    for unit in land["garrison"]:
        message += f'\n• {unit["amount"]} {unit["troop_name"]} ({client.get_user(int(unit["user_id"]))})'

    if land["siegeCamp"] != []:
        message += f'\nSiege camp: '
        for unit in land["siegeCamp"]:
            message += f'\n• {unit["amount"]} {unit["troop_name"]} ({client.get_user(int(unit["user_id"]))})'

    await reply(interaction, message)


@client.tree.command(name="taskqueue", description="Check out the task queue.")
async def view_task_queue(interaction: discord.Interaction):
    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    message = f'__**Task Queue**__'

    for task in global_info["task_queue"]:
        land = await get_land(task["location_id"])
        message += f'\n{client.get_user(int(task["user_id"]))} @ {land["name"]} ({task["location_id"]})'

        if task["target_land_id"] > 0:
            target = await get_land(task["target_land_id"])
            message += f' → {target["name"]} ({task["target_land_id"]})'

        message += f': {task["task"]}'

        if task["amount"] > 1:
            message += f' {task["amount"]} {task["item"]}s'
        else:
            message += f' {task["item"]}'

        if task["time"] > 1:
            message += f' (turns remaining: {task["time"]})'

    await reply(interaction, message)


@client.tree.command(name="help", description="See the guide.")
async def help(interaction: discord.Interaction):
    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    message = global_info["help_message"]

    await reply(interaction, message)


@client.tree.command(name="mischief", description=">:)")
async def mischief(interaction: discord.Interaction, target_user_id: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id
    username = client.get_user(user_id)
    
    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return
    
    # Fail if the user's species isn't mischievous
    species = await get_species(user["species"])

    if not bool(species["mischief"]):
        await reply(interaction, "I don't know what command you are referring to.")
        return
    
    # Make sure the target player exists in user_info
    try:
        target = user_info[target_user_id]
        target_username = client.get_user(int(target_user_id))
        if user == target:
            await reply(interaction, "You can't do that to yourself.")
            return
        elif target_user_id == "default":
            await reply(interaction, "You can't do that to the default user.")
            return
    except:
        await reply(interaction, "Target has not quacked yet.")
        return
    
    # Fail if the user has already done mischief today
    if bool(user["mischief"]):
        await reply(interaction, "Woah there. That's enough mischief for one day.")
        return
    
    #Fail if the target has no qq left
    if target["quackerinos"] <= 0:
        await reply(interaction, f'You couldn\'t find any quackerinos on {target_username} so you beat them up instead.')
        await dm(target_user_id, 'You got beaten up by the raccoon :/')
        return
    
    #Steal 1qq from the target
    target["quackerinos"] -= 1
    user["quackerinos"] += 1
    user["mischief"] = True

    #Send a random message
    with open("./data/mischief.txt", "r") as file:
        randomresponses = file.readlines()
        response = random.choice(randomresponses)
    
    await dm(target_user_id, response)
    
    message = f'You helped lighten {target_username}\'s purse by 1qq.'

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, message)


async def get_quack_rank(quacks):
    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    quack_rank = ""

    for rank, requirement in global_info["quackRank"].items():
        if int(quacks) >= int(requirement):
            quack_rank = rank

    return quack_rank


async def get_next_quack_rank(quack_rank):
    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    next_quack_rank = ""

    try:
        current_quacks = int(global_info["quackRank"][quack_rank])
    except:
        current_quacks = 0

    for rank, requirement in global_info["quackRank"].items():
        # Check greater than current quack rank requirement but lower/eq to target quack rank
        if requirement > current_quacks and (next_quack_rank == "" or requirement < int(global_info["quackRank"][next_quack_rank])):
            next_quack_rank = rank

    return next_quack_rank


@client.tree.command(name="homeland", description="Establish a new homeland for you and your people.")
async def establish_homeland(interaction: discord.Interaction, name: str, species_name: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the species exists and is enabled
    species = await get_species(species_name)
    if species != "":
        if not bool(species["enabled"]) and user_id != 693257736867020870:
            await reply(interaction, "This species is not enabled.")
            return
    else:
        await reply(interaction, "Species not found.")
        return

    # Make sure this player hasn't made a homeland already
    if user.get("homeland_id", -1) >= 0:
        await reply(interaction, "You already have a homeland.")
        return
    
    #Make sure this player can't make a homeland if they have already chosen a species without a homeland
    if user["species"] != "":
        await reply(interaction, "You have already chosen a species without a homeland.")
        return
    
    #If this player chooses a mischief species don't let them have a homeland
    if bool(species["mischief"]):
        #If not sprout then fail
        if user_id != 693257736867020870:
            await reply(interaction, "I don't know which species you are talking about.")
            return

        user["species"] = species_name
        
        # Save to database
        with open("./data/user_info.json", "w") as file:
            json.dump(user_info, file, indent=4)

        await reply(interaction, "You try to establish a homeland, but you realize that the joys in life come from mischievous adventures in others' homelands. You have chosen the path of the raccoon.")
        return

    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    # Create the new land
    try:
        new_land = deepcopy(lands["default"])
        new_land["name"] = name
        new_land["owner_id"] = user_id
        new_land["species"] = species_name
        new_land_id = global_info["landCounter"] + 1

        lands[new_land_id] = new_land

        user["homeland_id"] = new_land_id
        user["species"] = species_name
        user["land_ids"] = [new_land_id]

        global_info["landCounter"] += 1
        message = 'New land created'

        # Save to database
        with open("./data/user_info.json", "w") as file:
            json.dump(user_info, file, indent=4)

        # Save to database
        with open("./data/global_info.json", "w") as file:
            json.dump(global_info, file, indent=4)

        # Save to database
        with open("./data/lands.json", "w") as file:
            json.dump(lands, file, indent=4)
    except:
        message = 'There was an error trying to add the new land.'

    await reply(interaction, message)


@client.tree.command(name="species", description="View all the enabled species.")
async def list_species(interaction: discord.Interaction):
    with open("./data/species.json", "r") as file:
        species_list = json.load(file)

    message = f'**List of Playable Species**'

    for species_name, species in species_list.items():
        if bool(species.get("enabled", species_list["default"].get("enabled"))):
            message += f'\n{species_name}: {species.get("description", species)}'

    await reply(interaction, message)


@client.tree.command(name="buildings", description="View all the buildings that can be built.")
async def list_buildings(interaction: discord.Interaction):
    with open("./data/buildings.json", "r") as file:
        buildings = json.load(file)

    message = f'__**All Buildings**__'

    for building_name, building in buildings.items():
        if bool(building.get("enabled", buildings["default"].get("enabled"))):
            # message += f'\n{building_name}: {building.get("description", building)}'
            message += f'\n**{building_name}:** '
            for key, value in building.items():
                if key != "enabled":
                    message += f'{key}: {value}; '

    await reply(interaction, message)


@client.tree.command(name="troops", description="View all the troops that can be hired.")
async def list_troops(interaction: discord.Interaction, species_name: str):
    with open("./data/troops.json", "r") as file:
        troops = json.load(file)

    message = f'__**All Troops**__'

    for troop_name, troop in troops.items():
        if troop["species"] == species_name or troop["species"] == "":
            message += f'\n**{troop_name}:** '
            for key, value in troop.items():
                if value != "":
                    message += f'{key}: {value}; '

    await reply(interaction, message)


@client.tree.command(name="listlands", description="List all the lands currently in the game.")
async def list_lands(interaction: discord.Interaction):
    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    message = ""

    for land_id, land in lands.items():
        if land_id == "default":
            continue

        species = await get_species(land["species"])
        species_emoji = species.get("emoji", land["species"])
        total_defenders = 0
        total_attackers = 0

        for unit in land["garrison"]:
            total_defenders += unit["amount"]

        for unit in land["siegeCamp"]:
            total_attackers += unit["amount"]

        message += f'[ID: {land_id}] [{client.get_user(land["owner_id"])}] {land["name"]} - {species_emoji} | :coin: {land["quality"]}/{land["maxQuality"]}'

        if len(land["buildings"]) > 0:
            message += f' | :homes: {len(land["buildings"])}'

        if total_defenders > 0:
            message += f' | :shield: {total_defenders}'

        if total_attackers > 0:
            message += f' | :crossed_swords: {total_attackers}'

        if await is_surrounded(land):
            message += f' | :triangular_flag_on_post:'
        
        message += f'\n'

    await reply(interaction, message)


@client.tree.command(name="build", description="Build a new building in one of your lands (takes one month).")
async def build(interaction: discord.Interaction, location_id: int, building_name: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    building = await get_building(building_name)
    land = await get_land(location_id)

    # Fail if building doesn't exist
    if building == "" or not bool(building["enabled"]):
        await reply(interaction, 'Building not found.')
        return

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    # Fail if the specified land doesn't belong to that player
    if location_id not in user["land_ids"]:
        await reply(interaction, 'That land doesn\'t belong to you.')
        return

    # Fail if the building has already been built on that land
    if building_name in land["buildings"]:
        await reply(interaction, 'That building has already been built there.')
        return

    # Fail if the building has to be upgraded to and is missing their requirement
    if bool(building["fromUpgradeOnly"]):
        requirement = False
        for building_x_name in land["buildings"]:
            building_x = await get_building(building_x_name)
            if building_x["upgradesTo"] == building_name:
                requirement = True
                break
        if not requirement:
            await reply(interaction, 'That building needs to be built by upgrading a lower tier one.')
            return

    # Add it to the queue
    await add_to_queue(user_id, "build", building_name, location_id, time=building["constructionTime"])

    await reply(interaction, f'{client.get_user(user_id)} has started building a {building_name} at {land["name"]}.')


@client.tree.command(name="demolish", description="Destroy a building in one of your lands.")
async def demolish(interaction: discord.Interaction, location_id: int, building_name: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    building = await get_building(building_name)
    land = lands.get(str(location_id), "")

    # Fail if building doesn't exist
    if building == "":
        await reply(interaction, 'Building not found.')
        return

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    # Fail if the specified land doesn't belong to that player
    if location_id not in user["land_ids"]:
        await reply(interaction, 'That land doesn\'t belong to you.')
        return

    # Fail if that building has not been built on that land yet
    if building_name not in land["buildings"]:
        await reply(interaction, 'That building has not been built there yet.')
        return

    # Remove the building from that land and give the user a percent of the money
    land["buildings"].remove(building_name)
    refund = building["refundPercent"] * building["cost"]
    user["quackerinos"] += refund

    # Add the lower tier building if necessary
    if building["demolishedTo"] != "":
        land["buildings"].append(building["demolishedTo"])
        message = f'The {building_name} was demolished into a {building["demolishedTo"]} and you were refunded {refund} qq.'
    else:
        message = f'The {building_name} was destroyed and you were refunded {refund} qq.'

    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    with open("./data/lands.json", "w") as file:
        json.dump(lands, file, indent=4)

    await reply(interaction, message)


@client.tree.command(name="hire", description="Hire some troops (takes one month).")
async def hire(interaction: discord.Interaction, location_id: int, troop_name: str, amount: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    troop = await get_troop(troop_name)
    # land = lands.get("location_id", "")
    land = await get_land(location_id)

    # Fail if troop doesn't exist
    if troop == "" or troop == "default_tier1":
        await reply(interaction, 'Troop not found.')
        return

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    # Fail if the specified land doesn't belong to that player
    if location_id not in user["land_ids"]:
        await reply(interaction, 'That land doesn\'t belong to you.')
        return

    # Fail if that troop doesn't match the species of the land
    if bool(troop["requiresSpeciesMatch"]) and troop["species"] != land["species"]:
        await reply(interaction, 'You can\'t hire that troop there.')
        return

    # Fail if that troop requires upgrading and can't be hired directly
    if bool(troop["fromUpgradeOnly"]):
        await reply(interaction, 'That troop requires that you upgrade from a lower tier.')
        return

    # Fail if the land quality is 0
    if land["quality"] <= 0 and bool(troop["requiresSpeciesMatch"]):
        await reply(interaction, 'You cannot hire troops from a land that has zero quality.')
        return

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    current_time = datetime.datetime.now(tz=datetime.timezone.utc).time()
    daily_reset_time = current_time.replace(hour=12,minute=0,second=0,microsecond=0)
    attack_cutoff_time = current_time.replace(hour=4,minute=0,second=0,microsecond=0)

    # Fail if it is too late in the day for the first attack
    if not bool(global_info["first_attack"]) and daily_reset_time > current_time > attack_cutoff_time:
        await reply(interaction, f'You cannot be the first to hire troops/attack someone 8 hours before the daily reset time.')
        return
    else:
        if not bool(global_info["first_attack"]):
            global_info["first_attack"] = True
            
            with open("./data/global_info.json", "w") as file:
                json.dump(global_info, file, indent=4)

    # Add the task to the queue
    await add_to_queue(user_id, "hire", troop_name, location_id, amount)

    await reply(interaction, f'You have started to hire {amount} {troop_name}s in {land["name"]}.')


@client.tree.command(name="upgrade", description="Upgrade some troops (takes one month).")
async def upgrade(interaction: discord.Interaction, location_id: int, troop_name: str, amount: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    troop = await get_troop(troop_name)
    # land = lands.get("location_id", "")
    land = await get_land(location_id)

    # Fail if troop doesn't exist
    if troop == "":
        await reply(interaction, 'Troop not found.')
        return

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    # Fail if the specified land doesn't belong to that player
    if location_id not in user["land_ids"]:
        await reply(interaction, 'That land doesn\'t belong to you.')
        return

    # Fail if that troop can't be upgraded
    if troop["upgradesTo"] == "":
        await reply(interaction, 'That troop can\'t be upgraded.')
        return

    unit = await get_unit(land["garrison"], troop_name, user_id)

    # Fail if that troop isn't in that land or if there aren't as many as specified
    if unit == "" or unit["amount"] < amount:
        await reply(interaction, f'You don\'t have enough of that troop to upgrade {amount} of them.')
        return

    # Add the task to the queue
    await add_to_queue(user_id, "upgrade", troop_name, location_id, amount)

    await reply(interaction, f'You have started to upgrade {amount} {troop_name}s in {land["name"]}.')


@client.tree.command(name="disband", description="Disband some of your troops.")
async def disband(interaction: discord.Interaction, location_id: int, troop_name: str, amount: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    troop = await get_troop(troop_name)
    land = lands.get(str(location_id), "")
    species = await get_species(troop["species"])

    # Fail if troop doesn't exist
    if troop == "":
        await reply(interaction, 'Troop not found.')
        return

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    unit = await get_unit(land["garrison"], troop_name, user_id)

    # Fail if that troop isn't in that land or if there aren't as many as specified
    if unit == "" or unit["amount"] < amount:
        await reply(interaction, f'You don\'t have enough of that troop to disband {amount} of them.')
        return
    
    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    quality_gain_probability = species["qualityReplenishProbabilityPerTroop"]
    quality_gain = 0

    #If disbanding troops on one of your lands, with matching species, and if that species has qualityReplenishProbabilityPerTroop then replenish that land's quality
    if location_id in user["land_ids"] and troop["species"] == land["species"] and quality_gain_probability > 0 and land["quality"] < land["maxQuality"]:
        for x in range(amount):
            if random.random() < quality_gain_probability:
                quality_gain += 1
        
        land["quality"] += quality_gain
        land["quality"] = min(land["maxQuality"], land["quality"])

    # Remove troops from that user's land
    unit["amount"] -= amount

    if unit["amount"] == 0:
        land["garrison"].remove(unit)

    # Give refund to user if necessary
    refund = troop["refundPercentOnDisband"] * troop["cost"] * amount
    user["quackerinos"] += refund

    message = f'{amount} {troop_name}s were disbanded. '

    if refund > 0:
        message += f'{refund} qq were refunded to the user. '

    if quality_gain > 0:
        message += f'{quality_gain} land quality was replenished at {land["name"]}. '

    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    with open("./data/lands.json", "w") as file:
        json.dump(lands, file, indent=4)

    await reply(interaction, message)


@client.tree.command(name="attack", description="Launch an assault on someone's land/castle (takes one month).")
async def attack(interaction: discord.Interaction, location_id: int, troop_name: str, amount: int, target_land_id: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Prevent this player from using this action if they still are in the safety period
    if user["safety_count"] > 0:
        await reply(interaction, "You cannot use this action during your safety period.")
        return

    land = await get_land(location_id)
    target_land = await get_land(target_land_id)

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    # Fail if the other land doesn't exist
    if target_land == "":
        await reply(interaction, 'Target land doesn\'t exist.')
        return

    target_user = user_info[str(target_land["owner_id"])]

    # Prevent this player from attacking someone who is in their safety period
    if target_user["safety_count"] > 0:
        await reply(interaction, "You cannot use this action against someone who is still in their safety period.")
        return

    # Prevent this player from using this action if they still are in the safety period
    if user["safety_count"] > 0:
        await reply(interaction, "You cannot use this action during your safety period.")
        return

    unit = await get_unit(land["siegeCamp"], troop_name, user_id)

    # Fail if that troop isn't in that land or if there aren't as many as specified
    if unit == "" or unit["amount"] < amount:
        unit = await get_unit(land["garrison"], troop_name, user_id)
        if unit == "" or unit["amount"] < amount:
            await reply(interaction, f'You don\'t have enough of that troop from that location to send on an attack.')
            return

    # Fail if the target land is yours
    if target_land["owner_id"] == user_id:
        await reply(interaction, f'You can\'t attack yourself.')
        return

    allies = await get_allies(user_id)

    # Fail if the target is one of your allies
    if str(target_land["owner_id"]) in allies:
        await reply(interaction, f'You can\'t attack this person for one of the following reasons: they are your liege, fellow vassal, your vassal, your ally, or a vassal of your ally.')
        return

    # Fail if the your land is already surrounded
    if await is_surrounded(land) and land != target_land:
        await reply(interaction, f'You cannot move troops out of {land["name"]} because it is fully surrounded.')
        return

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    troop = await get_troop(troop_name)
    species = await get_species(troop["species"])

    # Fail if the troop can't move during this season
    if not bool(species["canAttack"]):
        await reply(interaction, f'You cannot move {troop["species"]} troops out of {land["name"]} during the {global_info["current_season"]}.')
        return
    
    current_time = datetime.datetime.now(tz=datetime.timezone.utc).time()
    daily_reset_time = current_time.replace(hour=12,minute=0,second=0,microsecond=0)
    attack_cutoff_time = current_time.replace(hour=4,minute=0,second=0,microsecond=0)

    # Fail if it is too late in the day for the first attack
    if not bool(global_info["first_attack"]) and daily_reset_time > current_time > attack_cutoff_time:
        await reply(interaction, f'You cannot be the first to hire troops/attack someone 8 hours before the daily reset time.')
        return
    else:
        if not bool(global_info["first_attack"]):
            global_info["first_attack"] = True
            
            with open("./data/global_info.json", "w") as file:
                json.dump(global_info, file, indent=4)

    # Add the task to the queue and alert the defender
    await add_to_queue(user_id, "attack", troop_name, location_id, amount, target_land=target_land_id)
    await dm(target_land["owner_id"], f'{client.get_user(int(user_id))} has sent {amount} {troop_name}s to attack {target_land["name"]}!')

    message = f'{amount} {troop_name}s were sent to attack {target_land["name"]}.'

    await reply(interaction, message)


@client.tree.command(name="defend", description="Defend someone's land/castle from an incoming assault (takes one month).")
async def defend(interaction: discord.Interaction, location_id: int, troop_name: str, amount: int, target_land_id: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Prevent this player from using this action if they still are in the safety period
    if user["safety_count"] > 0:
        await reply(interaction, "You cannot use this action during your safety period.")
        return

    land = await get_land(location_id)
    target_land = await get_land(target_land_id)

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    unit = await get_unit(land["siegeCamp"], troop_name, user_id)

    # Fail if that troop isn't in that land or if there aren't as many as specified
    if unit == "" or unit["amount"] < amount:
        unit = await get_unit(land["garrison"], troop_name, user_id)
        if unit == "" or unit["amount"] < amount:
            await reply(interaction, f'You don\'t have enough of that troop from that location to send on an attack.')
            return

    # Fail if the other land doesn't exist
    if target_land == "":
        await reply(interaction, 'Target land doesn\'t exist.')
        return

    # Fail if they are both the same land
    if location_id == target_land_id:
        await reply(interaction, 'You don\'t need to use this command for troops in the garrison of a land being attacked.')
        return

    # Fail if the your land is already surrounded
    if await is_surrounded(land):
        await reply(interaction, f'You cannot move troops out of {land["name"]} because it is fully surrounded.')
        return

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    troop = await get_troop(troop_name)
    species = await get_species(troop["species"])

    # Fail if the troop can't move during this season
    if not bool(species["canAttack"]):
        await reply(interaction, f'You cannot move {troop["species"]} troops out of {land["name"]} during the {global_info["current_season"]}.')
        return

    # Add the task to the queue
    await add_to_queue(user_id, "defend", troop_name, location_id, amount, target_land=target_land_id)

    message = f'{amount} {troop_name}s were sent to defend {target_land["name"]}.'

    await reply(interaction, message)


@client.tree.command(name="siege", description="Initiate or join a siege on someone's land (takes one month).")
async def siege(interaction: discord.Interaction, location_id: int, troop_name: str, amount: int, target_land_id: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Prevent this player from using this action if they still are in the safety period
    if user["safety_count"] > 0:
        await reply(interaction, "You cannot use this action during your safety period.")
        return

    land = await get_land(location_id)
    target_land = await get_land(target_land_id)

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    # Fail if the other land doesn't exist
    if target_land == "":
        await reply(interaction, 'Target land doesn\'t exist.')
        return

    target_user = user_info[str(target_land["owner_id"])]

    # Prevent this player from attacking someone who is in their safety period
    if target_user["safety_count"] > 0:
        await reply(interaction, "You cannot use this action against someone who is still in their safety period.")
        return

    unit = await get_unit(land["siegeCamp"], troop_name, user_id)

    # Fail if that troop isn't in that land or if there aren't as many as specified
    if unit == "" or unit["amount"] < amount:
        unit = await get_unit(land["garrison"], troop_name, user_id)
        if unit == "" or unit["amount"] < amount:
            await reply(interaction, f'You don\'t have enough of that troop from that location to send to the siege camp.')
            return

    # Fail if the target land is yours
    if target_land["owner_id"] == user_id:
        await reply(interaction, f'You can\'t siege yourself.')
        return

    allies = await get_allies(user_id)

    # Fail if the target is one of your allies
    if str(target_land["owner_id"]) in allies:
        await reply(interaction, f'You can\'t siege this person for one of the following reasons: they are your liege, fellow vassal, your vassal, your ally, or a vassal of your ally.')
        return

    # Fail if the your land is already surrounded
    if await is_surrounded(land):
        await reply(interaction, f'You cannot move troops out of {land["name"]} because it is fully surrounded.')
        return

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    troop = await get_troop(troop_name)
    species = await get_species(troop["species"])

    # Fail if the troop can't move during this season
    if not bool(species["canMove"]):
        await reply(interaction, f'You cannot move {troop["species"]} troops out of {land["name"]} during the {global_info["current_season"]}.')
        return

    # Add the task to the queue
    await add_to_queue(user_id, "siege", troop_name, location_id, amount, target_land=target_land_id)

    message = f'{amount} {troop_name}s were sent to siege {target_land["name"]}.'

    await reply(interaction, message)


@client.tree.command(name="sallyout", description="Launch an assault on a siege camp (takes one month).")
async def sallyout(interaction: discord.Interaction, location_id: int, troop_name: str, amount: int, target_land_id: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Prevent this player from using this action if they still are in the safety period
    if user["safety_count"] > 0:
        await reply(interaction, "You cannot use this action during your safety period.")
        return

    land = await get_land(location_id)
    target_land = await get_land(target_land_id)

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    # Fail if the other land doesn't exist
    if target_land == "":
        await reply(interaction, 'Target land doesn\'t exist.')
        return

    unit = await get_unit(land["siegeCamp"], troop_name, user_id)

    # Fail if that troop isn't in that land or if there aren't as many as specified
    if unit == "" or unit["amount"] < amount:
        unit = await get_unit(land["garrison"], troop_name, user_id)
        if unit == "" or unit["amount"] < amount:
            await reply(interaction, f'You don\'t have enough of that troop from that location to send on an attack.')
            return

    # Fail if the your land is already surrounded
    if await is_surrounded(land) and land != target_land:
        await reply(interaction, f'You cannot move troops out of {land["name"]} because it is fully surrounded.')
        return

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    troop = await get_troop(troop_name)
    species = await get_species(troop["species"])

    # Fail if the troop can't move during this season
    if not bool(species["canMove"]):
        await reply(interaction, f'You cannot move {troop["species"]} troops out of {land["name"]} during the {global_info["current_season"]}.')
        return

    # Add the task to the queue
    await add_to_queue(user_id, "sallyout", troop_name, location_id, amount, target_land=target_land_id)

    message = f'{amount} {troop_name}s were sent to attack the siege camp at {target_land["name"]}.'

    await reply(interaction, message)


@client.tree.command(name="move", description="Move troops to one of your or an ally's garrisons (takes one month).")
async def move(interaction: discord.Interaction, location_id: int, troop_name: str, amount: int, target_land_id: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Prevent this player from using this action if they still are in the safety period
    if user["safety_count"] > 0:
        await reply(interaction, "You cannot use this action during your safety period.")
        return

    land = await get_land(location_id)
    target_land = await get_land(target_land_id)

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    # Fail if the other land doesn't exist
    if target_land == "":
        await reply(interaction, 'Target land doesn\'t exist.')
        return

    unit = await get_unit(land["siegeCamp"], troop_name, user_id)

    # Fail if that troop isn't in that land or if there aren't as many as specified
    if unit == "" or unit["amount"] < amount:
        # Fail if they are both the same land
        if location_id == target_land_id:
            await reply(interaction, 'The developers stopped you from taking a useless action.')
            return
        
        unit = await get_unit(land["garrison"], troop_name, user_id)
        if unit == "" or unit["amount"] < amount:
            await reply(interaction, f'You don\'t have enough of that troop from that location to send to {target_land["name"]}.')
            return


    allies = await get_allies(user_id)

    # Fail if the target land isn't yours or an ally's
    if target_land["owner_id"] != user_id and str(target_land["owner_id"]) not in allies:
        await reply(interaction, f'You can only move troops to lands that belong to you, your liege, fellow vassal, your vassal, your ally, or a vassal of your ally.')
        return

    # Fail if the your land is already surrounded
    if await is_surrounded(land):
        await reply(interaction, f'You cannot move troops out of {land["name"]} because it is fully surrounded.')
        return

    # Fail if the target land is already surrounded
    if await is_surrounded(target_land):
        await reply(interaction, f'You cannot move troops into the garrrison of {target_land["name"]} because it is fully surrounded.')
        return

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    troop = await get_troop(troop_name)
    species = await get_species(troop["species"])

    # Fail if the troop can't move during this season
    if not bool(species["canMove"]):
        await reply(interaction, f'You cannot move {troop["species"]} troops out of {land["name"]} during the {global_info["current_season"]}.')
        return

    # Add the task to the queue
    await add_to_queue(user_id, "move", troop_name, location_id, amount, target_land=target_land_id)

    message = f'{amount} {troop_name}s were sent to {target_land["name"]}\'s garrison.'

    await reply(interaction, message)


@client.tree.command(name="support", description="Lend your support to another player to improve one of their land's income by 10%.")
async def support(interaction: discord.Interaction, target_user_id: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the target player exists in user_info
    try:
        target = user_info[target_user_id]
        if user == target:
            await reply(interaction, "You can't give support to yourself.")
            return
        elif target_user_id == "default":
            await reply(interaction, "You can't give support to the default user.")
            return
    except:
        await reply(interaction, "Target has not quacked yet.")
        return

    # Make sure this user has already had a homeland
    if user["homeland_id"] == -1:
        await reply(interaction, "You cannot use this command without a homeland.")
        return

    # Make sure this user has no lands
    if len(user["land_ids"]) > 0:
        await reply(interaction, "You cannot use this command if you already have lands.")
        return

    # Make sure the user hasn't supported anyone yet
    if user["supportee_id"] == 0:
        await reply(interaction, "You can only use this command once per day.")
        return

    target["support"] += 1
    user["supportee_id"] = target_user_id

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, f'You have lent your support to {client.get_user(int(target_user_id))}.')


@client.tree.command(name="giveland", description="Give your occupied land to another player.")
async def give_land(interaction: discord.Interaction, location_id: int, target_user_id: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the target player exists in user_info
    try:
        target = user_info[target_user_id]
        if user == target:
            await reply(interaction, "You can't give support to yourself.")
            return
        elif target_user_id == "default":
            await reply(interaction, "You can't give support to the default user.")
            return
    except:
        await reply(interaction, "Target has not quacked yet.")
        return

    land = lands.get(str(location_id), "")

    # Fail if the specified land doesn't exist
    if land == "":
        await reply(interaction, 'Land not found.')
        return

    # Fail if the specified land doesn't belong to that player
    if location_id not in user["land_ids"]:
        await reply(interaction, 'That land doesn\'t belong to you.')
        return

    # Fail if this is the user's homeland
    if location_id == user["homeland_id"]:
        await reply(interaction, 'You cannot give your homeland away.')
        return

    # Prevent this player from giving the land to someone who is in their safety period
    if target["safety_count"] > 0:
        await reply(interaction, "You cannot give lands to a protected user.")
        return

    # Give the land to the target user
    user["land_ids"].remove(location_id)
    target["land_ids"].append(location_id)
    land["owner_id"] = int(target_user_id)

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    with open("./data/lands.json", "w") as file:
        json.dump(lands, file, indent=4)

    await reply(interaction, f'You have given control of {land["name"]} to {client.get_user(int(target_user_id))}.')


@client.tree.command(name="addally", description="Add a user to your ally list.")
async def add_ally(interaction: discord.Interaction, target_user_id: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the target player exists in user_info
    try:
        target = user_info[target_user_id]
        if user == target:
            await reply(interaction, "You can't ally yourself.")
            return
        elif target_user_id == "default":
            await reply(interaction, "You can't ally with the default user.")
            return
    except:
        await reply(interaction, "Target has not quacked yet.")
        return

    # Fail if target user already is in your ally list
    if target_user_id in user["ally_ids"]:
        await reply(interaction, f'You have already allied with that person. Your ally list is: {user["ally_ids"]}')
        return

    user["ally_ids"].append(target_user_id)

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, f'You have added {client.get_user(int(target_user_id))} to your allylist. Your ally list is now: {user["ally_ids"]}')


@client.tree.command(name="removeally", description="Remove a user to your ally list.")
async def remmove_ally(interaction: discord.Interaction, target_user_id: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the target player exists in user_info
    try:
        target = user_info[target_user_id]
    except:
        await reply(interaction, "Target has not quacked yet.")
        return

    # Fail if target user is not in your ally list
    if target_user_id not in user["ally_ids"]:
        await reply(interaction, f'You aren\'t allied with that person. Your ally list is: {user["ally_ids"]}')
        return

    user["ally_ids"].remove(target_user_id)

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, f'You have removed {client.get_user(int(target_user_id))} from your allylist. Your ally list is now: {user["ally_ids"]}')


@client.tree.command(name="declareallegiance", description="Declare your allegiance to a user.")
async def declare_allegiance(interaction: discord.Interaction, target_user_id: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the target player exists in user_info
    try:
        target = user_info[target_user_id]
        if user == target:
            await reply(interaction, "You can't declare allegiance to yourself.")
            return
        elif target_user_id == "default":
            await reply(interaction, "You can't declare allegiance to  the default user.")
            return
    except:
        await reply(interaction, "Target has not quacked yet.")
        return

    # Fail if target user already is in your ally list
    if target_user_id == user["liege_id"]:
        await reply(interaction, f'This user is already your liege.')
        return

    # Fail if user already has a liege
    if user["liege_id"] != 0:
        await reply(interaction, f'You already have a liege. You must renounce your oath before declaring your allegiance to someone else.')
        return

    # Fail if user already on the target user's vassal waitlist
    if user_id in target["vassal_waitlist_ids"]:
        await reply(interaction, f'You already are on this person\'s vassal waitlist. You must wait until they accept your allegiance.')
        return

    target["vassal_waitlist_ids"].append(user_id)

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, f'You have added yourself to {client.get_user(int(target_user_id))}\'s vassal waitlist. You must wait until they accept your allegiance.')


@client.tree.command(name="acceptallegiance", description="Accept an oath of allegiance from a user.")
async def accept_allegiance(interaction: discord.Interaction, target_user_id: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the target player exists in user_info
    try:
        target = user_info[target_user_id]
    except:
        await reply(interaction, "Target has not quacked yet.")
        return

    # Fail if target user has already been accepted as a vassal
    if user_id == target["liege_id"]:
        user["vassal_waitlist_ids"].remove(int(target_user_id))

        # Save to database
        with open("./data/user_info.json", "w") as file:
            json.dump(user_info, file, indent=4)

        await reply(interaction, f'This user is already your vassal.')
        return

    # Fail if target user already has a liege
    if target["liege_id"] != 0:
        user["vassal_waitlist_ids"].remove(int(target_user_id))

        # Save to database
        with open("./data/user_info.json", "w") as file:
            json.dump(user_info, file, indent=4)

        await reply(interaction, f'This user already has a liege. They must renounce your oath before declaring their allegiance to someone else.')
        return
    
    user["vassal_waitlist_ids"].remove(int(target_user_id))
    target["liege_id"] = user_id

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, f'You have accepted {client.get_user(int(target_user_id))}\'s oath of allegiance.')


@client.tree.command(name="releasevassal", description="Release one of your vassals from their oath of allegiance.")
async def release_vassal(interaction: discord.Interaction, target_user_id: str):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the target player exists in user_info
    try:
        target = user_info[target_user_id]
    except:
        await reply(interaction, "Target has not quacked yet.")
        return

    # Fail if target user does not have this user as their liege
    if user_id != target["liege_id"]:
        await reply(interaction, "Target user is not your vassal.")
        return

    target["liege_id"] = 0

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, f'You have released {client.get_user(int(target_user_id))} from their oath of allegiance.')


@client.tree.command(name="renounceallegiance", description="Renounce your allegiance to your liege. THERE WILL BE CONSEQUENCES.")
async def renounce_allegiance(interaction: discord.Interaction):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the target player exists in user_info
    try:
        target_user_id = user["liege_id"]
        target = user_info[str(target_user_id)]
    except:
        await reply(interaction, "Target has not quacked yet.")
        return

    # Fail if this user does not have a liege
    if user["liege_id"] == 0:
        await reply(interaction, "You don't have a liege.")
        return

    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    # Fail if this user doesn't have the required money to renounce allegiance
    if user["quackerinos"] < global_info["qq_requirement_to_renounce"]:
        await reply(interaction, f'You don\'t have the required funds ({global_info["qq_requirement_to_renounce"]}) to renounce allegiance.')
        return

    # Disband all deserting troops
    for land_id, land in lands.items():
        if land_id == "default":
            continue

        # Check the garrison for deserters
        for unit in land["garrison"]:
            user = user_info[str(unit["user_id"])]
            troop = await get_troop(unit["troop_name"])
            species = await get_species(troop["species"])

            percent_desert = species["percentDesertsOnOathbreaker"]
            total_amount = unit["amount"]
            num_desert = 0
            if percent_desert > 0:
                for x in range(unit["amount"]):
                    if random.random() > percent_desert:
                        num_desert += 1

                unit["amount"] -= num_desert

                # DM user that units have been disbanded
                await dm(unit["user_id"], f'{num_desert}/{total_amount} of {unit["troop_name"]} have been disbanded at {land["name"]} because of your oath breaking.')

        # Disband empty units from the garrison
        index = 0
        while index < len(land["garrison"]):
            if land["garrison"][index]["amount"] <= 0:
                land["garrison"].pop(index)
            else:
                index += 1

        # Check the siegecamp for deserters
        for unit in land["siegeCamp"]:
            user = user_info[str(unit["user_id"])]
            troop = await get_troop(unit["troop_name"])
            species = await get_species(troop["species"])

            percent_desert = species["percentDesertsOnOathbreaker"]
            total_amount = unit["amount"]
            num_desert = 0
            if percent_desert > 0:
                for x in range(unit["amount"]):
                    if random.random() > percent_desert:
                        num_desert += 1

                unit["amount"] -= num_desert

                # DM user that units have been disbanded
                await dm(unit["user_id"], f'{num_desert}/{unit["amount"]} of {unit["troop_name"]} have been disbanded at {land["name"]} because of your oath breaking.')

        # Disband empty units from the siegeCamp
        index = 0
        while index < len(land["siegeCamp"]):
            if land["siegeCamp"][index]["amount"] <= 0:
                land["siegeCamp"].pop(index)
            else:
                index += 1

    user["liege_id"] = 0
    user["quackerinos"] -= int(user["quackerinos"] *
                               global_info["percentPlunderedOnOathbreaker"])

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    with open("./data/lands.json", "w") as file:
        json.dump(lands, file, indent=4)

    await reply(interaction, f'You have renounced your oath to {client.get_user(int(target_user_id))}. Half of all your troops have deserted and looted a quarter of your wealth.')
    await dm(target_user_id, f'Your vassal {client.get_user(int(user_id))} has renounced their oath to you.')


@client.tree.command(name="setvassaltax", description="Set a flat tax rate per land for all vassals.")
async def set_vassal_tax(interaction: discord.Interaction, amount: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Prevent the number from being lower than 0
    if amount < 0:
        await reply(interaction, "You cannot set a negative tax rate.")
        return

    # Prevent the number from being too high
    if amount > global_info["maxtaxPerVassalLand"]:
        await reply(interaction, f'You cannot set a tax rate higher than the maximum ({global_info["maxtaxPerVassalLand"]}).')
        return

    user["taxPerVassalLand"] = amount

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, f'You have set a tax rate of {amount} per land for all your vassals.')



@client.tree.command(name="flip", description="Flip a coin to double your qq bet.")
async def flip(interaction: discord.Interaction, number: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Make sure the player can't bet negative quackerinos
    if number < 1:
        await reply(interaction, "Nice try.")
        return

    # Make sure the player can't bet more quackerinos than they have
    try:
        if int(user["quackerinos"]) < number:
            await reply(interaction, "You don't have enough quackerinos for that.")
            return
    except:
        await reply(interaction, "You don't have enough quackerinos for that.")
        return

    # Give the player quackerinos if they win the bet
    if random.choice([True, False]):
        user["quackerinos"] += number
        message = f'You won {number} qq!'
    else:
        user["quackerinos"] -= number
        message = f'You lost {number} qq...'

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, message)


@client.tree.command(name="slotmachine", description="Spin the slot machine for a chance to win the jackpot!")
async def slotmachine(interaction: discord.Interaction):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/slots.json", "r") as file:
        slots = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return

    # Fail if the user has no spins left
    if user["spins"] < 1:
        await reply(interaction, "You have no spins left. Do /buyspins to pull the slot machine some more!")
        return
    
    # Get the weights for each roller
    total_weight = 0

    all_position_ids = []

    for position_id, position in slots["positions"].items():
        total_weight += position["weight"]
        all_position_ids.append(position_id)
    
    result_code = ""

    # Spin the slot machine
    for x in range(slots["num_positions"]):
        result_number = random.randint(1, total_weight)

        for position_id, position in slots["positions"].items():
            if result_number <= position["weight"]:
                result_code += position_id
                break
            else:
                result_number -= position["weight"]
    
    player_reward = {}
    
    # Get the reward
    for reward_id, reward in slots["rewards"].items():
        # Shows which position correlates to which reward id
        position_ids = {}

        is_match = False

        for x in range(len(reward_id)):
            # Shows all the possible position_ids a reward id might be referring to
            reward_position_ids = []

            # Check if the reward id refers to a specific position id OR multiple position ids
            if reward_id[x] not in all_position_ids:
                reward_position_ids = slots["identifiers"][reward_id[x]]
            else:
                reward_position_ids = reward_id[x]

            # Check if the current element in the result code is referred to by the reward_id
            if result_code[x] in reward_position_ids:
                position_id = position_ids.get(reward_id[x], None)

                # If that reward_id has been used already, then make sure this current element matches it
                if position_id is None:
                    position_ids[reward_id[x]] = result_code[x]
                elif result_code[x] != position_id:
                    break

                if x+1 == len(reward_id):
                    is_match = True
                    break
            else:
                break

        if is_match:
            player_reward = reward
            break

    # Get the result code as a list of emojis
    formatted_result_code = ""

    for position_id in result_code:
        formatted_result_code += slots["positions"][position_id].get("emoji", position_id)

    message = f'You got {formatted_result_code}. '

    if player_reward.get("quackerinos", 0) > 0 and player_reward.get("spin", 0) > 0:
        message += f'You received {player_reward["quackerinos"]} qq and {player_reward["spin"]} free spins as a reward!'
    elif player_reward.get("quackerinos", 0) > 0:
        message += f'You received {player_reward["quackerinos"]} qq as a reward!'
    elif player_reward.get("spin", 0) > 0:
        message += f'You received {player_reward["spin"]} free spins as a reward!'
    else:
        message += f'You didn\'t receive any reward.'

    # Give rewards
    user["spins"] -= 1
    user["spins"] += player_reward.get("spin", 0)
    user["quackerinos"] += player_reward.get("quackerinos", 0)

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, message)


@client.tree.command(name="buyspins", description="Buy spins to use on the slot machine (5qq each).")
async def buyspins(interaction: discord.Interaction, number: int):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    with open("./data/slots.json", "r") as file:
        slots = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return
    
    cost = slots["spin_cost"] * number

    # Make sure the player can't spend more quackerinos than they have
    try:
        if int(user["quackerinos"]) < cost:
            await reply(interaction, "You don't have enough quackerinos for that.")
            return
    except:
        await reply(interaction, "You don't have enough quackerinos for that.")
        return
    
    #Add spins to the user
    user["spins"] += number
    user["quackerinos"] -= cost
    
    message = f'You bought {number} spins for a total of {cost} qq.'

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, message)


@client.tree.command(name="dailyreminder", description="Toggle your daily reminder (default: off).")
async def dailyreminder(interaction: discord.Interaction):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user_id = interaction.user.id

    # Make sure this player exists in user_info
    try:
        user = user_info[str(user_id)]
    except:
        await reply(interaction, "You have not quacked yet.")
        return
    
    if bool(user["daily_reminder"]):
        user["daily_reminder"] = False
        message = f'Daily reminders are now: off'
    else:
        user["daily_reminder"] = True
        message = f'Daily reminders are now: on'

    # Save to database
    with open("./data/user_info.json", "w") as file:
        json.dump(user_info, file, indent=4)

    await reply(interaction, message)


@client.tree.command(name="dailychannel", description="Manage where the daily message is sent in your server. Modes = view/set/remove")
@commands.has_permissions(administrator=True)
async def dailychannel(interaction: discord.Interaction, mode: str = "view", channel_id: str = None):
    with open("./data/server_info.json", "r") as file:
        server_info = json.load(file)

    server_id = interaction.guild_id

    # Make sure this server exists in user_info
    server = server_info.get(str(server_id), None)

    if server == None:
        server = {
            "daily_channels": []
        }
        server_info[server_id] = server
    
    if mode == "view":
        print()
    elif mode == "set":
        server["daily_channels"].append(int(channel_id))
    elif mode == "remove":
        server["daily_channels"].remove(int(channel_id))
    
    message = f'__**{client.get_guild(server_id)} - Daily Channels**__'

    #Give the name and id for each channel in this server
    for channel_id in server["daily_channels"]:
        message += f'\n{client.get_channel(channel_id)} (id:{channel_id})'

    # Save to database
    with open("./data/server_info.json", "w") as file:
        json.dump(server_info, file, indent=4)

    await reply(interaction, message)


async def is_surrounded(land):
    defender_score = 0
    sieger_score = 0

    num_defenders = 0
    num_siegers = 0

    # 1 troop = +1 score
    for unit in land["garrison"]:
        num_defenders += unit["amount"]
    for unit in land["siegeCamp"]:
        num_siegers += unit["amount"]

    defender_score += num_defenders
    sieger_score += num_siegers

    # HP and DEF bonuses of buildings increase the defender score
    for building_name in land["buildings"]:
        building = await get_building(building_name)
        defender_score += min(building["maxAPbonus"], building["APbonus"] + building["APbonusPerTroop"] * num_defenders) + min(
            building["maxHPbonus"], building["HPbonus"] + building["HPbonusPerTroop"] * num_defenders)

    if sieger_score > defender_score:
        return True
    else:
        return False


async def remove_unit(army, unit, amount):
    moved_unit = deepcopy(unit)
    moved_unit["amount"] = amount
    unit["amount"] -= amount
    if unit["amount"] == 0:
        army.remove(unit)

    return moved_unit


async def add_unit(army, unit, amount=-1):
    target_unit = await get_unit(army, unit["troop_name"], unit["user_id"])
    if target_unit == "":
        army.append(unit)
    else:
        if amount < 0:
            target_unit["amount"] += unit["amount"]
        else:
            target_unit["amount"] += amount


async def get_allies(user_id):
    with open("./data/user_info.json", "r") as file:
        user_info = json.load(file)

    user = user_info.get(str(user_id), "")
    allies = user["ally_ids"]

    if user["liege_id"] != 0:
        allies.append(user["liege_id"])

    # Check every person on the user list to see if:
    # 1) They are the vassal of the user's liege (and the user has a liege)
    # 2) They are the vassal of the user
    # 3) They are the vassal of one of the user's allies
    for target_id, target in user_info.items():
        if (user["liege_id"] != 0 and user["liege_id"] == target["liege_id"]) or target["liege_id"] == user_id or target["liege_id"] in user["ally_ids"]:
            allies.append(target_id)

    return allies


async def get_troop(troop_name):
    with open("./data/troops.json", "r") as file:
        troops = json.load(file)

    try:
        overrides = troops[troop_name]
    except:
        return ""

    troop = troops.get(f'default_tier{overrides["tier"]}', {})

    # Replace the attributes with the troop specific overrides
    for attr, value in overrides.items():
        troop[attr] = value

    return troop


async def get_building(building_name):
    with open("./data/buildings.json", "r") as file:
        buildings = json.load(file)

    try:
        overrides = buildings[building_name]
    except:
        return ""

    building = buildings.get("default", "")

    # Replace the attributes with the building specific overrides
    for attr, value in overrides.items():
        building[attr] = value

    return building


async def get_land(land_id):
    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    land = lands.get(str(land_id), "")

    return land


async def get_land_by_name(land_name):
    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    for land in lands:
        if land["name"] == land_name:
            return land

    return ""


async def get_land_id(query_land):
    with open("./data/lands.json", "r") as file:
        lands = json.load(file)

    for land_id, land in lands.items():
        if land == query_land:
            return land_id

    return -1


async def get_species(species_name):
    with open("./data/species.json", "r") as file:
        species_list = json.load(file)
    
    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    try:
        overrides = species_list[species_name]
    except:
        return ""

    default_species = species_list.get("default", "")
    species = {}
    
    #Only add the non seasonal default attributes to the species
    for attr, value in default_species.items():
        if attr not in ["all-season", "spring", "summer", "fall", "winter"]:
            species[attr] = value
    
    # Give the species all of the default all-season attributes
    for attr, value in default_species["all-season"].items():
        species[attr] = value

    # Override the all-season attributes with the proper season from the default species
    for attr, value in default_species[global_info["current_season"]].items():
        species[attr] = value

    # Replace all non seasonal default attributes with species specific overrides
    for attr, value in overrides.items():
        if attr not in ["all-season", "spring", "summer", "fall", "winter"]:
            species[attr] = value

    # Give the species all of the all-season attributes
    for attr, value in overrides["all-season"].items():
        species[attr] = value

    # Override the all-season attributes with the proper season
    for attr, value in overrides[global_info["current_season"]].items():
        species[attr] = value

    return species


async def get_season(day):
    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    dayx = deepcopy(day)

    while True:
        for season_name, length in global_info["seasons"].items():
            if dayx <= length:
                return season_name
            else:
                dayx -= length


async def get_unit(army, troop_name, user_id):
    for unit in army:
        if unit["troop_name"] == troop_name and str(unit["user_id"]) == str(user_id):
            return unit

    return ""


async def resolve_battle(attack_army, defend_army, land=""):
    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    percent_casualties_attackers = 0
    percent_casualties_defenders = 0
    total_attackers = await get_total_troops(attack_army)
    total_defenders = await get_total_troops(defend_army)

    round = 0

    message = f'__**Battle Report @ {land.get("name")}**__'
    message += f'\n**Round {round}**'
    message += f'\nAttackers:'
    message += f'{await print_army(attack_army)}'
    message += f'\nDefenders:'
    message += f'{await print_army(defend_army)}'

    attacker_HP = 0
    defender_HP = 0

    for unit in attack_army:
        troop = await get_troop(unit["unit"]["troop_name"])
        species = await get_species(troop["species"])
        attacker_HP += (troop["HP"] + species["bonusHPPerTroop"]) * unit["amount"]

    for unit in defend_army:
        troop = await get_troop(unit["unit"]["troop_name"])
        species = await get_species(troop["species"])
        defender_HP += (troop["HP"] + species["bonusHPPerTroop"]) * unit["amount"]

    if land != "":
        for building_name in land["buildings"]:
            building = await get_building(building_name)
            hpbonus = building["HPbonus"] + \
                building["HPbonusPerTroop"] * total_defenders
            hpbonus = min(hpbonus, building["maxHPbonus"])
            defender_HP += hpbonus

    while percent_casualties_attackers < global_info["max_casualties_attackers"] and percent_casualties_defenders < global_info["max_casualties_defenders"]:
        updated_total_attackers = await get_total_troops(attack_army)
        updated_total_defenders = await get_total_troops(defend_army)
        attacker_ATK = 0
        defender_ATK = 0
        attacker_DEF = 0
        defender_DEF = 0

        for unit in attack_army:
            troop = await get_troop(unit["unit"]["troop_name"])
            species = await get_species(troop["species"])
            attacker_ATK += int(troop["ATK"] + species["bonusATKPerTroop"]) * unit["amount"]
            attacker_DEF += (troop["AP"] + species["bonusDEFPerTroop"]) * unit["amount"]

        for unit in defend_army:
            troop = await get_troop(unit["unit"]["troop_name"])
            species = await get_species(troop["species"])
            defender_ATK += (troop["ATK"] + species["bonusATKPerTroop"]) * unit["amount"]
            defender_DEF += (troop["AP"] + species["bonusDEFPerTroop"]) * unit["amount"]

        if land != "":
            for building_name in land["buildings"]:
                building = await get_building(building_name)
                atkbonus = building["ATKbonus"] + \
                    building["ATKbonusPerTroop"] * updated_total_defenders
                atkbonus = min(atkbonus, building["maxATKbonus"])
                defender_ATK += atkbonus
                defbonus = building["APbonus"] + \
                    building["APbonusPerTroop"] * updated_total_defenders
                defbonus = min(defbonus, building["maxAPbonus"])
                defender_DEF += defbonus

        attacker_score = await get_battle_score(attacker_ATK)
        defender_score = await get_battle_score(defender_ATK)

        attack_spite = deepcopy(attacker_score["spite"])
        defend_spite = deepcopy(defender_score["spite"])
        attacker_score["spite"] -= defender_DEF + defender_HP
        defender_score["spite"] -= attacker_DEF + attacker_HP
        defender_HP -= attack_spite
        defender_HP = max(0, defender_HP)
        attacker_HP -= defend_spite
        attacker_HP = max(0, attacker_HP)

        for x in range(attacker_score["spite"]):
            await remove_casualty(defend_army)
        for x in range(defender_score["spite"]):
            await remove_casualty(attack_army)

        percent_casualties_attackers = 1 - await get_total_troops(attack_army) / total_attackers
        percent_casualties_defenders = 1 - await get_total_troops(defend_army) / total_defenders

        round += 1
        message += f'\n\n\n**Round {round}**'
        message += f'\nAttackers:'
        message += f'{await print_army(attack_army)}'
        message += f'\n\nDefenders:'
        message += f'{await print_army(defend_army)}'

    return message


async def print_army(army_collection):
    message = ""
    for company in army_collection:
        message += f'\n{company["amount"]} {company["unit"]["troop_name"]} ({client.get_user(int(company["unit"]["user_id"]))})'
    return message


async def remove_casualty(army_collection):
    try:
        target_index = random.randint(0, len(army_collection) - 1)
    except:
        return

    army_collection[target_index]["amount"] -= 1
    army_collection[target_index]["unit"]["amount"] -= 1

    if army_collection[target_index]["amount"] <= 0:
        army_collection.pop(target_index)


async def get_total_troops(army_collection):
    total = 0

    for company in army_collection:
        total += company["amount"]

    return total


async def get_battle_score(num):
    score = 0
    spite = 0

    for x in range(num):
        a = random.randint(1, 6)
        score += a
        if a >= 5:
            spite += 1

    return {"score": score, "spite": spite}


async def dm(user_id, message):
    try:
        user = await client.fetch_user(int(user_id))
        #user = await client.fetch_user(107886996365508608)
        if len(message) <= 2000:
            await user.send(message)
        else:
            new_message = deepcopy(message)
            message_fragments = new_message.split("\n")
            message_to_send = ""
            for x in range(len(message_fragments)):
                if len(message_to_send) + len(message_fragments[x-1]) < 2000:
                    message_to_send += "\n" + message_fragments[x-1]
                else:
                    await user.send(message_to_send)
                    message_to_send = message_fragments[x-1]
            
            if len(message_to_send) > 0:
                if len(message_to_send) < 2000:
                    await user.send(message_to_send)
                else:
                    await user.send('Last message fragment too long to send. Ask developer to include more linebreaks in output.')
    except:
        print(f'{user_id} not found. Message: {message}')
        return


async def reply(interaction, message):
    try: 
        if len(message) <= 2000:
            await interaction.response.send_message(message)
        else:
            new_message = deepcopy(message)
            message_fragments = new_message.split("\n")
            message_to_send = ""
            first_reply_sent = False
            channel = await client.fetch_channel(interaction.channel_id)
            for x in range(len(message_fragments)):
                if len(message_to_send) + len(message_fragments[x-1]) < 2000:
                    message_to_send += "\n" + message_fragments[x-1]
                else:
                    if not first_reply_sent:
                        await interaction.response.send_message(message_to_send)
                        first_reply_sent = True
                    else:
                        await channel.send(message_to_send)
                    message_to_send = message_fragments[x-1]
            
            if len(message_to_send) > 0:
                if len(message_to_send) < 2000:
                    if not first_reply_sent:
                        await interaction.response.send_message(message_to_send)
                    else:
                        await channel.send(message_to_send)
                else:
                    await reply(interaction, 'Last message fragment too long to send. Ask developer to include more linebreaks in output.')
    except:
        print(f'Unable to send message: {message}')


async def add_to_queue(user_id, action, item, location_id, amount=1, time=1, target_land=0):
    with open("./data/global_info.json", "r") as file:
        global_info = json.load(file)

    task = {
        "user_id": user_id,
        "task": action,
        "item": item,
        "location_id": location_id,
        "amount": amount,
        "time": time,
        "target_land_id": target_land
    }

    global_info["task_queue"].append(task)

    with open("./data/global_info.json", "w") as file:
        json.dump(global_info, file, indent=4)


async def main():
    async with client:
        # Reading token from environment variable
        discord_token = os.getenv('DISCORD_BOT_TOKEN')
        if not discord_token:
            raise ValueError(
                "No token provided. Set the DISCORD_BOT_TOKEN environment variable.")
        await client.load_extension("gambling.quackjack")
        await client.start(discord_token)

# async def main():
#     async with client:
#         with open("config.json", "r") as file:
#             config = json.load(file)

#         await client.start(config['token'])

asyncio.run(main())
