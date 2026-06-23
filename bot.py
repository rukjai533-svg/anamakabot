"""
aNamaka CRPT Escrow Bot
- /form or /dd  → blank form
- Admin replies form with /got → deal created #CRPTIN00001
- Buyer + Seller confirm buttons → confirm msgs auto-deleted after both confirm
- /close CRPTIN00001 → complete + vouch
- Fee: $1 flat
- Escrower shown as clickable name link
- Premium emojis throughout
"""

import telebot
from telebot import types
import json, os, datetime, threading, time, re

# ══════════════════════════════════════════════════
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
VOUCH_CHANNEL  = os.environ.get("VOUCH_CHANNEL", "@YourVouchChannel")
FLAT_FEE       = 1.0        # $1 flat fee
DEAL_PREFIX    = "CRPTIN"   # #CRPTIN00001
CONFIRM_TIMEOUT = 15 * 60
DATA_FILE      = "crpt_data.json"
# ══════════════════════════════════════════════════

# Premium Emoji IDs
E_LOCK       = "5197288647275071607"   # 🔒 Deal ID
E_PERSON     = "5879770735999717115"   # 👤 Buyer/Seller
E_HANDSHAKE  = "5472284034459532343"   # 🤝 Both confirmed
E_TICK       = "6120635817674149717"   # ✅ Green tick
E_FEES       = "5201691993775818138"   # 🛡 Fees
E_DOLLAR     = "6098329329496758311"   # 💵 Dollar/Amount
E_SHIELD     = "6120946172010959542"   # 🛡 Escrowed By

bot = telebot.TeleBot(BOT_TOKEN)

# ──────────────────────────────────────────────────
#  HELPER: Send message with premium emojis
# ──────────────────────────────────────────────────

def make_entity(etype, offset, length, custom_emoji_id=None, url=None):
    e = types.MessageEntity(type=etype, offset=offset, length=length)
    if custom_emoji_id:
        e.custom_emoji_id = custom_emoji_id
    if url:
        e.url = url
    return e

def send_premium(chat_id, lines, reply_to=None):
    """
    lines = list of dicts:
    {
      "text": "🔒 DEAL ID: #CRPTIN00001",
      "emojis": [
         {"char": "🔒", "id": "519..."},
      ],
      "links": [
         {"text": "Shadow", "url": "tg://user?id=123456789"}
      ]
    }
    """
    full_text = ""
    entities  = []

    for line in lines:
        line_text  = line.get("text", "")
        line_start = len(full_text.encode("utf-16-le")) // 2  # UTF-16 offset

        # Premium emojis in this line
        for em in line.get("emojis", []):
            char    = em["char"]
            eid     = em["id"]
            pos     = line_text.find(char)
            if pos == -1:
                continue
            # Calculate UTF-16 offset of char within line
            before  = line_text[:pos]
            off_add = len(before.encode("utf-16-le")) // 2
            ch_len  = len(char.encode("utf-16-le")) // 2
            entities.append(
                make_entity("custom_emoji", line_start + off_add, ch_len, custom_emoji_id=eid)
            )

        # Clickable text links in this line
        for lnk in line.get("links", []):
            ltxt = lnk["text"]
            lurl = lnk["url"]
            pos  = line_text.find(ltxt)
            if pos == -1:
                continue
            before  = line_text[:pos]
            off_add = len(before.encode("utf-16-le")) // 2
            lt_len  = len(ltxt.encode("utf-16-le")) // 2
            entities.append(
                make_entity("text_link", line_start + off_add, lt_len, url=lurl)
            )

        full_text += line_text + "\n"

    full_text = full_text.rstrip("\n")

    kwargs = {"entities": entities} if entities else {}
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to

    return bot.send_message(chat_id, full_text, **kwargs)

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
    return f"{DEAL_PREFIX}{str(n).zfill(5)}"

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
        "user_id":  user_id or 0,
        "total_deals": 0, "completed_deals": 0,
        "total_volume": 0.0, "highest_deal": 0.0,
        "ongoing_deals": 0, "as_buyer": 0,
        "as_seller": 0, "as_escrow": 0,
        "deal_ids": [],
        "joined": datetime.datetime.now().isoformat()
    }
    return uid_key

def _parse_deal_id(raw):
    raw = raw.lstrip("#").upper()
    if raw.isdigit():
        return f"{DEAL_PREFIX}{raw.zfill(5)}"
    if not raw.startswith(DEAL_PREFIX):
        return f"{DEAL_PREFIX}{raw}"
    return raw

def fmt(amount):
    try:
        f = float(amount)
        if f == int(f):
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except:
        return str(amount)

def is_admin(chat_id, user_id):
    try:
        m = bot.get_chat_member(chat_id, user_id)
        return m.status in ("administrator","creator")
    except:
        return False

# ──────────────────────────────────────────────────
#  FORM PARSER
# ──────────────────────────────────────────────────

def parse_form(text):
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.lower().startswith("terms"):
            # Terms line — grab everything after "Terms and Condition"
            if "terms" in line.lower() and "[" in line:
                result["terms"] = line.split("[",1)[1].rstrip("]").strip()
            continue
        if ":" not in line and "-" not in line:
            continue
        # Split on : or -
        sep = ":" if ":" in line else "-"
        key, _, val = line.partition(sep)
        key = key.strip().lower()
        val = val.strip()
        if not val:
            continue
        if "buyer" in key:
            result["buyer"] = val.lstrip("@").split()[0].strip(".,")
        elif "seller" in key:
            result["seller"] = val.lstrip("@").split()[0].strip(".,")
        elif "time" in key or "complete" in key:
            result["timeframe"] = val
        elif "amount" in key:
            result["amount_raw"] = val
            clean = re.sub(r"[₹$€£\s/]"," ",val)
            m = re.search(r"[\d,]+\.?\d*", clean)
            if m:
                try:
                    result["amount"] = float(m.group(0).replace(",",""))
                except:
                    result["amount"] = 0.0
        elif "network" in key or "mode" in key or "payment" in key:
            result["network"] = val
    return result

# ──────────────────────────────────────────────────
#  PENDING
# ──────────────────────────────────────────────────
pending = {}

# ──────────────────────────────────────────────────
#  /form  /dd  — Blank form
# ──────────────────────────────────────────────────

BLANK_FORM = (
    "Username of Buyer - \n"
    "Username of Seller - \n"
    "Time to complete - \n"
    "Amount - \n"
    "Network - \n"
    "\n"
    "\n"
    "Terms and Condition [ Mention terms , dont regret later]"
)

@bot.message_handler(commands=["form","dd"])
def cmd_form(message):
    bot.send_message(message.chat.id, BLANK_FORM)

# ──────────────────────────────────────────────────
#  /got — Admin replies on filled form
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["got"])
def cmd_got(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /got.")
        return

    escrow_uname = message.from_user.username or message.from_user.first_name
    escrow_id    = message.from_user.id

    if not message.reply_to_message:
        bot.reply_to(message, "ℹ️ Reply on the filled form and type /got.")
        return

    form_text = message.reply_to_message.text or ""
    if "buyer" not in form_text.lower() and "seller" not in form_text.lower():
        bot.reply_to(message, "❌ This does not look like a valid form.")
        return

    form    = parse_form(form_text)
    missing = [f for f in ["buyer","seller","amount","timeframe","network"]
               if not form.get(f)]
    if missing:
        bot.reply_to(message,
            "❌ These fields are missing:\n" +
            "\n".join(f"  • {m}" for m in missing))
        return

    data    = load()
    deal_id = next_id(data)
    amount  = form["amount"]
    fee     = FLAT_FEE
    total   = round(amount + fee, 2)
    terms   = form.get("terms", "N/A")

    pending[deal_id] = {
        "deal_id":          deal_id,
        "buyer":            form["buyer"],
        "seller":           form["seller"],
        "escrow":           escrow_uname,
        "escrow_id":        escrow_id,
        "timeframe":        form["timeframe"],
        "amount":           amount,
        "fee":              fee,
        "total":            total,
        "network":          form["network"],
        "terms":            terms,
        "confirmed_buyer":  False,
        "confirmed_seller": False,
        "confirm_msg_ids":  [],
        "chat_id":          message.chat.id,
        "status":           "AWAITING_CONFIRM",
        "created_at":       datetime.datetime.now().isoformat(),
    }

    # ── Deal card with premium emojis ──
    card_text = (
        f"🔒 DEAL ID: #{deal_id}\n\n"
        f"👤 Buyer: @{form['buyer']}\n"
        f"👤 Seller: @{form['seller']}\n"
        f"🔐 Escrow Condition: {terms}\n"
        f"⏱ Timeframe: {form['timeframe']}\n"
        f"💵 Deal Amount: ${fmt(amount)}\n"
        f"🗂 Mode of Payment: {form['network']}\n"
        f"🛡 Escrow Fee: $1\n"
        f"💰 Total Payable: ${fmt(total)}\n\n"
        f"🔑 Escrower: @{escrow_uname}\n\n"
        f"📋 Please review and confirm the deal.\n"
        f"Auto-cancels in 15 minutes if not confirmed."
    )

    lines = [
        {"text": f"🔒 DEAL ID: #{deal_id}", "emojis": [{"char":"🔒","id":E_LOCK}]},
        {"text": ""},
        {"text": f"👤 Buyer: @{form['buyer']}",  "emojis": [{"char":"👤","id":E_PERSON}]},
        {"text": f"👤 Seller: @{form['seller']}", "emojis": [{"char":"👤","id":E_PERSON}]},
        {"text": f"🔐 Escrow Condition: {terms}"},
        {"text": f"⏱ Timeframe: {form['timeframe']}"},
        {"text": f"💵 Deal Amount: ${fmt(amount)}", "emojis": [{"char":"💵","id":E_DOLLAR}]},
        {"text": f"🗂 Mode of Payment: {form['network']}"},
        {"text": f"🛡 Escrow Fee: $1",    "emojis": [{"char":"🛡","id":E_FEES}]},
        {"text": f"💰 Total Payable: ${fmt(total)}"},
        {"text": ""},
        {"text": f"🔑 Escrower: @{escrow_uname}"},
        {"text": ""},
        {"text": "📋 Please review and confirm the deal."},
        {"text": "Auto-cancels in 15 minutes if not confirmed."},
    ]

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Seller Confirm ✅", callback_data=f"cs_{deal_id}"),
        types.InlineKeyboardButton("Buyer Confirm ✅",  callback_data=f"cb_{deal_id}")
    )
    markup.add(types.InlineKeyboardButton("❌ Cancel Deal", callback_data=f"cx_{deal_id}"))

    try:
        sent = send_premium(message.chat.id, lines)
        bot.edit_message_reply_markup(message.chat.id, sent.message_id, reply_markup=markup)
    except:
        sent = bot.send_message(message.chat.id, card_text, reply_markup=markup)

    pending[deal_id]["msg_id"] = sent.message_id

    def _auto_cancel():
        time.sleep(CONFIRM_TIMEOUT)
        if deal_id in pending and pending[deal_id]["status"] == "AWAITING_CONFIRM":
            del pending[deal_id]
            try:
                bot.edit_message_reply_markup(message.chat.id, sent.message_id, reply_markup=None)
                bot.send_message(message.chat.id,
                    f"⏰ Deal #{deal_id} auto-cancelled (15 min timeout).")
            except: pass
    threading.Thread(target=_auto_cancel, daemon=True).start()

# ──────────────────────────────────────────────────
#  CONFIRM CALLBACKS
# ──────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data[:3] in ("cs_","cb_","cx_"))
def handle_confirm(call):
    prefix    = call.data[:3]
    deal_id   = call.data[3:]
    caller_u  = (call.from_user.username or call.from_user.first_name or "").lower()
    caller_id = call.from_user.id

    if deal_id not in pending:
        bot.answer_callback_query(call.id, "❌ Deal not found or expired.")
        return

    state = pending[deal_id]

    # Cancel
    if prefix == "cx_":
        if caller_id != state["escrow_id"] and not is_admin(call.message.chat.id, caller_id):
            bot.answer_callback_query(call.id, "❌ Only escrow can cancel.", show_alert=True)
            return
        del pending[deal_id]
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        bot.send_message(call.message.chat.id, f"❌ Deal #{deal_id} has been cancelled.")
        bot.answer_callback_query(call.id, "Cancelled.")
        return

    # Seller confirm
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
        # Send confirm msg and save its ID for later deletion
        m = bot.send_message(call.message.chat.id,
            f"✅ Seller @{state['seller']} has confirmed the deal for #{deal_id}.")
        state["confirm_msg_ids"].append(m.message_id)

    # Buyer confirm
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
        m = bot.send_message(call.message.chat.id,
            f"✅ Buyer @{state['buyer']} has confirmed the deal for #{deal_id}.")
        state["confirm_msg_ids"].append(m.message_id)

    # Update button labels
    _update_buttons(call.message.chat.id, call.message.message_id, state, deal_id)

    # Both confirmed?
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

    # Delete the two individual confirm messages
    for mid in state.get("confirm_msg_ids", []):
        try:
            bot.delete_message(chat_id, mid)
        except: pass

    # Remove confirm buttons from deal card
    try:
        bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
    except: pass

    # Save deal
    record = {k: v for k, v in state.items()
              if k not in ("confirmed_buyer","confirmed_seller","msg_id","confirm_msg_ids")}
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

    amount = state["amount"]
    fee    = state["fee"]
    total  = state["total"]
    escrow_id = state["escrow_id"]
    escrow_name = state["escrow"]

    # ── Both confirmed message (Image 3 style) ──
    # Escrower as clickable name link
    lines = [
        {"text": f"🔒 DEAL ID: #{deal_id}", "emojis": [{"char":"🔒","id":E_LOCK}]},
        {"text": ""},
        {"text": f"👤 Buyer - @{state['buyer']}",  "emojis": [{"char":"👤","id":E_PERSON}]},
        {"text": f"👤 Seller - @{state['seller']}", "emojis": [{"char":"👤","id":E_PERSON}]},
        {"text": ""},
        {"text": f"🤝 Both Buyer And Seller Have Confirmed The Deal. ✅",
         "emojis": [{"char":"🤝","id":E_HANDSHAKE}, {"char":"✅","id":E_TICK}]},
        {"text": ""},
        {"text": f"Please Pay To Your Escrower {escrow_name} To Continue Your Deal",
         "links": [{"text": escrow_name, "url": f"tg://user?id={escrow_id}"}]},
    ]

    try:
        send_premium(chat_id, lines)
    except:
        bot.send_message(chat_id,
            f"🔒 DEAL ID: #{deal_id}\n\n"
            f"👤 Buyer - @{state['buyer']}\n"
            f"👤 Seller - @{state['seller']}\n\n"
            f"🤝 Both Buyer And Seller Have Confirmed The Deal. ✅\n\n"
            f"Please Pay To Your Escrower {escrow_name} To Continue Your Deal"
        )

# ──────────────────────────────────────────────────
#  AUTO DETECT: find escrow's active deal in this chat
# ──────────────────────────────────────────────────

def find_escrow_deal(caller_username, chat_id, data, need_received=False):
    """
    Find the active deal where caller is escrow in this chat.
    need_received=True → find deal that is IN_PROGRESS (for /close)
    need_received=False → find deal that is AWAITING_PAYMENT (for /received)
    Returns deal_id or None
    """
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
    if len(matches) == 1:
        return matches[0]
    return None

# ──────────────────────────────────────────────────
#  /received — Auto detect deal ID
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["received"])
def cmd_received(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /received.")
        return

    caller = (message.from_user.username or message.from_user.first_name or "").lower()
    parts  = message.text.split()
    data   = load()

    # Try to get deal_id from command or auto-detect
    if len(parts) >= 2:
        deal_id = _parse_deal_id(parts[1])
    else:
        deal_id = find_escrow_deal(caller, message.chat.id, data, need_received=False)
        if not deal_id:
            bot.reply_to(message,
                "⚠️ No active deal found for you.\n"
                "Usage: /received CRPTIN00001")
            return

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

    deal["received"]    = True
    deal["status"]      = "IN_PROGRESS"
    deal["received_at"] = datetime.datetime.now().isoformat()
    save(data)

    bot.send_message(message.chat.id,
        f"💰 Received Amount: ${fmt(total)}\n"
        f"💸 Release/Refund Amount: ${fmt(amount)}\n"
        f"🛡 Escrow Fee: ${fmt(fee)}\n"
        f"🔒 Trade ID: #{deal_id}\n\n"
        f"➡️ Continue the Deal\n"
        f"👤 Buyer: @{deal['buyer']}\n"
        f"👤 Seller: @{deal['seller']}\n"
        f"🔐 Escrower: @{deal['escrow']}"
    )

# ──────────────────────────────────────────────────
#  /close DEAL_ID — Complete + Vouch
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["close"])
def cmd_close(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /close.")
        return

    parts  = message.text.split()
    caller = (message.from_user.username or message.from_user.first_name or "").lower()
    data   = load()

    # Auto-detect or use provided ID
    if len(parts) >= 2:
        deal_id = _parse_deal_id(parts[1])
    else:
        deal_id = find_escrow_deal(caller, message.chat.id, data, need_received=True)
        if not deal_id:
            # Try AWAITING_PAYMENT too (if /received was skipped)
            deal_id = find_escrow_deal(caller, message.chat.id, data, need_received=False)
        if not deal_id:
            bot.reply_to(message,
                "⚠️ No active deal found for you.\n"
                "Usage: /close CRPTIN00001")
            return

    if deal_id not in data["deals"]:
        bot.reply_to(message, f"❌ Deal #{deal_id} not found.")
        return

    data = load()

    if deal_id not in data["deals"]:
        bot.reply_to(message, f"❌ Deal #{deal_id} not found.")
        return

    deal = data["deals"][deal_id]
    if caller != deal["escrow"].lower():
        bot.reply_to(message, f"❌ Only escrow @{deal['escrow']} can close this deal.")
        return
    if deal.get("completed"):
        bot.reply_to(message, f"⚠️ Deal #{deal_id} is already completed.")
        return

    amount     = deal["amount"]
    network    = deal.get("network","")
    escrow_id  = deal.get("escrow_id", 0)
    escrow_name = deal["escrow"]

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

    # ── Vouch message (Image 5 style) with premium emojis ──
    # Escrower as clickable name
    vouch_lines = [
        {"text": "✅ Deal Completed", "emojis": [{"char":"✅","id":E_TICK}]},
        {"text": ""},
        {"text": f"🔒 Trade ID: #{deal_id}", "emojis": [{"char":"🔒","id":E_LOCK}]},
        {"text": f"💵 Released Amount: ${fmt(amount)}", "emojis": [{"char":"💵","id":E_DOLLAR}]},
        {"text": f"👤 Buyer: @{deal['buyer']}",  "emojis": [{"char":"👤","id":E_PERSON}]},
        {"text": f"👤 Seller: @{deal['seller']}", "emojis": [{"char":"👤","id":E_PERSON}]},
        {"text": f"🛡 Escrowed By: {escrow_name}",
         "emojis": [{"char":"🛡","id":E_SHIELD}],
         "links": [{"text": escrow_name, "url": f"tg://user?id={escrow_id}"}]},
    ]

    try:
        send_premium(message.chat.id, vouch_lines)
    except:
        bot.send_message(message.chat.id,
            f"✅ Deal Completed\n\n"
            f"🔒 Trade ID: #{deal_id}\n"
            f"💵 Released Amount: ${fmt(amount)}\n"
            f"👤 Buyer: @{deal['buyer']}\n"
            f"👤 Seller: @{deal['seller']}\n"
            f"🛡 Escrowed By: @{escrow_name}"
        )

    # Vouch channel
    try:
        # Try premium in vouch channel too
        send_vouch_to_channel(deal_id, deal, amount, escrow_name, escrow_id, date_str)
    except Exception as ex:
        bot.send_message(message.chat.id, f"⚠️ Vouch channel error: {ex}")


def send_vouch_to_channel(deal_id, deal, amount, escrow_name, escrow_id, date_str):
    vouch_lines = [
        {"text": "✅ Deal Completed", "emojis": [{"char":"✅","id":E_TICK}]},
        {"text": ""},
        {"text": f"🔒 Trade ID: #{deal_id}", "emojis": [{"char":"🔒","id":E_LOCK}]},
        {"text": f"💵 Released Amount: ${fmt(amount)}", "emojis": [{"char":"💵","id":E_DOLLAR}]},
        {"text": f"👤 Buyer: @{deal['buyer']}",  "emojis": [{"char":"👤","id":E_PERSON}]},
        {"text": f"👤 Seller: @{deal['seller']}", "emojis": [{"char":"👤","id":E_PERSON}]},
        {"text": f"🛡 Escrowed By: {escrow_name}",
         "emojis": [{"char":"🛡","id":E_SHIELD}],
         "links": [{"text": escrow_name, "url": f"tg://user?id={escrow_id}"}]},
        {"text": ""},
        {"text": f"📅 {date_str}"},
    ]
    full_text = "\n".join(l["text"] for l in vouch_lines)
    entities  = []

    offset = 0
    for line in vouch_lines:
        line_text = line.get("text","")
        for em in line.get("emojis",[]):
            char = em["char"]
            eid  = em["id"]
            pos  = line_text.find(char)
            if pos == -1: continue
            before  = line_text[:pos]
            off_add = len(before.encode("utf-16-le")) // 2
            ch_len  = len(char.encode("utf-16-le")) // 2
            entities.append(make_entity("custom_emoji", offset+off_add, ch_len, custom_emoji_id=eid))
        for lnk in line.get("links",[]):
            ltxt = lnk["text"]
            lurl = lnk["url"]
            pos  = line_text.find(ltxt)
            if pos == -1: continue
            before  = line_text[:pos]
            off_add = len(before.encode("utf-16-le")) // 2
            lt_len  = len(ltxt.encode("utf-16-le")) // 2
            entities.append(make_entity("text_link", offset+off_add, lt_len, url=lurl))
        offset += len((line_text+"\n").encode("utf-16-le")) // 2

    bot.send_message(VOUCH_CHANNEL, full_text, entities=entities)

# ──────────────────────────────────────────────────
#  /cancel DEAL_ID
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /cancel.")
        return

    parts  = message.text.split()
    caller = (message.from_user.username or message.from_user.first_name or "").lower()

    if len(parts) < 2:
        bot.reply_to(message, "Usage: /cancel CRPTIN00001")
        return

    deal_id = _parse_deal_id(parts[1])

    if deal_id in pending:
        del pending[deal_id]
        bot.send_message(message.chat.id, f"❌ Deal #{deal_id} cancelled.")
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
        bot.reply_to(message, "❌ Completed deal cannot be cancelled.")
        return

    deal["status"] = "CANCELLED"
    deal["cancelled_at"] = datetime.datetime.now().isoformat()
    for uid_key, u in data["users"].items():
        if u.get("username","").lower() in [deal["buyer"].lower(), deal["seller"].lower()]:
            u["ongoing_deals"] = max(0, u.get("ongoing_deals",1) - 1)
    save(data)

    bot.send_message(message.chat.id,
        f"❌ Deal #{deal_id} cancelled.\n"
        f"👤 Buyer: @{deal['buyer']}\n"
        f"👤 Seller: @{deal['seller']}"
    )

# ──────────────────────────────────────────────────
#  /kickall — Scan FULL group, kick everyone except admins + active deal users
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["kickall"])
def cmd_kickall(message):
    if message.chat.type not in ["group","supergroup"]:
        bot.reply_to(message, "❌ This command only works in groups.")
        return
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only admins can use /kickall.")
        return

    chat_id = message.chat.id
    data    = load()

    # Users with active deals — protected
    protected = set()
    for did, deal in data["deals"].items():
        if not deal.get("completed") and deal.get("status") != "CANCELLED":
            protected.add(deal["buyer"].lower())
            protected.add(deal["seller"].lower())
            protected.add(deal["escrow"].lower())

    # Get all admins
    try:
        admins    = bot.get_chat_administrators(chat_id)
        admin_ids = {a.user.id for a in admins}
    except:
        admin_ids = {message.from_user.id}

    # ── Collect ALL members from our tracked users DB ──
    # Plus try to get from Telegram directly for supergroups
    all_user_ids = {}  # user_id -> username

    # From our data
    for uid_key, u in data["users"].items():
        uid_int = u.get("user_id", 0)
        uname   = u.get("username","")
        if isinstance(uid_int, int) and uid_int > 0:
            all_user_ids[uid_int] = uname

    # Also try getChatMembers if possible (works for small groups)
    try:
        count = bot.get_chat_members_count(chat_id)
        # For supergroups Telegram doesn't expose full member list via API
        # We rely on our tracked data + check membership status
    except:
        pass

    kicked         = []
    protected_list = []
    not_in_group   = []

    for uid_int, uname in all_user_ids.items():
        if uid_int in admin_ids:
            continue

        # Check if actually in group
        try:
            member = bot.get_chat_member(chat_id, uid_int)
            status = member.status
            if status in ("left", "kicked"):
                not_in_group.append(uname)
                continue
        except:
            not_in_group.append(uname)
            continue

        # Protected: active deal
        if uname.lower() in protected:
            protected_list.append(f"@{uname}")
            continue

        # Kick (ban then unban = soft kick, can rejoin)
        try:
            bot.ban_chat_member(chat_id, uid_int)
            bot.unban_chat_member(chat_id, uid_int)
            kicked.append(f"@{uname}")
        except:
            pass

    try:
        total = bot.get_chat_members_count(chat_id)
    except:
        total = 0

    result = (
        f"✅ Kick Operation Completed!\n\n"
        f"👥 Total Group Members: {total}\n"
        f"🦵 Users Kicked: {len(kicked)}\n"
        f"🔒 Protected (Active Deals): {len(protected_list)}\n"
    )
    if kicked:
        result += "\n🦵 Kicked:\n" + "\n".join(f"• {k}" for k in kicked)
    if protected_list:
        result += "\n\n🔒 Protected (Active Deals):\n" + \
                  "\n".join(f"• {p}" for p in protected_list)
    result += "\n\nℹ️ Kicked users can rejoin via invite link."

    bot.send_message(chat_id, result)

# ──────────────────────────────────────────────────
#  /mydeal
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["mydeal"])
def cmd_mydeal(message):
    caller_u  = (message.from_user.username or "").lower()
    caller_id = message.from_user.id
    data      = load()
    active    = []

    for did, d in data["deals"].items():
        if d.get("completed") or d.get("status") == "CANCELLED":
            continue
        involved = [d.get("buyer","").lower(), d.get("seller","").lower(), d.get("escrow","").lower()]
        if caller_u in involved or caller_id == d.get("escrow_id",0):
            active.append((did, d))

    if not active:
        bot.reply_to(message, "✅ You have no active deals right now.")
        return

    text = f"📋 Your Active Deals ({len(active)})\n" + "─"*22 + "\n"
    for did, d in active:
        text += f"\n🔒 #{did}\n💵 ${fmt(d['amount'])} | {d.get('status','').replace('_',' ').title()}\n👤 B:@{d.get('buyer','?')} | S:@{d.get('seller','?')}\n"
    bot.reply_to(message, text)

# ──────────────────────────────────────────────────
#  /help
# ──────────────────────────────────────────────────

@bot.message_handler(commands=["start","help"])
def cmd_help(message):
    bot.send_message(message.chat.id,
        "🤖 aNamaka CRPT Escrow Bot\n"
        "─────────────────────────\n"
        "/form or /dd    → Get blank form\n"
        "/got            → Reply on form to create deal (Admin)\n"
        "/close [ID]     → Complete deal + vouch (Admin)\n"
        "/cancel [ID]    → Cancel deal (Admin)\n"
        "/mydeal         → Your active deals\n"
        "─────────────────────────\n"
        "💸 Flat Fee: $1 per deal"
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

print("🤖 aNamaka CRPT Escrow Bot running | Fee: $1 flat")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
