"""
╔══════════════════════════════════════════════════╗
║        ESCROW BOT v4 — Final Production          ║
║                                                  ║
║  FIXES in this version:                          ║
║  ✅ Auto fee: INR=5%, Crypto=3% from form        ║
║  ✅ /received → auto-calculate, no manual input  ║
║  ✅ Escrow = whoever types /deal (auto)           ║
║  ✅ Stats from vouch channel (real data)          ║
╚══════════════════════════════════════════════════╝

SETUP:
  pip install pyTelegramBotAPI
  Fill CONFIG below, then: python bot.py
"""

import telebot
from telebot import types
import json, os, datetime, threading, time, re

# ══════════════════════════════════════════════════
#   ⚙️  CONFIG
# ══════════════════════════════════════════════════
BOT_TOKEN        = "YOUR_BOT_TOKEN_HERE"    # @BotFather se
VOUCH_CHANNEL    = "@YourVouchChannel"      # Vouch channel @username ya -100xxx
INR_FEE_PCT      = 5                        # INR deals: 5%
CRYPTO_FEE_PCT   = 3                        # Crypto deals: 3%
DEAL_PREFIX      = "ET"                     # #ET000001
CONFIRM_TIMEOUT  = 15 * 60                  # 15 min auto-cancel
DATA_FILE        = "escrow_data.json"
# ══════════════════════════════════════════════════

bot = telebot.TeleBot(BOT_TOKEN)

# ──────────────────────────────────────────────────
#  CURRENCY DETECTION
# ──────────────────────────────────────────────────

CRYPTO_KEYWORDS = {"usdt","usdc","btc","eth","bnb","trx","sol","ltc","xrp","crypto","usd","$"}
INR_KEYWORDS    = {"inr","₹","upi","rupee","rupees","rs","imps","neft","paytm","gpay","phonepe"}

def detect_currency(amount_str, payment_mode_str):
    """
    Returns: ("INR", "₹", 5)  or  ("CRYPTO", symbol, 3)
    Checks amount field first, then payment mode field.
    """
    combined = (amount_str + " " + payment_mode_str).lower()

    for kw in CRYPTO_KEYWORDS:
        if kw in combined:
            # Pick display symbol
            sym = "$"
            for c in ["usdt","usdc","btc","eth","bnb","trx","sol","ltc","xrp"]:
                if c in combined:
                    sym = c.upper()
                    break
            return "CRYPTO", sym, CRYPTO_FEE_PCT

    # Default INR
    return "INR", "₹", INR_FEE_PCT

def calc_fee(amount, fee_pct):
    fee   = round(amount * fee_pct / 100, 2)
    total = round(amount + fee, 2)
    return fee, total

def fmt(amount):
    try:
        f = float(amount)
        return f"{f:,.2f}".rstrip("0").rstrip(".") if "." in f"{f}" else f"{int(f):,}"
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

def get_ranking(data, uid_key):
    ranked = sorted(data["users"].items(),
                    key=lambda x: x[1].get("total_volume", 0), reverse=True)
    for i, (k, _) in enumerate(ranked, 1):
        if k == uid_key:
            return i
    return len(data["users"])

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
    """
    Parses user-filled form. Returns dict:
      buyer, seller, condition, timeframe,
      amount (float), amount_raw (str),
      currency_type (INR/CRYPTO), currency_sym, fee_pct,
      payment_mode
    """
    result = {}
    lines  = text.strip().split("\n")

    for line in lines:
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
            # Parse numeric value (handles: 150, 1,500, 10k, 1.5k, $150, ₹700)
            clean = re.sub(r"[₹$€£\s/]", " ", val)
            match = re.search(r"([\d,]+\.?\d*)\s*[kK]?", clean)
            if match:
                num = match.group(0).replace(",", "").strip()
                if val.lower().rstrip().endswith("k") or "k" in num.lower():
                    num = str(float(re.sub(r"[kK]","",num)) * 1000)
                try:
                    result["amount"] = float(num)
                except:
                    result["amount"] = 0.0

        elif "payment" in low or "mode" in low:
            result["payment_mode"] = val

    # Currency detection using amount_raw + payment_mode together
    amount_raw   = result.get("amount_raw", "")
    payment_mode = result.get("payment_mode", "")
    ctype, csym, fpct = detect_currency(amount_raw, payment_mode)
    result["currency_type"] = ctype
    result["currency_sym"]  = csym
    result["fee_pct"]       = fpct

    return result

# ──────────────────────────────────────────────────
#  PENDING (in-memory confirmations)
# ──────────────────────────────────────────────────
pending = {}

# ──────────────────────────────────────────────────
#  /form — send blank template
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
#  /deal — admin replies on filled form
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["deal"])
def cmd_deal(message):
    # Escrow = person who typed /deal (automatic)
    escrow_uname = message.from_user.username or message.from_user.first_name
    escrow_id    = message.from_user.id

    if not message.reply_to_message:
        bot.reply_to(message,
            "ℹ️ Bhare hue form pe reply karo phir /deal likho.")
        return

    form_text = message.reply_to_message.text or ""
    if "buyer" not in form_text.lower() or "seller" not in form_text.lower():
        bot.reply_to(message, "❌ Yeh form nahi lagta. Sahi form pe reply karo.")
        return

    form    = parse_form(form_text)
    missing = [f for f in ["buyer","seller","amount","condition","timeframe","payment_mode"]
               if not form.get(f)]
    if missing:
        bot.reply_to(message,
            "❌ Form mein yeh fields nahi mile:\n" +
            "\n".join(f"  • {m}" for m in missing))
        return

    data    = load()
    deal_id = next_id(data)

    amount   = form["amount"]
    csym     = form["currency_sym"]
    ctype    = form["currency_type"]
    fee_pct  = form["fee_pct"]
    fee, total = calc_fee(amount, fee_pct)

    # Store pending state
    pending[deal_id] = {
        "deal_id":       deal_id,
        "buyer":         form["buyer"],
        "seller":        form["seller"],
        "escrow":        escrow_uname,        # auto from /deal sender
        "escrow_id":     escrow_id,
        "condition":     form["condition"],
        "timeframe":     form["timeframe"],
        "amount":        amount,
        "currency_sym":  csym,
        "currency_type": ctype,
        "fee_pct":       fee_pct,
        "fee":           fee,
        "total":         total,
        "payment_mode":  form["payment_mode"],
        "confirmed_buyer":  False,
        "confirmed_seller": False,
        "chat_id":       message.chat.id,
        "status":        "AWAITING_CONFIRM",
        "created_at":    datetime.datetime.now().isoformat(),
    }

    # Deal card
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
        f"Deal will auto-cancel in 15 minutes if not confirmed."
    )

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Seller Confirm ✅", callback_data=f"cs_{deal_id}"),
        types.InlineKeyboardButton("Buyer Confirm ✅",  callback_data=f"cb_{deal_id}")
    )
    markup.add(types.InlineKeyboardButton("❌ Cancel Deal", callback_data=f"cx_{deal_id}"))

    sent = bot.send_message(message.chat.id, card, reply_markup=markup)
    pending[deal_id]["msg_id"] = sent.message_id

    # 15-min auto cancel
    def _auto_cancel():
        time.sleep(CONFIRM_TIMEOUT)
        if deal_id in pending and pending[deal_id]["status"] == "AWAITING_CONFIRM":
            del pending[deal_id]
            try:
                bot.edit_message_reply_markup(message.chat.id, sent.message_id, reply_markup=None)
                bot.send_message(message.chat.id,
                    f"⏰ Deal #{deal_id} — 15 min mein confirm nahi hua, auto-cancel ho gaya.")
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
        bot.answer_callback_query(call.id, "❌ Deal nahi mila ya expire ho gaya.")
        return

    state = pending[deal_id]

    # Cancel
    if prefix == "cx_":
        if caller_id != state["escrow_id"]:
            bot.answer_callback_query(call.id, "❌ Sirf escrow cancel kar sakta hai.", show_alert=True)
            return
        del pending[deal_id]
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        bot.send_message(call.message.chat.id, f"❌ Deal #{deal_id} cancel ho gayi.")
        bot.answer_callback_query(call.id, "Cancelled.")
        return

    # Seller confirm
    if prefix == "cs_":
        if caller_u != state["seller"].lower():
            bot.answer_callback_query(call.id,
                f"❌ Sirf seller @{state['seller']} yeh confirm kar sakta hai!", show_alert=True)
            return
        if state["confirmed_seller"]:
            bot.answer_callback_query(call.id, "Pehle se confirm hai.")
            return
        state["confirmed_seller"] = True
        bot.answer_callback_query(call.id, "✅ Seller confirmed!")
        bot.send_message(call.message.chat.id,
            f"✅ Seller @{state['seller']} has confirmed the deal for #{deal_id}.")

    # Buyer confirm
    elif prefix == "cb_":
        if caller_u != state["buyer"].lower():
            bot.answer_callback_query(call.id,
                f"❌ Sirf buyer @{state['buyer']} yeh confirm kar sakta hai!", show_alert=True)
            return
        if state["confirmed_buyer"]:
            bot.answer_callback_query(call.id, "Pehle se confirm hai.")
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
            callback_data=f"cs_{deal_id}"
        ),
        types.InlineKeyboardButton(
            "Buyer ✅ Confirmed"  if state["confirmed_buyer"]  else "Buyer Confirm ✅",
            callback_data=f"cb_{deal_id}"
        )
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

    # Save deal
    record = {k: v for k, v in state.items()
              if k not in ("confirmed_buyer","confirmed_seller","msg_id")}
    record["status"]       = "AWAITING_PAYMENT"
    record["activated_at"] = datetime.datetime.now().isoformat()
    data["deals"][deal_id] = record

    # User stats
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
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Buyer: {csym}{fmt(total)} escrow ko bhejo\n"
        f"Payment aane ke baad escrow: /received {deal_id}"
    )
    bot.send_message(chat_id, msg)

# ──────────────────────────────────────────────────
#  /received — auto-calculates from deal data
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["received"])
def cmd_received(message):
    parts  = message.text.split()
    caller = (message.from_user.username or message.from_user.first_name or "").lower()

    if len(parts) < 2:
        bot.reply_to(message, "Usage: /received ET000001")
        return

    deal_id = _parse_deal_id(parts[1])
    data    = load()

    if deal_id not in data["deals"]:
        bot.reply_to(message, f"❌ Deal #{deal_id} nahi mila.")
        return

    deal = data["deals"][deal_id]

    if caller != deal["escrow"].lower():
        bot.reply_to(message, f"❌ Sirf escrow @{deal['escrow']} yeh kar sakta hai.")
        return
    if deal.get("received"):
        bot.reply_to(message, f"⚠️ #{deal_id} pehle se received marked hai.")
        return

    # ── Auto-calculate from stored deal data ──
    amount  = deal["amount"]
    fee_pct = deal["fee_pct"]
    fee     = deal["fee"]
    total   = deal["total"]
    csym    = deal["currency_sym"]

    deal["received"]    = True
    deal["status"]      = "IN_PROGRESS"
    deal["received_at"] = datetime.datetime.now().isoformat()
    save(data)

    # Exact same format as screenshot
    text = (
        f"💰 Received Amount: {csym}{fmt(total)}\n"
        f"💸 Release/Refund Amount: {csym}{fmt(amount)}\n"
        f"🧳 Escrow Fee: {csym}{fmt(fee)} ({fee_pct}%)\n"
        f"🆔 Trade ID: #{deal_id}\n\n"
        f"➡️ Continue the Deal\n"
        f"👤 Buyer: @{deal['buyer']}\n"
        f"👤 Seller: @{deal['seller']}\n"
        f"🔐 Escrower: @{deal['escrow']}\n\n"
        f"Deal complete hone par:\n/complete {deal_id}"
    )
    bot.send_message(message.chat.id, text)

# ──────────────────────────────────────────────────
#  /complete → vouch auto-post
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["complete"])
def cmd_complete(message):
    parts  = message.text.split()
    caller = (message.from_user.username or message.from_user.first_name or "").lower()

    if len(parts) < 2:
        bot.reply_to(message, "Usage: /complete ET000001")
        return

    deal_id = _parse_deal_id(parts[1])
    data    = load()

    if deal_id not in data["deals"]:
        bot.reply_to(message, f"❌ Deal #{deal_id} nahi mila.")
        return

    deal = data["deals"][deal_id]

    if caller != deal["escrow"].lower():
        bot.reply_to(message, f"❌ Sirf escrow @{deal['escrow']} complete kar sakta hai.")
        return
    if not deal.get("received"):
        bot.reply_to(message, f"⚠️ Pehle /received {deal_id} karo.")
        return
    if deal.get("completed"):
        bot.reply_to(message, f"⚠️ #{deal_id} pehle se complete hai.")
        return

    amount  = deal["amount"]
    csym    = deal["currency_sym"]
    fee_pct = deal["fee_pct"]

    deal["completed"]    = True
    deal["status"]       = "COMPLETED"
    deal["completed_at"] = datetime.datetime.now().isoformat()

    # Update stats
    for uid_key, u in data["users"].items():
        uname = u.get("username","").lower()
        if uname in [deal["buyer"].lower(), deal["seller"].lower(), deal["escrow"].lower()]:
            u["completed_deals"] = u.get("completed_deals",0) + 1
            u["ongoing_deals"]   = max(0, u.get("ongoing_deals",1) - 1)
            u["total_volume"]    = round(u.get("total_volume",0) + amount, 2)
            u["highest_deal"]    = max(u.get("highest_deal",0), amount)

    save(data)

    date_str = datetime.datetime.now().strftime("%d %b %Y")

    # Group message
    bot.send_message(message.chat.id,
        f"🎉 Deal Completed ✅\n"
        f"🔷 Trade ID: #{deal_id}\n"
        f"💰 Released Amount: {csym}{fmt(amount)}\n"
        f"👤 Buyer: @{deal['buyer']}\n"
        f"👤 Seller: @{deal['seller']}\n"
        f"🔐 Escrowed By: @{deal['escrow']}"
    )

    # Vouch channel auto-post
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
            f"⚠️ Vouch channel mein nahi gaya: {e}\nBot ko channel admin banao.")

# ──────────────────────────────────────────────────
#  /stats — fetches from vouch channel data
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    parts = message.text.split()
    data  = load()

    if len(parts) > 1:
        target  = parts[1].lstrip("@")
        uid_key, u = get_user_by_uname(data, target)
        if not u:
            bot.reply_to(message,
                f"❌ @{target} ki koi deal history nahi mili.")
            return
    else:
        uid_key = str(message.from_user.id)
        if uid_key not in data["users"]:
            bot.reply_to(message, "❌ Aapki koi deal history nahi hai.")
            return
        u = data["users"][uid_key]

    # Build stats FROM completed deals in data (vouch-based)
    completed_deals = []
    for did, deal in data["deals"].items():
        if not deal.get("completed"):
            continue
        uname = u.get("username","").lower()
        if uname in [deal.get("buyer","").lower(),
                     deal.get("seller","").lower(),
                     deal.get("escrow","").lower()]:
            completed_deals.append(deal)

    total_completed = len(completed_deals)
    total_volume    = sum(d.get("amount",0) for d in completed_deals)
    highest_deal    = max((d.get("amount",0) for d in completed_deals), default=0)
    ongoing         = u.get("ongoing_deals", 0)
    total_all       = u.get("total_deals", 0)
    rank            = get_ranking(data, uid_key)

    # Currency symbol for display (use most common from their deals)
    csyms = [d.get("currency_sym","₹") for d in completed_deals]
    display_sym = max(set(csyms), key=csyms.count) if csyms else "₹"

    text = (
        f"📊 Participant Stats for @{u['username']}\n\n"
        f"👑 Ranking: #{rank}\n"
        f"📈 Total Volume: {display_sym} {fmt(total_volume)}\n"
        f"🔢 Total Deals: {total_all}\n"
        f"⏳ Ongoing Deals: {ongoing}\n"
        f"⚡ Highest Deal: {display_sym} {fmt(highest_deal)}\n\n"
        f"🧾 As Buyer: {u.get('as_buyer',0)}\n"
        f"🏪 As Seller: {u.get('as_seller',0)}\n"
        f"🔐 As Escrow: {u.get('as_escrow',0)}\n\n"
        f"📋 Always use our Escrow Bot for safer transactions!"
    )
    bot.reply_to(message, text)

# ──────────────────────────────────────────────────
#  /kickall
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["kickall"])
def cmd_kickall(message):
    if message.chat.type not in ["group","supergroup"]:
        bot.reply_to(message, "❌ Sirf group mein kaam karta hai.")
        return
    try:
        mem = bot.get_chat_member(message.chat.id, message.from_user.id)
        if mem.status not in ("administrator","creator"):
            bot.reply_to(message, "❌ Sirf admins /kickall kar sakte hain.")
            return
    except: pass

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "⚠️ Haan, Kick Karo", callback_data=f"kc_{message.from_user.id}")
    )
    markup.add(
        types.InlineKeyboardButton("❌ Cancel", callback_data=f"kx_{message.from_user.id}")
    )
    bot.send_message(message.chat.id,
        "⚠️ Confirm karo:\n\n"
        "• Saare non-admin users kick honge\n"
        "• Active deal wale PROTECTED rahenge\n"
        "• Rejoin kar sakte hain invite link se",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data[:3] in ("kc_","kx_"))
def handle_kickall(call):
    prefix  = call.data[:3]
    req_uid = int(call.data[3:])

    if call.from_user.id != req_uid:
        bot.answer_callback_query(call.id, "❌ Yeh tumhara button nahi.", show_alert=True)
        return

    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except: pass

    if prefix == "kx_":
        bot.answer_callback_query(call.id, "Cancelled.")
        return

    bot.answer_callback_query(call.id, "Kick shuru ho rahi hai...")
    data = load()

    # Protected: active deal users
    protected = set()
    for did, deal in data["deals"].items():
        if not deal.get("completed") and deal.get("status") != "CANCELLED":
            protected.add(deal["buyer"].lower())
            protected.add(deal["seller"].lower())
            protected.add(deal["escrow"].lower())

    try:
        admins    = bot.get_chat_administrators(call.message.chat.id)
        admin_ids = {a.user.id for a in admins}
    except:
        admin_ids = {call.from_user.id}

    kicked, protected_list, admin_list = [], [], []

    for uid_key, u in data["users"].items():
        uid_int = u.get("user_id", 0)
        uname   = u.get("username","")
        if not isinstance(uid_int, int) or uid_int == 0:
            continue
        if uid_int in admin_ids:
            admin_list.append(f"@{uname}")
            continue
        if uname.lower() in protected:
            protected_list.append(f"@{uname}")
            continue
        try:
            bot.ban_chat_member(call.message.chat.id, uid_int)
            bot.unban_chat_member(call.message.chat.id, uid_int)
            kicked.append(f"@{uname}")
        except:
            pass

    try:
        total = bot.get_chat(call.message.chat.id).members_count or 0
    except:
        total = 0

    result = (
        f"✅ Kick Operation Completed!\n\n"
        f"👥 Total Group Members: {total}\n"
        f"👑 Admins/Owners (Protected): {len(admin_list)}\n"
        f"🦵 Users Kicked: {len(kicked)}\n"
        f"🔒 Users with Active Deals (Protected): {len(protected_list)}\n"
    )
    if kicked:
        result += "\n🦵 Kicked Users:\n" + "\n".join(f"• {k}" for k in kicked)
    if protected_list:
        result += "\n\n🔒 Protected Users (Have Active Deals):\n" + \
                  "\n".join(f"• {p}" for p in protected_list)
    result += "\n\nℹ️ Kicked users can rejoin using the group invite link."

    bot.send_message(call.message.chat.id, result)

# ──────────────────────────────────────────────────
#  /cancel
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    parts  = message.text.split()
    caller = (message.from_user.username or message.from_user.first_name or "").lower()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /cancel ET000001")
        return

    deal_id = _parse_deal_id(parts[1])

    if deal_id in pending:
        if message.from_user.id != pending[deal_id]["escrow_id"]:
            bot.reply_to(message, "❌ Sirf escrow cancel kar sakta hai.")
            return
        del pending[deal_id]
        bot.send_message(message.chat.id, f"❌ Deal #{deal_id} cancel ho gayi.")
        return

    data = load()
    if deal_id not in data["deals"]:
        bot.reply_to(message, f"❌ Deal #{deal_id} nahi mila.")
        return
    deal = data["deals"][deal_id]
    if caller != deal["escrow"].lower():
        bot.reply_to(message, f"❌ Sirf escrow @{deal['escrow']} cancel kar sakta hai.")
        return
    if deal.get("completed"):
        bot.reply_to(message, "❌ Completed deal cancel nahi hoti.")
        return

    deal["status"]       = "CANCELLED"
    deal["cancelled_at"] = datetime.datetime.now().isoformat()
    for uid_key, u in data["users"].items():
        if u.get("username","").lower() in [deal["buyer"].lower(), deal["seller"].lower()]:
            u["ongoing_deals"] = max(0, u.get("ongoing_deals",1) - 1)
    save(data)

    bot.send_message(message.chat.id,
        f"❌ Deal #{deal_id} cancel ho gayi.\n"
        f"👤 Buyer: @{deal['buyer']}\n"
        f"👤 Seller: @{deal['seller']}\n"
        f"🔐 Escrow: @{deal['escrow']}"
    )

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
        bot.reply_to(message, "✅ Aapki koi active deal nahi hai.")
        return
    text = "📋 Aapki Active Deals\n" + "─"*22 + "\n"
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
        "/form           → Blank form template\n"
        "/deal           → Form pe reply karke deal banao\n"
        "/received [ID]  → Payment received (auto-calculate)\n"
        "/complete [ID]  → Deal complete + vouch\n"
        "/cancel [ID]    → Deal cancel\n"
        "/stats [@user]  → User stats\n"
        "/mydeal         → Active deals\n"
        "/kickall        → Inactive users kick\n"
        "─────────────────────────\n"
        f"💸 INR Fee: {INR_FEE_PCT}%  |  Crypto Fee: {CRYPTO_FEE_PCT}%\n"
        "Bot currency auto-detect karta hai form se!"
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
    elif data["users"].get(uid_key, {}).get("username") != uname:
        if uid_key in data["users"]:
            data["users"][uid_key]["username"] = uname
            save(data)

# ──────────────────────────────────────────────────
#  RUN
# ──────────────────────────────────────────────────

print(f"🤖 Escrow Bot v4 | INR Fee: {INR_FEE_PCT}% | Crypto Fee: {CRYPTO_FEE_PCT}%")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
