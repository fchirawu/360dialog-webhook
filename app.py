"""
Kuzuva WhatsApp Webhook Receiver (360dialog)
------------------------------------------------
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

app = Flask(__name__)

LOG_FILE = "messages_log.jsonl"
DB_PATH = "kuzuva_bot.db"

D360_API_KEY = os.environ.get("D360_API_KEY")
D360_BASE_URL = "https://waba-v2.360dialog.io"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

SESSION_WINDOW_SECONDS = 24 * 60 * 60  # WhatsApp's 24h free-form reply rule

SYSTEM_PROMPT = """You are the WhatsApp assistant for Kuzuva Technology, a
Harare, Zimbabwe-based tech company founded by Farai Chirawu.

Kuzuva's services:
- AI consultancy with WhatsApp workflow automation (primary service)
- Website design and development
- App and web development
- Marketing
- Software installation (e.g. QuickBooks)
- Hosting, domain search, and email setup

Tone: direct, professional, friendly -- no corporate fluff. Keep replies
short, WhatsApp-appropriate (2-4 sentences max unless asked for detail).

Boundaries:
- Never quote firm prices. Say pricing is USD-only and depends on scope,
  and offer to connect them with Farai directly.
- Never commit to timelines or contractual terms.
- If the user wants a quote or seems ready to move forward, say you'll get
  Farai to follow up.
- Contact for handoff: farai@kuzuva.com / +263 785 222 656.
"""

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
            last_user_message_at REAL
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
                    sender = m.get("from")
                    text = m.get("text", {}).get("body")
                    print(f"[{entry['received_at']}] New message from {sender}: {text}")

                    if not sender or not text:
                        continue

                    save_message(sender, "user", text)

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
