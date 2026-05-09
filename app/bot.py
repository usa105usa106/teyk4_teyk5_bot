import asyncio
import os
import time
from html import escape
from dotenv import load_dotenv
import psutil
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .charting import make_signal_chart
from .config import EXCHANGES, RR_VALUES, TAKE_PROFIT_MODES, TOP_LIMITS
from .scanner import scan_market
from .exchange import resolve_symbols
from .storage import get_settings, init_db, log_trade, save_api_keys, save_settings
from .trade_manager import maybe_execute_trade

load_dotenv()
STARTED_AT = time.time()
PENDING_API_INPUT: dict[int, str] = {}


def universe_label(settings: dict) -> str:
    if settings.get("btc_eth_enabled", False):
        return "BTC/ETH"
    if settings.get("top_enabled", True):
        return f"Top-{settings.get('top_n', 100)}"
    custom = settings.get("custom_symbols") or []
    return f"Custom {len(custom)}" if custom else "Universe OFF"


def owner_allowed(user_id: int) -> bool:
    owner = os.getenv("OWNER_TELEGRAM_ID", "").strip()
    return not owner or str(user_id) == owner


def persistent_menu() -> ReplyKeyboardMarkup:
    """Bottom Telegram keyboard that stays available even if inline buttons disappear."""
    return ReplyKeyboardMarkup(
        [
            ["⬆️ Меню", "🔎 Скан"],
            ["📡 Ping", "❔ Help"],
            ["💤 Sleep", "🚀 Wake"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Нажми ⬆️ Меню, чтобы вернуть кнопки",
    )


def keyboard(settings: dict) -> InlineKeyboardMarkup:
    bot_state = "🟢 Бот ON" if settings["bot_enabled"] else "🔴 Бот OFF"
    auto_state = "🤖 Auto ON" if settings["auto_trade"] else "🤖 Auto OFF"
    mode = "🧪 Paper" if settings["trade_mode"] == "paper" else "💰 Live"
    rows = [
        [InlineKeyboardButton(bot_state, callback_data="toggle_bot"), InlineKeyboardButton("📡 Ping", callback_data="ping")],
        [InlineKeyboardButton(f"Биржа: {settings['exchange'].upper()}", callback_data="cycle_exchange")],
        [InlineKeyboardButton(f"Монеты: {universe_label(settings)}", callback_data="cycle_top"), InlineKeyboardButton(f"RR 1:{settings['rr']:g}", callback_data="cycle_rr")],
        [InlineKeyboardButton(f"TP: {settings.get('tp_mode','dynamic_tp')}", callback_data="cycle_tp"), InlineKeyboardButton(f"Entry: {settings.get('auto_entry_mode','smart_limit')}", callback_data="cycle_entry")],
        [InlineKeyboardButton(f"Скан: {settings['scan_minutes']} мин", callback_data="cycle_scan"), InlineKeyboardButton(f"Runner: {settings.get('runner_size_pct',50)}%", callback_data="cycle_runner")],
        [InlineKeyboardButton(f"🌊 Elliott {'ON' if settings.get('elliott_enabled') else 'OFF'}", callback_data="toggle_elliott"), InlineKeyboardButton(f"🎨 Renderer {'ON' if settings.get('premium_renderer', True) else 'OFF'}", callback_data="toggle_renderer")],
        [InlineKeyboardButton(auto_state, callback_data="toggle_auto"), InlineKeyboardButton(mode, callback_data="toggle_mode")],
        [InlineKeyboardButton("💤 Sleep", callback_data="sleep"), InlineKeyboardButton("🚀 Wake", callback_data="wake")],
        [InlineKeyboardButton("🔎 Скан сейчас", callback_data="scan_now"), InlineKeyboardButton("👁 Watchlist", callback_data="watchlist")],
        [InlineKeyboardButton("🔐 API ключи", callback_data="api_help")],
        [InlineKeyboardButton("❔ Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(rows)


def settings_text(settings: dict) -> str:
    return (
        "<b>Crypto RR Scanner Bot</b>\n\n"
        f"Статус: {'ON' if settings['bot_enabled'] else 'OFF'}\n"
        f"Биржа: <b>{settings['exchange'].upper()}</b>\n"
        f"Монеты: <b>{universe_label(settings)}</b>\n"
        f"Risk/Reward: <b>1:{settings['rr']:g}</b>\n"
        f"TP режим: <b>{settings.get('tp_mode','dynamic_tp')}</b>\n"
        f"Auto Entry: <b>{settings.get('auto_entry_mode','smart_limit').upper()}</b>\n"
        f"Breakeven: <b>{'ON' if settings.get('breakeven_enabled', True) else 'OFF'}</b> | Trailing: <b>{'ON' if settings.get('trailing_enabled', True) else 'OFF'}</b>\n"
        f"Elliott: <b>{'ON' if settings.get('elliott_enabled') else 'OFF'}</b>\n"
        f"Renderer: <b>{'Premium TradingView-style' if settings.get('premium_renderer', True) else 'Simple low-resource'}</b>\n"
        f"Скан: <b>каждые {settings['scan_minutes']} минут</b>\n"
        f"Автоторговля: <b>{'ON' if settings['auto_trade'] else 'OFF'}</b>\n"
        f"Режим: <b>{settings['trade_mode'].upper()}</b>\n\n"
        "Live-режим не включится без ALLOW_LIVE_TRADING=1 и API-ключей."
    )


HELP_TEXT = """
<b>Помощь по командам</b>

/start — открыть главное меню и настройки.
/menu — вернуть нижнее iLine-меню и inline-кнопки.
/help — показать эту помощь.
/ping — время работы, память, CPU и статус.
/scan — ручной скан рынка сейчас.
/jobs — включить/перезапустить плановый скан по текущему периоду.
/period 45 — выставить период скана в минутах. Примеры: /period 30, /period 60, /period 300.
/renderer on — красивый TradingView-style график. /renderer off — простой быстрый график с низкой нагрузкой.
/sleep — остановить тяжёлый сканер и автоторговлю, оставить только Telegram-контроллер.
/wake — включить бота и плановый сканер обратно.
/api binance API_KEY API_SECRET — сохранить API-ключи для биржи.
/new btc — добавить монету в custom watchlist для ручного/планового анализа без автоторговли. Примеры: /new eth, /new sol.
/delete all — удалить custom watchlist и выключить Top-N/BTC-ETH, пока ты снова не выберешь режим монет или не добавишь монеты.

<b>iLine меню внизу</b>
⬆️ Меню — вернуть основные inline-кнопки, если они пропали.
🔎 Скан — ручной скан.
📡 Ping — статус.
❔ Help — помощь.

<b>Кнопки</b>
🟢/🔴 Бот ON/OFF — включает или выключает сигналы и автоторговлю.
📡 Ping — проверка отклика, uptime, RAM, CPU.
Биржа — переключает Binance / BingX / MEXC.
Монеты — переключает Top-10 / 50 / 100 / 200 / 300 / BTC-ETH / OFF. Режим BTC-ETH автоматически сканирует и может автоторговать только BTCUSDT и ETHUSDT.
Custom watchlist — монеты из /new сканируются только как сигналы, без автоторговли.
RR — переключает 1:3 / 1:4 / 1:5.
TP — переключает Fixed TP / Dynamic TP / Runner Mode. По умолчанию Dynamic TP.
Entry — Smart Limit: лимитка внутри Entry Zone.
Runner — размер остатка позиции для Runner Mode.
🌊 Elliott — включает/выключает волновой фильтр Эллиотта и стрелку направления на графике.
🎨 Renderer — ON: красивый TradingView-style график, OFF: простой low-resource график.
Auto — включает/выключает автоторговлю. По умолчанию выключена.
Paper/Live — режим симуляции или реальной торговли. Live дополнительно требует ALLOW_LIVE_TRADING=1.
""".strip()


def schedule_scan_job(context: ContextTypes.DEFAULT_TYPE, user_id: int, minutes: int) -> None:
    """Create or replace the periodic scan job for a user."""
    for job in context.job_queue.get_jobs_by_name(f"scan_{user_id}"):
        job.schedule_removal()
    context.job_queue.run_repeating(
        scheduled_scan,
        interval=int(minutes) * 60,
        first=10,
        name=f"scan_{user_id}",
        data={"user_id": user_id},
    )


async def renderer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        return
    if not context.args or context.args[0].lower() not in {"on", "off"}:
        await update.message.reply_text("Формат: /renderer on или /renderer off")
        return
    s = get_settings(user_id)
    s["premium_renderer"] = context.args[0].lower() == "on"
    save_settings(user_id, s)
    await update.message.reply_html(
        "🎨 Renderer: <b>" + ("ON — Premium TradingView-style" if s["premium_renderer"] else "OFF — Simple low-resource") + "</b>"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        return
    await update.message.reply_html(HELP_TEXT, reply_markup=persistent_menu())


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        return
    s = get_settings(user_id)
    await update.message.reply_text("iLine меню закреплено внизу. Inline-кнопки ниже 👇", reply_markup=persistent_menu())
    await update.message.reply_html(settings_text(s), reply_markup=keyboard(s))


async def period_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        return
    if not context.args:
        await update.message.reply_text("Формат: /period 45 — число минут от 1 до 1440.")
        return
    try:
        minutes = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Период должен быть числом минут. Пример: /period 45")
        return
    if minutes < 1 or minutes > 1440:
        await update.message.reply_text("Период должен быть от 1 до 1440 минут.")
        return
    s = get_settings(user_id)
    s["scan_minutes"] = minutes
    save_settings(user_id, s)
    if s.get("bot_enabled"):
        schedule_scan_job(context, user_id, minutes)
        await update.message.reply_text(f"Период скана установлен: каждые {minutes} минут. Плановый сканер перезапущен.")
    else:
        await update.message.reply_text(f"Период скана установлен: {minutes} минут. Бот сейчас OFF, сканер запустится после /wake или кнопки ON.")


async def sleep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        return
    s = get_settings(user_id)
    s["bot_enabled"] = False
    s["auto_trade"] = False
    save_settings(user_id, s)
    for job in context.job_queue.get_jobs_by_name(f"scan_{user_id}"):
        job.schedule_removal()
    await update.message.reply_text("💤 Sleep включён: сканер и автоторговля остановлены. Telegram-контроллер остаётся доступен для /wake.")


async def wake_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        return
    s = get_settings(user_id)
    s["bot_enabled"] = True
    save_settings(user_id, s)
    schedule_scan_job(context, user_id, int(s["scan_minutes"]))
    await update.message.reply_text(f"🚀 Бот включён. Плановый скан активирован: каждые {s['scan_minutes']} минут.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        await update.message.reply_text("Доступ закрыт.")
        return
    s = get_settings(user_id)
    await update.message.reply_text("iLine меню включено внизу. Нажми ⬆️ Меню, если inline-кнопки пропадут.", reply_markup=persistent_menu())
    await update.message.reply_html(settings_text(s), reply_markup=keyboard(s))


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    """Show bot health and real Telegram response latency in milliseconds."""
    started = time.perf_counter()

    # First send/edit a tiny measuring message. The elapsed time after this await
    # includes Telegram API round-trip, so it no longer shows a fake 0 ms.
    if edit and update.callback_query:
        msg = await update.callback_query.edit_message_text("📡 Измеряю отклик...")
    else:
        msg = await update.message.reply_text("📡 Измеряю отклик...")

    latency_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
    proc = psutil.Process(os.getpid())
    mem = proc.memory_info().rss / 1024 / 1024
    uptime = int(time.time() - STARTED_AT)
    cpu = psutil.cpu_percent(interval=0.05)
    text = (
        "📡 <b>Ping / Status</b>\n"
        f"Отклик Telegram: {latency_ms} ms\n"
        f"Uptime: {uptime // 3600}ч {(uptime % 3600) // 60}м\n"
        f"Память: {mem:.1f} MB\n"
        f"CPU: {cpu:.1f}%"
    )
    await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard(get_settings(update.effective_user.id)) if edit else None)


def cycle(values, current):
    i = values.index(current) if current in values else -1
    return values[(i + 1) % len(values)]


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        await q.edit_message_text("Доступ закрыт.")
        return
    s = get_settings(user_id)
    data = q.data

    if data == "toggle_bot":
        s["bot_enabled"] = not s["bot_enabled"]
        if s["bot_enabled"]:
            schedule_scan_job(context, user_id, int(s["scan_minutes"]))
        else:
            s["auto_trade"] = False
            for job in context.job_queue.get_jobs_by_name(f"scan_{user_id}"):
                job.schedule_removal()
    elif data == "cycle_exchange":
        s["exchange"] = cycle(EXCHANGES, s["exchange"])
    elif data == "cycle_top":
        # Universe cycle: Top-10/50/100/200/300 -> BTC/ETH -> OFF -> Top-10.
        if s.get("btc_eth_enabled", False):
            s["btc_eth_enabled"] = False
            s["top_enabled"] = False
        elif s.get("top_enabled", True):
            current_top = int(s.get("top_n", 100))
            if current_top == TOP_LIMITS[-1]:
                s["top_enabled"] = False
                s["btc_eth_enabled"] = True
            else:
                s["top_n"] = cycle(TOP_LIMITS, current_top)
        else:
            s["top_enabled"] = True
            s["btc_eth_enabled"] = False
            s["top_n"] = TOP_LIMITS[0]
    elif data == "cycle_rr":
        s["rr"] = cycle(RR_VALUES, float(s["rr"]))
    elif data == "cycle_tp":
        s["tp_mode"] = cycle(TAKE_PROFIT_MODES, s.get("tp_mode", "dynamic_tp"))
        s["trade_management_mode"] = s["tp_mode"]
    elif data == "cycle_entry":
        s["auto_entry_mode"] = "smart_limit"
    elif data == "cycle_runner":
        s["runner_size_pct"] = cycle([25, 50, 75], int(s.get("runner_size_pct", 50)))
    elif data == "toggle_elliott":
        s["elliott_enabled"] = not bool(s.get("elliott_enabled", False))
    elif data == "toggle_renderer":
        s["premium_renderer"] = not bool(s.get("premium_renderer", True))
    elif data == "cycle_scan":
        s["scan_minutes"] = cycle([15, 30, 60, 120, 300], int(s["scan_minutes"]))
        if s.get("bot_enabled"):
            schedule_scan_job(context, user_id, int(s["scan_minutes"]))
    elif data == "toggle_auto":
        s["auto_trade"] = not s["auto_trade"]
    elif data == "toggle_mode":
        s["trade_mode"] = "live" if s["trade_mode"] == "paper" else "paper"
    elif data == "ping":
        await ping(update, context, edit=True)
        return
    elif data == "help":
        await q.edit_message_text(HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=keyboard(s))
        return
    elif data == "sleep":
        s["bot_enabled"] = False
        s["auto_trade"] = False
        save_settings(user_id, s)
        for job in context.job_queue.get_jobs_by_name(f"scan_{user_id}"):
            job.schedule_removal()
        await q.edit_message_text("💤 Sleep включён: сканер и автоторговля остановлены. Telegram-контроллер остаётся доступен.", reply_markup=keyboard(s))
        return
    elif data == "wake":
        s["bot_enabled"] = True
        save_settings(user_id, s)
        schedule_scan_job(context, user_id, int(s["scan_minutes"]))
        await q.edit_message_text(settings_text(s), parse_mode=ParseMode.HTML, reply_markup=keyboard(s))
        return
    elif data == "watchlist":
        custom = s.get("custom_symbols") or []
        text = (
            "👁 <b>Custom watchlist</b>\n\n"
            f"Universe: <b>{universe_label(s)}</b>\n"
            f"Монеты: <b>{', '.join(custom) if custom else 'пусто'}</b>\n\n"
            "Добавить: <code>/new btc</code>, <code>/new eth</code>, <code>/new sol</code>\n"
            "Очистить и выключить Top-N/BTC-ETH: <code>/delete all</code>\n\n"
            "Важно: custom-монеты сканируются только для сигналов. Автоторговля по ним не открывается."
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard(s))
        return
    elif data == "api_help":
        PENDING_API_INPUT[user_id] = s["exchange"]
        await q.edit_message_text(
            "🔐 Отправь API для текущей биржи одной строкой:\n\n"
            f"<code>/api {s['exchange']} API_KEY API_SECRET</code>\n\n"
            "Для BingX/MEXC, если нужен password/passphrase:\n"
            f"<code>/api {s['exchange']} API_KEY API_SECRET PASSWORD</code>\n\n"
            "Рекомендация: сначала ключи только read-only/paper, без вывода средств.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard(s),
        )
        return
    elif data == "scan_now":
        await q.edit_message_text("🔎 Запускаю ручной скан...", parse_mode=ParseMode.HTML)
        await run_scan_for_user(context, user_id, manual=True)
        await context.bot.send_message(user_id, settings_text(get_settings(user_id)), parse_mode=ParseMode.HTML, reply_markup=keyboard(get_settings(user_id)))
        return

    save_settings(user_id, s)
    await q.edit_message_text(settings_text(s), parse_mode=ParseMode.HTML, reply_markup=keyboard(s))


async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        return
    if not context.args:
        await update.message.reply_text("Формат: /new btc или /new btc eth sol")
        return
    s = get_settings(user_id)
    coins = []
    for raw in context.args:
        coin = raw.strip().upper().replace("/USDT", "").replace("USDT", "").replace(",", "")
        if coin and coin not in coins:
            coins.append(coin)
    if not coins:
        await update.message.reply_text("Не нашёл монету. Пример: /new btc")
        return
    # Check what can be resolved on the currently selected exchange.
    try:
        resolved = await resolve_symbols(s["exchange"], coins)
    except Exception as exc:
        await update.message.reply_text(f"Не смог проверить монеты на {s['exchange'].upper()}: {exc}")
        return
    resolved_bases = {r.split('/')[0].upper() for r in resolved}
    valid = [c for c in coins if c in resolved_bases]
    invalid = [c for c in coins if c not in resolved_bases]
    current = list(dict.fromkeys([str(x).upper() for x in (s.get("custom_symbols") or [])]))
    for c in valid:
        if c not in current:
            current.append(c)
    s["custom_symbols"] = current
    # /new intentionally switches the scanner to custom-watchlist mode.
    # User can enable Top-N again by pressing the Top button.
    s["top_enabled"] = False
    s["btc_eth_enabled"] = False
    save_settings(user_id, s)
    msg = "✅ Добавлено в custom watchlist: " + (", ".join(valid) if valid else "ничего")
    if invalid:
        msg += "\n⚠️ Не нашёл USDT perpetual/futures на текущей бирже: " + ", ".join(invalid)
    msg += "\n\nTop-N выключен. Эти монеты будут сканироваться как SIGNAL ONLY — без автоторговли. Чтобы вернуть Top-N, нажми кнопку Top OFF."
    await update.message.reply_text(msg)


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        return
    if not context.args or context.args[0].lower() != "all":
        await update.message.reply_text("Формат: /delete all")
        return
    s = get_settings(user_id)
    s["custom_symbols"] = []
    s["top_enabled"] = False
    s["btc_eth_enabled"] = False
    s["auto_trade"] = False
    save_settings(user_id, s)
    await update.message.reply_text("🗑 Все custom-монеты удалены. Top-N и BTC/ETH выключены. Автоторговля выключена. Сканер будет пустым, пока ты не выберешь режим монет или не добавишь монеты через /new btc.")


async def api_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        return
    parts = update.message.text.strip().split()
    if len(parts) < 4:
        await update.message.reply_text("Формат: /api binance API_KEY API_SECRET [PASSWORD]")
        return
    _, exchange, api_key, api_secret, *rest = parts
    password = rest[0] if rest else ""
    try:
        save_api_keys(user_id, exchange.lower(), api_key, api_secret, password)
        await update.message.reply_text(f"API ключи для {exchange.upper()} сохранены зашифрованно.")
    except Exception as e:
        await update.message.reply_text(f"Не удалось сохранить API: {e}")


async def run_scan_for_user(context: ContextTypes.DEFAULT_TYPE, user_id: int, manual: bool = False):
    s = get_settings(user_id)
    if not s.get("bot_enabled"):
        if manual:
            await context.bot.send_message(user_id, "Бот выключен. Включи через кнопку 🟢/🔴.")
        return
    try:
        results = await scan_market(s)
    except Exception as e:
        await context.bot.send_message(user_id, f"Ошибка скана: {escape(str(e))}", parse_mode=ParseMode.HTML)
        return

    if not results:
        if manual:
            universe = []
            if s.get("btc_eth_enabled", False):
                universe.append("BTC/ETH")
            elif s.get("top_enabled", True):
                universe.append(f"Top-{s.get('top_n', 100)}")
            if s.get("custom_symbols"):
                universe.append("custom: " + ", ".join(s.get("custom_symbols") or []))
            await context.bot.send_message(user_id, f"Сигналов под RR 1:{s['rr']:g} сейчас нет. Universe: {', '.join(universe) if universe else 'пусто — выбери Top-N/BTC-ETH или добавь /new btc'}.")
        return

    for signal, df in results[:5]:
        trade_result = await maybe_execute_trade(user_id, signal, s)
        status = trade_result.get("status", "unknown")
        plan = trade_result.get("plan", {})
        log_trade(user_id, signal, s["trade_mode"], status=status)
        long_p = float(signal.get("long_probability", signal.get("probability", 0)))
        short_p = float(signal.get("short_probability", 100 - long_p))
        conf_score = float(signal.get("confidence_score", signal.get("probability", 0)))
        conf_label = signal.get("confidence_label", "MEDIUM")
        conf_icon = "🟢" if conf_label == "HIGH" else "🟡" if conf_label == "MEDIUM" else "🔴"
        risk_pct = abs((signal["entry"] - signal["stop"]) / signal["entry"] * 100)
        reward_pct = abs((signal["take_profit"] - signal["entry"]) / signal["entry"] * 100)
        caption = (
            f"{'🟢' if signal['side']=='LONG' else '🔴'} <b>SIGNAL: {signal['side']} {escape(signal['symbol'])}</b>\n\n"
            f"📊 <b>Market Analysis</b>\n"
            f"🟢 LONG probability: <b>{long_p:.0f}%</b>\n"
            f"🔴 SHORT probability: <b>{short_p:.0f}%</b>\n"
            f"🎯 Decision: <b>{signal['side']}</b>\n"
            f"{conf_icon} Confidence score: <b>{conf_score:.0f}/100 — {escape(str(conf_label))}</b>\n\n"
            f"🏦 Exchange: <b>{signal['exchange'].upper()}</b>\n"
            f"💰 Entry Zone: <code>{signal.get('entry_zone_low', signal['entry'])} – {signal.get('entry_zone_high', signal['entry'])}</code>\n"
            f"🤖 Auto Entry: <b>{escape(str(plan.get('entry_mode', s.get('auto_entry_mode', 'smart_limit')))).upper()}</b> @ <code>{plan.get('entry_price', signal['entry'])}</code>\n"
            f"🛑 Stop Loss: <code>{signal['stop']}</code>\n"
            f"🎯 Take Profit: <code>{signal['take_profit']}</code>\n"
            f"📐 Risk/Reward: <b>1:{signal['rr']:g}</b>\n"
            f"📉 Risk to SL: <b>-{risk_pct:.2f}%</b>\n"
            f"📈 Potential to TP: <b>+{reward_pct:.2f}%</b>\n"
            f"🌊 Elliott: <b>{'ON' if signal.get('elliott_enabled') else 'OFF'}</b>"
            f"{(' — ' + escape(str(signal.get('elliott_direction', 'NEUTRAL'))) + ' / ' + escape(str(signal.get('elliott_wave', '')))) if signal.get('elliott_enabled') else ''}\n\n"
            f"🧠 Почему {signal['side']}: {escape(signal['reason'])}\n\n"
            f"🧩 Management: <b>{escape(str(s.get('tp_mode', 'dynamic_tp')))}</b> | BE: <b>{'ON' if s.get('breakeven_enabled', True) else 'OFF'}</b> | Trailing: <b>{'ON' if s.get('trailing_enabled', True) else 'OFF'}</b>\n"
            f"🚪 Exit rules: TP / trailing / reversal / structure break / RSI divergence / Elliott completion\n"
            f"🤖 Auto status: <b>{escape(str(status))}</b>"
        )
        img = make_signal_chart(df, signal, premium=bool(s.get("premium_renderer", True)))
        await context.bot.send_photo(user_id, photo=open(img, "rb"), caption=caption, parse_mode=ParseMode.HTML)
        if s.get("auto_trade") and status in {"paper_limit_placed", "live_limit_sent"}:
            await context.bot.send_message(
                user_id,
                f"🤖 <b>Автовход подготовлен</b>\n"
                f"Сделал автовход лимитной заявкой по цене: <code>{plan.get('entry_price')}</code>\n"
                f"SL: <code>{plan.get('stop_loss')}</code> | TP: <code>{plan.get('take_profit')}</code>\n"
                f"Breakeven trigger: <code>{plan.get('breakeven_trigger')}</code>\n"
                f"Trailing trigger: <code>{plan.get('trailing_trigger')}</code>\n"
                f"Mode: <b>{escape(str(s.get('trade_mode', 'paper')).upper())}</b> / <b>{escape(str(s.get('tp_mode', 'dynamic_tp')))}</b>",
                parse_mode=ParseMode.HTML,
            )


async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data["user_id"]
    await run_scan_for_user(context, user_id)


async def jobs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    s = get_settings(user_id)
    schedule_scan_job(context, user_id, int(s["scan_minutes"]))
    await update.message.reply_text(f"Плановый скан активирован: каждые {s['scan_minutes']} минут.")


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 Сканирую рынок...")
    await run_scan_for_user(context, update.effective_user.id, manual=True)


async def text_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        return
    text = (update.message.text or "").strip()
    if text == "⬆️ Меню":
        await menu_cmd(update, context)
    elif text == "🔎 Скан":
        await scan_cmd(update, context)
    elif text == "📡 Ping":
        await ping(update, context)
    elif text == "❔ Help":
        await help_cmd(update, context)
    elif text == "💤 Sleep":
        await sleep_cmd(update, context)
    elif text == "🚀 Wake":
        await wake_cmd(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("BOT ERROR:", context.error)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
    init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("api", api_cmd))
    app.add_handler(CommandHandler("new", new_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("jobs", jobs_cmd))
    app.add_handler(CommandHandler("period", period_cmd))
    app.add_handler(CommandHandler("renderer", renderer_cmd))
    app.add_handler(CommandHandler("sleep", sleep_cmd))
    app.add_handler(CommandHandler("wake", wake_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_menu_handler))
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
