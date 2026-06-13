"""
aNamaka Transactions Bot
- Only /stats @username works
- Everything else = silence
- Reads from same escrow_data.json as main escrow bot
"""

import telebot
import json, os, datetime

BOT_TOKEN  = os.environ.get("STATS_BOT_TOKEN", "")
DATA_FILE  = "escrow_data.json"
PROMO_TAG  = "@anamakafranchise"

bot = telebot.TeleBot(BOT_TOKEN)

# ──────────────────────────────────────────────────
#  DATA
# ──────────────────────────────────────────────────

def load():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"deals": {}, "users": {}, "counter": 1}

def fmt(amount):
    try:
        f = float(amount)
        if f == int(f):
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except:
        return str(amount)

def get_user_by_uname(data, uname):
    uname = uname.lstrip("@").lower()
    for uid, u in data["users"].items():
        if u.get("username","").lower() == uname:
            return uid, u
    return None, None

def get_ranking(data, uid_key):
    """Rank by total_volume descending"""
    ranked = sorted(data["users"].items(),
                    key=lambda x: x[1].get("total_volume", 0), reverse=True)
    for i, (k, _) in enumerate(ranked, 1):
        if k == uid_key:
            return i
    return len(data["users"])

def get_full_stats(data, uid_key, u):
    """
    Calculate real stats from completed deals.
    Separates INR and Crypto volumes.
    """
    uname = u.get("username","").lower()

    inr_volume    = 0.0
    inr_deals     = 0
    crypto_volume = 0.0
    crypto_deals  = 0
    crypto_sym    = "USDT"
    highest_inr   = 0.0
    highest_crypto = 0.0
    completed     = 0
    ongoing       = 0

    for did, deal in data["deals"].items():
        involved = [
            deal.get("buyer","").lower(),
            deal.get("seller","").lower(),
            deal.get("escrow","").lower()
        ]
        if uname not in involved:
            continue

        amount = deal.get("amount", 0)
        ctype  = deal.get("currency_type", "INR")
        csym   = deal.get("currency_sym", "₹")

        if deal.get("completed"):
            completed += 1
            if ctype == "INR":
                inr_volume += amount
                inr_deals  += 1
                highest_inr = max(highest_inr, amount)
            else:
                crypto_volume += amount
                crypto_deals  += 1
                crypto_sym     = csym
                highest_crypto = max(highest_crypto, amount)
        elif deal.get("status") not in ("CANCELLED",):
            ongoing += 1

    total_deals = u.get("total_deals", completed + ongoing)
    rank        = get_ranking(data, uid_key)

    return {
        "rank":           rank,
        "total_deals":    total_deals,
        "completed":      completed,
        "ongoing":        ongoing,
        "inr_volume":     round(inr_volume, 2),
        "inr_deals":      inr_deals,
        "highest_inr":    round(highest_inr, 2),
        "crypto_volume":  round(crypto_volume, 2),
        "crypto_deals":   crypto_deals,
        "crypto_sym":     crypto_sym,
        "highest_crypto": round(highest_crypto, 2),
        "as_buyer":       u.get("as_buyer", 0),
        "as_seller":      u.get("as_seller", 0),
        "as_escrow":      u.get("as_escrow", 0),
    }

# ──────────────────────────────────────────────────
#  /stats @username
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    parts = message.text.split()
    data  = load()

    # /stats @username  OR  /stats (own)
    if len(parts) > 1:
        target  = parts[1].lstrip("@")
        uid_key, u = get_user_by_uname(data, target)
        if not u:
            bot.reply_to(message,
                f"❌ No deal history found for @{target}.\n"
                f"They may not have participated in any deals yet.")
            return
    else:
        uid_key = str(message.from_user.id)
        if uid_key not in data["users"]:
            bot.reply_to(message,
                "❌ You have no deal history yet.")
            return
        u = data["users"][uid_key]

    s = get_full_stats(data, uid_key, u)

    # ── Build message — simplified, INR only ──
    text  = f"📊 Participant Stats for @{u['username']}\n\n"
    text += f"👑 Ranking: #{s['rank']}\n"
    text += f"🔢 Total Deals: {s['total_deals']}\n"
    text += f"✅ Completed: {s['completed']}\n"
    text += f"⏳ Ongoing Deals: {s['ongoing']}\n\n"
    text += f"💵 Total Volume: ₹{fmt(s['inr_volume'])}\n"
    text += f"⚡ Highest Deal: ₹{fmt(s['highest_inr'])}\n\n"
    text += f"Always use {PROMO_TAG} for safer deals!"

    bot.reply_to(message, text)

# ──────────────────────────────────────────────────
#  IGNORE EVERYTHING ELSE — complete silence
# ──────────────────────────────────────────────────

@bot.message_handler(func=lambda m: True,
    content_types=["text","photo","video","document","sticker","audio","voice"])
def ignore_all(message):
    pass

# ──────────────────────────────────────────────────
#  RUN
# ──────────────────────────────────────────────────

print("📊 aNamaka Transactions Bot running — only /stats works")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
