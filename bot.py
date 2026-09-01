import asyncio
import json
import os
import sys
import websockets

# --- КОНФИГУРАЦИЯ ИЗ ОКРУЖЕНИЯ ---
ENGINE_URL = os.environ.get("ENGINE_URL", "ws://localhost:8081/ws")
BOT_ID = os.environ.get("BOT_ID", "player1")
BOT_ICON = os.environ.get("BOT_ICON", "🧙")

# Константы правил игры
EPIC_ELEMENTS = ["philosophers_stone", "elixir_of_life", "shadow_soul", "stardust"]

# Пул фраз для психологического и дипломатического блефа
BLUFF_MESSAGES = [
    "Кажется, я нашел рецепт Философского Камня...",
    "Не подходи к центральному котлу, там ловушка!",
    "Давай мирный крафт? Я не атакую.",
    "Ого, библиотека дала отличную подсказку.",
    "У тебя фейк в инвентаре, я видел."
]


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
        """Делает один безопасный шаг в противоположную сторону от врага."""
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
        """Максимально продвинутый автомат принятия решений."""
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

        # Извлекаем или инициализируем приватную память бота
        memory = my_bot.get("memory") or {}
        last_aggression_tick = memory.get("last_aggression_tick", -100)

        # Переменные инвентаря
        has_free_slot = None in inventory
        filled_slots = [i for i, elem in enumerate(inventory) if elem is not None]

        # Обнаружение обманок
        fake_slots = [i for i in filled_slots if isinstance(inventory[i], str) and "_fake" in inventory[i]]
        has_fake = len(fake_slots) > 0

        # Наличие Хаоса
        has_chaos = "chaos" in inventory

        dist_to_enemy = cls.get_distance(mx, my, ex, ey)

        # Шаблон итогового ответа, чтобы всегда прокидывать апдейт памяти
        def build_cmd(action: str, params: dict = None) -> dict:
            return {
                "type": "command",
                "action": action,
                "params": params or {},
                "memory_update": memory
            }

        # ==========================================
        # ДИПЛОМАТИЧЕСКИЙ БЛЕФ (Каждые 45 тиков)
        # ==========================================
        if current_tick > 0 and current_tick % 45 == 0:
            msg_index = (current_tick // 45) % len(BLUFF_MESSAGES)
            return build_cmd("send_message", {"text": BLUFF_MESSAGES[msg_index]})

        # ==========================================
        # ПРИОРИТЕТ 1: Уклонение (если враг не истощен)
        # ==========================================
        if dist_to_enemy <= 2 and ehp > 30:
            return build_cmd(cls.step_away(mx, my, ex, ey, width, height)["action"],
                             cls.step_away(mx, my, ex, ey, width, height)["params"])

        # ==========================================
        # ПРИОРИТЕТ 2: Агрессия — кража (steal) у слабого врага
        # ==========================================
        if dist_to_enemy == 1 and ehp <= 30 and has_free_slot:
            print(f"[{BOT_ID}] Совершаю кражу! Сбрасываю Бонус Алхимика.", flush=True)
            memory["last_aggression_tick"] = current_tick  # Запоминаем тик сброса бонуса
            return build_cmd("steal")

        # ==========================================
        # ПРИОРИТЕТ 3: Безопасность — утилизация обманок через ловушки
        # ==========================================
        if has_fake:
            return build_cmd("set_trap", {"element_slot": fake_slots[0]})

        # ==========================================
        # ПРИОРИТЕТ 4: Сохранение рецептов и Бонус Алхимика
        # ==========================================
        last_recipe = my_bot.get("last_recipe")
        has_stone = "philosophers_stone" in inventory

        if (last_recipe and last_recipe.get("success")) or (has_stone and last_recipe and last_recipe.get("success")):
            lab = next((c for c in cells if c.get("type") == "lab_table" and c.get("owner") == BOT_ID), None)
            if lab:
                if cls.get_distance(mx, my, lab["x"], lab["y"]) == 0:
                    # ПРОВЕРКА БОНУСА АЛХИМИКА: прошло ли 10 тиков без атак и краж?
                    ticks_since_aggression = current_tick - last_aggression_tick
                    if ticks_since_aggression < 10:
                        print(f"[{BOT_ID}] Выжидаю Бонус Алхимика (прошло {ticks_since_aggression}/10 тиков). Пропуск.", flush=True)
                        return build_cmd("wait")

                    print(f"[{BOT_ID}] Сохраняю рецепт с активным Бонусом Алхимика (×1.5)!", flush=True)
                    return build_cmd("save")

                return build_cmd(cls.step_towards(mx, my, lab["x"], lab["y"])["action"],
                                 cls.step_towards(mx, my, lab["x"], lab["y"])["params"])

        # ==========================================
        # ПРИОРИТЕТ 5: Саботаж котла Хаосом
        # ==========================================
        if has_chaos:
            # Ищем ближайший незаблокированный котел
            free_cauldrons = [c for c in cells if c.get("type") == "cauldron" and not c.get("blocked", False)]
            if free_cauldrons:
                closest_cauldron = min(free_cauldrons, key=lambda c: cls.get_distance(mx, my, c["x"], c["y"]))
                dist_to_cauldron = cls.get_distance(mx, my, closest_cauldron["x"], closest_cauldron["y"])

                if dist_to_cauldron == 0:
                    print(f"[{BOT_ID}] Стою на котле. Активирую саботаж Хаосом block_cauldron!", flush=True)
                    return build_cmd("block_cauldron")
                return build_cmd(cls.step_towards(mx, my, closest_cauldron["x"], closest_cauldron["y"])["action"],
                                 cls.step_towards(mx, my, closest_cauldron["x"], closest_cauldron["y"])["params"])

        # ==========================================
        # ПРИОРИТЕТ 6: Сбор ресурсов (жилы и библиотеки)
        # ==========================================
        if has_free_slot:
            targets = [c for c in cells if c.get("type") in ["vein", "library"] and c.get("exhausted_ticks", 0) <= 0]
            if targets:
                closest_target = min(targets, key=lambda c: cls.get_distance(mx, my, c["x"], c["y"]))
                if cls.get_distance(mx, my, closest_target["x"], closest_target["y"]) == 0:
                    return build_cmd("collect")
                return build_cmd(cls.step_towards(mx, my, closest_target["x"], closest_target["y"])["action"],
                                 cls.step_towards(mx, my, closest_target["x"], closest_target["y"])["params"])

        # ==========================================
        # ПРИОРИТЕТ 7: Алхимия и Минирование
        # ==========================================
        if len(filled_slots) >= 2:
            cauldrons = [c for c in cells if c.get("type") == "cauldron"]
            if cauldrons:
                closest_cauldron = min(cauldrons, key=lambda c: cls.get_distance(mx, my, c["x"], c["y"]))
                dist_to_cauldron = cls.get_distance(mx, my, closest_cauldron["x"], closest_cauldron["y"])

                if dist_to_cauldron == 1:
                    for slot in filled_slots:
                        if inventory[slot] not in EPIC_ELEMENTS:
                            return build_cmd("set_trap", {"element_slot": slot})

                if dist_to_cauldron == 0:
                    return build_cmd("mix", {"slot1": filled_slots[0], "slot2": filled_slots[1]})

                return build_cmd(cls.step_towards(mx, my, closest_cauldron["x"], closest_cauldron["y"])["action"],
                                 cls.step_towards(mx, my, closest_cauldron["x"], closest_cauldron["y"])["params"])

        return build_cmd("wait")


# --- СЕТЕВОЙ КЛИЕНТ ---
async def start_bot():
    print(f"Запуск гроссмейстерского Python-бота [{BOT_ID}]...", flush=True)
    try:
        async with websockets.connect(ENGINE_URL) as ws:
            await ws.send(json.dumps({"type": "register", "bot_id": BOT_ID, "icon": BOT_ICON}))
            ready_msg = await ws.recv()
            ready_data = json.loads(ready_msg)
