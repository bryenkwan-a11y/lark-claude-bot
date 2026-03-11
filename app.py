"""
Bryen Assistant v2.2 — Lark × Claude
- Reliable: sends thinking message first, then final reply as separate message
- No message editing required (works on all Lark plans)
- All features: tasks, calendar, weather, email, briefing, group chat
"""

import os, json, time, threading, datetime, requests, re
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── ENV VARS ──────────────────────────────────────────────────────────────────
LARK_APP_ID       = os.environ["LARK_APP_ID"]
LARK_APP_SECRET   = os.environ["LARK_APP_SECRET"]
LARK_VERIFY_TOKEN = os.environ["LARK_VERIFY_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
WEATHER_API_KEY   = os.environ.get("WEATHER_API_KEY", "")
MY_OPEN_ID        = os.environ.get("MY_OPEN_ID", "")
MY_CITY           = os.environ.get("MY_CITY", "Kuala Lumpur")
LARK_BASE         = "https://open.larksuite.com/open-apis"

# ── In-memory stores ──────────────────────────────────────────────────────────
conv_history  = {}
task_board    = {}
processed_ids = set()
task_counter  = [0]

# ── Thinking messages ─────────────────────────────────────────────────────────
def get_thinking_msg(text):
    t = text.lower()
    if any(k in t for k in ["calendar", "schedule", "meeting", "event", "今天", "会议"]):
        return "📅 Checking your calendar..."
    if any(k in t for k in ["task", "todo", "任务", "assign", "please", "tolong"]):
        return "📋 Checking your task board..."
    if any(k in t for k in ["weather", "rain", "hot", "天气"]):
        return "🌤️ Fetching weather..."
    if any(k in t for k in ["email", "邮件", "draft", "write to"]):
        return "📧 Drafting your email..."
    if any(k in t for k in ["meeting note", "summarize", "summary", "总结"]):
        return "📊 Preparing meeting notes..."
    if any(k in t for k in ["search", "find", "what is", "who is", "搜索", "查"]):
        return "🔍 Searching for information..."
    if any(k in t for k in ["brief", "morning", "早上"]):
        return "🌅 Preparing your daily briefing..."
    return "🤔 Thinking..."

SYSTEM_PROMPT = """You are Bryen Assistant — a smart, proactive executive personal assistant on Lark.

CAPABILITIES:
1. 📋 TASK MANAGEMENT — Record, track, assign tasks. Use ⏳ pending, ✅ done, 🔴 overdue
2. 📅 CALENDAR & MEETINGS — Schedule meetings, add notes, suggest prep steps
3. 📧 EMAIL DRAFTING — Write professional emails. Always show Subject + Body
4. 📊 MEETING NOTES — Summarize discussions into clean action items
5. 🌐 WEB SEARCH — Answer questions about current info and facts
6. 🌤️ WEATHER — Factor weather into scheduling suggestions
7. 🔔 DAILY BRIEFING — Structured morning briefings with tasks, meetings, priorities
8. 👥 TASK ASSIGNMENT — Assign tasks to team members and track them

RULES:
- Reply in the SAME language the user writes in (English, Chinese, Malay)
- In GROUP CHATS: only respond when @mentioned, be concise
- Always end with "💡 Suggestion:" with one proactive tip
- For tasks mentioned to the user, auto-record them
- Format responses cleanly with emoji headers
- Keep replies concise — bullet points over long paragraphs

TASK FORMAT:
📌 Task recorded:
• Title: [task]
• Assigned to: [person]
• Due: [date if mentioned]
• Status: ⏳ Pending
"""

# ── Token cache ───────────────────────────────────────────────────────────────
_token = {"v": None, "exp": 0}

def get_token():
    if time.time() < _token["exp"] - 60:
        return _token["v"]
    r = requests.post(
        f"{LARK_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET}
    )
    d = r.json()
    _token["v"] = d["tenant_access_token"]
    _token["exp"] = time.time() + d.get("expire", 7200)
    return _token["v"]

def lark_headers():
    return {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}

# ── Send message ──────────────────────────────────────────────────────────────
def send_msg(receive_id, text, id_type="open_id"):
    try:
        r = requests.post(
            f"{LARK_BASE}/im/v1/messages?receive_id_type={id_type}",
            headers=lark_headers(),
            json={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text})
            },
            timeout=10
        )
        return r.json()
    except Exception as e:
        print(f"send_msg error: {e}", flush=True)
        return {}

# ── Calendar ──────────────────────────────────────────────────────────────────
def get_today_calendar():
    try:
        now   = datetime.datetime.utcnow()
        start = int(datetime.datetime(now.year, now.month, now.day, 0, 0).timestamp())
        end   = int(datetime.datetime(now.year, now.month, now.day, 23, 59).timestamp())
        r = requests.get(
            f"{LARK_BASE}/calendar/v4/calendars/primary/events",
            headers=lark_headers(),
            params={"start_time": str(start), "end_time": str(end)},
            timeout=10
        )
        events = r.json().get("data", {}).get("items", [])
        if not events:
            return "📅 No calendar events today."
        lines = ["📅 Today's Calendar:"]
        for e in events:
            summary = e.get("summary", "Untitled")
            ts = e.get("start_time", {}).get("timestamp", "")
            t  = datetime.datetime.fromtimestamp(int(ts)).strftime("%I:%M %p") if ts else "All day"
            lines.append(f"  • {t} — {summary}")
        return "\n".join(lines)
    except Exception as ex:
        return f"📅 Calendar: unavailable ({ex})"

# ── Weather ───────────────────────────────────────────────────────────────────
def get_weather():
    if not WEATHER_API_KEY:
        return "🌤️ Weather: (Add WEATHER_API_KEY in Render to enable)"
    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": MY_CITY, "appid": WEATHER_API_KEY, "units": "metric"},
            timeout=10
        )
        d     = r.json()
        desc  = d["weather"][0]["description"].capitalize()
        temp  = d["main"]["temp"]
        humid = d["main"]["humidity"]
        return f"🌤️ {MY_CITY}: {desc}, {temp}°C, Humidity {humid}%"
    except:
        return "🌤️ Weather: unavailable"

# ── Tasks ─────────────────────────────────────────────────────────────────────
def get_pending_tasks():
    pending = [t for t in task_board.values() if t["status"] == "pending"]
    if not pending:
        return "✅ Task board is clear!"
    lines = ["📋 Pending Tasks:"]
    for t in pending:
        due = f" (due {t['due']})" if t.get("due") else ""
        lines.append(f"  ⏳ [{t['id']}] {t['title']}{due} → {t.get('assignee','me')}")
    return "\n".join(lines)

def add_task(title, assignee="me", assigner="me", due=""):
    task_counter[0] += 1
    tid = f"T{task_counter[0]:03d}"
    task_board[tid] = {
        "id": tid, "title": title, "assignee": assignee,
        "assigner": assigner, "due": due, "status": "pending", "notes": ""
    }
    return tid

def detect_and_record_task(text, sender_id):
    keywords = ["please", "tolong", "请", "需要", "task:", "assign", "can you", "follow up"]
    if any(k in text.lower() for k in keywords):
        return add_task(title=text[:80], assignee="me", assigner=sender_id)
    return None

# ── Daily briefing ────────────────────────────────────────────────────────────
def send_daily_briefing():
    if not MY_OPEN_ID:
        print("MY_OPEN_ID not set — skipping briefing", flush=True)
        return
    weather  = get_weather()
    calendar = get_today_calendar()
    tasks    = get_pending_tasks()
    today    = datetime.datetime.now().strftime("%A, %B %d %Y")
    msg = f"""🌅 Good morning, Bryen! Here's your briefing for {today}

{weather}

{calendar}

{tasks}

💡 Tip: Block your top focus time before meetings fill your day!

— Bryen Assistant 🤖"""
    send_msg(MY_OPEN_ID, msg)

def briefing_scheduler():
    while True:
        now = datetime.datetime.utcnow()
        if now.hour == 0 and now.minute == 1:
            send_daily_briefing()
            time.sleep(60)
        time.sleep(50)

threading.Thread(target=briefing_scheduler, daemon=True).start()

# ── Claude API ────────────────────────────────────────────────────────────────
def call_claude(open_id, user_text, extra_context=""):
    history = conv_history.get(open_id, [])
    full_msg = f"{extra_context}\n\nUser: {user_text}" if extra_context else user_text
    history.append({"role": "user", "content": full_msg})
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
        },
        timeout=30
    )
    reply = resp.json()["content"][0]["text"]
    history.append({"role": "assistant", "content": reply})
    conv_history[open_id] = history[-20:]
    return reply

# ── Core handler ──────────────────────────────────────────────────────────────
def handle_event(body):
    header   = body.get("header", {})
    event    = body.get("event", {})
    msg      = event.get("message", {})
    event_id = header.get("event_id", "")

    if event_id in processed_ids:
        return
    processed_ids.add(event_id)
    if len(processed_ids) > 1000:
        processed_ids.clear()

    if msg.get("message_type") != "text":
        return

    raw       = json.loads(msg.get("content", "{}"))
    user_text = raw.get("text", "").strip()
    sender_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")
    chat_type = msg.get("chat_type", "p2p")
    chat_id   = msg.get("chat_id", "")
    is_group  = chat_type == "group"

    print(f"MSG from {sender_id} | group={is_group} | text={user_text[:50]}", flush=True)

    # Group chat: only reply if @mentioned
    if is_group and "<at" not in user_text:
        detect_and_record_task(user_text, sender_id)
        return

    # Clean @mention tags
    clean_text = re.sub(r'<at[^>]*>.*?</at>', '', user_text).strip()

    receive_id = chat_id if is_group else sender_id
    id_type    = "chat_id" if is_group else "open_id"

    # ── Send thinking message immediately ────────────────────────────────────
    thinking = get_thinking_msg(clean_text)
    send_msg(receive_id, thinking, id_type)

    # ── Build context ─────────────────────────────────────────────────────────
    extra = ""
    triggers = ["brief", "today", "schedule", "calendar", "task", "meeting",
                "weather", "plan", "morning", "什么", "今天", "任务", "会议"]
    if any(t in clean_text.lower() for t in triggers):
        send_msg(receive_id, "⏳ Gathering your data...", id_type)
        extra = f"{get_weather()}\n{get_today_calendar()}\n{get_pending_tasks()}"

    # ── Auto-record task ──────────────────────────────────────────────────────
    tid = detect_and_record_task(clean_text, sender_id)
    if tid:
        extra += f"\n\n[SYSTEM: Task {tid} auto-recorded to task board]"

    # ── Call Claude and send reply ────────────────────────────────────────────
    try:
        reply = call_claude(sender_id, clean_text, extra)
        send_msg(receive_id, reply, id_type)
    except Exception as e:
        print(f"Claude error: {e}", flush=True)
        send_msg(receive_id, f"⚠️ Sorry, something went wrong. Please try again.\nError: {str(e)}", id_type)

# ── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json(force=True)
    if body.get("type") == "url_verification":
        return jsonify({"challenge": body["challenge"]})
    if body.get("header", {}).get("token") != LARK_VERIFY_TOKEN:
        return jsonify({"error": "bad token"}), 403
    threading.Thread(target=handle_event, args=(body,), daemon=True).start()
    return jsonify({"code": 0})

@app.route("/briefing", methods=["GET"])
def manual_briefing():
    threading.Thread(target=send_daily_briefing, daemon=True).start()
    return "✅ Briefing sent!", 200

@app.route("/tasks", methods=["GET"])
def view_tasks():
    return jsonify(task_board)

@app.route("/", methods=["GET"])
def health():
    return "✅ Bryen Assistant v2.2 is running!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
