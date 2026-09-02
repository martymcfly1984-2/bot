import asyncio
import json
import os
import sys
import random
import websockets

# --- КОНФИГУРАЦИЯ ИЗ ОКРУЖЕНИЯ ---
ENGINE_URL = os.environ.get("ENGINE_URL", "ws://engine:8081/ws")
BOT_ID = os.environ.get("BOT_ID", "Top_of_the_Bot")
BOT_ICON = os.environ.get("BOT_ICON", "🧙")

# Константы правил игры
EPIC_ELEMENTS = ["philosophers_stone", "elixir_of_life", "shadow_soul", "stardust"]


class AlchemyStrategy:
    @staticmethod
    def get_distance(x1: int, y1: int, x2: int, y2: int) -> int:
        """Вычисляет манхэттенское расстояние между точками."""
        return abs(x1 - x2) + abs(y1 - y2)

    @staticmethod
    def step_towards(mx: int, my: int, tx: int, ty: int) -> dict:
        """Делает один шаг по направлению к целевой координате (tx, ty)."""
        dx = (tx > mx) - (tx < mx)
        dy = (ty > my) - (ty < my)
        if dx != 0:
            return {"type": "command", "action": "move", "params": {"x": mx + dx, "y": my}}
        return {"type": "command", "action": "move", "params": {"x": mx, "y": my + dy}}

    @staticmethod
    def step_away(mx: int, my: int, ex: int, ey: int, width: int, height: int) -> dict:
        """Делает один безопасный шаг в противоположную сторону от врага с учетом границ поля."""
        dx = (mx > ex) - (mx < ex)
        dy = (my > ey) - (my < ey)
        if dx == 0 and dy == 0:
            dx = 1
        if dx != 0 and 0 <= mx + dx < width:
            return {"type": "command", "action": "move", "params": {"x": mx + dx, "y": my}}
        if dy != 0 and 0 <= my + dy < height:
            return {"type": "command", "action": "move", "params": {"x": mx, "y": my + dy}}
        if 0 <= mx - dx < width:
            return {"type": "command", "action": "move", "params": {"x": mx - dx, "y": my}}
        return {"type": "command", "action": "wait", "params": {}}

    @classmethod
    def decide(cls, state: dict) -> dict:
        """Адаптивный чемпионский автомат принятия решений."""
        my_bot = state.get("my_bot", {})
        enemy_bot = state.get("enemy_bot", {})
        game_map = state.get("map", {})
        current_tick = state.get("tick", 0)

        mx, my = my_bot.get("x", 0), my_bot.get("y", 0)
        ex, ey = enemy_bot.get("x", 0), enemy_bot.get("y", 0)
        ehp = enemy_bot.get("hp", 100)

        width = game_map.get("width", 30)
        height = game_map.get("height", 20)
        cells = game_map.get("cells", [])
        inventory = my_bot.get("inventory", [None, None, None])

        # Восстановление и инициализация расширенной памяти матча
        memory = my_bot.get("memory") or {}
        if "last_aggression_tick" not in memory:
            memory["last_aggression_tick"] = -100
        if "failed_recipes" not in memory:
            memory["failed_recipes"] = []  # База опасных пар этого матча

        # Свойства инвентаря
        has_free_slot = None in inventory
        filled_slots = [i for i, elem in enumerate(inventory) if elem is not None]

        # Безопасное обнаружение обманок
        fake_slots = [i for i in filled_slots if isinstance(inventory[i], str) and "_fake" in inventory[i]]
        has_fake = len(fake_slots) > 0
        has_chaos = "chaos" in inventory
        dist_to_enemy = cls.get_distance(mx, my, ex, ey)

        # "Щит территории" базы врага (Раздел 10.2 правил)
        enemy_altar = next((c for c in cells if c.get("type") == "altar" and c.get("owner") != BOT_ID), None)
        is_enemy_near_base = False
        if enemy_altar:
            is_enemy_near_base = cls.get_distance(ex, ey, enemy_altar["x"], enemy_altar["y"]) <= 2

        # ДИНАМИЧЕСКАЯ ДЕЗИНФОРМАЦИЯ: Выбираем случайную жилу, чтобы запутать вражеский listen
        veins = [c for c in cells if c.get("type") == "vein"]
        fake_target = random.choice(veins) if veins else {"x": 0, "y": 0}

        fake_thought = {
            "action_intent": "gather",
            "confidence": 0.99,
            "text": "Пойду соберу ресурсы в другом углу...",
            "target_location": {"x": fake_target.get("x", 0), "y": fake_target.get("y", 0)}
        }

        def build_cmd(action: str, params: dict = None) -> dict:
            return {
                "type": "command",
                "action": action,
                "params": params or {},
                "memory_update": memory,
                "fake_thought": fake_thought
            }

        # Самообучение: анализируем результаты предыдущего тика
        server_last_recipe = my_bot.get("last_recipe")
        if server_last_recipe:
            used_elements = sorted(server_last_recipe.get("elements", []))
            if server_last_recipe.get("success"):
                memory["last_recipe"] = server_last_recipe
            elif server_last_recipe.get("result") == "explosion":
                # Запоминаем плохую комбинацию текущего сида, чтобы больше не взрываться
                if used_elements and used_elements not in memory["failed_recipes"]:
                    memory["failed_recipes"].append(used_elements)

        # 1. Защита — уклонение от преследования (Вне зоны базы врага)
        if dist_to_enemy <= 2 and (ehp > 30 or is_enemy_near_base):
            escape_move = cls.step_away(mx, my, ex, ey, width, height)
            return build_cmd(escape_move["action"], escape_move["params"])

        # 2. Агрессия — кража (steal) у слабого соперника в открытом поле
        if dist_to_enemy == 1 and ehp <= 30 and has_free_slot and not is_enemy_near_base:
            memory["last_aggression_tick"] = current_tick  # Сброс Бонуса Алхимика
            return build_cmd("steal")

        # 3. Безопасность — утилизация вражеских фейков в ловушки (Защищено от IndexError)
        if has_fake and fake_slots:
            return build_cmd("set_trap", {"element_slot": fake_slots[0]})

        # 4. Экономика — Сохранение рецептов и Бонус Алхимика (×1.5)
        memory_recipe = memory.get("last_recipe")
        has_stone = "philosophers_stone" in inventory

        if (memory_recipe and memory_recipe.get("success")) or (has_stone and memory_recipe and memory_recipe.get("success")):
            lab = next((c for c in cells if c.get("type") == "lab_table" and c.get("owner") == BOT_ID), None)
            if lab:
                if cls.get_distance(mx, my, lab["x"], lab["y"]) == 0:
                    if current_tick - memory["last_aggression_tick"] < 10:
                        return build_cmd("wait")

                    memory["last_recipe"] = None  # Сдаём рецепт
                    return build_cmd("save")
                move_cmd = cls.step_towards(mx, my, lab["x"], lab["y"])
                return build_cmd(move_cmd["action"], move_cmd["params"])

        # 5. Диверсия — Саботаж котла Хаосом (ИСПРАВЛЕНО: только если у нас нет готового рецепта!)
        if has_chaos and not (memory_recipe and memory_recipe.get("success")):
            clean_cauldrons = [c for c in cells if c.get("type") == "cauldron" and not c.get("blocked", False)]
            if clean_cauldrons:
                closest_cauldron = min(clean_cauldrons, key=lambda c: cls.get_distance(mx, my, c["x"], c["y"]))
                if cls.get_distance(mx, my, closest_cauldron["x"], closest_cauldron["y"]) == 0:
                    return build_cmd("block_cauldron")
                move_cmd = cls.step_towards(mx, my, closest_cauldron["x"], closest_cauldron["y"])
                return build_cmd(move_cmd["action"], move_cmd["params"])

        # 6. Сбор ресурсов с учетом Оккупации (Библиотеки и Жилы)
        if len(filled_slots) < 2 and has_free_slot:
            targets = [c for c in cells if c.get("type") in ["library", "vein"] and c.get("exhausted_ticks", 0) <= 0]
            targets = [c for c in targets if not (c["x"] == ex and c["y"] == ey)]

            if targets:
                targets.sort(key=lambda c: (c.get("type") != "library", cls.get_distance(mx, my, c["x"], c["y"])))
                closest_target = targets[0]
                if cls.get_distance(mx, my, closest_target["x"], closest_target["y"]) == 0:
                    return build_cmd("collect")
                move_cmd = cls.step_towards(mx, my, closest_target["x"], closest_target["y"])
                return build_cmd(move_cmd["action"], move_cmd["params"])

        # 7. Алхимия (Синтез в котле)
        if len(filled_slots) >= 2:
            # ИСПРАВЛЕНО: Анти-взрывная защита. Если пара в инвентаре уже взрывалась — сливаем один элемент в ловушку
            current_pair = sorted([inventory[filled_slots[0]], inventory[filled_slots[1]]])
            if current_pair in memory["failed_recipes"]:
                return build_cmd("set_trap", {"element_slot": filled_slots[0]})

            cauldrons = [c for c in cells if c.get("type") == "cauldron"]
            if cauldrons:
                cauldrons.sort(key=lambda c: (c.get("blocked", False), cls.get_distance(mx, my, c["x"], c["y"])))
                target_cauldron = cauldrons[0]
                dist_to_cauldron = cls.get_distance(mx, my, target_cauldron["x"], target_cauldron["y"])
                is_occupied = (target_cauldron["x"] == ex and target_cauldron["y"] == ey)

                if dist_to_cauldron == 0:
                    if is_occupied:
                        return build_cmd("attack")
                    if dist_to_enemy <= 5:
                        return build_cmd("wait")  # Защита от Ауры соперничества
                    return build_cmd("mix", {"slot1": filled_slots[0], "slot2": filled_slots[1]})

                # ИСПРАВЛЕНО: Защитное минирование активируется ТОЛЬКО при полном инвентаре (3 элемента)
                if dist_to_cauldron == 1 and dist_to_enemy <= 3 and len(filled_slots) == 3:
                    for slot in filled_slots:
                        if inventory[slot] not in EPIC_ELEMENTS:
                            return build_cmd("set_trap", {"element_slot": slot})

                move_cmd = cls.step_towards(mx, my, target_cauldron["x"], target_cauldron["y"])
                return build_cmd(move_cmd["action"], move_cmd["params"])

        return build_cmd("wait")


# --- СЕТЕВОЙ КЛИЕНТ (Требование раздела 11.6) ---
async def start_bot():
    print(f"Запуск гроссмейстерского Python-бота [{BOT_ID}]...", flush=True)
    try:
        # Отключаем дефолтные пинги библиотеки websockets (Keepalive таймауты убраны)
        async with websockets.connect(ENGINE_URL, ping_interval=None, ping_timeout=None) as ws:
            register_payload = {
                "type": "register",
                "bot_id": BOT_ID,
                "icon": BOT_ICON
            }
            await ws.send(json.dumps(register_payload))
            print("Пакет регистрации отправлен в сеть. Ожидание ready...", flush=True)

            ready_msg = await ws.recv()
            ready_data = json.loads(ready_msg)
            if ready_data.get("type") == "ready":
                print("Регистрация успешна! Матч начался.", flush=True)

            async for msg in ws:
                data = json.loads(msg)
                if data.get("type") == "game_over":
                    print(f"Матч завершен движком. Причина: {data.get('reason', 'Нет описания')}", flush=True)
                    break
                elif data.get("type") == "state":
                    command = AlchemyStrategy.decide(data)
                    await ws.send(json.dumps(command))
    except Exception as e:
        print(f"Критическая ошибка сетевого WebSocket-клиента: {e}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    asyncio.run(start_bot())
