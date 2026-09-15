import asyncio
import logging
import os

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, Message
from aiohttp import web
from dotenv import load_dotenv

import utils

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("matplotlib").setLevel(logging.WARNING)  # он шумит на каждый график

users = {}

HELP = """Бот считает нормы воды и калорий и ведет дневник.

/set_profile - заполнить профиль
/log_water <мл> - записать воду
/log_food <продукт> - записать еду
/log_workout <тип> <минуты> - записать тренировку
/check_progress - прогресс за сегодня
/chart - графики за неделю
/recommend - советы
/cancel - прервать ввод
/help - это сообщение"""


class LogMiddleware(BaseMiddleware):
    # пишем в логи все сообщения, чтобы их было видно на render
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            logging.info("user %s (%s): %s", event.from_user.id,
                         event.from_user.username, event.text)
        return await handler(event, data)


class Profile(StatesGroup):
    weight = State()
    height = State()
    age = State()
    sex = State()
    activity = State()
    city = State()
    goal = State()


class Food(StatesGroup):
    grams = State()


dp = Dispatcher(storage=MemoryStorage())
dp.message.middleware(LogMiddleware())


def get_user(message):
    return users.get(message.from_user.id)


async def need_profile(message):
    # почти всем командам нужен заполненный профиль
    if get_user(message) is None:
        await message.answer("Сначала заполните профиль: /set_profile")
        return True
    return False


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(HELP)


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(HELP)


@dp.message(Command("set_profile"))
async def set_profile(message: Message, state: FSMContext):
    await state.set_state(Profile.weight)
    await message.answer("Введите ваш вес (в кг):")


@dp.message(Command("log_water"))
async def log_water(message: Message, command: CommandObject):
    if await need_profile(message):
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Напишите так: /log_water 250")
        return

    user = get_user(message)
    day = utils.today(user)
    day["water"] += int(command.args.strip())
    goal = utils.water_goal(user)
    left = goal - day["water"]
    if left > 0:
        await message.answer(f"Записано. Выпито {day['water']} из {goal} мл, осталось {left} мл.")
    else:
        await message.answer(f"Записано. Выпито {day['water']} из {goal} мл, норма выполнена.")


@dp.message(Command("log_food"))
async def log_food(message: Message, command: CommandObject, state: FSMContext):
    if await need_profile(message):
        return
    if not command.args:
        await message.answer("Напишите так: /log_food банан")
        return

    await message.answer("Ищу продукт...")
    food = await utils.get_food_calories(command.args)
    if food is None:
        await message.answer("Не нашел такой продукт, попробуйте написать иначе")
        return

    await state.set_state(Food.grams)
    await state.update_data(name=food["name"], kcal=food["kcal"])
    await message.answer(
        f"{food['name']} - {food['kcal']:.0f} ккал на 100 г ({food['source']}). "
        "Сколько грамм вы съели?"
    )


@dp.message(Command("log_workout"))
async def log_workout(message: Message, command: CommandObject):
    if await need_profile(message):
        return
    parts = (command.args or "").split()
    if len(parts) < 2 or not parts[-1].isdigit():
        await message.answer("Напишите так: /log_workout бег 30")
        return

    kind = " ".join(parts[:-1])
    minutes = int(parts[-1])
    user = get_user(message)
    day = utils.today(user)

    burned = utils.workout_calories(kind, minutes, user["weight"])
    extra_water = 200 * (minutes // 30)
    day["burned"] += burned
    day["extra_water"] += extra_water

    text = f"{kind} {minutes} мин - {burned} ккал."
    if extra_water:
        text += f" Дополнительно выпейте {extra_water} мл воды."
    text += f"\nВсего сожжено за сегодня: {day['burned']} ккал."
    await message.answer(text)


@dp.message(Command("check_progress"))
async def check_progress(message: Message):
    if await need_profile(message):
        return
    user = get_user(message)
    user["temp"] = await utils.get_temperature(user["city"])  # погода могла поменяться
    day = utils.today(user)

    goal = utils.water_goal(user)
    balance = day["eaten"] - day["burned"]
    await message.answer(
        "Прогресс:\n"
        "Вода:\n"
        f"- Выпито: {day['water']} мл из {goal} мл\n"
        f"- Осталось: {max(0, goal - day['water'])} мл\n\n"
        "Калории:\n"
        f"- Потреблено: {day['eaten']:.0f} ккал из {user['calorie_goal']} ккал\n"
        f"- Сожжено: {day['burned']} ккал\n"
        f"- Баланс: {balance:.0f} ккал"
    )


@dp.message(Command("chart"))
async def chart(message: Message):
    if await need_profile(message):
        return
    png = utils.progress_chart(get_user(message))
    await message.answer_photo(BufferedInputFile(png, "chart.png"),
                               caption="Прогресс за неделю")


@dp.message(Command("recommend"))
async def recommend(message: Message):
    if await need_profile(message):
        return
    await message.answer("Думаю...")
    answer = await utils.get_recommendations(get_user(message))
    await message.answer(answer or "Не получилось получить совет, попробуйте позже")


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменил")


@dp.message(Profile.weight)
async def profile_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Нужно число, например 80")
        return
    await state.update_data(weight=weight)
    await state.set_state(Profile.height)
    await message.answer("Введите ваш рост (в см):")


@dp.message(Profile.height)
async def profile_height(message: Message, state: FSMContext):
    try:
        height = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Нужно число, например 184")
        return
    await state.update_data(height=height)
    await state.set_state(Profile.age)
    await message.answer("Введите ваш возраст:")


@dp.message(Profile.age)
async def profile_age(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Нужно целое число, например 26")
        return
    await state.update_data(age=int(message.text))
    await state.set_state(Profile.sex)
    await message.answer("Ваш пол (м/ж):")


@dp.message(Profile.sex)
async def profile_sex(message: Message, state: FSMContext):
    sex = message.text.strip().lower()[:1]
    if sex not in ("м", "ж"):
        await message.answer("Напишите м или ж")
        return
    await state.update_data(sex=sex)
    await state.set_state(Profile.activity)
    await message.answer("Сколько минут активности у вас в день?")


@dp.message(Profile.activity)
async def profile_activity(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Нужно целое число минут, например 45")
        return
    await state.update_data(activity=int(message.text))
    await state.set_state(Profile.city)
    await message.answer("В каком городе вы находитесь?")


@dp.message(Profile.city)
async def profile_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await state.set_state(Profile.goal)
    await message.answer("Цель по калориям? Напишите число или 'авто'")


@dp.message(Profile.goal)
async def profile_goal(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    if text not in ("авто", "auto") and not text.isdigit():
        await message.answer("Напишите число или 'авто'")
        return

    data = await state.get_data()
    await state.clear()

    user = dict(data, days={})
    user["temp"] = await utils.get_temperature(user["city"])
    user["calorie_goal"] = int(text) if text.isdigit() else utils.calorie_goal(user)
    users[message.from_user.id] = user

    temp = user["temp"]
    weather = f"{temp} градусов" if temp is not None else "не удалось узнать, погоду не учитываю"
    await message.answer(
        f"Профиль сохранен.\n"
        f"Погода в городе {user['city']}: {weather}\n"
        f"Норма воды: {utils.water_goal(user)} мл\n"
        f"Цель по калориям: {user['calorie_goal']} ккал"
    )


@dp.message(Food.grams)
async def food_grams(message: Message, state: FSMContext):
    try:
        grams = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Нужно число грамм, например 150")
        return

    data = await state.get_data()
    await state.clear()

    user = get_user(message)
    day = utils.today(user)
    kcal = round(data["kcal"] * grams / 100, 1)
    day["eaten"] += kcal
    await message.answer(
        f"Записано: {kcal} ккал ({data['name']}, {grams:.0f} г).\n"
        f"Всего за сегодня: {day['eaten']:.0f} из {user['calorie_goal']} ккал."
    )


@dp.message(F.text)
async def unknown(message: Message):
    await message.answer("Не знаю такой команды, посмотрите /help")


async def run_health_server():
    # render бесплатно хостит только веб-сервисы, поэтому рядом с ботом висит заглушка
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()


async def main():
    bot = Bot(os.environ["BOT_TOKEN"])
    await run_health_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
