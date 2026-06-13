"""
ESCROW BOT v6
Changes:
  - No tutorial/instructions after /deal confirm message
  - /kickall fixed: only kicks users currently IN the group
  - /escrowstats: monthly escrow leaderboard
"""

import telebot
from telebot import types
import json, os, datetime, threading, time, re

# ══════════════════════════════════════════════════
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "")
VOUCH_CHANNEL    = os.environ.get("VOUCH_CHANNEL", "@YourVouchChannel")
INR_FEE_PCT      = 5
CRYPTO_FEE_PCT   = 3
DEAL_PREFIX      = "ET"
CONFIRM_TIMEOUT  = 15 * 60
DATA_FILE        = "escrow_data.json"
# ══════════════════════════════════════════════════

bot = telebot.TeleBot(BOT_TOKEN)

# ──────────────────────────────────────────────────
#  ADMIN CHECK
# ──────────────────────────────────────────────────

def is_admin(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except:
        return False

def is_in_group(chat_id, user_id):
    """Check if user is currently a member of the group"""
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ("member", "administrator", "creator", "restricted")
    except:
        return False

# ──────────────────────────────────────────────────
#  CURRENCY DETECTION
# ──────────────────────────────────────────────────

CRYPTO_KEYWORDS = {"usdt","usdc","btc","eth","bnb","trx","sol","ltc","xrp","crypto","usd","$"}

def detect_currency(amount_str, payment_mode_str):
    combined = (amount_str + " " + payment_mode_str).lower()
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
    fee   = round(amount * fee_pct / 100, 2)
    total = round(amount + fee, 2)
    return fee, total

def fmt(amount):
    try:
        f = float(amount)
        if f == int(f):
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except:
        return str(amount)

# ──────────────────────────────────────────────────
#  DATA LAYER
# ──────────────────────────────────────────────────

def load():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"deals": {}, "users": {}, "counter": 1}

def save(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def next_id(data):
    n = data["counter"]
    data["counter"] += 1
    save(data)
    return f"{DEAL_PREFIX}{str(n).zfill(6)}"

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
        "username":        username.lstrip("@"),
        "user_id":         user_id or 0,
        "total_deals":     0,
        "completed_deals": 0,
        "total_volume":    0.0,
        "highest_deal":    0.0,
        "ongoing_deals":   0,
        "as_buyer":        0,
        "as_seller":       0,
        "as_escrow":       0,
        "deal_ids":        [],
        "joined":          datetime.datetime.now().isoformat()
    }
    return uid_key

def _parse_deal_id(raw):
    raw = raw.lstrip("#").upper()
    if raw.isdigit():
        return f"{DEAL_PREFIX}{raw.zfill(6)}"
    if not raw.startswith(DEAL_PREFIX):
        return f"{DEAL_PREFIX}{raw}"
    return raw

# ──────────────────────────────────────────────────
#  FORM PARSER
# ──────────────────────────────────────────────────

def parse_form(text):
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("🔒") or line.startswith("•"):
            continue
        low = line.lower()
        if ":" not in line:
            continue
        val = line.split(":", 1)[1].strip()
        if not val:
            continue
        if re.search(r"\bbuyer\b", low):
            result["buyer"] = val.lstrip("@").split()[0].strip(".,")
        elif re.search(r"\bseller\b", low):
            result["seller"] = val.lstrip("@").split()[0].strip(".,")
        elif "condition" in low:
            result["condition"] = val
        elif "timeframe" in low or "completion" in low:
            result["timeframe"] = val
        elif "amount" in low:
            result["amount_raw"] = val
            clean = re.sub(r"[₹$€£\s/]", " ", val)
            match = re.search(r"[\d,]+\.?\d*", clean)
            if match:
                num = match.group(0).replace(",","").strip()
                try:
                    result["amount"] = float(num)
                except:
                    result["amount"] = 0.0
        elif "payment" in low or "mode" in low:
            result["payment_mode"] = val

    ctype, csym, fpct = detect_currency(
        result.get("amount_raw",""), result.get("payment_mode",""))
    result["currency_type"] = ctype
    result["currency_sym"]  = csym
    result["fee_pct"]       = fpct
    return result

# ──────────────────────────────────────────────────
#  PENDING STATE
# ──────────────────────────────────────────────────
pending = {}

# ──────────────────────────────────────────────────
#  /form
# ──────────────────────────────────────────────────

BLANK_FORM = """📝 Please Fill Out the Form Below:

1. Username of Buyer: 
2. Username of Seller: 
3. Escrow Condition: 
4. Timeframe for Completion: 
5. Deal Amount: ₹ / $ 
6. Mode of Payment: 

🔒 Notes:
• Edited forms will not be accepted.
• Payment from digital banks is not acceptable.
• For UPI payments, mention the full bank name."""

@bot.message_handler(commands=["form"])
def cmd_form(message):
    bot.send_message(message.chat.id, BLANK_FORM)

# ──────────────────────────────────────────────────
#  /deal — ADMINS ONLY
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["deal"])
def cmd_deal(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /deal.")
        return

    escrow_uname = message.from_user.username or message.from_user.first_name
    escrow_id    = message.from_user.id

    if not message.reply_to_message:
        bot.reply_to(message, "ℹ️ Reply on the filled form message and type /deal.")
        return

    form_text = message.reply_to_message.text or ""
    if "buyer" not in form_text.lower() or "seller" not in form_text.lower():
        bot.reply_to(message, "❌ This does not look like a valid form.")
        return

    form    = parse_form(form_text)
    missing = [f for f in ["buyer","seller","amount","condition","timeframe","payment_mode"]
               if not form.get(f)]
    if missing:
        bot.reply_to(message,
            "❌ These fields are missing:\n" +
            "\n".join(f"  • {m}" for m in missing))
        return

    data    = load()
    deal_id = next_id(data)
    amount  = form["amount"]
    csym    = form["currency_sym"]
    ctype   = form["currency_type"]
    fee_pct = form["fee_pct"]
    fee, total = calc_fee(amount, fee_pct)

    pending[deal_id] = {
        "deal_id":          deal_id,
        "buyer":            form["buyer"],
        "seller":           form["seller"],
        "escrow":           escrow_uname,
        "escrow_id":        escrow_id,
        "condition":        form["condition"],
        "timeframe":        form["timeframe"],
        "amount":           amount,
        "currency_sym":     csym,
        "currency_type":    ctype,
        "fee_pct":          fee_pct,
        "fee":              fee,
        "total":            total,
        "payment_mode":     form["payment_mode"],
        "confirmed_buyer":  False,
        "confirmed_seller": False,
        "chat_id":          message.chat.id,
        "status":           "AWAITING_CONFIRM",
        "created_at":       datetime.datetime.now().isoformat(),
    }

    # ── Deal card — NO instructions at bottom ──
    card = (
        f"🆔 DEAL ID: #{deal_id}\n\n"
        f"👤 Buyer: @{form['buyer']}\n"
        f"👤 Seller: @{form['seller']}\n"
        f"🔒 Escrow Condition: {form['condition']}\n"
        f"⏱ Timeframe: {form['timeframe']}\n"
        f"💰 Deal Amount: {csym}{fmt(amount)}\n"
        f"💳 Mode of Payment: {form['payment_mode']}\n"
        f"💸 Escrow Fee: {fee_pct}% ({csym}{fmt(fee)})\n"
        f"💵 Total Payable: {csym}{fmt(total)}\n\n"
        f"🔐 Escrower: @{escrow_uname}\n\n"
        f"📋 Please review and confirm the deal.\n"
        f"Auto-cancels in 15 minutes if not confirmed."
    )

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Seller Confirm ✅", callback_data=f"cs_{deal_id}"),
        types.InlineKeyboardButton("Buyer Confirm ✅",  callback_data=f"cb_{deal_id}")
    )
    markup.add(types.InlineKeyboardButton("❌ Cancel Deal", callback_data=f"cx_{deal_id}"))

    sent = bot.send_message(message.chat.id, card, reply_markup=markup)
    pending[deal_id]["msg_id"] = sent.message_id

    def _auto_cancel():
        time.sleep(CONFIRM_TIMEOUT)
        if deal_id in pending and pending[deal_id]["status"] == "AWAITING_CONFIRM":
            del pending[deal_id]
            try:
                bot.edit_message_reply_markup(message.chat.id, sent.message_id, reply_markup=None)
                bot.send_message(message.chat.id,
                    f"⏰ Deal #{deal_id} was not confirmed within 15 minutes and has been automatically cancelled.")
            except: pass
    threading.Thread(target=_auto_cancel, daemon=True).start()

# ──────────────────────────────────────────────────
#  CONFIRM / CANCEL CALLBACKS
# ──────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data[:3] in ("cs_","cb_","cx_"))
def handle_confirm(call):
    prefix    = call.data[:3]
    deal_id   = call.data[3:]
    caller_u  = (call.from_user.username or call.from_user.first_name or "").lower()
    caller_id = call.from_user.id

    if deal_id not in pending:
        bot.answer_callback_query(call.id, "❌ Deal not found or already expired.")
        return

    state = pending[deal_id]

    if prefix == "cx_":
        if caller_id != state["escrow_id"] and not is_admin(call.message.chat.id, caller_id):
            bot.answer_callback_query(call.id, "❌ Only the escrow can cancel.", show_alert=True)
            return
        del pending[deal_id]
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        bot.send_message(call.message.chat.id, f"❌ Deal #{deal_id} has been cancelled.")
        bot.answer_callback_query(call.id, "Cancelled.")
        return

    if prefix == "cs_":
        if caller_u != state["seller"].lower():
            bot.answer_callback_query(call.id,
                f"❌ Only seller @{state['seller']} can confirm.", show_alert=True)
            return
        if state["confirmed_seller"]:
            bot.answer_callback_query(call.id, "Already confirmed.")
            return
        state["confirmed_seller"] = True
        bot.answer_callback_query(call.id, "✅ Seller confirmed!")
        bot.send_message(call.message.chat.id,
            f"✅ Seller @{state['seller']} has confirmed the deal for #{deal_id}.")

    elif prefix == "cb_":
        if caller_u != state["buyer"].lower():
            bot.answer_callback_query(call.id,
                f"❌ Only buyer @{state['buyer']} can confirm.", show_alert=True)
            return
        if state["confirmed_buyer"]:
            bot.answer_callback_query(call.id, "Already confirmed.")
            return
        state["confirmed_buyer"] = True
        bot.answer_callback_query(call.id, "✅ Buyer confirmed!")
        bot.send_message(call.message.chat.id,
            f"✅ Buyer @{state['buyer']} has confirmed the deal for #{deal_id}.")

    _update_buttons(call.message.chat.id, call.message.message_id, state, deal_id)

    if state["confirmed_buyer"] and state["confirmed_seller"]:
        _activate_deal(call.message.chat.id, call.message.message_id, deal_id, state)


def _update_buttons(chat_id, msg_id, state, deal_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(
            "Seller ✅ Confirmed" if state["confirmed_seller"] else "Seller Confirm ✅",
            callback_data=f"cs_{deal_id}"),
        types.InlineKeyboardButton(
            "Buyer ✅ Confirmed"  if state["confirmed_buyer"]  else "Buyer Confirm ✅",
            callback_data=f"cb_{deal_id}")
    )
    if not (state["confirmed_buyer"] and state["confirmed_seller"]):
        markup.add(types.InlineKeyboardButton("❌ Cancel Deal", callback_data=f"cx_{deal_id}"))
    try:
        bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=markup)
    except: pass


def _activate_deal(chat_id, msg_id, deal_id, state):
    data = load()
    try:
        bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
    except: pass

    record = {k: v for k, v in state.items()
              if k not in ("confirmed_buyer","confirmed_seller","msg_id")}
    record["status"]       = "AWAITING_PAYMENT"
    record["activated_at"] = datetime.datetime.now().isoformat()
    data["deals"][deal_id] = record

    for uname, role in [(state["buyer"],"buyer"), (state["seller"],"seller")]:
        uk = ensure_user(data, uname)
        u  = data["users"][uk]
        u["total_deals"]   += 1
        u[f"as_{role}"]    += 1
        u["ongoing_deals"] += 1
        if deal_id not in u["deal_ids"]:
            u["deal_ids"].append(deal_id)

    ek = ensure_user(data, state["escrow"], state["escrow_id"])
    e  = data["users"][ek]
    e["total_deals"]   += 1
    e["as_escrow"]     += 1
    e["ongoing_deals"] += 1
    if deal_id not in e["deal_ids"]:
        e["deal_ids"].append(deal_id)

    save(data)
    if deal_id in pending:
        del pending[deal_id]

    csym  = state["currency_sym"]
    amt   = state["amount"]
    fee   = state["fee"]
    total = state["total"]
    fpct  = state["fee_pct"]

    # ── Both confirmed message — NO tutorial/instructions ──
    msg = (
        f"🆔 DEAL ID: #{deal_id}\n"
        f"👤 Buyer: @{state['buyer']}\n"
        f"👤 Seller: @{state['seller']}\n\n"
        f"✅ Both buyer and seller have confirmed the deal.\n\n"
        f"🔐 Escrower: @{state['escrow']}\n\n"
        f"Please proceed with the deal. 🚀\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Deal Amount:  {csym}{fmt(amt)}\n"
        f"💸 Escrow Fee:   {csym}{fmt(fee)} ({fpct}%)\n"
        f"💵 Total to Pay: {csym}{fmt(total)}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    bot.send_message(chat_id, msg)

# ──────────────────────────────────────────────────
#  /received — ADMINS ONLY
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["received"])
def cmd_received(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /received.")
        return

    parts  = message.text.split()
    caller = (message.from_user.username or message.from_user.first_name or "").lower()

    if len(parts) < 2:
        bot.reply_to(message, "Usage: /received ET000001")
        return

    deal_id = _parse_deal_id(parts[1])
    data    = load()

    if deal_id not in data["deals"]:
        bot.reply_to(message, f"❌ Deal #{deal_id} not found.")
        return

    deal = data["deals"][deal_id]
    if caller != deal["escrow"].lower():
        bot.reply_to(message, f"❌ Only escrow @{deal['escrow']} can mark this.")
        return
    if deal.get("received"):
        bot.reply_to(message, f"⚠️ Deal #{deal_id} already marked as received.")
        return

    amount  = deal["amount"]
    fee     = deal["fee"]
    total   = deal["total"]
    csym    = deal.get("currency_sym","₹")
    fee_pct = deal.get("fee_pct", INR_FEE_PCT)

    deal["received"]    = True
    deal["status"]      = "IN_PROGRESS"
    deal["received_at"] = datetime.datetime.now().isoformat()
    save(data)

    bot.send_message(message.chat.id,
        f"💰 Received Amount: {csym}{fmt(total)}\n"
        f"💸 Release/Refund Amount: {csym}{fmt(amount)}\n"
        f"🧳 Escrow Fee: {csym}{fmt(fee)} ({fee_pct}%)\n"
        f"🆔 Trade ID: #{deal_id}\n\n"
        f"➡️ Continue the Deal\n"
        f"👤 Buyer: @{deal['buyer']}\n"
        f"👤 Seller: @{deal['seller']}\n"
        f"🔐 Escrower: @{deal['escrow']}"
    )

# ──────────────────────────────────────────────────
#  /complete — ADMINS ONLY
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["complete"])
def cmd_complete(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /complete.")
        return

    parts  = message.text.split()
    caller = (message.from_user.username or message.from_user.first_name or "").lower()

    if len(parts) < 2:
        bot.reply_to(message, "Usage: /complete ET000001")
        return

    deal_id = _parse_deal_id(parts[1])
    data    = load()

    if deal_id not in data["deals"]:
        bot.reply_to(message, f"❌ Deal #{deal_id} not found.")
        return

    deal = data["deals"][deal_id]
    if caller != deal["escrow"].lower():
        bot.reply_to(message, f"❌ Only escrow @{deal['escrow']} can complete this.")
        return
    if not deal.get("received"):
        bot.reply_to(message, f"⚠️ Please run /received {deal_id} first.")
        return
    if deal.get("completed"):
        bot.reply_to(message, f"⚠️ Deal #{deal_id} is already completed.")
        return

    amount  = deal["amount"]
    csym    = deal.get("currency_sym","₹")

    deal["completed"]    = True
    deal["status"]       = "COMPLETED"
    deal["completed_at"] = datetime.datetime.now().isoformat()

    for uid_key, u in data["users"].items():
        uname = u.get("username","").lower()
        if uname in [deal["buyer"].lower(), deal["seller"].lower(), deal["escrow"].lower()]:
            u["completed_deals"] = u.get("completed_deals",0) + 1
            u["ongoing_deals"]   = max(0, u.get("ongoing_deals",1) - 1)
            u["total_volume"]    = round(u.get("total_volume",0) + amount, 2)
            u["highest_deal"]    = max(u.get("highest_deal",0), amount)

    save(data)
    date_str = datetime.datetime.now().strftime("%d %b %Y")

    bot.send_message(message.chat.id,
        f"🎉 Deal Completed ✅\n"
        f"🔷 Trade ID: #{deal_id}\n"
        f"💰 Released Amount: {csym}{fmt(amount)}\n"
        f"👤 Buyer: @{deal['buyer']}\n"
        f"👤 Seller: @{deal['seller']}\n"
        f"🔐 Escrowed By: @{deal['escrow']}"
    )

    vouch = (
        f"🎉 Deal Completed ✅\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔷 Trade ID: #{deal_id}\n"
        f"💰 Released Amount: {csym}{fmt(amount)}\n"
        f"👤 Buyer: @{deal['buyer']}\n"
        f"👤 Seller: @{deal['seller']}\n"
        f"🔐 Escrowed By: @{deal['escrow']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {date_str}"
    )
    try:
        bot.send_message(VOUCH_CHANNEL, vouch)
    except Exception as e:
        bot.send_message(message.chat.id,
            f"⚠️ Could not post to vouch channel: {e}")

# ──────────────────────────────────────────────────
#  /cancel — ADMINS ONLY
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /cancel.")
        return

    parts  = message.text.split()
    caller = (message.from_user.username or message.from_user.first_name or "").lower()

    if len(parts) < 2:
        bot.reply_to(message, "Usage: /cancel ET000001")
        return

    deal_id = _parse_deal_id(parts[1])

    if deal_id in pending:
        del pending[deal_id]
        bot.send_message(message.chat.id, f"❌ Deal #{deal_id} has been cancelled.")
        return

    data = load()
    if deal_id not in data["deals"]:
        bot.reply_to(message, f"❌ Deal #{deal_id} not found.")
        return

    deal = data["deals"][deal_id]
    if caller != deal["escrow"].lower():
        bot.reply_to(message, f"❌ Only escrow @{deal['escrow']} can cancel.")
        return
    if deal.get("completed"):
        bot.reply_to(message, "❌ A completed deal cannot be cancelled.")
        return

    deal["status"]       = "CANCELLED"
    deal["cancelled_at"] = datetime.datetime.now().isoformat()
    for uid_key, u in data["users"].items():
        if u.get("username","").lower() in [deal["buyer"].lower(), deal["seller"].lower()]:
            u["ongoing_deals"] = max(0, u.get("ongoing_deals",1) - 1)
    save(data)

    bot.send_message(message.chat.id,
        f"❌ Deal #{deal_id} has been cancelled.\n"
        f"👤 Buyer: @{deal['buyer']}\n"
        f"👤 Seller: @{deal['seller']}\n"
        f"🔐 Escrow: @{deal['escrow']}"
    )

# ──────────────────────────────────────────────────
#  /kickall — FIXED: only kicks users IN group right now
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["kickall"])
def cmd_kickall(message):
    if message.chat.type not in ["group","supergroup"]:
        bot.reply_to(message, "❌ This command only works in groups.")
        return
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /kickall.")
        return

    data = load()

    # Users with active deals — protected
    protected = set()
    for did, deal in data["deals"].items():
        if not deal.get("completed") and deal.get("status") != "CANCELLED":
            protected.add(deal["buyer"].lower())
            protected.add(deal["seller"].lower())
            protected.add(deal["escrow"].lower())

    try:
        admins    = bot.get_chat_administrators(message.chat.id)
        admin_ids = {a.user.id for a in admins}
    except:
        admin_ids = {message.from_user.id}

    kicked, protected_list, not_in_group = [], [], []

    for uid_key, u in data["users"].items():
        uid_int = u.get("user_id", 0)
        uname   = u.get("username","")

        if not isinstance(uid_int, int) or uid_int == 0:
            continue
        if uid_int in admin_ids:
            continue

        # ── KEY FIX: check if user is actually IN the group right now ──
        if not is_in_group(message.chat.id, uid_int):
            not_in_group.append(uname)
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
        total = bot.get_chat(message.chat.id).members_count or 0
    except:
        total = 0

    result = (
        f"✅ Kick Operation Completed!\n\n"
        f"👥 Total Group Members: {total}\n"
        f"🦵 Users Kicked: {len(kicked)}\n"
        f"🔒 Users with Active Deals (Protected): {len(protected_list)}\n"
    )
    if kicked:
        result += "\n🦵 Kicked Users:\n" + "\n".join(f"• {k}" for k in kicked)
    if protected_list:
        result += "\n\n🔒 Protected Users (Have Active Deals):\n" + \
                  "\n".join(f"• {p}" for p in protected_list)
    result += "\n\nℹ️ Kicked users can rejoin using the group invite link."

    bot.send_message(message.chat.id, result)

# ──────────────────────────────────────────────────
#  /escrowstats — Monthly Escrow Leaderboard
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["escrowstats"])
def cmd_escrowstats(message):
    data = load()
    now  = datetime.datetime.now()

    # Current month range
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_name  = now.strftime("%B %Y")

    # Build escrow leaderboard from completed deals this month
    escrow_stats = {}  # username → {deals, volume, highest}

    for did, deal in data["deals"].items():
        if not deal.get("completed"):
            continue

        # Check if completed this month
        completed_at = deal.get("completed_at","")
        try:
            dt = datetime.datetime.fromisoformat(completed_at)
            if dt < month_start:
                continue
        except:
            continue

        escrow = deal.get("escrow","").lower()
        if not escrow:
            continue

        amount = deal.get("amount", 0)
        csym   = deal.get("currency_sym", "₹")

        if escrow not in escrow_stats:
            escrow_stats[escrow] = {
                "username": deal.get("escrow",""),
                "deals":    0,
                "volume":   0.0,
                "highest":  0.0,
                "currency": csym
            }

        escrow_stats[escrow]["deals"]   += 1
        escrow_stats[escrow]["volume"]  = round(escrow_stats[escrow]["volume"] + amount, 2)
        escrow_stats[escrow]["highest"] = max(escrow_stats[escrow]["highest"], amount)

    if not escrow_stats:
        bot.reply_to(message,
            f"📊 Escrow Leaderboard — {month_name}\n\n"
            f"No completed deals this month yet.")
        return

    # Sort by deals done (primary), volume (secondary)
    ranked = sorted(escrow_stats.values(),
                    key=lambda x: (x["deals"], x["volume"]), reverse=True)

    medals = ["🥇","🥈","🥉"]
    text   = f"🏆 Escrow Leaderboard — {month_name}\n"
    text  += "━━━━━━━━━━━━━━━━━━━━\n\n"

    for i, e in enumerate(ranked, 1):
        medal = medals[i-1] if i <= 3 else f"#{i}"
        csym  = e["currency"]
        text += (
            f"{medal} @{e['username']}\n"
            f"   📦 Deals: {e['deals']}\n"
            f"   💰 Volume: {csym}{fmt(e['volume'])}\n"
            f"   ⚡ Highest: {csym}{fmt(e['highest'])}\n\n"
        )

    text += "━━━━━━━━━━━━━━━━━━━━\n"
    text += f"📅 Stats for {month_name}"

    bot.reply_to(message, text)

# ──────────────────────────────────────────────────
#  /mydeal
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["mydeal"])
def cmd_mydeal(message):
    caller = (message.from_user.username or message.from_user.first_name or "").lower()
    data   = load()
    active = [
        (did, d) for did, d in data["deals"].items()
        if not d.get("completed") and d.get("status") != "CANCELLED"
        and caller in [d["buyer"].lower(), d["seller"].lower(), d["escrow"].lower()]
    ]
    if not active:
        bot.reply_to(message, "✅ You have no active deals.")
        return
    text = "📋 Your Active Deals\n" + "─"*22 + "\n"
    for did, d in active:
        csym = d.get("currency_sym","₹")
        text += (f"\n🆔 #{did}\n"
                 f"💰 {csym}{fmt(d['amount'])} | {d['status']}\n"
                 f"👤 B:@{d['buyer']} | S:@{d['seller']}\n")
    bot.reply_to(message, text)

# ──────────────────────────────────────────────────
#  /help
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["start","help"])
def cmd_help(message):
    bot.send_message(message.chat.id,
        "🤖 Escrow Bot — Commands\n"
        "─────────────────────────\n"
        "/form              → Get blank form template\n"
        "/deal              → Reply on form to create deal (Admin)\n"
        "/received [ID]     → Mark payment received (Admin)\n"
        "/complete [ID]     → Complete deal + auto vouch (Admin)\n"
        "/cancel [ID]       → Cancel a deal (Admin)\n"
        "/mydeal            → View your active deals\n"
        "/kickall           → Kick inactive users (Admin)\n"
        "/escrowstats       → Monthly escrow leaderboard\n"
        "─────────────────────────\n"
        f"💸 INR Fee: {INR_FEE_PCT}%  |  Crypto Fee: {CRYPTO_FEE_PCT}%"
    )

# ──────────────────────────────────────────────────
#  PASSIVE USER TRACKER
# ──────────────────────────────────────────────────

@bot.message_handler(func=lambda m: True, content_types=["text"])
def track(message):
    if not message.from_user or not message.from_user.username:
        return
    data    = load()
    uid_key = str(message.from_user.id)
    uname   = message.from_user.username
    if uid_key not in data["users"]:
        ensure_user(data, uname, message.from_user.id)
        save(data)
    elif data["users"].get(uid_key,{}).get("username") != uname:
        if uid_key in data["users"]:
            data["users"][uid_key]["username"] = uname
            save(data)

# ──────────────────────────────────────────────────
#  RUN
# ──────────────────────────────────────────────────

print(f"🤖 Escrow Bot v6 | INR: {INR_FEE_PCT}% | Crypto: {CRYPTO_FEE_PCT}%")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
