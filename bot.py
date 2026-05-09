import asyncio
import os
import time
from html import escape
from dotenv import load_dotenv
import psutil
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .charting import make_signal_chart
from .config import EXCHANGES, RR_VALUES, TAKE_PROFIT_MODES, TOP_LIMITS
from .scanner import scan_market
from .storage import get_settings, init_db, log_trade, save_api_keys, save_settings
from .trading import maybe_execute_trade

load_dotenv()
STARTED_AT = time.time()
PENDING_API_INPUT: dict[int, str] = {}


def owner_allowed(user_id: int) -> bool:
    owner = os.getenv("OWNER_TELEGRAM_ID", "").strip()
    return not owner or str(user_id) == owner


def keyboard(settings: dict) -> InlineKeyboardMarkup:
    bot_state = "🟢 Бот ON" if settings["bot_enabled"] else "🔴 Бот OFF"
    auto_state = "🤖 Auto ON" if settings["auto_trade"] else "🤖 Auto OFF"
    mode = "🧪 Paper" if settings["trade_mode"] == "paper" else "💰 Live"
    rows = [
        [InlineKeyboardButton(bot_state, callback_data="toggle_bot"), InlineKeyboardButton("📡 Ping", callback_data="ping")],
        [InlineKeyboardButton(f"Биржа: {settings['exchange'].upper()}", callback_data="cycle_exchange")],
        [InlineKeyboardButton(f"Top-{settings['top_n']}", callback_data="cycle_top"), InlineKeyboardButton(f"RR 1:{settings['rr']:g}", callback_data="cycle_rr")],
        [InlineKeyboardButton(f"TP: {settings.get('tp_mode','TP2')}", callback_data="cycle_tp"), InlineKeyboardButton(f"Скан: {settings['scan_minutes']} мин", callback_data="cycle_scan")],
        [InlineKeyboardButton(auto_state, callback_data="toggle_auto"), InlineKeyboardButton(mode, callback_data="toggle_mode")],
        [InlineKeyboardButton("🔎 Скан сейчас", callback_data="scan_now"), InlineKeyboardButton("🔐 API ключи", callback_data="api_help")],
    ]
    return InlineKeyboardMarkup(rows)


def settings_text(settings: dict) -> str:
    return (
        "<b>Crypto RR Scanner Bot</b>\n\n"
        f"Статус: {'ON' if settings['bot_enabled'] else 'OFF'}\n"
        f"Биржа: <b>{settings['exchange'].upper()}</b>\n"
        f"Монеты: <b>Top-{settings['top_n']}</b>\n"
        f"Risk/Reward: <b>1:{settings['rr']:g}</b>\n"
        f"TP режим: <b>{settings.get('tp_mode','TP2')}</b>\n"
        f"Скан: <b>каждые {settings['scan_minutes']} минут</b>\n"
        f"Автоторговля: <b>{'ON' if settings['auto_trade'] else 'OFF'}</b>\n"
        f"Режим: <b>{settings['trade_mode'].upper()}</b>\n\n"
        "Live-режим не включится без ALLOW_LIVE_TRADING=1 и API-ключей."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not owner_allowed(user_id):
        await update.message.reply_text("Доступ закрыт.")
        return
    s = get_settings(user_id)
    await update.message.reply_html(settings_text(s), reply_markup=keyboard(s))


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    proc = psutil.Process(os.getpid())
    mem = proc.memory_info().rss / 1024 / 1024
    uptime = int(time.time() - STARTED_AT)
    text = (
        "📡 <b>Ping / Status</b>\n"
        f"Отклик: OK\n"
        f"Uptime: {uptime // 3600}ч {(uptime % 3600) // 60}м\n"
        f"Память: {mem:.1f} MB\n"
        f"CPU: {psutil.cpu_percent(interval=0.1):.1f}%"
    )
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard(get_settings(update.effective_user.id)))
    else:
        await update.message.reply_html(text)


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
    elif data == "cycle_exchange":
        s["exchange"] = cycle(EXCHANGES, s["exchange"])
    elif data == "cycle_top":
        s["top_n"] = cycle(TOP_LIMITS, int(s["top_n"]))
    elif data == "cycle_rr":
        s["rr"] = cycle(RR_VALUES, float(s["rr"]))
    elif data == "cycle_tp":
        s["tp_mode"] = cycle(TAKE_PROFIT_MODES, s.get("tp_mode", "TP2"))
    elif data == "cycle_scan":
        s["scan_minutes"] = cycle([15, 30, 60], int(s["scan_minutes"]))
    elif data == "toggle_auto":
        s["auto_trade"] = not s["auto_trade"]
    elif data == "toggle_mode":
        s["trade_mode"] = "live" if s["trade_mode"] == "paper" else "paper"
    elif data == "ping":
        await ping(update, context, edit=True)
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
            await context.bot.send_message(user_id, f"Сигналов под RR 1:{s['rr']:g} сейчас нет.")
        return

    for signal, df in results[:5]:
        status = await maybe_execute_trade(user_id, signal, s)
        log_trade(user_id, signal, s["trade_mode"], status=status)
        caption = (
            f"{'🟢' if signal['side']=='LONG' else '🔴'} <b>{signal['side']} {escape(signal['symbol'])}</b>\n"
            f"Биржа: <b>{signal['exchange'].upper()}</b>\n"
            f"Вероятность: <b>{signal['probability']:.0f}%</b>\n"
            f"Вход: <code>{signal['entry']}</code>\n"
            f"SL: <code>{signal['stop']}</code>\n"
            f"TP: <code>{signal['take_profit']}</code>\n"
            f"Risk/Reward: <b>1:{signal['rr']:g}</b>\n"
            f"Auto status: <b>{status}</b>\n"
            f"Причина: {escape(signal['reason'])}"
        )
        img = make_signal_chart(df, signal)
        await context.bot.send_photo(user_id, photo=open(img, "rb"), caption=caption, parse_mode=ParseMode.HTML)


async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data["user_id"]
    await run_scan_for_user(context, user_id)


async def jobs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    s = get_settings(user_id)
    for job in context.job_queue.get_jobs_by_name(f"scan_{user_id}"):
        job.schedule_removal()
    context.job_queue.run_repeating(scheduled_scan, interval=int(s["scan_minutes"]) * 60, first=10, name=f"scan_{user_id}", data={"user_id": user_id})
    await update.message.reply_text(f"Плановый скан активирован: каждые {s['scan_minutes']} минут.")


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 Сканирую рынок...")
    await run_scan_for_user(context, update.effective_user.id, manual=True)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("BOT ERROR:", context.error)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
    init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("api", api_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("jobs", jobs_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
