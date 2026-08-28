"""
plus263 WhatsApp Webhook Receiver (360dialog)
------------------------------------------------
Receives incoming WhatsApp messages forwarded by 360dialog, logs them to
disk, and exposes a /messages endpoint to view everything received so far.
Also exposes a /send endpoint to send outbound messages via 360dialog.

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
  D360_API_KEY  -> your 360dialog API key
"""
from flask import Flask, request, jsonify
from datetime import datetime
import json
import os
import requests

app = Flask(__name__)

LOG_FILE = "messages_log.jsonl"

D360_API_KEY = os.environ.get("D360_API_KEY")
D360_BASE_URL = "https://waba-v2.360dialog.io"

# In-memory cache for fast /messages reads. Rebuilt from disk on startup.
# Note: Render's free tier filesystem is ephemeral -- this resets on
# restart/sleep. Fine for testing; move to a real database for anything
# you need to keep long-term.
received_messages = []


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


@app.route("/webhook", methods=["POST"])
def receive():
    payload = request.get_json(force=True, silent=True) or {}
    entry = {
        "received_at": datetime.utcnow().isoformat(),
        "payload": payload,
    }
    received_messages.append(entry)
    save_to_disk(entry)

    # Best-effort readable summary in the logs
    try:
        for e in payload.get("entry", []):
            for change in e.get("changes", []):
                value = change.get("value", {})
                for m in value.get("messages", []):
                    sender = m.get("from")
                    text = m.get("text", {}).get("body")
                    print(f"[{entry['received_at']}] New message from {sender}: {text}")
    except Exception as err:
        print(f"Could not parse message summary: {err}")

    # 360dialog just needs a 200 OK to know it was received
    return jsonify({"status": "ok"}), 200


@app.route("/send", methods=["POST"])
def send():
    """
    Send a WhatsApp message via 360dialog.
    Expects JSON body: {"to": "263785222656", "text": "your message"}
    "to" must be in international format, no '+' and no leading zeros.
    Only works within 24h of the user's last message to you -- otherwise
    360dialog will reject it and a pre-approved template is required instead.
    """
    data = request.get_json(force=True, silent=True) or {}
    to = data.get("to")
    text = data.get("text")

    if not to or not text:
        return jsonify({"error": "Both 'to' and 'text' are required"}), 400
    if not D360_API_KEY:
        return jsonify({"error": "D360_API_KEY not configured"}), 500

    try:
        resp = requests.post(
            f"{D360_BASE_URL}/messages",
            headers={
                "D360-API-KEY": D360_API_KEY,
                "Content-Type": "application/json",
            },
            json={"to": to, "type": "text", "text": {"body": text}},
            timeout=15,
        )
    except requests.RequestException as err:
        return jsonify({"error": f"Request to 360dialog failed: {err}"}), 502

    return jsonify(resp.json()), resp.status_code


@app.route("/messages", methods=["GET"])
def list_messages():
    """View everything received so far, most recent first."""
    return jsonify(list(reversed(received_messages)))


@app.route("/")
def health():
    return jsonify({"status": "plus263 webhook is running", "message_count": len(received_messages)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
