"""
aNamaka CRPT Escrow Bot v8
- Deal ID: #ET000001 format
- Normal emojis (no premium)
- /backup + /restore features
- /allowgroup (owner only: SHADOWZ_HERE - 8174588447)
- /kickall fix (current + old members)
- /escrowstats (monthly leaderboard)
- INR 5% + Crypto 3% fees
- /received auto detect
"""

import telebot
from telebot import types
import json, os, datetime, threading, re
from datetime import datetime as dt

BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
VOUCH_CHANNEL  = os.environ.get("VOUCH_CHANNEL", "@YourVouchChannel")
OWNER_ID       = 8174588447  # SHADOWZ_HERE
INR_FEE_PCT    = 5
CRYPTO_FEE_PCT = 3
DEAL_PREFIX    = "ET"
DATA_FILE      = "crpt_data.json"

bot = telebot.TeleBot(BOT_TOKEN)

# ──────────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────────

def load():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"deals": {}, "users": {}, "counter": 1, "allowed_groups": [OWNER_ID], "whitelist_groups": []}

def save(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def next_id(data):
    n = data["counter"]
    data["counter"] += 1
    save(data)
    return f"#{DEAL_PREFIX}{str(n).zfill(6)}"

def fmt(amount):
    try:
        f = float(amount)
        if f == int(f):
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except:
        return str(amount)

CRYPTO_KEYWORDS = {"usdt","usdc","btc","eth","bnb","trx","sol","ltc","xrp","crypto","usd","$"}

def detect_currency(amount_raw, network):
    combined = (str(amount_raw) + " " + str(network)).lower()
    for kw in CRYPTO_KEYWORDS:
        if kw in combined:
            sym = "$"
            for c in ["usdt","usdc","btc","eth","bnb","trx","sol","ltc","xrp"]:
                if c in combined:
                    sym = c.upper()
                    break
            return "CRYPTO", sym, CRYPTO_FEE_PCT
    return "INR", "₹", INR_FEE_PCT

def calc_fee(amount, fee_pct):
    fee = round(amount * fee_pct / 100, 2)
    total = round(amount + fee, 2)
    return fee, total

def is_admin(chat_id, user_id):
    try:
        m = bot.get_chat_member(chat_id, user_id)
        return m.status in ("administrator","creator")
    except:
        return False

def is_in_group(chat_id, user_id):
    try:
        m = bot.get_chat_member(chat_id, user_id)
        return m.status in ("member","administrator","creator","restricted")
    except:
        return False

def get_user_by_uname(data, uname):
    uname = uname.lstrip("@").lower()
    for uid, u in data["users"].items():
        if u.get("username","").lower() == uname:
            return uid, u
    return None, None

def ensure_user(data, username, user_id=None):
    uid_key, u = get_user_by_uname(data, username)
    if uid_key:
        if user_id and not u.get("user_id"):
            u["user_id"] = user_id
        return uid_key
    uid_key = str(user_id) if user_id else f"u_{username.lower()}"
    data["users"][uid_key] = {
        "username": username.lstrip("@"),
        "user_id": user_id or 0,
        "total_deals": 0, "completed_deals": 0,
        "total_volume": 0.0, "highest_deal": 0.0,
        "ongoing_deals": 0, "as_buyer": 0, "as_seller": 0, "as_escrow": 0,
        "deal_ids": [], "joined": dt.now().isoformat()
    }
    return uid_key

# ──────────────────────────────────────────────────
#  /allowgroup — Owner only
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["allowgroup"])
def cmd_allowgroup(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ Only owner (@SHADOWZ_HERE) can allow groups.")
        return
    
    data = load()
    current_chat = message.chat.id
    
    if current_chat not in data.get("whitelist_groups", []):
        data["whitelist_groups"] = data.get("whitelist_groups", [])
        data["whitelist_groups"].append(current_chat)
        save(data)
        bot.reply_to(message, f"✅ Group {current_chat} has been whitelisted.\nBot can now be used here.")
    else:
        bot.reply_to(message, "⚠️ Group is already whitelisted.")

@bot.message_handler(commands=["removegroup"])
def cmd_removegroup(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ Only owner can remove groups.")
        return
    
    data = load()
    current_chat = message.chat.id
    
    if current_chat in data.get("whitelist_groups", []):
        data["whitelist_groups"].remove(current_chat)
        save(data)
        bot.reply_to(message, f"✅ Group {current_chat} has been removed from whitelist.")
    else:
        bot.reply_to(message, "⚠️ Group is not whitelisted.")

# ──────────────────────────────────────────────────
#  /backup — Save data
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["backup"])
def cmd_backup(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ Only owner can backup data.")
        return
    
    try:
        with open(DATA_FILE, "r") as f:
            data = f.read()
        bot.send_document(message.chat.id, 
            telebot.types.InputFile(DATA_FILE),
            caption="📦 Backup of escrow data")
        bot.reply_to(message, "✅ Backup created successfully!")
    except:
        bot.reply_to(message, "❌ Backup failed.")

# ──────────────────────────────────────────────────
#  /restore — Restore data
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["restore"])
def cmd_restore(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ Only owner can restore data.")
        return
    
    if not message.reply_to_message or not message.reply_to_message.document:
        bot.reply_to(message, "❌ Reply to a document to restore.")
        return
    
    try:
        file_info = bot.get_file(message.reply_to_message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        with open(DATA_FILE, "wb") as f:
            f.write(downloaded)
        bot.reply_to(message, "✅ Data restored successfully!")
    except Exception as e:
        bot.reply_to(message, f"❌ Restore failed: {e}")

# ──────────────────────────────────────────────────
#  FORM & DEAL
# ──────────────────────────────────────────────────

BLANK_FORM = """📝 Please Fill Out the Form Below:

1. Username of Buyer: @
2. Username of Seller: @
3. Escrow Condition: 
4. Timeframe for Completion: 
5. Deal Amount: ₹ / $
6. Mode of Payment: 

🔒 Notes:
• Edited forms will not be accepted.
• Payment from digital banks is not acceptable.
• For UPI payments, mention the full bank name."""

@bot.message_handler(commands=["form","dd"])
def cmd_form(message):
    data = load()
    if message.chat.id not in data.get("whitelist_groups", []):
        bot.reply_to(message, "❌ Bot not allowed in this group. Owner must enable with /allowgroup")
        return
    bot.send_message(message.chat.id, BLANK_FORM)

def parse_form(text):
    result = {}
    lines = text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line or line.lower().startswith("🔒"):
            continue
        line = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
        low = line.lower()
        if ":" in line:
            key, _, val = line.partition(":")
        elif "-" in line:
            key, _, val = line.partition("-")
        else:
            continue
        key, val = key.strip().lower(), val.strip()
        if not val:
            continue
        if "buyer" in key:
            result["buyer"] = val.lstrip("@").split()[0].strip(".,")
        elif "seller" in key:
            result["seller"] = val.lstrip("@").split()[0].strip(".,")
        elif any(w in key for w in ["time","complete","timeframe"]):
            result["timeframe"] = val
        elif "amount" in key:
            result["amount_raw"] = val
            clean = re.sub(r"[₹$€£\s/]", " ", val)
            m = re.search(r"[\d,]+\.?\d*", clean)
            if m:
                try:
                    result["amount"] = float(m.group(0).replace(",",""))
                except:
                    result["amount"] = 0.0
        elif any(w in key for w in ["network","payment","mode"]):
            result["network"] = val
    return result

pending = {}

@bot.message_handler(commands=["deal"])
def cmd_deal(message):
    data = load()
    if message.chat.id not in data.get("whitelist_groups", []):
        bot.reply_to(message, "❌ Bot not allowed in this group.")
        return
    
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /deal.")
        return

    if not message.reply_to_message:
        bot.reply_to(message, "ℹ️ Reply on form and type /deal.")
        return

    form_text = message.reply_to_message.text or ""
    form = parse_form(form_text)

    missing = [f for f in ["buyer","seller"] if not form.get(f)]
    if missing:
        bot.reply_to(message, "❌ Buyer and Seller username required.")
        return

    deal_id = next_id(data)
    amount = form.get("amount", 0.0)
    timeframe = form.get("timeframe", "")
    network = form.get("network", "")
    terms = form.get("condition", "")

    ctype, csym, fee_pct = detect_currency(form.get("amount_raw",""), network)
    fee, total = calc_fee(amount, fee_pct)

    escrow_uname = message.from_user.username or message.from_user.first_name
    escrow_id = message.from_user.id

    pending[deal_id] = {
        "deal_id": deal_id,
        "buyer": form["buyer"],
        "seller": form["seller"],
        "escrow": escrow_uname,
        "escrow_id": escrow_id,
        "timeframe": timeframe,
        "amount": amount,
        "fee": fee,
        "fee_pct": fee_pct,
        "total": total,
        "currency_type": ctype,
        "currency_sym": csym,
        "network": network,
        "terms": terms,
        "confirmed_buyer": False,
        "confirmed_seller": False,
        "confirm_msg_ids": [],
        "chat_id": message.chat.id,
        "status": "AWAITING_CONFIRM",
        "created_at": dt.now().isoformat(),
    }

    card = f"{deal_id}\n\n👤 Buyer: @{form['buyer']}\n👤 Seller: @{form['seller']}\n"
    if terms:
        card += f"Condition: {terms}\n"
    if timeframe:
        card += f"Timeframe: {timeframe}\n"
    if amount:
        card += f"Amount: {csym}{fmt(amount)}\n"
    if network:
        card += f"Payment: {network}\n"
    card += f"Fee: {fee_pct}% ({csym}{fmt(fee)})\nTotal: {csym}{fmt(total)}\n\nEscrower: @{escrow_uname}\n\nPlease confirm the deal."

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Seller ✅", callback_data=f"cs_{deal_id}"),
        types.InlineKeyboardButton("Buyer ✅", callback_data=f"cb_{deal_id}")
    )
    markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data=f"cx_{deal_id}"))

    sent = bot.send_message(message.chat.id, card, reply_markup=markup)
    pending[deal_id]["msg_id"] = sent.message_id

@bot.callback_query_handler(func=lambda c: c.data[:3] in ("cs_","cb_","cx_"))
def handle_confirm(call):
    prefix = call.data[:3]
    deal_id = call.data[3:]
    caller_u = (call.from_user.username or call.from_user.first_name or "").lower()
    caller_id = call.from_user.id

    if deal_id not in pending:
        bot.answer_callback_query(call.id, "❌ Deal not found.")
        return

    state = pending[deal_id]

    if prefix == "cx_":
        if caller_id != state["escrow_id"]:
            bot.answer_callback_query(call.id, "❌ Only escrow can cancel.", show_alert=True)
            return
        del pending[deal_id]
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        bot.send_message(call.message.chat.id, f"❌ Deal {deal_id} cancelled.")
        bot.answer_callback_query(call.id, "Cancelled.")
        return

    if prefix == "cs_":
        if caller_u != state["seller"].lower():
            bot.answer_callback_query(call.id, f"❌ Only @{state['seller']} can confirm.", show_alert=True)
            return
        if state["confirmed_seller"]:
            bot.answer_callback_query(call.id, "Already confirmed.")
            return
        state["confirmed_seller"] = True
        bot.answer_callback_query(call.id, "✅ Confirmed!")
        m = bot.send_message(call.message.chat.id, f"✅ Seller @{state['seller']} confirmed {deal_id}.")
        state["confirm_msg_ids"].append(m.message_id)

    elif prefix == "cb_":
        if caller_u != state["buyer"].lower():
            bot.answer_callback_query(call.id, f"❌ Only @{state['buyer']} can confirm.", show_alert=True)
            return
        if state["confirmed_buyer"]:
            bot.answer_callback_query(call.id, "Already confirmed.")
            return
        state["confirmed_buyer"] = True
        bot.answer_callback_query(call.id, "✅ Confirmed!")
        m = bot.send_message(call.message.chat.id, f"✅ Buyer @{state['buyer']} confirmed {deal_id}.")
        state["confirm_msg_ids"].append(m.message_id)

    if state["confirmed_buyer"] and state["confirmed_seller"]:
        _activate_deal(call.message.chat.id, call.message.message_id, deal_id, state)

def _activate_deal(chat_id, msg_id, deal_id, state):
    data = load()
    for mid in state.get("confirm_msg_ids", []):
        try:
            bot.delete_message(chat_id, mid)
        except: pass
    try:
        bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
    except: pass

    record = {k: v for k, v in state.items() if k not in ("confirmed_buyer","confirmed_seller","msg_id","confirm_msg_ids")}
    record["status"] = "AWAITING_PAYMENT"
    record["activated_at"] = dt.now().isoformat()
    data["deals"][deal_id] = record

    for uname, role in [(state["buyer"],"buyer"), (state["seller"],"seller")]:
        uk = ensure_user(data, uname)
        u = data["users"][uk]
        u["total_deals"] += 1
        u[f"as_{role}"] += 1
        u["ongoing_deals"] += 1
        if deal_id not in u["deal_ids"]:
            u["deal_ids"].append(deal_id)

    ek = ensure_user(data, state["escrow"], state["escrow_id"])
    e = data["users"][ek]
    e["total_deals"] += 1
    e["as_escrow"] += 1
    e["ongoing_deals"] += 1
    if deal_id not in e["deal_ids"]:
        e["deal_ids"].append(deal_id)

    save(data)
    if deal_id in pending:
        del pending[deal_id]

    msg = f"{deal_id}\n\n👤 Buyer: @{state['buyer']}\n👤 Seller: @{state['seller']}\n\nBoth confirmed ✅\n\nPay escrow @{state['escrow']} to proceed."
    bot.send_message(chat_id, msg)

# ──────────────────────────────────────────────────
#  /received — Auto detect
# ──────────────────────────────────────────────────

def find_escrow_deal(caller_username, chat_id, data, need_received=False):
    matches = []
    for did, deal in data["deals"].items():
        if deal.get("completed") or deal.get("status") == "CANCELLED":
            continue
        if deal.get("escrow","").lower() != caller_username.lower():
            continue
        if deal.get("chat_id") != chat_id:
            continue
        if need_received and deal.get("status") == "IN_PROGRESS":
            matches.append(did)
        elif not need_received and deal.get("status") == "AWAITING_PAYMENT":
            matches.append(did)
    return matches[0] if len(matches) == 1 else None

@bot.message_handler(commands=["received"])
def cmd_received(message):
    data = load()
    if message.chat.id not in data.get("whitelist_groups", []):
        bot.reply_to(message, "❌ Bot not allowed.")
        return
    
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins.")
        return

    parts = message.text.split()
    caller = (message.from_user.username or message.from_user.first_name or "").lower()

    if len(parts) >= 2:
        deal_id = parts[1]
        if not deal_id.startswith("#"):
            deal_id = "#" + deal_id
    else:
        deal_id = find_escrow_deal(caller, message.chat.id, data, need_received=False)
        if not deal_id:
            bot.reply_to(message, "No active deal found. Usage: /received DEAL_ID")
            return

    if deal_id not in data["deals"]:
        bot.reply_to(message, f"❌ {deal_id} not found.")
        return

    deal = data["deals"][deal_id]
    if caller != deal["escrow"].lower():
        bot.reply_to(message, f"❌ Only @{deal['escrow']} can mark received.")
        return

    amount, fee, total = deal["amount"], deal["fee"], deal["total"]
    csym = deal.get("currency_sym", "₹")

    deal["received"] = True
    deal["status"] = "IN_PROGRESS"
    deal["received_at"] = dt.now().isoformat()
    save(data)

    bot.send_message(message.chat.id,
        f"Received: {csym}{fmt(total)}\nRelease: {csym}{fmt(amount)}\nFee: {csym}{fmt(fee)}\n\n{deal_id}\nBuyer: @{deal['buyer']}\nSeller: @{deal['seller']}")

# ──────────────────────────────────────────────────
#  /complete — Auto detect + Vouch
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["complete"])
def cmd_complete(message):
    data = load()
    if message.chat.id not in data.get("whitelist_groups", []):
        bot.reply_to(message, "❌ Bot not allowed.")
        return
    
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins.")
        return

    parts = message.text.split()
    caller = (message.from_user.username or message.from_user.first_name or "").lower()

    if len(parts) >= 2:
        deal_id = parts[1]
        if not deal_id.startswith("#"):
            deal_id = "#" + deal_id
    else:
        deal_id = find_escrow_deal(caller, message.chat.id, data, need_received=True)
        if not deal_id:
            deal_id = find_escrow_deal(caller, message.chat.id, data, need_received=False)
        if not deal_id:
            bot.reply_to(message, "No deal found. Usage: /complete DEAL_ID")
            return

    if deal_id not in data["deals"]:
        bot.reply_to(message, f"❌ {deal_id} not found.")
        return

    deal = data["deals"][deal_id]
    if caller != deal["escrow"].lower():
        bot.reply_to(message, f"❌ Only @{deal['escrow']} can complete.")
        return
    if deal.get("completed"):
        bot.reply_to(message, f"⚠️ Already completed.")
        return

    amount = deal["amount"]
    csym = deal.get("currency_sym", "₹")
    escrow_id = deal.get("escrow_id", 0)
    escrow_name = deal["escrow"]

    deal["completed"] = True
    deal["status"] = "COMPLETED"
    deal["completed_at"] = dt.now().isoformat()

    for uid_key, u in data["users"].items():
        uname = u.get("username","").lower()
        if uname in [deal["buyer"].lower(), deal["seller"].lower(), deal["escrow"].lower()]:
            u["completed_deals"] = u.get("completed_deals",0) + 1
            u["ongoing_deals"] = max(0, u.get("ongoing_deals",1) - 1)
            u["total_volume"] = round(u.get("total_volume",0) + amount, 2)
            u["highest_deal"] = max(u.get("highest_deal",0), amount)

    save(data)
    date_str = dt.now().strftime("%d %b %Y")

    vouch = f"Deal Completed ✅\n\n{deal_id}\nReleased: {csym}{fmt(amount)}\nBuyer: @{deal['buyer']}\nSeller: @{deal['seller']}\nEscrowed By: @{escrow_name}\n\n{date_str}"

    bot.send_message(message.chat.id, vouch)
    try:
        bot.send_message(VOUCH_CHANNEL, vouch)
    except:
        pass

# ──────────────────────────────────────────────────
#  /escrowstats — Monthly leaderboard
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["escrowstats"])
def cmd_escrowstats(message):
    data = load()
    now = dt.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_name = now.strftime("%B %Y")

    escrow_stats = {}
    total_deals = 0
    total_inr = 0.0
    total_crypto = 0.0

    for did, deal in data["deals"].items():
        if not deal.get("completed"):
            continue
        try:
            dt_obj = dt.fromisoformat(deal.get("completed_at",""))
            if dt_obj < month_start:
                continue
        except:
            continue

        escrow = deal.get("escrow","")
        if not escrow:
            continue
        ekey = escrow.lower()
        amount = deal.get("amount", 0)
        ctype = deal.get("currency_type", "INR")
        csym = deal.get("currency_sym", "₹")

        if ekey not in escrow_stats:
            escrow_stats[ekey] = {
                "username": escrow, "deals": 0,
                "inr_volume": 0.0, "inr_deals": 0,
                "crypto_volume": 0.0, "crypto_deals": 0,
                "crypto_sym": "USDT"
            }

        escrow_stats[ekey]["deals"] += 1
        if ctype == "INR":
            escrow_stats[ekey]["inr_volume"] = round(escrow_stats[ekey]["inr_volume"] + amount, 2)
            escrow_stats[ekey]["inr_deals"] += 1
            total_inr = round(total_inr + amount, 2)
        else:
            escrow_stats[ekey]["crypto_volume"] = round(escrow_stats[ekey]["crypto_volume"] + amount, 2)
            escrow_stats[ekey]["crypto_deals"] += 1
            escrow_stats[ekey]["crypto_sym"] = csym
            total_crypto = round(total_crypto + amount, 2)
        total_deals += 1

    if not escrow_stats:
        bot.reply_to(message, f"No deals completed in {month_name}.")
        return

    ranked = sorted(escrow_stats.values(), key=lambda x: x["deals"], reverse=True)
    medals = ["🥇","🥈","🥉"]

    text = f"Escrow Leaderboard — {month_name}\n" + "━"*30 + "\n\n"
    for i, e in enumerate(ranked, 1):
        medal = medals[i-1] if i <= 3 else f"#{i}"
        text += f"{medal} @{e['username']} — {e['deals']} deals\n"
        if e["inr_deals"] > 0:
            text += f"   ₹{fmt(e['inr_volume'])} INR ({e['inr_deals']} deals)\n"
        if e["crypto_deals"] > 0:
            text += f"   {e['crypto_sym']}{fmt(e['crypto_volume'])} ({e['crypto_deals']} deals)\n"
        text += "\n"

    text += "━"*30 + f"\n\nGroup Total — {month_name}\n"
    text += f"Total Deals: {total_deals}\n"
    if total_inr > 0:
        text += f"Total INR: ₹{fmt(total_inr)}\n"
    if total_crypto > 0:
        text += f"Total USDT: ${fmt(total_crypto)}\n"

    bot.reply_to(message, text)

# ──────────────────────────────────────────────────
#  /stats — Participant stats
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    data = load()
    parts = message.text.split()

    if len(parts) >= 2:
        target_uname = parts[1].lstrip("@")
        uid_key, user = get_user_by_uname(data, target_uname)
        if not uid_key:
            bot.reply_to(message, f"❌ No deal history found for @{target_uname}.\nThey may not have participated in any deals yet.")
            return
    else:
        caller_id = str(message.from_user.id)
        caller_uname = message.from_user.username or ""
        if caller_id in data["users"]:
            uid_key = caller_id
            user = data["users"][uid_key]
        else:
            uid_key, user = get_user_by_uname(data, caller_uname)
        if not uid_key:
            bot.reply_to(message, "❌ You have no deal history yet.")
            return

    uname = user.get("username", "Unknown")
    total = user.get("total_deals", 0)
    completed = user.get("completed_deals", 0)
    ongoing = user.get("ongoing_deals", 0)
    volume = user.get("total_volume", 0.0)
    highest = user.get("highest_deal", 0.0)

    # Ranking by completed deals
    all_users = [(u.get("completed_deals",0), u.get("username","")) for u in data["users"].values() if u.get("total_deals",0) > 0]
    all_users.sort(key=lambda x: -x[0])
    rank = next((i+1 for i, (_, un) in enumerate(all_users) if un.lower() == uname.lower()), "-")

    # Determine currency sym (last deal)
    csym = "₹"
    for did in reversed(user.get("deal_ids", [])):
        deal = data["deals"].get(did)
        if deal:
            csym = deal.get("currency_sym", "₹")
            break

    text = (
        f"Participant Stats for @{uname}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏅 Ranking: #{rank}\n"
        f"📋 Total Deals: {total}\n"
        f"✅ Completed: {completed}\n"
        f"🔄 Ongoing Deals: {ongoing}\n\n"
        f"💰 Total Volume: {csym}{fmt(volume)}\n"
        f"⚡ Highest Deal: {csym}{fmt(highest)}\n\n"
        f"Always use @anamakafranchise for safer deals!"
    )
    bot.reply_to(message, text)

# ──────────────────────────────────────────────────
#  /mydeal
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["mydeal"])
def cmd_mydeal(message):
    data = load()
    caller_u = (message.from_user.username or "").lower()
    caller_id = message.from_user.id
    active = []

    for did, d in data["deals"].items():
        if d.get("completed") or d.get("status") == "CANCELLED":
            continue
        involved = [d.get("buyer","").lower(), d.get("seller","").lower(), d.get("escrow","").lower()]
        if caller_u in involved or caller_id == d.get("escrow_id",0):
            active.append((did, d))

    if not active:
        bot.reply_to(message, "No active deals.")
        return

    text = f"Your Active Deals ({len(active)})\n" + "─"*20 + "\n"
    for did, d in active:
        csym = d.get("currency_sym","₹")
        text += f"\n{did}\n{csym}{fmt(d['amount'])} | {d.get('status','').replace('_',' ').title()}\nB:@{d.get('buyer','?')} | S:@{d.get('seller','?')}\n"
    bot.reply_to(message, text)

# ──────────────────────────────────────────────────
#  /kickall — Full group scan
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["kickall"])
def cmd_kickall(message):
    data = load()
    if message.chat.type not in ["group","supergroup"]:
        bot.reply_to(message, "❌ Groups only.")
        return
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Admins only.")
        return

    protected = set()
    for did, deal in data["deals"].items():
        if not deal.get("completed") and deal.get("status") != "CANCELLED":
            protected.add(deal["buyer"].lower())
            protected.add(deal["seller"].lower())
            protected.add(deal["escrow"].lower())

    try:
        admins = bot.get_chat_administrators(message.chat.id)
        admin_ids = {a.user.id for a in admins}
    except:
        admin_ids = {message.from_user.id}

    kicked = []
    protected_list = []

    for uid_key, u in data["users"].items():
        uid_int = u.get("user_id", 0)
        uname = u.get("username","")
        if not isinstance(uid_int, int) or uid_int == 0:
            continue
        if uid_int in admin_ids:
            continue
        
        try:
            member = bot.get_chat_member(message.chat.id, uid_int)
            if member.status in ("left","kicked"):
                continue
        except:
            continue

        if uname.lower() in protected:
            protected_list.append(f"@{uname}")
            continue

        try:
            bot.ban_chat_member(message.chat.id, uid_int)
            bot.unban_chat_member(message.chat.id, uid_int)
            kicked.append(f"@{uname}")
        except:
            pass

    try:
        total = bot.get_chat_members_count(message.chat.id)
    except:
        total = 0

    result = f"Kick Operation Complete!\n\nTotal Members: {total}\nKicked: {len(kicked)}\nProtected: {len(protected_list)}\n"
    if kicked:
        result += "\nKicked:\n" + "\n".join(f"• {k}" for k in kicked)
    if protected_list:
        result += "\n\nProtected (Active Deals):\n" + "\n".join(f"• {p}" for p in protected_list)
    result += "\n\nUsers can rejoin via invite link."

    bot.send_message(message.chat.id, result)

# ──────────────────────────────────────────────────
#  /help
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["start","help"])
def cmd_help(message):
    data = load()
    if message.chat.id not in data.get("whitelist_groups", []):
        bot.send_message(message.chat.id, "❌ Bot not enabled in this group. Owner must run /allowgroup first.")
        return
    
    bot.send_message(message.chat.id,
        "aNamaka CRPT Escrow Bot v8\n"
        "─────────────────────────\n"
        "/form or /dd → Blank form\n"
        "/deal → Create deal (Admin)\n"
        "/received → Mark received - auto detect (Admin)\n"
        "/complete → Complete + vouch - auto detect (Admin)\n"
        "/stats → Your stats | /stats @user\n"
        "/mydeal → Active deals\n"
        "/escrowstats → Monthly leaderboard\n"
        "/kickall → Kick inactive (Admin)\n"
        "/backup → Save data (Owner)\n"
        "/restore → Restore data (Owner)\n"
        "/allowgroup → Enable group (Owner)\n"
        "─────────────────────────\n"
        f"INR Fee: {INR_FEE_PCT}% | Crypto Fee: {CRYPTO_FEE_PCT}%")

# ──────────────────────────────────────────────────
#  PASSIVE TRACKER
# ──────────────────────────────────────────────────

@bot.message_handler(func=lambda m: True, content_types=["text"])
def track(message):
    if not message.from_user or not message.from_user.username:
        return
    data = load()
    uid_key = str(message.from_user.id)
    uname = message.from_user.username
    if uid_key not in data["users"]:
        ensure_user(data, uname, message.from_user.id)
        save(data)

# ──────────────────────────────────────────────────
#  RUN
# ──────────────────────────────────────────────────

print("🤖 aNamaka CRPT Escrow Bot v8 running")

# ──────────────────────────────────────────────────
#  STATS BOT (separate token, same data file)
# ──────────────────────────────────────────────────

STATS_BOT_TOKEN = os.environ.get("STATS_BOT_TOKEN", "")

if STATS_BOT_TOKEN:
    stats_bot = telebot.TeleBot(STATS_BOT_TOKEN)

    @stats_bot.message_handler(commands=["start"])
    def sb_start(message):
        stats_bot.reply_to(message, "aNamaka Stats Bot\nUse /stats or /stats @username")

    @stats_bot.message_handler(commands=["stats"])
    def sb_stats(message):
        data = load()
        parts = message.text.split()

        if len(parts) >= 2:
            target_uname = parts[1].lstrip("@")
            uid_key, user = get_user_by_uname(data, target_uname)
            if not uid_key:
                stats_bot.reply_to(message, f"❌ No deal history found for @{target_uname}.\nThey may not have participated in any deals yet.")
                return
        else:
            caller_id = str(message.from_user.id)
            caller_uname = message.from_user.username or ""
            if caller_id in data["users"]:
                uid_key = caller_id
                user = data["users"][uid_key]
            else:
                uid_key, user = get_user_by_uname(data, caller_uname)
            if not uid_key:
                stats_bot.reply_to(message, "❌ You have no deal history yet.")
                return

        uname = user.get("username", "Unknown")
        total = user.get("total_deals", 0)
        completed = user.get("completed_deals", 0)
        ongoing = user.get("ongoing_deals", 0)

        USDT_TO_INR = 100

        def user_total_inr(u):
            total = 0.0
            for did in u.get("deal_ids", []):
                d = data["deals"].get(did)
                if not d:
                    continue
                amt = d.get("amount", 0.0)
                if d.get("currency_type", "INR") == "INR":
                    total += amt
                else:
                    total += amt * USDT_TO_INR
            return total

        all_users = [(user_total_inr(u), u.get("username", "")) for u in data["users"].values() if u.get("total_deals", 0) > 0]
        all_users.sort(key=lambda x: -x[0])
        rank = next((i + 1 for i, (_, un) in enumerate(all_users) if un.lower() == uname.lower()), "-")

        inr_volume = 0.0
        inr_highest = 0.0
        crypto_volumes = {}
        crypto_highest = {}

        for did in user.get("deal_ids", []):
            deal = data["deals"].get(did)
            if not deal:
                continue
            amount = deal.get("amount", 0.0)
            ctype = deal.get("currency_type", "INR")
            csym = deal.get("currency_sym", "₹")
            if ctype == "INR":
                inr_volume += amount
                if amount > inr_highest:
                    inr_highest = amount
            else:
                crypto_volumes[csym] = round(crypto_volumes.get(csym, 0.0) + amount, 2)
                if amount > crypto_highest.get(csym, 0.0):
                    crypto_highest[csym] = amount

        vol_parts = []
        if inr_volume > 0:
            vol_parts.append(f"₹{fmt(inr_volume)}")
        for sym, vol in crypto_volumes.items():
            vol_parts.append(f"${fmt(vol)}" if sym in ("USDT","USDC","USD") else f"{sym} {fmt(vol)}")
        volume_str = " , ".join(vol_parts) if vol_parts else "₹0"

        high_parts = []
        if inr_highest > 0:
            high_parts.append(f"₹{fmt(inr_highest)}")
        for sym, high in crypto_highest.items():
            high_parts.append(f"${fmt(high)}" if sym in ("USDT","USDC","USD") else f"{sym} {fmt(high)}")
        highest_str = " , ".join(high_parts) if high_parts else "₹0"

        text = (
            f"📊 Participant Stats for @{uname}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏅 Ranking: #{rank}\n"
            f"📋 Total Deals: {total}\n"
            f"✅ Completed: {completed}\n"
            f"🔄 Ongoing Deals: {ongoing}\n\n"
            f"💰 Total Volume: {volume_str}\n"
            f"⚡️ Highest Deal: {highest_str}\n\n"
            f"Always use @anamakafranchise for safer deals!"
        )
        stats_bot.reply_to(message, text)

    def run_stats_bot():
        print("📊 aNamaka Stats Bot running")
        stats_bot.infinity_polling(timeout=60, long_polling_timeout=60)

    threading.Thread(target=run_stats_bot, daemon=True).start()

bot.infinity_polling(timeout=60, long_polling_timeout=60)
