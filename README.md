# Crypto RR Telegram Bot

MVP Telegram-бота для скана крипторынка по MEXC / BingX / MEXC через CCXT.

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
- `/help` — помощь по всем командам и кнопкам.
- `/ping` — статус: uptime, память, CPU.
- `/scan` — скан сейчас.
- `/jobs` — включить/перезапустить повторяющийся скан по текущему интервалу.
- `/period 45` — выставить период скана в минутах. Можно `/period 30`, `/period 60`, `/period 300` и т.д.
- `/sleep` — остановить сканер и автоторговлю, оставить Telegram-контроллер доступным.
- `/wake` — включить бота и плановый скан обратно.
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

## Обновление: sleep/wake, help и period

Добавлено:

- `/help` — показывает все команды и назначение кнопок.
- `/period <минуты>` — задаёт любой интервал скана в минутах от 1 до 1440.
  - Примеры: `/period 30`, `/period 45`, `/period 60`, `/period 300`.
  - Если бот включён, плановый сканер автоматически перезапускается с новым периодом.
- `/sleep` — останавливает тяжёлый сканер и автоторговлю, но оставляет Telegram-контроллер живым.
- `/wake` — включает бота и заново запускает плановый сканер.
- Кнопки `💤 Sleep`, `🚀 Wake`, `❔ Help` добавлены в меню.
- Кнопка Bot OFF теперь также снимает активную job-задачу сканера и принудительно выключает Auto Trade.

Важно для Railway: `/sleep` снижает нагрузку, потому что сканер и торговая логика не работают, но сам Railway-сервис остаётся запущенным, чтобы Telegram-команда `/wake` могла сработать. Полностью обнулить runtime можно только остановкой Deployment в Railway.

## Обновление: LONG/SHORT probability + Confidence Score

В сигналы добавлен расширенный анализ:

- `LONG probability` — расчетная вероятность лонг-сценария.
- `SHORT probability` — расчетная вероятность шорт-сценария.
- `Decision` — итоговое направление, которое выбрал алгоритм.
- `Confidence score` — общий балл качества сетапа от 0 до 100.
- Уровни качества:
  - `HIGH` — сильный сетап, лучший кандидат для сигнала.
  - `MEDIUM` — допустимый сетап, но с меньшим запасом качества.
  - `LOW` — слабый/конфликтный сетап, по умолчанию не отправляется.

Важно: проценты и confidence score — это не гарантия прибыли, а скоринг на основе тренда, EMA, RSI, объема, импульса, структуры рынка и выбранного Risk/Reward.

Пример сообщения:

```text
🟢 SIGNAL: LONG BTC/USDT:USDT

📊 Market Analysis
🟢 LONG probability: 72%
🔴 SHORT probability: 28%
🎯 Decision: LONG
🟢 Confidence score: 81/100 — HIGH

🏦 Exchange: BINANCE
💰 Entry: 66000.0
🛑 Stop Loss: 64325.0
🎯 Take Profit: 71150.0
📐 Risk/Reward: 1:4
📉 Risk to SL: -2.54%
📈 Potential to TP: +7.80%

🧠 Почему LONG: тренд выше EMA20/50/200, объем выше среднего, импульс последней свечи | LONG 72% / SHORT 28%

🤖 Auto status: skipped_auto_off
```

## Elliott ON/OFF

Добавлен опциональный модуль `🌊 Elliott`.

Что делает:
- включается/выключается кнопкой `🌊 Elliott ON/OFF`;
- при включении бот ищет упрощённую pivot-структуру волн;
- добавляет Elliott-фактор в LONG/SHORT probability и confidence score;
- если направление Elliott совпадает с сетапом, сигнал получает бонус;
- если Elliott против сетапа или структура мутная, сигнал получает штраф;
- на графике появляется синяя стрелка предполагаемого направления и подпись Elliott.

Важно: Elliott-модуль — эвристический фильтр, а не гарантия. Он помогает отсеивать часть слабых сетапов, но не должен использоваться как единственный источник входа.

## Обновление: Smart Limit Entry + Trade Management

Добавлен отдельный модуль `app/trade_manager.py`.

Что добавлено:

- `Auto Entry Mode: smart_limit` — автоторговля больше не входит market-ордером по факту сигнала.
- Бот строит `Entry Zone` и рассчитывает лимитную цену внутри зоны.
- Для LONG лимитка ставится ближе к выгодной нижней части зоны.
- Для SHORT лимитка ставится ближе к выгодной верхней части зоны.
- После подготовки автовхода бот присылает отдельное сообщение:

```text
🤖 Автовход подготовлен
Сделал автовход лимитной заявкой по цене: 75000
SL: 73500 | TP: 81000
Breakeven trigger: 78000
Trailing trigger: 79500
Mode: PAPER / dynamic_tp
```

### Режимы сопровождения сделки

В настройках TP теперь переключается между:

1. `fixed_tp` — фиксированный выход по TP.
2. `dynamic_tp` — режим по умолчанию: TP + breakeven + trailing + динамическое сопровождение.
3. `runner` — часть позиции можно тянуть дальше тренда после основной цели.

По умолчанию включено:

```text
TP Mode: dynamic_tp
Auto Entry: smart_limit
Breakeven: ON
Trailing: ON
Runner size: 50%
```

### Логика сопровождения

После входа бот планирует:

1. Вход лимитной заявкой внутри Entry Zone.
2. Контроль риска через SL.
3. Breakeven trigger после движения примерно на 1:2.
4. Trailing trigger после движения примерно на 1:3.
5. Dynamic TP / Runner Mode в зависимости от настроек.
6. Exit по правилам.

### Условия выхода

Бот учитывает следующие причины выхода:

- TP reached;
- trailing stop;
- reversal signal;
- structure break;
- RSI divergence;
- Elliott completion.

### Важное по live-торговле

В live-режиме бот отправляет лимитный entry-ордер и best-effort SL/TP через CCXT. У разных бирж разные параметры stop/take-profit ордеров, поэтому перед реальными деньгами обязательно проверить Binance/BingX/MEXC отдельно на testnet или минимальном размере. Paper-режим безопасен и показывает, какие ордера были бы выставлены.

## Custom watchlist: свои монеты без автоторговли

Добавлены команды для ручного списка монет:

```text
/new btc
/new eth
/new sol
/new btc eth sol
```

Что происходит:
- бот проверяет, есть ли USDT futures/perpetual-пара на текущей выбранной бирже;
- добавляет монету в `custom_symbols`;
- выключает Top-N режим, чтобы скан шёл только по указанным монетам;
- такие монеты помечаются как `CUSTOM WATCHLIST — SIGNAL ONLY`;
- автоторговля по ним не открывается даже если `Auto Trade: ON`.

Автоторговля разрешена только для сигналов из автоматического Top-N scanner.

Очистка:

```text
/delete all
```

Команда:
- удаляет все custom-монеты;
- выключает Top-N;
- выключает Auto Trade;
- оставляет сканер пустым, пока пользователь не включит Top-N кнопкой или не добавит монеты через `/new`.

В интерфейсе добавлена кнопка `👁 Watchlist`, где показывается статус Top-N и список своих монет.

## Обновление: режим BTC/ETH

Добавлен отдельный режим выбора монет `BTC/ETH` рядом с Top-N.

Кнопка `Монеты` теперь циклически переключает:

```text
Top-10 -> Top-50 -> Top-100 -> Top-200 -> Top-300 -> BTC/ETH -> Universe OFF -> Top-10
```

В режиме `BTC/ETH` бот:

- сканирует только BTCUSDT и ETHUSDT на выбранной бирже;
- присылает автоматические сигналы только по BTC и ETH;
- может открывать автоторговлю только по BTC и ETH, если `Auto ON`, выбран `paper/live`, заданы API-ключи и разрешён live-режим через `ALLOW_LIVE_TRADING=1`;
- не смешивает этот режим с Top-N.

Custom watchlist через `/new btc` остаётся отдельным режимом `SIGNAL ONLY`: монеты из `/new` не открывают автоторговлю.

## Chart renderer update
- Signal charts are now candlestick-based instead of line charts.
- Price scale is shown on the right side.
- Entry Zone is drawn in a green frame with separate Entry High / Entry / Entry Low labels.
- Stop Loss and Take Profit labels are placed on the right with automatic spacing to prevent overlaps.
- Elliott ON draws swing-based 1-2-3-4-5 and A-B-C labels when enough pivots are detected; if structure is unclear, the bot does not force fake waves.
- Elliott info box is placed in the upper-left corner and does not cover Entry Zone.


## Обновление
- Биржа по умолчанию: MEXC.
- График: усиленная синяя пунктирная стрелка Elliott Expected Move.


## Renderer ON/OFF

Команда:

```text
/renderer on
/renderer off
```

- `ON` — красивый TradingView-style график: свечи, объёмы, Elliott-разметка, стрелка Expected Move, Entry/SL/TP labels. Требует больше CPU/RAM и строится дольше.
- `OFF` — простой low-resource график: линия цены + Entry Zone + SL/TP. Быстрее и легче для Railway.

Также можно переключать кнопкой `🎨 Renderer ON/OFF` в меню.

## Update: strict Elliott + iLine menu

### Strict Elliott filter
When `Elliott ON` is enabled, signal filtering is stricter:

- A signal is sent only when the main strategy direction matches Elliott bias.
- Elliott structure must be `VALID` or `POSSIBLE`.
- Opposite Elliott bias, neutral, unclear, or invalid structure is skipped.
- Auto-trading is stricter than signals: it is allowed only when Elliott structure is `VALID` and confidence is `HIGH`.

### Correct Elliott drawing
The premium renderer now draws wave labels only when the wave structure is consistent:

- 5-wave impulse: exactly `1-2-3-4-5` labels.
- 3-wave correction: exactly `A-B-C` labels.
- `VALID` structures use solid lines.
- `POSSIBLE` structures use dashed lines.
- `INVALID/UNCLEAR` structures are not force-drawn.

### iLine bottom menu
A persistent bottom menu was added so Telegram buttons can be restored at any time:

- `/menu` opens the inline settings menu again.
- `⬆️ Меню` restores buttons if they disappeared.
- `🔎 Скан`, `📡 Ping`, `❔ Help`, `💤 Sleep`, `🚀 Wake` are always available from the bottom keyboard.

### Defaults

- Default exchange: `MEXC`.
- Default TP mode: `dynamic_tp`.
- Default renderer: `OFF` low-resource mode. Use `/renderer on` for premium TradingView-style charts.

## Обновление Elliott strict v2

- Elliott-разметка стала строже: маленькие движения и рыночный шум больше не размечаются как A-B-C или 1-2-3-4-5.
- Добавлены фильтры минимального swing distance, candle separation и пропорциональности волн.
- A-B-C получает `VALID` только если есть нормальные A/B/C ноги и подтверждение от точки C.
- Если структура слабая — будет `POSSIBLE` или Elliott не рисуется.
- Автоторговля по-прежнему разрешается только при `Elliott VALID + Confidence HIGH`.

## Elliott v3.1 — классическая логика 5-3

В этой версии модуль Elliott переписан по классическому принципу:

- Импульс = 5 волн: `1-2-3-4-5`.
- Коррекция = 3 волны: `A-B-C`.
- После завершённого импульса вверх бот ожидает коррекцию `A-B-C` вниз.
- После завершённого импульса вниз бот ожидает коррекцию `A-B-C` вверх.
- После завершённой коррекции `A-B-C` вниз бот ожидает новый импульс вверх.
- После завершённой коррекции `A-B-C` вверх бот ожидает новый импульс вниз.

График теперь не рисует повторяющиеся цифры и буквы. Если нет чистых 5 точек для импульса или 3 точек для коррекции, волновая разметка не рисуется принудительно.

Статусы Elliott:

- `VALID` — структура прошла строгие правила.
- `POSSIBLE` — структура похожа, но требует подтверждения.
- `INVALID` — структура не используется в сигнале и не размечается на графике.

Для автоторговли Elliott остаётся строгим фильтром: при включённом Elliott автоторговля разрешается только при `VALID + HIGH confidence`.

## Elliott engine v5 strict 5+3

Обновление в этом архиве:
- Elliott больше не выбирает случайные последние 3 точки как полноценный сигнал.
- Сначала ищется полный классический цикл 5+3:
  - LONG setup: `1-2-3-4-5` вверх, затем `A-B-C` вниз, после подтверждённого отскока от `C` ожидается новый импульс вверх.
  - SHORT setup: `1-2-3-4-5` вниз, затем `A-B-C` вверх, после подтверждённого отбоя от `C` ожидается новый импульс вниз.
- На графике теперь рисуется полный набор меток `1,2,3,4,5,A,B,C`, если структура VALID.
- Если полный цикл не найден, Elliott не должен усиливать сигнал и не должен рисовать фиктивную разметку.
- Сигнал при Elliott ON всё ещё проходит только когда Elliott VALID совпадает с направлением сделки.
