"""
Kuzuva WhatsApp Webhook Receiver (360dialog)
---------------------------------------------------------------
Receives incoming WhatsApp messages forwarded by 360dialog, stores
conversation history in SQLite, generates an AI reply with Claude, and
sends it back via 360dialog. Also exposes /send for manual sends and
/messages for a raw view of everything received.

This is for the 360dialog integration only. 360dialog sits between you and
Meta and handles the Meta webhook verification handshake on their side --
so no GET /webhook verify route is needed here.

DEPLOY (Render):
  Build command: pip install -r requirements.txt
  Start command: gunicorn app:app

CONNECT TO 360DIALOG:
  Dashboard -> Kuzuva Technology channel -> API Settings -> Channel Webhook URL
  -> https://<your-app>.onrender.com/webhook

ENV VARS (Render -> Environment):
  D360_API_KEY        -> your 360dialog API key
  ANTHROPIC_API_KEY   -> your Claude API key

NOTE ON STORAGE:
  Render's free tier filesystem is ephemeral -- messages_log.jsonl and the
  SQLite file both reset on restart/sleep. Fine for testing; move to a
  managed database (e.g. Render's paid Postgres) once this has real clients.
"""
from flask import Flask, request, jsonify
from datetime import datetime
import json
import os
import sqlite3
import time
import requests
from anthropic import Anthropic
from kuzuva_skills import build_skills_section

app = Flask(__name__)

LOG_FILE = "messages_log.jsonl"
DB_PATH = "kuzuva_bot.db"

D360_API_KEY = os.environ.get("D360_API_KEY")
D360_BASE_URL = "https://waba-v2.360dialog.io"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

ESCALATION_PHONE = "263710386194"  # Farai's personal Zim number (internal alert only)
ESCALATION_KEYWORDS = [
    "price", "pricing", "quote", "cost", "how much", "sign up",
    "get started", "talk to farai", "speak to farai", "human", "agent",
]

SESSION_WINDOW_SECONDS = 24 * 60 * 60  # WhatsApp's 24h free-form reply rule

# ---------------------------------------------------------------------
# System prompt -- the AI assistant's identity, tone, and hard rules.
# Everything topic-specific (services framing, pricing, escalation
# language, self-identification) lives in kuzuva_skills.py instead, so
# that file is the only thing you need to edit going forward.
# ---------------------------------------------------------------------
BASE_SYSTEM_PROMPT = """You are the AI assistant for Kuzuva Technology, a
Harare, Zimbabwe-based tech company.

Kuzuva's services:
- Primary: AI consultancy & WhatsApp workflow automation (this is what Kuzuva
  is known for)
- Also offered: website/app/web development, marketing, software
  installation, hosting, domains, email setup

Tone: direct, professional, friendly -- no corporate fluff.

Reply length rule: 2-4 sentences MAX, always. This is a hard rule, not a
suggestion.

Identity rule: You are Kuzuva's AI assistant. Never imply you are a human.
Never call yourself an "agent" -- you can't yet take actions like booking or
looking things up, only answer questions. Never give out personal names,
personal emails, or personal phone numbers of staff.
"""

SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + "\n\n" + build_skills_section()

# In-memory cache for fast /messages reads. Rebuilt from disk on startup.
received_messages = []


# ---------------------------------------------------------------------
# JSONL log (unchanged from before -- kept as a raw audit trail)
# ---------------------------------------------------------------------

def save_to_disk(entry):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def load_from_disk():
    if not os.path.exists(LOG_FILE):
        return
    with open(LOG_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                received_messages.append(json.loads(line))


load_from_disk()


# ---------------------------------------------------------------------
# SQLite: conversation history per phone number
# ---------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            phone TEXT PRIMARY KEY,
            last_user_message_at REAL,
            escalated INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_wamids (
            wamid TEXT PRIMARY KEY,
            processed_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


init_db()


def save_message(phone, role, content):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO messages (phone, role, content, created_at) VALUES (?, ?, ?, ?)",
        (phone, role, content, time.time()),
    )
    if role == "user":
        conn.execute(
            """INSERT INTO contacts (phone, last_user_message_at) VALUES (?, ?)
               ON CONFLICT(phone) DO UPDATE SET last_user_message_at=excluded.last_user_message_at""",
            (phone, time.time()),
        )
    conn.commit()
    conn.close()


def get_history(phone, limit=20):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE phone = ? ORDER BY id DESC LIMIT ?",
        (phone, limit),
    ).fetchall()
    conn.close()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def within_24h_window(phone):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT last_user_message_at FROM contacts WHERE phone = ?", (phone,)
    ).fetchone()
    conn.close()
    if not row or row[0] is None:
        return False
    return (time.time() - row[0]) < SESSION_WINDOW_SECONDS


def already_processed(wamid):
    """
    Checks whether this exact WhatsApp message id has been handled before.
    360dialog/WhatsApp can redeliver the same webhook payload (e.g. if the
    first response was slow), which without this check causes the bot to
    generate and send two different AI replies to one customer message.
    """
    if not wamid:
        return False
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT 1 FROM processed_wamids WHERE wamid = ?", (wamid,)
    ).fetchone()
    conn.close()
    return row is not None


def mark_processed(wamid):
    if not wamid:
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO processed_wamids (wamid, processed_at) VALUES (?, ?)",
        (wamid, time.time()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------

def send_whatsapp_message(to_phone, text):
    if not D360_API_KEY:
        raise RuntimeError("D360_API_KEY is not set")
    resp = requests.post(
        f"{D360_BASE_URL}/messages",
        headers={"D360-API-KEY": D360_API_KEY, "Content-Type": "application/json"},
        json={
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": text},
        },
        timeout=15,
    )
    return resp.json(), resp.status_code


# ---------------------------------------------------------------------
# Escalation (internal alert to Farai only -- never customer-facing)
# ---------------------------------------------------------------------

def already_escalated(phone):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT escalated FROM contacts WHERE phone = ?", (phone,)
    ).fetchone()
    conn.close()
    return bool(row and row[0])


def mark_escalated(phone):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO contacts (phone, escalated) VALUES (?, 1)
           ON CONFLICT(phone) DO UPDATE SET escalated=1""",
        (phone,),
    )
    conn.commit()
    conn.close()


def maybe_escalate(phone, text):
    """
    Keyword-based trigger. Only fires once per contact so Farai isn't
    spammed on every follow-up message in the same conversation.
    Sends a WhatsApp alert to ESCALATION_PHONE -- note this only delivers
    if that number has messaged the Kuzuva business number within the
    last 24h, per WhatsApp's session window rule. This alert is internal
    only and never visible to the customer.
    """
    if already_escalated(phone):
        return False
    if not any(kw in text.lower() for kw in ESCALATION_KEYWORDS):
        return False

    mark_escalated(phone)
    alert = f"Kuzuva WhatsApp lead: {phone} may need you personally.\nMessage: {text}"
    try:
        result, status = send_whatsapp_message(ESCALATION_PHONE, alert)
        print(f"Escalation alert sent: status={status} result={result}")
    except Exception as err:
        print(f"Escalation alert failed to send: {err}")
    return True


# ---------------------------------------------------------------------
# AI reply
# ---------------------------------------------------------------------

def get_ai_reply(phone, incoming_text):
    if not anthropic_client:
        return None
    history = get_history(phone, limit=20)
    messages = history + [{"role": "user", "content": incoming_text}]
    response = anthropic_client.messages.create(
        model="claude-sonnet-5",
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return None


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def receive():
    payload = request.get_json(force=True, silent=True) or {}
    entry = {"received_at": datetime.utcnow().isoformat(), "payload": payload}
    received_messages.append(entry)
    save_to_disk(entry)

    try:
        for e in payload.get("entry", []):
            for change in e.get("changes", []):
                value = change.get("value", {})
                for m in value.get("messages", []):
                    wamid = m.get("id")
                    sender = m.get("from")
                    text = m.get("text", {}).get("body")
                    print(f"[{entry['received_at']}] New message from {sender}: {text}")

                    if not sender or not text:
                        continue

                    if already_processed(wamid):
                        print(f"Duplicate webhook delivery for wamid={wamid} -- skipping")
                        continue
                    mark_processed(wamid)

                    save_message(sender, "user", text)

                    escalated = maybe_escalate(sender, text)

                    reply = get_ai_reply(sender, text)
                    if reply is None:
                        print("ANTHROPIC_API_KEY not set -- skipping AI reply")
                        continue

                    save_message(sender, "assistant", reply)

                    if within_24h_window(sender):
                        result, status = send_whatsapp_message(sender, reply)
                        print(f"Sent AI reply to {sender}: status={status} result={result}")
                    else:
                        print(f"{sender} is outside the 24h window; reply not sent")
    except Exception as err:
        print(f"Error handling inbound message: {err}")

    return jsonify({"status": "ok"}), 200


@app.route("/send", methods=["POST"])
def send():
    """
    Send a WhatsApp message via 360dialog.
    Expects JSON body: {"to": "263785222656", "text": "your message"}
    """
    data = request.get_json(force=True, silent=True) or {}
    to = data.get("to")
    text = data.get("text")

    if not to or not text:
        return jsonify({"error": "Both 'to' and 'text' are required"}), 400
    if not D360_API_KEY:
        return jsonify({"error": "D360_API_KEY not configured"}), 500

    try:
        result, status = send_whatsapp_message(to, text)
    except requests.RequestException as err:
        return jsonify({"error": f"Request to 360dialog failed: {err}"}), 502

    return jsonify(result), status


@app.route("/messages", methods=["GET"])
def list_messages():
    """View everything received so far, most recent first."""
    return jsonify(list(reversed(received_messages)))


@app.route("/")
def health():
    return jsonify({"status": "Kuzuva webhook is running", "message_count": len(received_messages)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
