import html
import io
import json
import os
from datetime import date, timedelta

import aiohttp
import matplotlib

matplotlib.use("Agg")  # иначе matplotlib пытается открыть окно
import matplotlib.pyplot as plt

WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
OFF_URL = "https://search.openfoodfacts.org/search"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# met для типов тренировок, по ним считаются калории
WORKOUTS = {
    "бег": 10, "ходьба": 3.5, "велосипед": 8, "плавание": 8, "силовая": 6,
    "йога": 3, "футбол": 7, "танцы": 5, "лыжи": 9, "скакалка": 11,
}


def water_goal(user):
    # 30 мл на кг, плюс за активность, жару и тренировки за сегодня
    goal = user["weight"] * 30
    goal += 500 * (user["activity"] // 30)
    temp = user.get("temp")
    if temp is not None and temp > 25:
        goal += 500
    return round(goal + today(user)["extra_water"])


def calorie_goal(user):
    base = 10 * user["weight"] + 6.25 * user["height"] - 5 * user["age"]
    base += 5 if user["sex"] == "м" else -161
    if user["activity"] < 30:
        base += 200
    elif user["activity"] < 60:
        base += 300
    else:
        base += 400
    return round(base)


def workout_calories(kind, minutes, weight):
    met = WORKOUTS.get(kind.lower(), 6)  # незнакомую тренировку считаем средней
    return round(met * 3.5 * weight / 200 * minutes)


def today(user):
    key = date.today().isoformat()
    if key not in user["days"]:
        user["days"][key] = {"water": 0, "eaten": 0, "burned": 0, "extra_water": 0}
    return user["days"][key]


async def get_temperature(city):
    key = os.environ.get("OWM_API_KEY")
    if not key:
        return None
    params = {"q": city, "appid": key, "units": "metric", "lang": "ru"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(WEATHER_URL, params=params,
                                   timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
    except Exception as e:
        print("openweathermap не ответил:", e)
        return None
    return data.get("main", {}).get("temp")


async def ask_llm(prompt, max_tokens=300):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, json=body, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("openrouter не ответил:", e)
        return None


async def search_openfoodfacts(name):
    params = {"q": name, "page_size": 20,
              "fields": "product_name,product_name_ru,nutriments"}
    headers = {"User-Agent": "hw-bot/1.0"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(OFF_URL, params=params, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
    except Exception as e:
        print("openfoodfacts не ответил:", e)
        return None
    for p in data.get("hits", []):
        kcal = (p.get("nutriments") or {}).get("energy-kcal_100g")
        title = p.get("product_name_ru") or p.get("product_name")
        # в выдаче полно продуктов вообще без калорийности, их пропускаем
        if kcal and title:
            # в названиях попадаются html
            return {"name": html.unescape(title), "kcal": float(kcal), "source": "openfoodfacts"}
    return None


async def get_food_calories(name):
    # сначала openfoodfacts, если там ничего нет, спрашиваем у модели
    found = await search_openfoodfacts(name)
    if found:
        return found

    answer = await ask_llm(
        f'Сколько килокалорий в 100 г продукта "{name}"? '
        'Ответь только json вида {"name": "...", "kcal_100g": 0} без пояснений.',
        max_tokens=100,
    )
    if answer:
        try:
            text = answer[answer.index("{"):answer.rindex("}") + 1]
            data = json.loads(text)
            return {"name": data.get("name", name), "kcal": float(data["kcal_100g"]),
                    "source": "оценка модели"}
        except Exception:
            pass
    return None


async def get_recommendations(user):
    d = today(user)
    w_goal = water_goal(user)
    c_goal = user["calorie_goal"]
    prompt = (
        "Ты помощник по питанию. Данные пользователя за сегодня:\n"
        f"вес {user['weight']} кг, рост {user['height']} см, возраст {user['age']}, "
        f"активность {user['activity']} мин в день, город {user['city']}.\n"
        f"Воды выпито {d['water']} из {w_goal} мл. "
        f"Калорий съедено {d['eaten']} из {c_goal}, сожжено {d['burned']}.\n"
        "Дай короткий совет на русском: что выпить и съесть, какую тренировку сделать. "
        "Не больше 6 строк, без markdown."
    )
    answer = await ask_llm(prompt)
    return answer.strip() if answer else None


def progress_chart(user):
    # вода и калории за последнюю неделю
    days = [date.today() - timedelta(days=i) for i in range(6, -1, -1)]
    labels = [d.strftime("%d.%m") for d in days]
    empty = {"water": 0, "eaten": 0, "burned": 0, "extra_water": 0}
    stats = [user["days"].get(d.isoformat(), empty) for d in days]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 7))

    ax1.bar(labels, [s["water"] for s in stats], color="#4a90d9")
    ax1.axhline(water_goal(user), color="red", linestyle="--", label="норма")
    ax1.set_title("Вода, мл")
    ax1.legend()

    ax2.bar(labels, [s["eaten"] for s in stats], color="#f0ad4e", label="съедено")
    ax2.bar(labels, [-s["burned"] for s in stats], color="#5cb85c", label="сожжено")
    ax2.axhline(user["calorie_goal"], color="red", linestyle="--", label="цель")
    ax2.axhline(0, color="black", linewidth=0.8)
    ax2.set_title("Калории, ккал")
    ax2.legend()

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return buf.getvalue()
