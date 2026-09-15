# ДЗ 2, телеграм бот


* `bot.py` - хендлеры, FSM, middleware с логами
* `utils.py` - формулы, запросы к апи, графики

## Апи

* погода - OpenWeatherMap по названию города
* калорийность - поиск в OpenFoodFacts (новый search, у старого cgi/search.pl выдача хуже), берется первый продукт, у которого вообще указана калорийность.
  Если не нашли ничего, спрашиваем у модели через openrouter
* рекомендации - openrouter, модель получает текущий прогресс и отвечает советом

## Деплой на render

New - Web Service, репозиторий, runtime Docker. В Environment добавить `BOT_TOKEN`,
`OWM_API_KEY`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`. Бот работает на long polling, но рядом поднимается
маленький http-сервер на `PORT`, иначе render считает сервис упавшим. Команды пользователей
видно в логах сервиса.
