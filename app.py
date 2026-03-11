"""
Lark × Claude Personal Assistant Bot
Deploy on Render.com (free tier) — Python 3.11+
"""

import os, json, hashlib, hmac, base64, time, threading
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── ENV VARS (set in Render dashboard) ──────────────────────────────────────
LARK_APP_ID          = os.environ["LARK_APP_ID"]
LARK_APP_SECRET      = os.environ["LARK_APP_SECRET"]
LARK_VERIFY_TOKEN    = os.environ["LARK_VERIFY_TOKEN"]
ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]
LARK_API_BASE        = "https://open.larksuite.com/open-apis"

# ── Simple in-memory conversation history (per user, last 10 turns) ─────────
conv_history = {}
processed_ids = set()

SYSTEM_PROMPT = """You are a smart, proactive personal assistant. Your job is to:
1. 📋 TASK ANALYSIS — Break down complex tasks into clear, actionable steps
2. 📝 TASK DRAFTING — Write task lists, plans, and to-do items in structured format
3. 📅 SCHEDULE MANAGEMENT — Help plan and organize appointments, deadlines, and time blocks
4. 💡 PROACTIVE SUGGESTIONS — Offer smart follow-up ideas, reminders, or improvements the user may not have thought of
5. 🌐 MULTILINGUAL — Reply in the same language the user writes in (Chinese, English, Malay, etc.)

Formatting rules:
- Use clear headings and emoji for readability in Lark chat
- Be concise but thorough
- Always end task/schedule responses with a "💡 Suggestion:" section with one proactive tip
- If the user says something vague, ask ONE clarifying question before proceeding
"""

# ── Lark Token Cache ─────────────────────────────────────────────────────────
_token_cache = {"token": None, "expires_at": 0}

def get_tenant_token():
    if time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    r = requests.post(f"{LARK_API_BASE}/auth/v3/tenant_access_token/internal",
                      json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET})
    data = r.json()
    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expire", 7200)
    return _token_cache["token"]

# ── Send message back to Lark ────────────────────────────────────────────────
def send_lark_message(open_id, text):
    token = get_tenant_token()
    requests.post(
        f"{LARK_API_BASE}/im/v1/messages?receive_id_type=open_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "receive_id": open_id,
            "msg_type": "text",
            "content": json.dumps({"text": text})
        }
    )

# ── Call Claude API ──────────────────────────────────────────────────────────
def call_claude(open_id, user_text):
    history = conv_history.get(open_id, [])
    history.append({"role": "user", "content": user_text})
    if len(history) > 20:
        history = history[-20:]

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1500,
            "system": SYSTEM_PROMPT,
            "messages": history
        }
    )
    data = resp.json()
    reply = data["content"][0]["text"]

    history.append({"role": "assistant", "content": reply})
    conv_history[open_id] = history[-20:]
    return reply

# ── Process event in background (Lark needs 200 in <3s) ─────────────────────
def handle_event_async(open_id, text, event_id):
    if event_id in processed_ids:
        return
    processed_ids.add(event_id)
    if len(processed_ids) > 1000:
        processed_ids.clear()
    try:
        reply = call_claude(open_id, text)
        send_lark_message(open_id, reply)
    except Exception as e:
        send_lark_message(open_id, f"⚠️ Error: {str(e)}")

# ── Main webhook endpoint ────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json(force=True)

    # 1. URL verification challenge (first-time setup)
    if body.get("type") == "url_verification":
        return jsonify({"challenge": body["challenge"]})

    # 2. Verify token
    header = body.get("header", {})
    if header.get("token") != LARK_VERIFY_TOKEN:
        return jsonify({"error": "bad token"}), 403

    # 3. Handle message received event
    event = body.get("event", {})
    msg   = event.get("message", {})

    if msg.get("message_type") == "text":
        content = json.loads(msg.get("content", "{}"))
        user_text = content.get("text", "").strip()
        open_id   = event.get("sender", {}).get("sender_id", {}).get("open_id", "")
        event_id  = header.get("event_id", "")

        if user_text and open_id:
            t = threading.Thread(target=handle_event_async,
                                 args=(open_id, user_text, event_id))
            t.daemon = True
            t.start()

    return jsonify({"code": 0})

@app.route("/", methods=["GET"])
def health():
    return "✅ Lark Claude Bot is running!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
