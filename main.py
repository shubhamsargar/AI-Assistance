from __future__ import annotations

import hmac
import json
import os
import random
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
from typing import Generator

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from weather import get_weather

load_dotenv()

ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(ROOT / "instance" / "vidmahi.sqlite3")))
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
ACCESS_TOKEN = os.getenv("VIDMAHI_ACCESS_TOKEN", "")
AGENT_TOKEN = os.getenv("VIDMAHI_AGENT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1")
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "")

app = Flask(__name__)
app.json.ensure_ascii = False
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
db_lock = threading.Lock()

RIDDLES = [
    ("What has keys but cannot open locks?", "keyboard"),
    ("What has hands but cannot clap?", "clock"),
    ("What gets wetter as it dries?", "towel"),
    ("What has a neck but no head?", "bottle"),
]

SYSTEM_PROMPT = (
    "You are Vidmahi, a helpful and friendly assistant. Be clear and concise. "
    "Reply in the user's language and understand Hindi, English, Marathi, and Hinglish. "
    "Do not claim to have performed actions on the user's devices."
)


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_db() -> None:
    with db_lock, get_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY,
                action_json TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_seen TEXT NOT NULL
            );
            """
        )


initialize_db()


def token_matches(configured: str, supplied: str) -> bool:
    return bool(configured) and bool(supplied) and hmac.compare_digest(configured, supplied)


def bearer_token() -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def require_user() -> bool:
    authorization_header = request.headers.get("Authorization", "")
    supplied_token = bearer_token()
    accepted = token_matches(ACCESS_TOKEN, supplied_token)
    app.logger.info(
        "API auth check endpoint=%s authorization_header_present=%s "
        "bearer_token_present=%s access_token_configured=%s accepted=%s",
        request.path,
        bool(authorization_header.strip()),
        bool(supplied_token),
        bool(ACCESS_TOKEN),
        accepted,
    )
    return accepted


def require_agent() -> bool:
    return token_matches(AGENT_TOKEN, bearer_token())


def make_action(kind: str, **details: str) -> dict[str, str]:
    return {"kind": kind, **details}


def interpret_command(command: str) -> tuple[str | None, dict | None]:
    text = command.strip().lower().strip(" .?!")
    if not text:
        return "I didn't catch that. Please try again.", None

    if any(phrase in text for phrase in ("play a puzzle game", "play puzzle", "start puzzle", "lets play puzzle")):
        question, answer = random.choice(RIDDLES)
        return question, {"kind": "riddle", "answer": answer}

    if text in {"current time", "what time is it", "time"}:
        return f"The time is {datetime.now().astimezone().strftime('%I:%M %p')}.", None
    if text in {"current date", "what is the date", "today's date", "todays date"}:
        return f"Today is {datetime.now().astimezone().strftime('%d %B %Y')}.", None

    if "battery" in text:
        return "Checking the laptop battery.", make_action("battery")

    if "weather" in text:
        match = re.search(r"\bweather(?:\s+(?:in|for)\s+(.+))?$", text)
        city = match.group(1).strip(" .?!") if match and match.group(1) else "Pune"
        return None, make_action("weather", city=city)

    if text in {"open youtube", "open youtube.com"}:
        return "Opening YouTube on your laptop.", make_action("open_url", url="https://www.youtube.com/")
    if text in {"open google", "open google.com"}:
        return "Opening Google on your laptop.", make_action("open_url", url="https://www.google.com/")
    if "open whatsapp" in text:
        return "Opening WhatsApp on your laptop.", make_action("open_url", url="https://web.whatsapp.com/")
    if "open chat g p t" in text or "open chatgpt" in text:
        return "Opening ChatGPT on your laptop.", make_action("open_url", url="https://chatgpt.com/")

    match = re.match(r"^(?:search(?:\s+google)?|google)\s+(?:for\s+)?(.+)$", text)
    if match:
        query = match.group(1).strip()
        return f"Searching the web for {query}.", make_action(
            "open_url", url=f"https://www.google.com/search?q={quote_plus(query)}"
        )

    match = re.match(r"^play\s+(.+)$", text)
    if match:
        song = match.group(1).strip()
        return f"Searching YouTube for {song}.", make_action(
            "open_url", url=f"https://www.youtube.com/results?search_query={quote_plus(song)}"
        )

    action_patterns = (
        (r"^(?:lock laptop|lock computer)$", "lock"),
        (r"^(?:sleep laptop|put (?:the )?laptop to sleep)$", "sleep"),
        (r"^(?:shutdown|shut down)(?: laptop| computer)?$", "shutdown"),
        (r"^(?:restart|reboot)(?: laptop| computer)?$", "restart"),
        (r"^(?:close|quit) chrome$", "close_chrome"),
        (r"^(?:close|quit) (?:vs code|vscode|visual studio code)$", "close_code"),
        (r"^open chrome$", "open_chrome"),
        (r"^open (?:vs code|vscode|visual studio code)$", "open_code"),
        (r"^(?:refresh page|refresh)$", "refresh"),
        (r"^(?:close tab|new tab)$", "keyboard"),
        (r"^take (?:a )?screenshot$", "screenshot"),
    )
    for pattern, kind in action_patterns:
        if re.fullmatch(pattern, text):
            details = {"key": "ctrl+w" if text == "close tab" else "ctrl+t"} if kind == "keyboard" else {}
            return _action_message(kind), make_action(kind, **details)

    return None, None


def _action_message(kind: str) -> str:
    messages = {
        "lock": "Lock your laptop?",
        "sleep": "Put your laptop to sleep?",
        "shutdown": "Shut down your laptop?",
        "restart": "Restart your laptop?",
        "close_chrome": "Close Chrome on your laptop?",
        "close_code": "Close Visual Studio Code on your laptop?",
        "open_chrome": "Open Chrome on your laptop?",
        "open_code": "Open Visual Studio Code on your laptop?",
        "refresh": "Refresh the active page on your laptop?",
        "keyboard": "Perform this keyboard shortcut on your laptop?",
        "screenshot": "Take and save a screenshot on your laptop?",
    }
    return messages.get(kind, "Run this action on your laptop?")


def queue_action(action: dict) -> str:
    action_id = str(uuid.uuid4())
    now = datetime.now().astimezone().isoformat()
    with db_lock, get_db() as connection:
        connection.execute(
            "INSERT INTO actions (id, action_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (action_id, json.dumps(action), "pending_confirmation", now, now),
        )
    return action_id


def weather_report(city: str) -> str:
    return get_weather(city)


def ai_reply(messages: list[dict[str, str]], message: str) -> str:
    conversation = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *messages[-20:],
        {"role": "user", "content": message},
    ]
    if OPENROUTER_API_KEY:
        app.logger.info(
            "AI provider request provider=openrouter api_key_configured=%s",
            bool(OPENROUTER_API_KEY),
        )
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "X-Title": "Vidmahi",
        }
        if OPENROUTER_SITE_URL:
            headers["HTTP-Referer"] = OPENROUTER_SITE_URL
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json={"model": OPENROUTER_MODEL, "messages": conversation},
            timeout=(5, 45),
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        answer = (
            choices[0].get("message", {}).get("content")
            if choices and isinstance(choices[0], dict)
            else None
        )
    elif OPENAI_API_KEY:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(model=MODEL, messages=conversation)
        answer = response.choices[0].message.content
    else:
        raise RuntimeError(
            "No AI provider is configured. Set OPENROUTER_API_KEY or OPENAI_API_KEY "
            "in the server environment."
        )
    if not answer:
        raise RuntimeError("The AI provider returned an empty response.")
    return answer


@app.get("/")
def home():
    return render_template("index.html")


@app.after_request
def set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "style-src 'self'; script-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'",
    )
    return response


@app.get("/manifest.webmanifest")
def manifest():
    response = app.send_static_file("manifest.webmanifest")
    response.mimetype = "application/manifest+json"
    return response


@app.get("/service-worker.js")
def service_worker():
    response = app.send_static_file("service-worker.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "agent_connected": agent_connected()})


@app.post("/api/auth")
def authenticate():
    supplied = request.get_json(silent=True) or {}
    if not ACCESS_TOKEN:
        return jsonify({"error": "Server access is not configured. Set VIDMAHI_ACCESS_TOKEN."}), 503
    if not token_matches(ACCESS_TOKEN, str(supplied.get("token", ""))):
        return jsonify({"error": "That access token is not valid."}), 401
    return jsonify({"ok": True})


@app.post("/api/chat")
def chat():
    if not require_user():
        return jsonify({"error": "Enter your access token to use Vidmahi."}), 401
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", "")).strip()
    if not message or len(message) > 4000:
        return jsonify({"error": "Message must contain between 1 and 4000 characters."}), 400

    spoken_answer, action = interpret_command(message)
    if action and action["kind"] == "riddle":
        return jsonify({"reply": spoken_answer, "puzzle_answer": action["answer"]})

    puzzle_answer = str(body.get("puzzle_answer", "")).strip().lower()
    if puzzle_answer:
        cleaned_message = re.sub(r"[^a-z0-9 ]", "", message.lower()).strip()
        cleaned_answer = re.sub(r"[^a-z0-9 ]", "", puzzle_answer).strip()
        result = "That's right!" if cleaned_message == cleaned_answer else f"Not quite. The answer was {puzzle_answer}."
        return jsonify({"reply": result, "clear_puzzle": True})

    if action and action["kind"] == "weather":
        try:
            return jsonify({"reply": weather_report(action["city"])})
        except (requests.RequestException, KeyError, ValueError, RuntimeError):
            app.logger.exception("Weather request failed")
            return jsonify({"error": "The weather service could not provide an answer. Please try again."}), 502

    if action and action["kind"] == "battery":
        action_id = queue_action(action)
        return jsonify({
            "reply": "Checking the laptop battery. Please confirm the request.",
            "action": {"id": action_id, "status": "pending_confirmation"},
        })

    if action:
        action_id = queue_action(action)
        return jsonify({
            "reply": spoken_answer or _action_message(action["kind"]),
            "action": {"id": action_id, "status": "pending_confirmation"},
        })

    if not OPENROUTER_API_KEY and not OPENAI_API_KEY:
        return jsonify({
            "reply": (
                "AI chat is unavailable because no AI provider is configured. "
                "You can still ask for the time or date, check the weather, play a riddle, "
                "search the web, or use supported laptop commands."
            )
        })

    try:
        history = body.get("history", [])
        if not isinstance(history, list):
            history = []
        safe_history = [
            {"role": item["role"], "content": item["content"][:4000]}
            for item in history
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
            and isinstance(item.get("content"), str)
        ][-20:]
        reply = ai_reply(safe_history, message)
        return jsonify({"reply": reply})
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else None
        if status == 429:
            app.logger.warning("AI provider rate limit reached")
            return jsonify({"error": "The AI provider is rate limiting requests. Please wait and try again."}), 429
        app.logger.exception("AI provider returned an HTTP error")
        return jsonify({"error": "The AI service could not answer. Check the server provider configuration and try again."}), 502
    except Exception:
        app.logger.exception("AI response failed")
        return jsonify({"error": "The AI service could not answer. Check the server provider configuration and try again."}), 502


@app.post("/api/actions/<action_id>/confirm")
def confirm_action(action_id: str):
    if not require_user():
        return jsonify({"error": "Unauthorized."}), 401
    now = datetime.now().astimezone().isoformat()
    with db_lock, get_db() as connection:
        cursor = connection.execute(
            "UPDATE actions SET status = 'queued', updated_at = ? "
            "WHERE id = ? AND status = 'pending_confirmation'",
            (now, action_id),
        )
    if cursor.rowcount != 1:
        return jsonify({"error": "This action is no longer awaiting confirmation."}), 404
    return jsonify({"ok": True, "status": "queued"})


@app.post("/api/actions/<action_id>/cancel")
def cancel_action(action_id: str):
    if not require_user():
        return jsonify({"error": "Unauthorized."}), 401
    now = datetime.now().astimezone().isoformat()
    with db_lock, get_db() as connection:
        cursor = connection.execute(
            "UPDATE actions SET status = 'cancelled', updated_at = ? "
            "WHERE id = ? AND status IN ('pending_confirmation', 'queued')",
            (now, action_id),
        )
    if cursor.rowcount != 1:
        return jsonify({"error": "This action can no longer be cancelled."}), 404
    return jsonify({"ok": True, "status": "cancelled"})


@app.get("/api/actions/<action_id>")
def action_status(action_id: str):
    if not require_user():
        return jsonify({"error": "Unauthorized."}), 401
    with get_db() as connection:
        row = connection.execute(
            "SELECT status, result FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
    if row is None:
        return jsonify({"error": "Action not found."}), 404
    return jsonify({"status": row["status"], "result": row["result"]})


def agent_connected() -> bool:
    with get_db() as connection:
        row = connection.execute("SELECT last_seen FROM agent_state WHERE id = 1").fetchone()
    if not row:
        return False
    try:
        last_seen = datetime.fromisoformat(row["last_seen"])
        return (datetime.now().astimezone() - last_seen).total_seconds() < 15
    except ValueError:
        return False


@app.get("/api/agent/next")
def agent_next():
    if not require_agent():
        return jsonify({"error": "Unauthorized agent."}), 401
    now = datetime.now().astimezone().isoformat()
    with db_lock, get_db() as connection:
        connection.execute(
            "INSERT INTO agent_state (id, last_seen) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_seen = excluded.last_seen",
            (now,),
        )
        row = connection.execute(
            "SELECT id, action_json FROM actions WHERE status = 'queued' "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return jsonify({"action": None})
        connection.execute(
            "UPDATE actions SET status = 'running', updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
    return jsonify({"action": {"id": row["id"], **json.loads(row["action_json"])}})


@app.post("/api/agent/actions/<action_id>/result")
def agent_result(action_id: str):
    if not require_agent():
        return jsonify({"error": "Unauthorized agent."}), 401
    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status not in {"completed", "failed"}:
        return jsonify({"error": "Invalid action result."}), 400
    result = str(body.get("result", ""))[:1000]
    now = datetime.now().astimezone().isoformat()
    with db_lock, get_db() as connection:
        cursor = connection.execute(
            "UPDATE actions SET status = ?, result = ?, updated_at = ? "
            "WHERE id = ? AND status = 'running'",
            (status, result, now, action_id),
        )
    if cursor.rowcount != 1:
        return jsonify({"error": "Action not found or no longer running."}), 404
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
