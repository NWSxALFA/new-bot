import os
import json
import time
import random
import logging
import threading
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, Any, List, Optional

from flask import Flask
import telebot
from telebot import types
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
)

# =====================================
# CONFIG
# =====================================
TOKEN = os.getenv("BOT_TOKEN", "")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "")
PORT = int(os.getenv("PORT", "8000"))

if not TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi. Environment variable ga qo'ying.")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

DATA_FILE = "users.json"
ORDERS_FILE = "orders.json"
CONFIG_FILE = "config.json"
PROMO_FILE = "promo.json"
LOGS_FILE = "bot.log"
BACKUP_DIR = "backups"

DEFAULT_REQUIRED_CHANNELS = [
    {"username": "@ALFA_BONUS_NEWS", "title": "ALFA BONUS NEWS"},
    {"username": "@NWS_ALFA_07", "title": "NWS ALFA 07"},
    {"username": "@NWS_ALFA_UC", "title": "NWS ALFA UC"},
]

DEFAULT_ADMINS = {
    5996676608: {
        "username": "@NWSxALFA",
        "role": "superadmin",
        "added_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
}

DEFAULT_CONFIG = {
    "required_channels": DEFAULT_REQUIRED_CHANNELS,
    "earn_channels": [],
    "earn_posts": [],
    "referral_levels": {"1": 1000, "2": 300, "3": 100},
    "referral_requirements": {"level1": 10, "level2": 3},
    "min_withdrawal": 50000,
    "daily_bonus_range": [100, 500],
    "stars_rate": 350,
}

DEFAULT_PROMO_CODES = {
    "WELCOME100": 100,
    "BONUS500": 500,
    "SPECIAL1000": 1000,
    "REFERRAL50": 50,
}

logging.basicConfig(
    filename=LOGS_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger(__name__)
lock = threading.RLock()


# =====================================
# WEB / KEEP ALIVE
# =====================================
@app.route("/")
def home():
    return "Bot ishlamoqda! 🤖"


@app.route("/health")
def health():
    return {"status": "ok", "users": len(users), "time": datetime.now().isoformat()}


def run_web():
    app.run(host="0.0.0.0", port=PORT)


def keep_alive():
    t = threading.Thread(target=run_web, daemon=True)
    t.start()


# =====================================
# DATABASE
# =====================================
class Database:
    @staticmethod
    def _read_json(path: str, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"{path} o'qishda xato: {e}")
            return default

    @staticmethod
    def load_all():
        users_data = Database._read_json(DATA_FILE, {})
        orders_data = Database._read_json(ORDERS_FILE, [])
        config_data = Database._read_json(CONFIG_FILE, DEFAULT_CONFIG.copy())
        promo_data = Database._read_json(PROMO_FILE, DEFAULT_PROMO_CODES.copy())

        merged_config = DEFAULT_CONFIG.copy()
        merged_config.update(config_data or {})

        merged_config["referral_levels"] = {
            str(k): int(v)
            for k, v in merged_config.get("referral_levels", {"1": 1000, "2": 300, "3": 100}).items()
        }
        merged_config["daily_bonus_range"] = list(merged_config.get("daily_bonus_range", [100, 500]))

        return users_data, orders_data, merged_config, promo_data

    @staticmethod
    def save_all(users_data, orders_data, config_data, promo_data):
        with lock:
            for path, data in {
                DATA_FILE: users_data,
                ORDERS_FILE: orders_data,
                CONFIG_FILE: config_data,
                PROMO_FILE: promo_data,
            }.items():
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)

    @staticmethod
    def backup():
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for path in [DATA_FILE, ORDERS_FILE, CONFIG_FILE, PROMO_FILE]:
            if os.path.exists(path):
                backup_path = os.path.join(BACKUP_DIR, f"{path.replace('.json', '')}_{stamp}.json")
                try:
                    with open(path, "r", encoding="utf-8") as src, open(backup_path, "w", encoding="utf-8") as dst:
                        dst.write(src.read())
                except Exception as e:
                    logger.error(f"Backup xato {path}: {e}")


users, orders, config, promo_codes = Database.load_all()
ADMINS = DEFAULT_ADMINS


def auto_backup():
    while True:
        time.sleep(86400)
        Database.backup()
        logger.info("Avtomatik backup yaratildi")


threading.Thread(target=auto_backup, daemon=True).start()


# =====================================
# HELPERS
# =====================================
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


def is_superadmin(user_id: int) -> bool:
    return user_id in ADMINS and ADMINS[user_id].get("role") == "superadmin"


def get_ref_bonus(level: int) -> int:
    return int(config["referral_levels"].get(str(level), 0))


def ensure_user(message_or_user) -> str:
    if hasattr(message_or_user, "from_user"):
        user = message_or_user.from_user
    else:
        user = message_or_user

    user_id = str(user.id)
    if user_id not in users:
        users[user_id] = {
            "user_id": user_id,
            "username": f"@{user.username}" if user.username else "Noma'lum",
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "balance": 0,
            "stars": 0,
            "refs": {"level1": 0, "level2": 0, "level3": 0},
            "completed_tasks": {"channels": [], "posts": []},
            "bonus_date": "",
            "orders": [],
            "language": "uz",
            "referred_by": None,
            "join_date": now_str(),
            "last_active": now_str(),
            "used_promo": [],
            "blocked": False,
            "notifications": True,
            "games_played": 0,
            "games_won": 0,
            "subscription_bonus": False,
        }
        Database.save_all(users, orders, config, promo_codes)
    return user_id


def safe_username(user_or_id) -> str:
    if isinstance(user_or_id, (int, str)):
        uid = str(user_or_id)
        val = users.get(uid, {}).get("username", "Noma'lum")
        if val and val != "Noma'lum":
            return val if val.startswith("@") else f"@{val}"
        return "Noma'lum"

    if getattr(user_or_id, "username", None):
        return f"@{user_or_id.username}"
    if getattr(user_or_id, "first_name", None):
        return user_or_id.first_name
    return "Noma'lum"


def user_blocked(user_id: str) -> bool:
    return users.get(user_id, {}).get("blocked", False)


def is_channel_member(channel_username: str, user_id: int) -> bool:
    try:
        member = bot.get_chat_member(channel_username, user_id)
        return member.status not in ["left", "kicked"]
    except Exception as e:
        logger.error(f"Obuna tekshirish xato {channel_username}: {e}")
        return False


def check_subscription(user_id: int) -> bool:
    required_channels = config.get("required_channels", DEFAULT_REQUIRED_CHANNELS)
    for channel in required_channels:
        if not is_channel_member(channel["username"], user_id):
            return False
    return True


def subscription_required(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        uid = ensure_user(message)
        users[uid]["last_active"] = now_str()
        if user_blocked(uid):
            bot.send_message(message.chat.id, "❌ Siz bloklangansiz.")
            return
        if not check_subscription(message.from_user.id):
            show_required_channels(message.chat.id)
            return
        return func(message, *args, **kwargs)
    return wrapper


def admin_required(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            return
        return func(message, *args, **kwargs)
    return wrapper


def build_main_menu(is_admin_flag: bool = False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    rows = [
        ["💸 Pul ishlash", "🎁 Kunlik bonus"],
        ["📊 Hisobim", "👥 Referal"],
        ["➕ Hisobni to'ldirish", "💸 Pul yechish"],
        ["🛍 UC / Premium / Stars", "🏆 Top referallar"],
        ["🎟 Promokod", "⚙️ Sozlamalar"],
    ]
    for row in rows:
        kb.add(*row)
    if is_admin_flag:
        kb.add("👨‍💻 Admin panel")
    return kb


def admin_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📊 Statistika", "👤 Foydalanuvchilar")
    kb.add("📢 Reklama", "⚙️ Bot sozlamalari")
    kb.add("📦 Buyurtmalar", "💰 To'lovlar")
    kb.add("📝 Kanallar", "⬅️ Asosiy menyu")
    return kb


def back_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("⬅️ Ortga")
    return kb


def show_required_channels(chat_id: int):
    markup = InlineKeyboardMarkup(row_width=1)
    for channel in config.get("required_channels", DEFAULT_REQUIRED_CHANNELS):
        markup.add(
            InlineKeyboardButton(
                f"📢 {channel.get('title', channel['username'])}",
                url=f"https://t.me/{channel['username'].replace('@', '')}",
            )
        )
    markup.add(InlineKeyboardButton("✅ Tekshirish", callback_data="check_subs"))
    bot.send_message(
        chat_id,
        "⚠️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling.\n\n✅ Obuna bo'lgach «Tekshirish» ni bosing.",
        reply_markup=markup,
    )


def create_order(kind: str, user_id: str, extra: Dict[str, Any]) -> int:
    order_id = (max([o.get("id", 0) for o in orders], default=0) + 1) if orders else 1
    order = {
        "id": order_id,
        "kind": kind,
        "user_id": user_id,
        "status": "pending",
        "date": datetime.now().isoformat(),
    }
    order.update(extra)
    orders.append(order)
    Database.save_all(users, orders, config, promo_codes)
    return order_id


def find_order(order_id: int) -> Optional[Dict[str, Any]]:
    for order in orders:
        if order.get("id") == order_id:
            return order
    return None


# =====================================
# REFERRAL
# =====================================
def add_referral(referrer_id: str, new_user_id: str) -> bool:
    try:
        if referrer_id == new_user_id:
            return False
        if referrer_id not in users or new_user_id not in users:
            return False
        if users[new_user_id].get("referred_by"):
            return False

        users[new_user_id]["referred_by"] = referrer_id

        users[referrer_id]["refs"]["level1"] = users[referrer_id]["refs"].get("level1", 0) + 1
        bonus1 = get_ref_bonus(1)
        users[referrer_id]["balance"] += bonus1

        try:
            bot.send_message(
                int(referrer_id),
                f"👥 Sizga yangi referal qo'shildi!\n💰 Bonus: +{bonus1} so'm\n👤 ID: {new_user_id}",
            )
        except Exception:
            pass

        level2_id = users[referrer_id].get("referred_by")
        if level2_id and level2_id in users:
            users[level2_id]["refs"]["level2"] = users[level2_id]["refs"].get("level2", 0) + 1
            bonus2 = get_ref_bonus(2)
            users[level2_id]["balance"] += bonus2

            level3_id = users[level2_id].get("referred_by")
            if level3_id and level3_id in users:
                users[level3_id]["refs"]["level3"] = users[level3_id]["refs"].get("level3", 0) + 1
                users[level3_id]["balance"] += get_ref_bonus(3)

        Database.save_all(users, orders, config, promo_codes)
        return True
    except Exception as e:
        logger.error(f"Referral xato: {e}")
        return False


def get_referral_stats(user_id: str) -> Dict[str, Any]:
    refs = users[user_id].get("refs", {})
    level1 = refs.get("level1", 0)
    level2 = refs.get("level2", 0)
    level3 = refs.get("level3", 0)
    level1_users = []
    level2_users = []
    level3_users = []

    for uid, user_data in users.items():
        first_parent = user_data.get("referred_by")
        if first_parent == user_id:
            level1_users.append(uid)
        elif first_parent and first_parent in users:
            second_parent = users[first_parent].get("referred_by")
            if second_parent == user_id:
                level2_users.append(uid)
            elif second_parent and second_parent in users and users[second_parent].get("referred_by") == user_id:
                level3_users.append(uid)

    total_earned = level1 * get_ref_bonus(1) + level2 * get_ref_bonus(2) + level3 * get_ref_bonus(3)
    return {
        "level1": level1,
        "level2": level2,
        "level3": level3,
        "total_earned": total_earned,
        "level1_users": level1_users,
        "level2_users": level2_users,
        "level3_users": level3_users,
    }


# =====================================
# START
# =====================================
@bot.message_handler(commands=["start"])
def start(message):
    user_id = ensure_user(message)
    users[user_id]["last_active"] = now_str()

    if user_blocked(user_id):
        bot.send_message(message.chat.id, "❌ Siz bloklangansiz.")
        return

    if not check_subscription(message.from_user.id):
        show_required_channels(message.chat.id)
        return

    args = message.text.split(maxsplit=1)
    referrer_id = args[1].strip() if len(args) > 1 else None

    is_new = users[user_id].get("join_date") == users[user_id].get("last_active") or users[user_id].get("welcome_given") is None
    if users[user_id].get("welcome_given") is None:
        welcome_bonus = 100
        users[user_id]["balance"] += welcome_bonus
        users[user_id]["welcome_given"] = True
        if referrer_id and referrer_id.isdigit() and referrer_id in users and referrer_id != user_id:
            add_referral(referrer_id, user_id)
        Database.save_all(users, orders, config, promo_codes)
        bot.send_message(
            message.chat.id,
            f"👋 Xush kelibsiz!\n\n💰 Ro'yxatdan o'tish bonusi: +{welcome_bonus} so'm\n⚖️ Balans: {users[user_id]['balance']} so'm",
            reply_markup=build_main_menu(is_admin(message.from_user.id)),
        )
        return

    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(
        message.chat.id,
        f"👋 Xush kelibsiz!\n\n💰 Balans: {users[user_id]['balance']} so'm\n⭐ Stars: {users[user_id]['stars']}\n👥 Referallar: {users[user_id]['refs'].get('level1', 0)} ta",
        reply_markup=build_main_menu(is_admin(message.from_user.id)),
    )


@bot.callback_query_handler(func=lambda call: call.data == "check_subs")
def check_subs_callback(call):
    uid = ensure_user(call.from_user)
    if check_subscription(call.from_user.id):
        bonus_text = ""
        if not users[uid].get("subscription_bonus", False):
            users[uid]["balance"] += 200
            users[uid]["subscription_bonus"] = True
            Database.save_all(users, orders, config, promo_codes)
            bonus_text = "\n\n🎉 +200 so'm bonus berildi!"
        bot.answer_callback_query(call.id, "✅ Obuna tasdiqlandi")
        try:
            bot.edit_message_text(
                f"✅ Barcha kanallarga obuna bo'lgansiz!{bonus_text}",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass
        bot.send_message(call.message.chat.id, "🔽 Asosiy menyu:", reply_markup=build_main_menu(is_admin(call.from_user.id)))
    else:
        bot.answer_callback_query(call.id, "❌ Hali barcha kanallarga obuna bo'lmagansiz")
        show_required_channels(call.message.chat.id)


# =====================================
# PROFILE / BONUS / REF
# =====================================
@bot.message_handler(func=lambda m: m.text == "📊 Hisobim")
@subscription_required
def profile(message):
    user_id = str(message.from_user.id)
    user = users[user_id]
    ref = get_referral_stats(user_id)
    games_played = user.get("games_played", 0)
    games_won = user.get("games_won", 0)
    winrate = (games_won / games_played * 100) if games_played else 0

    text = (
        f"👤 <b>Shaxsiy kabinet</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📛 Username: {safe_username(message.from_user)}\n"
        f"📅 Ro'yxat: {user.get('join_date', 'Noma\'lum')}\n\n"
        f"💰 <b>Moliyaviy:</b>\n"
        f"• Balans: {user.get('balance', 0):,} so'm\n"
        f"• Stars: {user.get('stars', 0)} ⭐\n"
        f"• Referaldan daromad: {ref['total_earned']:,} so'm\n\n"
        f"👥 <b>Referallar:</b>\n"
        f"• 1-daraja: {ref['level1']}\n"
        f"• 2-daraja: {ref['level2']}\n"
        f"• 3-daraja: {ref['level3']}\n\n"
        f"🎮 <b>O'yinlar:</b>\n"
        f"• O'ynalgan: {games_played}\n"
        f"• Yutilgan: {games_won}\n"
        f"• G'alaba foizi: {winrate:.1f}%"
    )
    bot.send_message(message.chat.id, text)


@bot.message_handler(func=lambda m: m.text == "🎁 Kunlik bonus")
@subscription_required
def daily_bonus(message):
    user_id = str(message.from_user.id)
    today = datetime.now().strftime("%Y-%m-%d")
    if users[user_id].get("bonus_date") == today:
        next_dt = datetime.strptime(today, "%Y-%m-%d") + timedelta(days=1)
        delta = next_dt - datetime.now()
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        bot.send_message(message.chat.id, f"❌ Bugun bonus olgansiz.\n⏰ Keyingi bonus: {hours} soat {minutes} daqiqadan so'ng")
        return

    min_bonus, max_bonus = config.get("daily_bonus_range", [100, 500])
    bonus = random.randint(int(min_bonus), int(max_bonus))
    level1_count = users[user_id].get("refs", {}).get("level1", 0)
    multiplier = 1.5 if level1_count >= 10 else 1.2 if level1_count >= 5 else 1.0
    total = int(bonus * multiplier)
    users[user_id]["balance"] += total
    users[user_id]["bonus_date"] = today
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, f"🎉 Kunlik bonus!\n💰 Asosiy bonus: {bonus}\n📈 Ko'paytma: x{multiplier}\n💵 Jami: {total} so'm")


@bot.message_handler(func=lambda m: m.text == "👥 Referal")
@subscription_required
def referral_menu(message):
    user_id = str(message.from_user.id)
    me = bot.get_me().username
    ref_link = f"https://t.me/{me}?start={user_id}"
    ref = get_referral_stats(user_id)
    text = (
        f"👥 <b>Referal dasturi</b>\n\n"
        f"1-daraja: {ref['level1']} ta\n"
        f"2-daraja: {ref['level2']} ta\n"
        f"3-daraja: {ref['level3']} ta\n"
        f"Jami daromad: {ref['total_earned']:,} so'm\n\n"
        f"💰 Bonuslar:\n"
        f"• 1-daraja: {get_ref_bonus(1)} so'm\n"
        f"• 2-daraja: {get_ref_bonus(2)} so'm\n"
        f"• 3-daraja: {get_ref_bonus(3)} so'm\n\n"
        f"🔗 Havolangiz:\n<code>{ref_link}</code>"
    )
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📢 Do'stlarga yuborish", switch_inline_query=f"Taklif havolam: {ref_link}"))
    bot.send_message(message.chat.id, text, reply_markup=markup)


# =====================================
# EARN TASKS
# =====================================
@bot.message_handler(func=lambda m: m.text == "💸 Pul ishlash")
@subscription_required
def earn_menu(message):
    user_id = str(message.from_user.id)
    completed_channels = users[user_id].get("completed_tasks", {}).get("channels", [])
    completed_posts = users[user_id].get("completed_tasks", {}).get("posts", [])
    earn_channels = config.get("earn_channels", [])
    earn_posts = config.get("earn_posts", [])
    channel_left = len([i for i in range(len(earn_channels)) if i not in completed_channels])
    post_left = len([i for i in range(len(earn_posts)) if i not in completed_posts])

    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📢 Obuna bo'lish", "👁 Post ko'rish")
    kb.add("⬅️ Ortga")
    bot.send_message(
        message.chat.id,
        f"💸 <b>Pul ishlash</b>\n\n📢 Kanallar: {channel_left} ta\n👁 Postlar: {post_left} ta\n💰 Potensial: {channel_left * 100 + post_left * 20} so'm",
        reply_markup=kb,
    )


@bot.message_handler(func=lambda m: m.text == "📢 Obuna bo'lish")
@subscription_required
def subscribe_tasks(message):
    user_id = str(message.from_user.id)
    earn_channels = config.get("earn_channels", [])
    done = users[user_id].get("completed_tasks", {}).get("channels", [])
    for i, channel in enumerate(earn_channels):
        if i not in done:
            return show_channel_task(message.chat.id, i, channel)
    bot.send_message(message.chat.id, "✅ Barcha obuna topshiriqlari bajarilgan")


def show_channel_task(chat_id: int, index: int, channel: str):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("➕ Obuna bo'lish", url=channel),
        InlineKeyboardButton("✅ Tekshirish", callback_data=f"check_channel_{index}"),
        InlineKeyboardButton("⏭ Keyingi", callback_data=f"next_channel_{index}"),
    )
    bot.send_message(chat_id, f"📢 Kanal #{index + 1}\n{channel}\n\n💰 Mukofot: 100 so'm", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("check_channel_"))
def check_channel(c):
    user_id = str(c.from_user.id)
    index = int(c.data.split("_")[-1])
    earn_channels = config.get("earn_channels", [])
    if index >= len(earn_channels):
        bot.answer_callback_query(c.id, "❌ Kanal topilmadi")
        return
    done = users[user_id].setdefault("completed_tasks", {}).setdefault("channels", [])
    if index in done:
        bot.answer_callback_query(c.id, "❌ Bu topshiriq oldin bajarilgan")
        return
    username = earn_channels[index].split("/")[-1].replace("@", "")
    if not is_channel_member(f"@{username}", c.from_user.id):
        bot.answer_callback_query(c.id, "❌ Hali obuna bo'lmagansiz")
        return
    done.append(index)
    users[user_id]["balance"] += 100
    Database.save_all(users, orders, config, promo_codes)
    bot.answer_callback_query(c.id, "+100 so'm")
    remaining = [i for i in range(len(earn_channels)) if i not in done]
    if remaining:
        idx = remaining[0]
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("➕ Obuna bo'lish", url=earn_channels[idx]),
            InlineKeyboardButton("✅ Tekshirish", callback_data=f"check_channel_{idx}"),
            InlineKeyboardButton("⏭ Keyingi", callback_data=f"next_channel_{idx}"),
        )
        bot.edit_message_text(f"📢 Kanal #{idx + 1}\n{earn_channels[idx]}\n\n💰 Mukofot: 100 so'm", c.message.chat.id, c.message.message_id, reply_markup=markup)
    else:
        bot.edit_message_text("✅ Barcha obuna topshiriqlari bajarildi", c.message.chat.id, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("next_channel_"))
def next_channel(c):
    user_id = str(c.from_user.id)
    current = int(c.data.split("_")[-1])
    earn_channels = config.get("earn_channels", [])
    done = users[user_id].get("completed_tasks", {}).get("channels", [])
    remaining = [i for i in range(len(earn_channels)) if i not in done and i != current]
    if not remaining:
        bot.answer_callback_query(c.id, "❌ Boshqa kanal yo'q")
        return
    idx = remaining[0]
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("➕ Obuna bo'lish", url=earn_channels[idx]),
        InlineKeyboardButton("✅ Tekshirish", callback_data=f"check_channel_{idx}"),
        InlineKeyboardButton("⏭ Keyingi", callback_data=f"next_channel_{idx}"),
    )
    bot.edit_message_text(f"📢 Kanal #{idx + 1}\n{earn_channels[idx]}\n\n💰 Mukofot: 100 so'm", c.message.chat.id, c.message.message_id, reply_markup=markup)


@bot.message_handler(func=lambda m: m.text == "👁 Post ko'rish")
@subscription_required
def post_tasks(message):
    user_id = str(message.from_user.id)
    earn_posts = config.get("earn_posts", [])
    done = users[user_id].get("completed_tasks", {}).get("posts", [])
    for i, post in enumerate(earn_posts):
        if i not in done:
            return show_post_task(message.chat.id, i, post)
    bot.send_message(message.chat.id, "✅ Barcha post topshiriqlari bajarilgan")


def show_post_task(chat_id: int, index: int, post: str):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("👁 Ko'rish", url=post),
        InlineKeyboardButton("✅ Tekshirish", callback_data=f"check_post_{index}"),
        InlineKeyboardButton("⏭ Keyingi", callback_data=f"next_post_{index}"),
    )
    bot.send_message(chat_id, f"👁 Post #{index + 1}\n{post}\n\n💰 Mukofot: 20 so'm", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("check_post_"))
def check_post(c):
    user_id = str(c.from_user.id)
    index = int(c.data.split("_")[-1])
    earn_posts = config.get("earn_posts", [])
    if index >= len(earn_posts):
        bot.answer_callback_query(c.id, "❌ Post topilmadi")
        return
    done = users[user_id].setdefault("completed_tasks", {}).setdefault("posts", [])
    if index in done:
        bot.answer_callback_query(c.id, "❌ Bu topshiriq oldin bajarilgan")
        return
    done.append(index)
    users[user_id]["balance"] += 20
    Database.save_all(users, orders, config, promo_codes)
    bot.answer_callback_query(c.id, "+20 so'm")
    remaining = [i for i in range(len(earn_posts)) if i not in done]
    if remaining:
        idx = remaining[0]
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("👁 Ko'rish", url=earn_posts[idx]),
            InlineKeyboardButton("✅ Tekshirish", callback_data=f"check_post_{idx}"),
            InlineKeyboardButton("⏭ Keyingi", callback_data=f"next_post_{idx}"),
        )
        bot.edit_message_text(f"👁 Post #{idx + 1}\n{earn_posts[idx]}\n\n💰 Mukofot: 20 so'm", c.message.chat.id, c.message.message_id, reply_markup=markup)
    else:
        bot.edit_message_text("✅ Barcha post topshiriqlari bajarildi", c.message.chat.id, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("next_post_"))
def next_post(c):
    user_id = str(c.from_user.id)
    current = int(c.data.split("_")[-1])
    earn_posts = config.get("earn_posts", [])
    done = users[user_id].get("completed_tasks", {}).get("posts", [])
    remaining = [i for i in range(len(earn_posts)) if i not in done and i != current]
    if not remaining:
        bot.answer_callback_query(c.id, "❌ Boshqa post yo'q")
        return
    idx = remaining[0]
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("👁 Ko'rish", url=earn_posts[idx]),
        InlineKeyboardButton("✅ Tekshirish", callback_data=f"check_post_{idx}"),
        InlineKeyboardButton("⏭ Keyingi", callback_data=f"next_post_{idx}"),
    )
    bot.edit_message_text(f"👁 Post #{idx + 1}\n{earn_posts[idx]}\n\n💰 Mukofot: 20 so'm", c.message.chat.id, c.message.message_id, reply_markup=markup)


# =====================================
# PAYMENTS MODULE
# =====================================

@bot.message_handler(func=lambda m: m.text == "➕ Hisobni to'ldirish")
@subscription_required
def topup_balance(message):
    markup = InlineKeyboardMarkup(row_width=2)
    amounts = [10000, 25000, 50000, 100000, 250000, 500000, 1000000]
    for amount in amounts:
        markup.add(InlineKeyboardButton(f"{amount:,} so'm", callback_data=f"pay_{amount}"))
    markup.add(InlineKeyboardButton("⭐ Stars orqali", callback_data="pay_stars"))
    bot.send_message(message.chat.id, "💳 To'ldirish summasini tanlang:", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pay_"))
def process_payment(c):
    uid = str(c.from_user.id)

    # Stars orqali to‘lov
    if c.data == "pay_stars":
        msg = bot.send_message(c.message.chat.id, "⭐ Nechta star ishlatmoqchisiz? (1 star = 350 so'm)")
        bot.register_next_step_handler(msg, process_stars_payment)
        return

    # Oddiy to‘lov
    amount = int(c.data.split("_")[1])
    if not PAYMENT_PROVIDER_TOKEN:
        bot.answer_callback_query(c.id, "❌ PAYMENT_PROVIDER_TOKEN yo'q")
        bot.send_message(c.message.chat.id, "To'lov ishlashi uchun haqiqiy PAYMENT_PROVIDER_TOKEN qo'yilishi kerak.")
        return

    payload = f"topup:{uid}:{int(time.time())}:{amount}"
    create_order("topup", uid, {"amount": amount, "payload": payload})

    prices = [LabeledPrice(label="Hisobni to'ldirish", amount=amount * 100)]
    try:
        bot.send_invoice(
            c.message.chat.id,
            title="Hisobni to'ldirish",
            description=f"Hisobingizni {amount:,} so'mga to'ldirish",
            invoice_payload=payload,
            provider_token=PAYMENT_PROVIDER_TOKEN,
            currency="UZS",
            prices=prices,
            start_parameter="topup",
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False,
            is_flexible=False,
        )
        bot.answer_callback_query(c.id, "✅ Invoice yuborildi")
    except Exception as e:
        logger.error(f"Invoice xato: {e}")
        bot.send_message(c.message.chat.id, "❌ Invoice yuborishda xato yuz berdi.")


def process_stars_payment(message):
    uid = str(message.from_user.id)
    try:
        stars = int(message.text)
        amount = stars * config.get("stars_rate", 350)
        if users[uid]["stars"] < stars:
            bot.send_message(message.chat.id, "❌ Yetarli stars yo'q")
            return
        users[uid]["stars"] -= stars
        users[uid]["balance"] += amount
        create_order("topup_stars", uid, {"stars": stars, "amount": amount})
        Database.save_all(users, orders, config, promo_codes)
        bot.send_message(message.chat.id, f"✅ {stars} stars ishlatildi!\n💰 Balans: {users[uid]['balance']} so'm")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Noto'g'ri qiymat kiritildi")


# Telegram to‘lov jarayonlari
@bot.pre_checkout_query_handler(func=lambda query: True)
def checkout_handler(query):
    bot.answer_pre_checkout_query(query.id, ok=True)


@bot.message_handler(content_types=['successful_payment'])
def got_payment(message):
    payload = message.successful_payment.invoice_payload
    uid = str(message.from_user.id)
    amount = message.successful_payment.total_amount // 100

    # Balansni yangilash
    users[uid]["balance"] += amount

    # Order statusni yangilash
    order = next((o for o in orders if o.get("payload") == payload), None)
    if order:
        order["status"] = "completed"

    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, f"✅ To'lov qabul qilindi!\n💰 Balans: {users[uid]['balance']} so'm")


@bot.message_handler(func=lambda m: m.text == "💰 To'lovlar")
@admin_required
def payments_admin(message):
    text = "📦 <b>To'lovlar ro'yxati</b>\n\n"
    for order in orders[-10:]:  # oxirgi 10 ta to‘lov
        text += f"ID: {order['id']} | User: {order['user_id']} | Amount: {order.get('amount', 0)} | Status: {order['status']}\n"
    bot.send_message(message.chat.id, text)


# =====================================
# WITHDRAW
# =====================================
@bot.message_handler(func=lambda m: m.text == "💸 Pul yechish")
@subscription_required
def withdraw(message):
    uid = str(message.from_user.id)
    ref = get_referral_stats(uid)
    req = config.get("referral_requirements", {"level1": 10, "level2": 3})
    min_withdrawal = int(config.get("min_withdrawal", 50000))

    if ref["level1"] < int(req.get("level1", 10)):
        bot.send_message(message.chat.id, f"❌ Kamida {req['level1']} ta 1-darajali referal kerak.")
        return
    if ref["level2"] < int(req.get("level2", 3)):
        bot.send_message(message.chat.id, f"❌ Kamida {req['level2']} ta 2-darajali referal kerak.")
        return
    if users[uid]["balance"] < min_withdrawal:
        bot.send_message(message.chat.id, f"❌ Minimal yechish: {min_withdrawal:,} so'm")
        return

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("💳 Uzcard", callback_data="withdraw_uzcard"))
    markup.add(InlineKeyboardButton("💳 Humo", callback_data="withdraw_humo"))
    markup.add(InlineKeyboardButton("💳 Visa/Mastercard", callback_data="withdraw_card"))
    markup.add(InlineKeyboardButton("⭐ Stars", callback_data="withdraw_stars"))
    bot.send_message(message.chat.id, f"💸 Balans: {users[uid]['balance']:,} so'm\nTo'lov usulini tanlang:", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("withdraw_"))
def withdraw_method(c):
    uid = str(c.from_user.id)
    method = c.data.split("_")[1]
    if method == "stars":
        stars = users[uid]["balance"] // int(config.get("stars_rate", 350))
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Tasdiqlash", callback_data="confirm_stars_withdraw"))
        markup.add(InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_withdraw"))
        bot.edit_message_text(f"⭐ {users[uid]['balance']:,} so'm ≈ {stars} stars\nTasdiqlaysizmi?", c.message.chat.id, c.message.message_id, reply_markup=markup)
        return
    users[uid]["withdraw_method"] = method
    Database.save_all(users, orders, config, promo_codes)
    msg = bot.send_message(c.message.chat.id, "💳 Karta raqamingizni kiriting (16-19 xonali):")
    bot.register_next_step_handler(msg, process_card_number)


@bot.callback_query_handler(func=lambda c: c.data == "confirm_stars_withdraw")
def confirm_stars_withdraw(c):
    uid = str(c.from_user.id)
    rate = int(config.get("stars_rate", 350))
    balance = users[uid]["balance"]
    stars = balance // rate
    if stars <= 0:
        bot.answer_callback_query(c.id, "❌ Yetarli mablag' yo'q")
        return
    used_balance = stars * rate
    users[uid]["balance"] -= used_balance
    users[uid]["stars"] += stars
    create_order("withdraw_stars", uid, {"stars": stars, "amount": used_balance, "status": "completed", "completed_date": datetime.now().isoformat()})
    Database.save_all(users, orders, config, promo_codes)
    bot.edit_message_text(f"✅ {stars} stars berildi.\n💰 {used_balance:,} so'm yechildi.", c.message.chat.id, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data == "cancel_withdraw")
def cancel_withdraw(c):
    bot.edit_message_text("❌ Bekor qilindi", c.message.chat.id, c.message.message_id)


def process_card_number(message):
    uid = str(message.from_user.id)
    card = message.text.strip().replace(" ", "")
    if not (card.isdigit() and 16 <= len(card) <= 19):
        msg = bot.send_message(message.chat.id, "❌ 16-19 xonali karta raqami kiriting")
        bot.register_next_step_handler(msg, process_card_number)
        return
    amount = int(users[uid]["balance"])
    order_id = create_order("withdraw", uid, {"amount": amount, "method": users[uid].get("withdraw_method", "card"), "card": card})
    users[uid]["balance"] = 0
    users[uid].pop("withdraw_method", None)
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, f"✅ So'rov qabul qilindi.\n💰 Miqdor: {amount:,} so'm\n🆔 Buyurtma: #{order_id}", reply_markup=build_main_menu(is_admin(message.from_user.id)))
    for admin_id in ADMINS:
        try:
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"approve_order_{order_id}"),
                InlineKeyboardButton("❌ Rad etish", callback_data=f"reject_order_{order_id}"),
            )
            bot.send_message(
                admin_id,
                f"🔄 Yangi pul yechish so'rovi\n👤 {safe_username(message.from_user)}\n🆔 <code>{uid}</code>\n💰 {amount:,} so'm\n💳 {card}\n🆔 Order: #{order_id}",
                reply_markup=kb,
            )
        except Exception as e:
            logger.error(f"Admin xabar xato: {e}")


# =====================================
# SHOP
# =====================================
@bot.message_handler(func=lambda m: m.text == "🛍 UC / Premium / Stars")
@subscription_required
def shop(message):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("💎 UC", callback_data="shop_uc"))
    markup.add(InlineKeyboardButton("🚀 Premium", callback_data="shop_premium"))
    markup.add(InlineKeyboardButton("⭐ Stars", callback_data="shop_stars"))
    bot.send_message(message.chat.id, "🛍 Do'kon bo'limi:", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("shop_"))
def shop_category(c):
    category = c.data.split("_")[1]
    data = {
        "uc": [("60 UC", 60, 12000), ("120 UC + 25 UC", 145, 24000), ("325 UC + 35 UC", 360, 60000), ("660 UC + 130 UC", 790, 118000)],
        "premium": [("Premium 1 oy", 1, 60000), ("Premium 3 oy", 3, 176000), ("Premium 6 oy", 6, 230000)],
        "stars": [("100 Stars", 100, 35000), ("250 Stars", 250, 70000), ("500 Stars", 500, 130000)],
    }
    markup = InlineKeyboardMarkup(row_width=1)
    for name, amount, price in data.get(category, []):
        markup.add(InlineKeyboardButton(f"{name} - {price:,} so'm", callback_data=f"buy_{category}_{amount}_{price}"))
    markup.add(InlineKeyboardButton("⬅️ Ortga", callback_data="shop_back"))
    bot.edit_message_text(f"🛍 {category.upper()} paketlari", c.message.chat.id, c.message.message_id, reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data == "shop_back")
def shop_back(c):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("💎 UC", callback_data="shop_uc"))
    markup.add(InlineKeyboardButton("🚀 Premium", callback_data="shop_premium"))
    markup.add(InlineKeyboardButton("⭐ Stars", callback_data="shop_stars"))
    bot.edit_message_text("🛍 Do'kon bo'limi:", c.message.chat.id, c.message.message_id, reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def buy_item(c):
    parts = c.data.split("_")
    item_type = parts[1]
    amount = int(parts[2])
    price = int(parts[3])
    uid = str(c.from_user.id)
    if users[uid]["balance"] < price:
        bot.answer_callback_query(c.id, "❌ Balans yetarli emas")
        return
    users[uid]["pending_purchase"] = {"type": item_type, "amount": amount, "price": price}
    Database.save_all(users, orders, config, promo_codes)
    msg = bot.send_message(c.message.chat.id, f"📝 {item_type.upper()} uchun ID yoki username kiriting:")
    bot.register_next_step_handler(msg, process_purchase_id)


def process_purchase_id(message):
    uid = str(message.from_user.id)
    pending = users[uid].get("pending_purchase")
    if not pending:
        bot.send_message(message.chat.id, "❌ Faol buyurtma topilmadi")
        return
    game_id = message.text.strip()
    users[uid]["balance"] -= int(pending["price"])
    order_id = create_order("shop", uid, {
        "type": pending["type"],
        "amount": pending["amount"],
        "price": pending["price"],
        "game_id": game_id,
    })
    users[uid].pop("pending_purchase", None)
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, f"✅ Buyurtma qabul qilindi.\n🆔 Order: #{order_id}\n📦 {pending['type'].upper()} {pending['amount']}\n💰 {pending['price']:,} so'm", reply_markup=build_main_menu(is_admin(message.from_user.id)))
    for admin_id in ADMINS:
        try:
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("✅ Bajarildi", callback_data=f"approve_order_{order_id}"),
                InlineKeyboardButton("❌ Rad etish", callback_data=f"reject_order_{order_id}"),
            )
            bot.send_message(admin_id, f"🛒 Yangi buyurtma #{order_id}\n👤 {safe_username(message.from_user)}\n🆔 <code>{uid}</code>\n📦 {pending['type'].upper()} {pending['amount']}\n💰 {pending['price']:,} so'm\n🎮 ID: {game_id}", reply_markup=kb)
        except Exception as e:
            logger.error(f"Shop admin xabar xato: {e}")


# =====================================
# ORDERS / ADMIN
# =====================================
def get_admin_stats() -> Dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "total_users": len(users),
        "new_today": sum(1 for u in users.values() if str(u.get("join_date", "")).startswith(today)),
        "active_today": sum(1 for u in users.values() if str(u.get("last_active", "")).startswith(today)),
        "total_balance": sum(int(u.get("balance", 0)) for u in users.values()),
        "pending_orders": sum(1 for o in orders if o.get("status") == "pending"),
    }


@bot.message_handler(func=lambda m: m.text == "👨‍💻 Admin panel" and is_admin(m.from_user.id))
def admin_panel(message):
    s = get_admin_stats()
    bot.send_message(
        message.chat.id,
        f"👨‍💻 <b>Admin panel</b>\n\n👥 Foydalanuvchilar: {s['total_users']}\n🆕 Bugun: {s['new_today']}\n⚡ Faol: {s['active_today']}\n💰 Jami balans: {s['total_balance']:,} so'm\n📦 Pending: {s['pending_orders']}",
        reply_markup=admin_menu(),
    )


@bot.message_handler(func=lambda m: m.text == "📊 Statistika" and is_admin(m.from_user.id))
@admin_required
def admin_stats(message):
    s = get_admin_stats()
    total_refs = sum(u.get("refs", {}).get("level1", 0) + u.get("refs", {}).get("level2", 0) + u.get("refs", {}).get("level3", 0) for u in users.values())
    total_stars = sum(int(u.get("stars", 0)) for u in users.values())
    completed_orders = sum(1 for o in orders if o.get("status") == "completed")
    total_payments = sum(int(o.get("amount", 0)) for o in orders if o.get("kind") == "topup" and o.get("status") == "completed")
    total_withdrawals = sum(int(o.get("amount", 0)) for o in orders if o.get("kind") == "withdraw" and o.get("status") == "completed")
    bot.send_message(
        message.chat.id,
        f"📊 <b>Batafsil statistika</b>\n\n👥 Jami user: {s['total_users']}\n⭐ Jami stars: {total_stars}\n👥 Jami referal: {total_refs}\n💰 Jami balans: {s['total_balance']:,}\n📥 Kirim: {total_payments:,}\n📤 Chiqim: {total_withdrawals:,}\n✅ Bajarilgan buyurtmalar: {completed_orders}\n⏳ Pending: {s['pending_orders']}",
    )


@bot.message_handler(func=lambda m: m.text == "📦 Buyurtmalar" and is_admin(m.from_user.id))
@admin_required
def admin_orders(message):
    pending = [o for o in orders if o.get("status") == "pending"]
    if not pending:
        bot.send_message(message.chat.id, "✅ Pending buyurtmalar yo'q", reply_markup=admin_menu())
        return
    text = [f"📦 <b>Pending buyurtmalar ({len(pending)})</b>"]
    markup = InlineKeyboardMarkup(row_width=1)
    for order in pending[:20]:
        text.append(f"\n#{order['id']} | {order.get('kind')} | {order.get('amount', order.get('price', 0))} so'm | user {order['user_id']}")
        markup.add(InlineKeyboardButton(f"Buyurtma #{order['id']}", callback_data=f"view_order_{order['id']}"))
    bot.send_message(message.chat.id, "\n".join(text), reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("view_order_"))
def view_order(c):
    if not is_admin(c.from_user.id):
        return
    order_id = int(c.data.split("_")[-1])
    order = find_order(order_id)
    if not order:
        bot.answer_callback_query(c.id, "❌ Buyurtma topilmadi")
        return
    text = (
        f"📦 <b>Buyurtma #{order['id']}</b>\n\n"
        f"Turi: {order.get('kind')}\n"
        f"User: {order.get('user_id')}\n"
        f"Miqdor: {order.get('amount', order.get('price', 0))} so'm\n"
        f"Holat: {order.get('status')}\n"
        f"Sana: {order.get('date')}"
    )
    if order.get("card"):
        text += f"\nKarta: {order['card']}"
    if order.get("game_id"):
        text += f"\nGame ID: {order['game_id']}"
    markup = InlineKeyboardMarkup(row_width=2)
    if order.get("status") == "pending":
        markup.add(
            InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"approve_order_{order_id}"),
            InlineKeyboardButton("❌ Rad etish", callback_data=f"reject_order_{order_id}"),
        )
    bot.edit_message_text(text, c.message.chat.id, c.message.message_id, reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("approve_order_"))
def approve_order(c):
    if not is_admin(c.from_user.id):
        return
    order_id = int(c.data.split("_")[-1])
    order = find_order(order_id)
    if not order:
        bot.answer_callback_query(c.id, "❌ Buyurtma topilmadi")
        return
    if order.get("status") != "pending":
        bot.answer_callback_query(c.id, "❌ Buyurtma allaqachon ko'rilgan")
        return
    order["status"] = "completed"
    order["approved_by"] = c.from_user.id
    order["approved_date"] = datetime.now().isoformat()
    Database.save_all(users, orders, config, promo_codes)
    try:
        if order.get("kind") == "withdraw":
            bot.send_message(int(order["user_id"]), f"✅ Pul yechish so'rovingiz tasdiqlandi.\n💰 {order.get('amount', 0):,} so'm")
        elif order.get("kind") == "shop":
            bot.send_message(int(order["user_id"]), f"✅ Buyurtmangiz bajarildi!\n🆔 Order: #{order_id}")
        elif order.get("kind") == "topup":
            bot.send_message(int(order["user_id"]), f"✅ To'lov tasdiqlandi.\n💰 {order.get('amount', 0):,} so'm")
    except Exception:
        pass
    bot.answer_callback_query(c.id, "✅ Tasdiqlandi")
    bot.edit_message_text(f"✅ Buyurtma #{order_id} tasdiqlandi", c.message.chat.id, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("reject_order_"))
def reject_order(c):
    if not is_admin(c.from_user.id):
        return
    order_id = int(c.data.split("_")[-1])
    order = find_order(order_id)
    if not order:
        bot.answer_callback_query(c.id, "❌ Buyurtma topilmadi")
        return
    if order.get("status") != "pending":
        bot.answer_callback_query(c.id, "❌ Buyurtma allaqachon ko'rilgan")
        return
    order["status"] = "rejected"
    order["rejected_by"] = c.from_user.id
    order["rejected_date"] = datetime.now().isoformat()
    if order.get("kind") == "withdraw":
        users[order["user_id"]]["balance"] += int(order.get("amount", 0))
    elif order.get("kind") == "shop":
        users[order["user_id"]]["balance"] += int(order.get("price", 0))
    Database.save_all(users, orders, config, promo_codes)
    try:
        if order.get("kind") == "withdraw":
            bot.send_message(int(order["user_id"]), f"❌ Pul yechish rad etildi.\n💰 {order.get('amount', 0):,} so'm balansga qaytarildi")
        elif order.get("kind") == "shop":
            bot.send_message(int(order["user_id"]), f"❌ Buyurtma rad etildi.\n💰 {order.get('price', 0):,} so'm balansga qaytarildi")
        else:
            bot.send_message(int(order["user_id"]), "❌ Buyurtma rad etildi")
    except Exception:
        pass
    bot.answer_callback_query(c.id, "❌ Rad etildi")
    bot.edit_message_text(f"❌ Buyurtma #{order_id} rad etildi", c.message.chat.id, c.message.message_id)


@bot.message_handler(commands=["approve_withdraw"])
@admin_required
def approve_withdraw_cmd(message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "Foydalanish: /approve_withdraw ORDER_ID")
        return
    order = find_order(int(parts[1]))
    if not order or order.get("kind") != "withdraw":
        bot.reply_to(message, "❌ Withdraw order topilmadi")
        return
    if order.get("status") != "pending":
        bot.reply_to(message, "❌ Buyurtma pending emas")
        return
    order["status"] = "completed"
    order["approved_by"] = message.from_user.id
    order["approved_date"] = datetime.now().isoformat()
    Database.save_all(users, orders, config, promo_codes)
    try:
        bot.send_message(int(order["user_id"]), f"✅ Withdraw tasdiqlandi.\n💰 {order.get('amount', 0):,} so'm")
    except Exception:
        pass
    bot.reply_to(message, f"✅ Order #{order['id']} tasdiqlandi")


@bot.message_handler(commands=["reject_withdraw"])
@admin_required
def reject_withdraw_cmd(message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "Foydalanish: /reject_withdraw ORDER_ID")
        return
    order = find_order(int(parts[1]))
    if not order or order.get("kind") != "withdraw":
        bot.reply_to(message, "❌ Withdraw order topilmadi")
        return
    if order.get("status") != "pending":
        bot.reply_to(message, "❌ Buyurtma pending emas")
        return
    order["status"] = "rejected"
    users[order["user_id"]]["balance"] += int(order.get("amount", 0))
    Database.save_all(users, orders, config, promo_codes)
    try:
        bot.send_message(int(order["user_id"]), f"❌ Withdraw rad etildi.\n💰 {order.get('amount', 0):,} so'm balansga qaytarildi")
    except Exception:
        pass
    bot.reply_to(message, f"❌ Order #{order['id']} rad etildi")


# =====================================
# ADMIN USERS
# =====================================
@bot.message_handler(func=lambda m: m.text == "👤 Foydalanuvchilar" and is_admin(m.from_user.id))
@admin_required
def admin_users(message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🔍 Foydalanuvchi qidirish", "⬅️ Ortga")
    bot.send_message(message.chat.id, "👤 Foydalanuvchilar bo'limi", reply_markup=kb)


@bot.message_handler(func=lambda m: m.text == "🔍 Foydalanuvchi qidirish" and is_admin(m.from_user.id))
@admin_required
def search_user(message):
    msg = bot.send_message(message.chat.id, "ID yoki username kiriting:", reply_markup=back_menu())
    bot.register_next_step_handler(msg, process_user_search)


def process_user_search(message):
    if message.text == "⬅️ Ortga":
        bot.send_message(message.chat.id, "🔙 Orqaga", reply_markup=admin_menu())
        return
    query = message.text.strip().lower().replace("@", "")
    found = []
    if query.isdigit() and query in users:
        found = [query]
    else:
        for uid, user in users.items():
            username = str(user.get("username", "")).lower().replace("@", "")
            if query in username:
                found.append(uid)
            if len(found) >= 10:
                break
    if not found:
        bot.send_message(message.chat.id, "❌ Topilmadi", reply_markup=admin_menu())
        return
    if len(found) == 1:
        return show_user_info(message.chat.id, found[0])
    markup = InlineKeyboardMarkup(row_width=1)
    for uid in found:
        markup.add(InlineKeyboardButton(f"{safe_username(uid)} | {users[uid].get('balance', 0):,} so'm", callback_data=f"admin_show_user_{uid}"))
    bot.send_message(message.chat.id, "Topilgan foydalanuvchilar:", reply_markup=markup)


def show_user_info(chat_id: int, user_id: str):
    user = users[user_id]
    ref = get_referral_stats(user_id)
    text = (
        f"👤 <b>Foydalanuvchi</b>\n\n"
        f"ID: <code>{user_id}</code>\n"
        f"Username: {user.get('username', 'Noma\'lum')}\n"
        f"Ism: {user.get('first_name', '')}\n"
        f"Balans: {user.get('balance', 0):,} so'm\n"
        f"Stars: {user.get('stars', 0)}\n"
        f"1L: {ref['level1']} | 2L: {ref['level2']} | 3L: {ref['level3']}\n"
        f"Blocked: {'✅' if user.get('blocked') else '❌'}"
    )
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("💰 Balans", callback_data=f"admin_edit_balance_{user_id}"),
        InlineKeyboardButton("⭐ Stars", callback_data=f"admin_edit_stars_{user_id}"),
    )
    markup.add(InlineKeyboardButton("🔒 Block/Unblock", callback_data=f"admin_toggle_block_{user_id}"))
    bot.send_message(chat_id, text, reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_show_user_"))
def admin_show_user(c):
    if not is_admin(c.from_user.id):
        return
    show_user_info(c.message.chat.id, c.data.split("_")[-1])


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_edit_balance_"))
def admin_edit_balance(c):
    if not is_admin(c.from_user.id):
        return
    user_id = c.data.split("_")[-1]
    msg = bot.send_message(c.message.chat.id, f"Yangi balansni kiriting ({users[user_id].get('balance', 0):,}):")
    bot.register_next_step_handler(msg, lambda m: process_balance_edit(m, user_id))


def process_balance_edit(message, user_id: str):
    try:
        amount = int(message.text.strip())
    except Exception:
        bot.send_message(message.chat.id, "❌ Son kiriting", reply_markup=admin_menu())
        return
    old = users[user_id].get("balance", 0)
    users[user_id]["balance"] = amount
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, f"✅ O'zgartirildi: {old:,} -> {amount:,}", reply_markup=admin_menu())
    try:
        bot.send_message(int(user_id), f"💰 Admin balansingizni o'zgartirdi.\nEski: {old:,}\nYangi: {amount:,}")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_edit_stars_"))
def admin_edit_stars(c):
    if not is_admin(c.from_user.id):
        return
    user_id = c.data.split("_")[-1]
    msg = bot.send_message(c.message.chat.id, f"Yangi stars ni kiriting ({users[user_id].get('stars', 0)}):")
    bot.register_next_step_handler(msg, lambda m: process_stars_edit(m, user_id))


def process_stars_edit(message, user_id: str):
    try:
        amount = int(message.text.strip())
    except Exception:
        bot.send_message(message.chat.id, "❌ Son kiriting", reply_markup=admin_menu())
        return
    old = users[user_id].get("stars", 0)
    users[user_id]["stars"] = amount
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, f"✅ O'zgartirildi: {old} -> {amount}", reply_markup=admin_menu())
    try:
        bot.send_message(int(user_id), f"⭐ Admin stars miqdoringizni o'zgartirdi.\nEski: {old}\nYangi: {amount}")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_toggle_block_"))
def admin_toggle_block(c):
    if not is_admin(c.from_user.id):
        return
    user_id = c.data.split("_")[-1]
    users[user_id]["blocked"] = not users[user_id].get("blocked", False)
    Database.save_all(users, orders, config, promo_codes)
    state = "bloklandi" if users[user_id]["blocked"] else "blokdan chiqarildi"
    bot.answer_callback_query(c.id, f"✅ {state}")
    try:
        bot.send_message(int(user_id), f"ℹ️ Siz {state}")
    except Exception:
        pass
    show_user_info(c.message.chat.id, user_id)


# =====================================
# SETTINGS / HISTORY / CONTACT
# =====================================
@bot.message_handler(func=lambda m: m.text == "⚙️ Sozlamalar")
@subscription_required
def settings(message):
    uid = str(message.from_user.id)
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🔔 Bildirishnomalar", "📜 Buyurtmalar tarixi")
    kb.add("💳 To'lovlar tarixi", "📩 Adminga yozish")
    kb.add("⬅️ Ortga")
    status = "✅ Yoqilgan" if users[uid].get("notifications", True) else "❌ O'chirilgan"
    bot.send_message(message.chat.id, f"⚙️ Sozlamalar\n\n🔔 Bildirishnomalar: {status}", reply_markup=kb)


@bot.message_handler(func=lambda m: m.text == "🔔 Bildirishnomalar")
@subscription_required
def toggle_notifications(message):
    uid = str(message.from_user.id)
    users[uid]["notifications"] = not users[uid].get("notifications", True)
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, f"✅ Holat: {'yoqildi' if users[uid]['notifications'] else 'o\'chirildi'}", reply_markup=build_main_menu(is_admin(message.from_user.id)))


@bot.message_handler(func=lambda m: m.text == "📜 Buyurtmalar tarixi")
@subscription_required
def order_history(message):
    uid = str(message.from_user.id)
    my_orders = [o for o in orders if o.get("user_id") == uid]
    my_orders.sort(key=lambda x: x.get("date", ""), reverse=True)
    if not my_orders:
        bot.send_message(message.chat.id, "📜 Buyurtmalar mavjud emas")
        return
    text = ["📜 <b>Oxirgi buyurtmalar</b>"]
    for order in my_orders[:10]:
        text.append(f"\n#{order.get('id')} | {order.get('kind')} | {order.get('amount', order.get('price', 0))} so'm | {order.get('status')}")
    bot.send_message(message.chat.id, "\n".join(text))


@bot.message_handler(func=lambda m: m.text == "💳 To'lovlar tarixi")
@subscription_required
def payment_history(message):
    uid = str(message.from_user.id)
    payments = [o for o in orders if o.get("user_id") == uid and o.get("kind") in ["topup", "withdraw", "stars_topup", "withdraw_stars"]]
    payments.sort(key=lambda x: x.get("date", ""), reverse=True)
    if not payments:
        bot.send_message(message.chat.id, "💳 To'lovlar mavjud emas")
        return
    text = ["💳 <b>Oxirgi to'lovlar</b>"]
    for p in payments[:10]:
        text.append(f"\n#{p.get('id', '-')} | {p.get('kind')} | {p.get('amount', 0)} so'm | {p.get('status')}")
    bot.send_message(message.chat.id, "\n".join(text))


@bot.message_handler(func=lambda m: m.text == "📩 Adminga yozish")
@subscription_required
def contact_admin(message):
    msg = bot.send_message(message.chat.id, "📩 Xabaringizni yozing:", reply_markup=back_menu())
    bot.register_next_step_handler(msg, send_to_admin)


def send_to_admin(message):
    if message.text == "⬅️ Ortga":
        bot.send_message(message.chat.id, "🔙 Orqaga", reply_markup=build_main_menu(is_admin(message.from_user.id)))
        return
    uid = str(message.from_user.id)
    for admin_id in ADMINS:
        try:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("✏️ Javob yozish", callback_data=f"reply_user_{uid}"))
            if message.content_type == "text":
                bot.send_message(admin_id, f"📩 Userdan xabar\n👤 {safe_username(message.from_user)}\n🆔 <code>{uid}</code>\n\n{message.text}", reply_markup=kb)
            elif message.content_type == "photo":
                bot.send_photo(admin_id, message.photo[-1].file_id, caption=f"📩 Userdan rasm\n👤 {safe_username(message.from_user)}\n🆔 <code>{uid}</code>\n\n{message.caption or ''}", reply_markup=kb)
        except Exception as e:
            logger.error(f"Admin msg xato: {e}")
    bot.send_message(message.chat.id, "✅ Xabar yuborildi", reply_markup=build_main_menu(is_admin(message.from_user.id)))


@bot.callback_query_handler(func=lambda c: c.data.startswith("reply_user_"))
def reply_user(c):
    if not is_admin(c.from_user.id):
        return
    uid = c.data.split("_")[-1]
    msg = bot.send_message(c.message.chat.id, f"User {uid} uchun javob yozing:")
    bot.register_next_step_handler(msg, lambda m: send_admin_reply(m, uid))


def send_admin_reply(message, user_id: str):
    try:
        if message.content_type == "text":
            bot.send_message(int(user_id), f"📩 <b>Admin javobi:</b>\n\n{message.text}")
        elif message.content_type == "photo":
            bot.send_photo(int(user_id), message.photo[-1].file_id, caption=f"📩 <b>Admin javobi:</b>\n\n{message.caption or ''}")
        bot.send_message(message.chat.id, "✅ Javob yuborildi", reply_markup=admin_menu())
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Xato: {e}", reply_markup=admin_menu())


# =====================================
# PROMO / TOP
# =====================================
@bot.message_handler(func=lambda m: m.text == "🎟 Promokod")
@subscription_required
def promo_menu(message):
    msg = bot.send_message(message.chat.id, "🎟 Promokodni kiriting:", reply_markup=back_menu())
    bot.register_next_step_handler(msg, process_promo)


def process_promo(message):
    if message.text == "⬅️ Ortga":
        bot.send_message(message.chat.id, "🔙 Orqaga", reply_markup=build_main_menu(is_admin(message.from_user.id)))
        return
    uid = str(message.from_user.id)
    code = message.text.strip().upper()
    if code not in promo_codes:
        bot.send_message(message.chat.id, "❌ Promokod noto'g'ri", reply_markup=build_main_menu(is_admin(message.from_user.id)))
        return
    if code in users[uid].get("used_promo", []):
        bot.send_message(message.chat.id, "❌ Bu promokod oldin ishlatilgan", reply_markup=build_main_menu(is_admin(message.from_user.id)))
        return
    amount = int(promo_codes[code])
    users[uid].setdefault("used_promo", []).append(code)
    users[uid]["balance"] += amount
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, f"✅ Promokod ishladi: +{amount} so'm", reply_markup=build_main_menu(is_admin(message.from_user.id)))


@bot.message_handler(func=lambda m: m.text == "🏆 Top referallar")
@subscription_required
def top_referrals(message):
    ranking = []
    for uid, user in users.items():
        refs = user.get("refs", {})
        total = refs.get("level1", 0) + refs.get("level2", 0) + refs.get("level3", 0)
        if total > 0:
            ranking.append((uid, user.get("username", "Noma'lum"), total, refs.get("level1", 0)))
    ranking.sort(key=lambda x: x[2], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    if not ranking:
        bot.send_message(message.chat.id, "Hali top referallar yo'q")
        return
    lines = ["🏆 <b>TOP referallar</b>"]
    for i, row in enumerate(ranking[:10], start=1):
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"\n{prefix} {row[1]} — {row[2]} ta")
    bot.send_message(message.chat.id, "\n".join(lines))


# =====================================
# BOT SETTINGS
# =====================================
@bot.message_handler(func=lambda m: m.text == "⚙️ Bot sozlamalari" and is_admin(m.from_user.id))
@admin_required
def bot_settings(message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📢 Pul ishlash kanallari", "👁 Postlar")
    kb.add("🔐 Majburiy kanallar", "💰 Referal sozlamalari")
    kb.add("⬅️ Ortga")
    bot.send_message(message.chat.id, "⚙️ Bot sozlamalari", reply_markup=kb)


@bot.message_handler(func=lambda m: m.text == "📢 Pul ishlash kanallari" and is_admin(m.from_user.id))
@admin_required
def manage_earn_channels(message):
    channels = config.get("earn_channels", [])
    text = "📢 Pul ishlash kanallari\n\n" + ("\n".join(f"{i+1}. {c}" for i, c in enumerate(channels)) if channels else "Hozircha bo'sh")
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("➕ Qo'shish", callback_data="add_earn_channel"))
    if channels:
        markup.add(InlineKeyboardButton("❌ O'chirish", callback_data="remove_earn_channel"))
    bot.send_message(message.chat.id, text, reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data == "add_earn_channel")
def add_earn_channel(c):
    if not is_admin(c.from_user.id):
        return
    msg = bot.send_message(c.message.chat.id, "Yangi kanal linkini yuboring (https://t.me/...)")
    bot.register_next_step_handler(msg, process_add_earn_channel)


def process_add_earn_channel(message):
    link = message.text.strip()
    if not link.startswith("https://t.me/"):
        bot.send_message(message.chat.id, "❌ Link noto'g'ri", reply_markup=admin_menu())
        return
    config.setdefault("earn_channels", []).append(link)
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, "✅ Kanal qo'shildi", reply_markup=admin_menu())


@bot.callback_query_handler(func=lambda c: c.data == "remove_earn_channel")
def remove_earn_channel(c):
    if not is_admin(c.from_user.id):
        return
    channels = config.get("earn_channels", [])
    markup = InlineKeyboardMarkup(row_width=1)
    for i, ch in enumerate(channels):
        markup.add(InlineKeyboardButton(f"{i+1}. {ch[:40]}", callback_data=f"delete_earn_channel_{i}"))
    bot.send_message(c.message.chat.id, "O'chirish uchun tanlang:", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("delete_earn_channel_"))
def delete_earn_channel(c):
    if not is_admin(c.from_user.id):
        return
    idx = int(c.data.split("_")[-1])
    channels = config.get("earn_channels", [])
    if 0 <= idx < len(channels):
        channels.pop(idx)
        config["earn_channels"] = channels
        Database.save_all(users, orders, config, promo_codes)
    bot.answer_callback_query(c.id, "✅ O'chirildi")


@bot.message_handler(func=lambda m: m.text == "👁 Postlar" and is_admin(m.from_user.id))
@admin_required
def manage_posts(message):
    posts = config.get("earn_posts", [])
    text = "👁 Postlar\n\n" + ("\n".join(f"{i+1}. {p}" for i, p in enumerate(posts)) if posts else "Hozircha bo'sh")
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("➕ Qo'shish", callback_data="add_earn_post"))
    if posts:
        markup.add(InlineKeyboardButton("❌ O'chirish", callback_data="remove_earn_post"))
    bot.send_message(message.chat.id, text, reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data == "add_earn_post")
def add_earn_post(c):
    if not is_admin(c.from_user.id):
        return
    msg = bot.send_message(c.message.chat.id, "Yangi post linkini yuboring")
    bot.register_next_step_handler(msg, process_add_earn_post)


def process_add_earn_post(message):
    link = message.text.strip()
    config.setdefault("earn_posts", []).append(link)
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, "✅ Post qo'shildi", reply_markup=admin_menu())


@bot.callback_query_handler(func=lambda c: c.data == "remove_earn_post")
def remove_earn_post(c):
    if not is_admin(c.from_user.id):
        return
    posts = config.get("earn_posts", [])
    markup = InlineKeyboardMarkup(row_width=1)
    for i, p in enumerate(posts):
        markup.add(InlineKeyboardButton(f"{i+1}. {p[:40]}", callback_data=f"delete_earn_post_{i}"))
    bot.send_message(c.message.chat.id, "O'chirish uchun tanlang:", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("delete_earn_post_"))
def delete_earn_post(c):
    if not is_admin(c.from_user.id):
        return
    idx = int(c.data.split("_")[-1])
    posts = config.get("earn_posts", [])
    if 0 <= idx < len(posts):
        posts.pop(idx)
        config["earn_posts"] = posts
        Database.save_all(users, orders, config, promo_codes)
    bot.answer_callback_query(c.id, "✅ O'chirildi")


@bot.message_handler(func=lambda m: m.text == "🔐 Majburiy kanallar" and is_admin(m.from_user.id))
@admin_required
def manage_required_channels(message):
    required = config.get("required_channels", DEFAULT_REQUIRED_CHANNELS)
    lines = ["🔐 Majburiy kanallar"]
    for i, ch in enumerate(required, start=1):
        lines.append(f"{i}. {ch.get('title')} - {ch.get('username')}")
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("➕ Qo'shish", callback_data="add_required_channel"))
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data == "add_required_channel")
def add_required_channel(c):
    if not is_admin(c.from_user.id):
        return
    msg = bot.send_message(c.message.chat.id, "Format: @username - Title")
    bot.register_next_step_handler(msg, process_add_required_channel)


def process_add_required_channel(message):
    raw = message.text.strip()
    parts = raw.split(" - ", 1)
    if len(parts) != 2:
        bot.send_message(message.chat.id, "❌ Format noto'g'ri", reply_markup=admin_menu())
        return
    username, title = parts[0].strip(), parts[1].strip()
    if not username.startswith("@"):
        username = "@" + username
    config.setdefault("required_channels", []).append({"username": username, "title": title})
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, "✅ Qo'shildi", reply_markup=admin_menu())


@bot.message_handler(func=lambda m: m.text == "💰 Referal sozlamalari" and is_admin(m.from_user.id))
@admin_required
def referral_settings(message):
    levels = config.get("referral_levels", {"1": 1000, "2": 300, "3": 100})
    req = config.get("referral_requirements", {"level1": 10, "level2": 3})
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("1-daraja", callback_data="edit_ref_level_1"), InlineKeyboardButton("2-daraja", callback_data="edit_ref_level_2"))
    markup.add(InlineKeyboardButton("3-daraja", callback_data="edit_ref_level_3"), InlineKeyboardButton("1L talabi", callback_data="edit_ref_req_1"))
    markup.add(InlineKeyboardButton("2L talabi", callback_data="edit_ref_req_2"))
    bot.send_message(message.chat.id, f"💰 Referal sozlamalari\n\n1L: {levels['1']}\n2L: {levels['2']}\n3L: {levels['3']}\n\nTalablar:\n1L: {req['level1']}\n2L: {req['level2']}", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("edit_ref_level_"))
def edit_ref_level(c):
    if not is_admin(c.from_user.id):
        return
    level = c.data.split("_")[-1]
    msg = bot.send_message(c.message.chat.id, f"{level}-daraja bonusini kiriting:")
    bot.register_next_step_handler(msg, lambda m: process_edit_ref_level(m, level))


def process_edit_ref_level(message, level: str):
    try:
        amount = int(message.text.strip())
    except Exception:
        bot.send_message(message.chat.id, "❌ Son kiriting", reply_markup=admin_menu())
        return
    config.setdefault("referral_levels", {})[str(level)] = amount
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, "✅ Saqlandi", reply_markup=admin_menu())


@bot.callback_query_handler(func=lambda c: c.data.startswith("edit_ref_req_"))
def edit_ref_req(c):
    if not is_admin(c.from_user.id):
        return
    level = c.data.split("_")[-1]
    msg = bot.send_message(c.message.chat.id, f"{level}-daraja talabini kiriting:")
    bot.register_next_step_handler(msg, lambda m: process_edit_ref_req(m, level))


def process_edit_ref_req(message, level: str):
    try:
        amount = int(message.text.strip())
    except Exception:
        bot.send_message(message.chat.id, "❌ Son kiriting", reply_markup=admin_menu())
        return
    config.setdefault("referral_requirements", {})[f"level{level}"] = amount
    Database.save_all(users, orders, config, promo_codes)
    bot.send_message(message.chat.id, "✅ Saqlandi", reply_markup=admin_menu())


# =====================================
# ADS
# =====================================
@bot.message_handler(func=lambda m: m.text == "📢 Reklama" and is_admin(m.from_user.id))
@admin_required
def send_ad(message):
    msg = bot.send_message(message.chat.id, "📢 Reklama matni yoki media yuboring:", reply_markup=back_menu())
    bot.register_next_step_handler(msg, process_ad)


def process_ad(message):
    if getattr(message, "text", None) == "⬅️ Ortga":
        bot.send_message(message.chat.id, "🔙 Orqaga", reply_markup=admin_menu())
        return
    sent = 0
    failed = 0
    for uid in list(users.keys()):
        try:
            if message.content_type == "text":
                bot.send_message(int(uid), message.text)
            elif message.content_type == "photo":
                bot.send_photo(int(uid), message.photo[-1].file_id, caption=message.caption)
            elif message.content_type == "video":
                bot.send_video(int(uid), message.video.file_id, caption=message.caption)
            elif message.content_type == "document":
                bot.send_document(int(uid), message.document.file_id, caption=message.caption)
            else:
                bot.send_message(int(uid), "📢 Sizga yangi e'lon yuborildi")
            sent += 1
        except Exception:
            failed += 1
    bot.send_message(message.chat.id, f"✅ Tugadi\nYuborildi: {sent}\nXato: {failed}", reply_markup=admin_menu())


# =====================================
# NAVIGATION / FALLBACK
# =====================================
@bot.message_handler(func=lambda m: m.text == "⬅️ Asosiy menyu")
def to_main_menu(message):
    bot.send_message(message.chat.id, "🔙 Asosiy menyu", reply_markup=build_main_menu(is_admin(message.from_user.id)))


@bot.message_handler(func=lambda m: m.text == "⬅️ Ortga")
def back(message):
    bot.send_message(message.chat.id, "🔙 Asosiy menyu", reply_markup=build_main_menu(is_admin(message.from_user.id)))


@bot.message_handler(func=lambda m: True, content_types=["text"])
def unknown(message):
    ensure_user(message)
    if user_blocked(str(message.from_user.id)):
        bot.send_message(message.chat.id, "❌ Siz bloklangansiz.")
        return
    bot.send_message(message.chat.id, "❌ Noto'g'ri buyruq. Menyudan tanlang.", reply_markup=build_main_menu(is_admin(message.from_user.id)))


# =====================================
# RUN
# =====================================
if __name__ == "__main__":
    print("=" * 50)
    print("🤖 BOT ISHGA TUSHMOQDA...")
    print("=" * 50)
    keep_alive()
    try:
        me = bot.get_me()
        print(f"✅ Bot: @{me.username}")
    except Exception as e:
        print(f"❌ Bot ma'lumotini olishda xato: {e}")
    print(f"👥 Foydalanuvchilar: {len(users)}")
    print(f"📦 Buyurtmalar: {len(orders)}")
    print(f"👨‍💻 Adminlar: {len(ADMINS)}")
    print("=" * 50)
    logger.info("Bot ishga tushdi")
    try:
        bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.error(f"Kritik xato: {e}", exc_info=True)
        print(f"❌ Xato: {e}")
    finally:
        Database.save_all(users, orders, config, promo_codes)
        Database.backup()
        logger.info("Bot to'xtatildi")
        print("✅ Ma'lumotlar saqlandi")
