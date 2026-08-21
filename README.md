# plus263 WhatsApp Webhook Receiver (360dialog)

Minimal Flask app that receives incoming WhatsApp messages forwarded by
360dialog. This is separate from any Meta direct-connect setup -- no
webhook verification handshake needed, since 360dialog handles that on
their end.

## What it does

- `POST /webhook` -- 360dialog sends incoming message events here.
  Logged to console, saved to `messages_log.jsonl`, kept in memory.
- `GET /messages` -- view everything received so far (JSON), most recent
  first.
- `GET /` -- health check, shows how many messages have been received.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Runs on `http://localhost:5000`. To test locally before deploying, use a
tunnel tool like `ngrok`:

```bash
ngrok http 5000
```

## Deploy to Render (free tier)

1. Push this folder to your GitHub repo.
2. Render.com -> New -> Web Service -> connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Your webhook URL will be `https://<your-app>.onrender.com/webhook`

## Connect it to 360dialog

1. 360dialog dashboard -> Kuzuva Technology channel -> **API Settings**
2. Click the pencil icon next to **Channel Webhook URL**
3. Paste your deployed URL + `/webhook`
   e.g. `https://your-app.onrender.com/webhook`
4. Click **Send test request** to confirm it's reachable, then **Save**.
5. Send a real WhatsApp message to your number to confirm end-to-end.
6. Check `https://your-app.onrender.com/messages` to see it land.

## Known limitation

Render's free tier filesystem is ephemeral -- `messages_log.jsonl` resets
on every restart/sleep. Fine for testing. For anything you need to keep
long-term, swap the file-based storage for a real database (even Render's
free-tier Postgres works).

## Next steps once this is stable

- Swap file storage for a database.
- Add a `/send` endpoint using your 360dialog API key to reply
  automatically -- the foundation for Kuzuva's WhatsApp automation
  workflows.
