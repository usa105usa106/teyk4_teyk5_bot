# Crypto RR Telegram Bot

MVP Telegram-бота для скана крипторынка по Binance / BingX / MEXC через CCXT.

> Важно: бот не гарантирует прибыль и не даёт финансовых рекомендаций. Сигналы — вероятностный технический скоринг. Перед live-режимом обязательно прогоните paper-тест и бэктест.

## Что сделано

- Telegram-меню кнопками.
- Глобальная кнопка Bot ON/OFF.
- Кнопка Ping: uptime, память, CPU.
- Переключение биржи: Binance / BingX / MEXC.
- Переключение Top-10 / 50 / 100 / 200 / 300 монет.
- Переключение RR: 1:3 / 1:4 / 1:5.
- Переключение TP-режима: TP2 / TP3 / TRAIL.
- Дефолты: TP2, Top-100, скан каждые 30 минут.
- Ручной скан кнопкой и командой `/scan`.
- Плановый скан командой `/jobs`.
- Сигналы с entry / SL / TP / probability / reason.
- PNG-график: красная зона entry-stop, зелёная зона до max TP.
- Хранение настроек в SQLite.
- API-ключи через Telegram-команду `/api`, хранение через Fernet encryption.
- Paper/live режимы. Live дополнительно заблокирован переменной `ALLOW_LIVE_TRADING=1` и требует `fixed_amount` в настройках/коде.
- Подготовлено под Railway: `railway.toml`, `Procfile`, `requirements.txt`.

## Команды

- `/start` — меню.
- `/ping` — статус.
- `/scan` — скан сейчас.
- `/jobs` — включить повторяющийся скан по текущему интервалу.
- `/api binance API_KEY API_SECRET` — сохранить ключи.
- `/api bingx API_KEY API_SECRET PASSWORD` — если биржа требует passphrase/password.

## Railway deploy

Railway запускает сервис через configured/detected start command; в проекте добавлен `railway.toml` со `startCommand = "python -m app.bot"`.

1. Создайте Telegram-бота через BotFather и получите токен.
2. Загрузите проект в GitHub.
3. В Railway создайте новый Project → Deploy from GitHub repo.
4. Добавьте переменные окружения:

```env
TELEGRAM_BOT_TOKEN=ваш_токен
OWNER_TELEGRAM_ID=ваш_telegram_user_id
FERNET_KEY=сгенерированный_ключ
ALLOW_LIVE_TRADING=0
```

Сгенерировать `FERNET_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

5. После деплоя откройте Telegram и отправьте `/start`.
6. Нажмите `/jobs`, чтобы включить плановый скан. Кнопка «Скан сейчас» работает сразу.

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
# заполните .env
python -m app.bot
```

## Как работает стратегия

Скоринг использует:

- EMA20/50/200 для направления тренда;
- пробой/возврат к EMA20;
- RSI;
- объём выше среднего;
- импульс последней свечи;
- ATR и локальный swing для стопа;
- фильтр RR 1:3 / 1:4 / 1:5;
- фильтр реалистичности цели относительно диапазона последних свечей.

Сигнал появляется только если итоговый скоринг >= 68 и выбранный RR достижим по текущей структуре.

## Безопасность live-торговли

Live-торговля намеренно заблокирована несколькими слоями:

1. В Telegram должен быть включён Auto ON.
2. Режим должен быть Live.
3. В Railway/env должно стоять `ALLOW_LIVE_TRADING=1`.
4. Должны быть API-ключи.
5. В коде/настройках должен быть задан `fixed_amount`.

Так сделано, чтобы бот случайно не открыл реальную сделку при первом запуске.

## Что стоит добавить перед реальными деньгами

- Бэктест по истории каждой биржи.
- Расчёт размера позиции от баланса и risk_pct.
- Биржевые стоп/тейк ордера после входа.
- Max daily loss.
- Лимит одновременно открытых позиций.
- Учёт комиссий и funding.
- Логи в Postgres вместо SQLite для Railway production.
- Webhook вместо polling, если нужен полностью web-based режим.
