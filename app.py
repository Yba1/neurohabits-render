from __future__ import annotations

import base64
import json
import os
import random
import re
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__, template_folder=".", static_folder=".")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret")

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = Path(os.getenv("DATA_FILE_PATH", str(BASE_DIR / "data.json")))
FEATHERLESS_URL = "https://api.featherless.ai/v1/chat/completions"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _default_data() -> dict[str, Any]:
    return {
        "users": {},
        "verification_codes": {},
        "habits_by_user": {},
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


def _load_data() -> dict[str, Any]:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not DATA_FILE.exists():
        data = _default_data()
        _save_data(data)
        return data

    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = _default_data()
        _save_data(data)
        return data

    # Migration from older global habits/history schema.
    if "habits_by_user" not in data:
        migrated = _default_data()
        migrated["users"] = data.get("users", {})
        migrated["verification_codes"] = data.get("verification_codes", {})

        old_habits = data.get("habits", [])
        old_history = data.get("history", [])
        if old_habits or old_history:
            migrated["habits_by_user"]["legacy@local"] = {
                "habits": old_habits,
                "history": old_history,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }

        data = migrated
        _save_data(data)

    data.setdefault("users", {})
    data.setdefault("verification_codes", {})
    data.setdefault("habits_by_user", {})
    return data


def _save_data(data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _current_user_email() -> str | None:
    email = session.get("user_email")
    if isinstance(email, str):
        return _normalize_email(email)
    return None


def _require_auth() -> tuple[str | None, Any | None]:
    email = _current_user_email()
    if not email:
        return None, (jsonify({"error": "Authentication required."}), 401)
    return email, None


def _get_user_bucket(data: dict[str, Any], email: str) -> dict[str, Any]:
    bucket = data["habits_by_user"].get(email)
    if not bucket:
        bucket = {
            "habits": [],
            "history": [],
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        data["habits_by_user"][email] = bucket

    bucket.setdefault("habits", [])
    bucket.setdefault("history", [])
    return bucket


def _get_user_data(email: str) -> dict[str, Any]:
    data = _load_data()
    bucket = _get_user_bucket(data, email)
    # Persist in case bucket was created.
    _save_data(data)
    return bucket


def _send_verification_email(email: str, code: str) -> tuple[bool, str]:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "")

    if not smtp_host or not smtp_user or not smtp_pass or not smtp_from:
        return False, "SMTP is not configured on the server."

    msg = EmailMessage()
    msg["Subject"] = "Your NeuroHabit verification code"
    msg["From"] = smtp_from
    msg["To"] = email
    msg.set_content(
        f"Your NeuroHabit verification code is: {code}\n\n"
        "This code expires in 10 minutes."
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        return False, f"Failed to send email: {exc}"

    return True, "Verification code sent."


def _generate_insight(user_data: dict[str, Any]) -> str:
    habits = user_data.get("habits", [])
    if not habits:
        return "Start by adding 2-3 habits you can realistically do daily."

    completed = [h for h in habits if h.get("done")]
    completion_rate = (len(completed) / len(habits)) * 100

    if completion_rate >= 80:
        return (
            "Strong consistency today. Keep the same routine tomorrow and add one "
            "short habit only if it stays manageable."
        )
    if completion_rate >= 50:
        return (
            "You are building momentum. Pick one missed habit and make it easier "
            "(shorter or earlier in the day) to improve consistency."
        )
    return (
        "Too many misses today. Focus on one priority habit tomorrow and set a "
        "specific start time."
    )


def _build_habit_summary(habits: list[dict[str, Any]]) -> str:
    if not habits:
        return "No habits logged yet."
    lines = []
    for habit in habits:
        state = "done" if habit.get("done") else "missed"
        lines.append(f"- {habit.get('name', 'Unnamed')}: {state}")
    return "\n".join(lines)


def _generate_ai_insight(user_data: dict[str, Any]) -> str:
    api_key = os.getenv("FEATHERLESS_API_KEY")
    model_id = os.getenv("FEATHERLESS_MODEL", "deepseek-ai/DeepSeek-V3-0324")
    if not api_key:
        return _generate_insight(user_data)

    habits = user_data.get("habits", [])
    history = user_data.get("history", [])[-7:]
    habit_summary = _build_habit_summary(habits)
    history_summary = json.dumps(history)

    prompt = (
        "You are a concise habit coach for students. "
        "Write 2-3 sentences with one positive observation and one specific "
        "action for tomorrow.\n\n"
        f"Habits today:\n{habit_summary}\n\n"
        f"Recent history: {history_summary}"
    )

    try:
        response = requests.post(
            FEATHERLESS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
            },
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"].strip()
        return content or _generate_insight(user_data)
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return _generate_insight(user_data)


def _text_to_speech(text: str) -> str | None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
    if not api_key:
        return None

    try:
        response = requests.post(
            ELEVENLABS_URL.format(voice_id=voice_id),
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
            },
            timeout=30,
        )
        response.raise_for_status()
        encoded = base64.b64encode(response.content).decode("utf-8")
        return f"data:audio/mpeg;base64,{encoded}"
    except requests.RequestException:
        return None


def _normalize_habit(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = str(raw.get("name") or raw.get("text") or "").strip()
    if not name:
        return None

    done = bool(raw.get("done", raw.get("completed", False)))

    target_value = raw.get("targetValue", raw.get("target"))
    current_value = raw.get("currentValue", raw.get("value"))
    time_window = raw.get("timeWindow", raw.get("time"))

    return {
        "name": name,
        "text": name,
        "done": done,
        "completed": done,
        "type": str(raw.get("type", "static")),
        "target": target_value,
        "targetValue": target_value,
        "unit": raw.get("unit"),
        "value": current_value,
        "currentValue": current_value,
        "timeWindow": time_window,
        "category": str(raw.get("category", "General") or "General"),
        "deferredUntil": raw.get("deferredUntil"),
        "created": raw.get("created") or datetime.utcnow().isoformat() + "Z",
        "lastUpdated": raw.get("lastUpdated") or datetime.utcnow().isoformat() + "Z",
        "streak": int(raw.get("streak", 0) or 0),
    }


def _save_habits_list(email: str, incoming_habits: list[Any]) -> dict[str, Any]:
    normalized_habits: list[dict[str, Any]] = []
    for raw in incoming_habits:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_habit(raw)
        if normalized:
            normalized_habits.append(normalized)

    data = _load_data()
    bucket = _get_user_bucket(data, email)
    bucket["habits"] = normalized_habits
    bucket.setdefault("history", []).append(
        {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "completed": sum(1 for h in normalized_habits if h["done"]),
            "total": len(normalized_habits),
        }
    )
    bucket["updated_at"] = datetime.utcnow().isoformat() + "Z"

    _save_data(data)
    return bucket


@app.get("/")
def home() -> str:
    return render_template("index.html")


@app.get("/dashboard")
def dashboard() -> str:
    return render_template("dashboard.html")


@app.get("/dashboard.html")
def dashboard_html() -> Any:
    return send_from_directory(".", "dashboard.html")


@app.get("/style.css")
def serve_style() -> Any:
    return send_from_directory(".", "style.css")


@app.get("/script.js")
def serve_script() -> Any:
    return send_from_directory(".", "script.js")


@app.get("/login.html")
def login_page() -> Any:
    return send_from_directory(".", "1login.html")


@app.get("/index.html")
def index_page() -> Any:
    return send_from_directory(".", "index.html")


@app.post("/api/auth/register")
def auth_register() -> Any:
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(str(payload.get("email", "")))
    password = str(payload.get("password", ""))
    username = str(payload.get("username", "")).strip()

    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Enter a valid email address."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400
    if len(username) < 2 or len(username) > 32:
        return jsonify({"error": "Username must be 2-32 characters."}), 400

    data = _load_data()
    existing = data.setdefault("users", {}).get(email)
    if existing and existing.get("verified"):
        return jsonify({"error": "Account already exists. Please sign in."}), 400

    created_at = datetime.utcnow().isoformat() + "Z"
    if isinstance(existing, dict) and existing.get("created_at"):
        created_at = existing["created_at"]

    data["users"][email] = {
        "email": email,
        "password_hash": generate_password_hash(password),
        "verified": False,
        "username": username,
        "created_at": created_at,
    }

    # Ensure habit bucket exists so synced data always has a home.
    _get_user_bucket(data, email)

    code = f"{random.randint(0, 999999):06d}"
    expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat() + "Z"
    data.setdefault("verification_codes", {})[email] = {
        "code": code,
        "expires_at": expires_at,
        "purpose": "signup",
        "attempts": 0,
    }
    _save_data(data)

    ok, message = _send_verification_email(email, code)
    if not ok:
        return jsonify({"error": message}), 500

    return jsonify({"message": message, "username": username})


@app.post("/api/auth/send-code")
def auth_send_code_compat() -> Any:
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(str(payload.get("email", "")))
    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Enter a valid email address."}), 400

    data = _load_data()
    if email not in data.get("users", {}):
        return jsonify({"error": "No account found. Please sign up first."}), 400

    code = f"{random.randint(0, 999999):06d}"
    expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat() + "Z"
    data.setdefault("verification_codes", {})[email] = {
        "code": code,
        "expires_at": expires_at,
        "purpose": "signup",
        "attempts": 0,
    }
    _save_data(data)

    ok, message = _send_verification_email(email, code)
    if not ok:
        return jsonify({"error": message}), 500
    return jsonify({"message": message})


@app.post("/api/auth/verify-code")
def auth_verify_code() -> Any:
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(str(payload.get("email", "")))
    code = str(payload.get("code", "")).strip()

    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Enter a valid email address."}), 400

    data = _load_data()
    stored = data.get("verification_codes", {}).get(email)
    if not stored:
        return jsonify({"error": "No code found. Request a new one."}), 400

    exp = _parse_iso(str(stored.get("expires_at", "")))
    if not exp or datetime.utcnow() > exp:
        data["verification_codes"].pop(email, None)
        _save_data(data)
        return jsonify({"error": "Verification code expired. Request a new code."}), 400

    if code != str(stored.get("code", "")):
        stored["attempts"] = int(stored.get("attempts", 0)) + 1
        _save_data(data)
        return jsonify({"error": "Invalid verification code."}), 400

    user = data.setdefault("users", {}).get(email)
    if not user:
        return jsonify({"error": "Account not found. Please sign up again."}), 400

    user["verified"] = True
    data["verification_codes"].pop(email, None)
    _save_data(data)

    session["user_email"] = email
    return jsonify({"message": "Email verified. Logged in.", "email": email})


@app.post("/api/auth/login")
def auth_login() -> Any:
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(str(payload.get("email", "")))
    password = str(payload.get("password", ""))

    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Enter a valid email address."}), 400

    data = _load_data()
    user = data.get("users", {}).get(email)
    if not user:
        return jsonify({"error": "Account not found. Please sign up."}), 400
    if not user.get("verified"):
        return jsonify({"error": "Please verify your email first."}), 400

    if not check_password_hash(str(user.get("password_hash", "")), password):
        return jsonify({"error": "Invalid email or password."}), 401

    session["user_email"] = email
    return jsonify({"message": "Login successful.", "email": email})


@app.get("/api/auth/me")
def auth_me() -> Any:
    email = _current_user_email()
    if not email:
        return jsonify({"authenticated": False}), 401

    data = _load_data()
    user = data.get("users", {}).get(email, {})
    username = str(user.get("username", "")).strip()
    if not username:
        username = email.split("@", 1)[0].title()

    return jsonify({"authenticated": True, "email": email, "username": username})


@app.post("/api/auth/logout")
def auth_logout() -> Any:
    session.pop("user_email", None)
    return jsonify({"message": "Logged out."})


@app.get("/api/habits")
def get_habits() -> Any:
    email, auth_error = _require_auth()
    if auth_error:
        return auth_error
    return jsonify(_get_user_data(email))


@app.post("/api/habits")
def save_habits() -> Any:
    email, auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    habits = payload.get("habits")
    if not isinstance(habits, list):
        return jsonify({"error": "Expected 'habits' to be a list."}), 400

    user_data = _save_habits_list(email, habits)
    return jsonify(
        {
            "message": "Habits saved successfully.",
            "insight": _generate_insight(user_data),
            "data": user_data,
        }
    )


@app.post("/save_habits")
def save_habits_compat() -> Any:
    email, auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True)
    if isinstance(payload, list):
        habits = payload
    else:
        habits = (payload or {}).get("habits")

    if not isinstance(habits, list):
        return jsonify({"error": "Expected a habits list."}), 400

    user_data = _save_habits_list(email, habits)
    return jsonify(
        {
            "message": "Habits saved!",
            "insight": _generate_insight(user_data),
            "habits": user_data["habits"],
        }
    )


@app.get("/get_habits")
def get_habits_compat() -> Any:
    email, auth_error = _require_auth()
    if auth_error:
        return auth_error
    user_data = _get_user_data(email)
    return jsonify({
        "habits": user_data.get("habits", []),
        "history": user_data.get("history", []),
    })


@app.get("/api/insight")
def get_insight() -> Any:
    email, auth_error = _require_auth()
    if auth_error:
        return auth_error
    user_data = _get_user_data(email)
    return jsonify({"insight": _generate_insight(user_data)})


@app.post("/api/ai-insight")
def get_ai_insight() -> Any:
    email, auth_error = _require_auth()
    if auth_error:
        return auth_error
    user_data = _get_user_data(email)
    return jsonify({"insight": _generate_ai_insight(user_data)})


@app.post("/api/voice-insight")
def get_voice_insight() -> Any:
    email, auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    if not text:
        user_data = _get_user_data(email)
        text = _generate_ai_insight(user_data)

    audio_data_url = _text_to_speech(text)
    if not audio_data_url:
        return jsonify(
            {
                "error": (
                    "Voice generation unavailable. Set ELEVENLABS_API_KEY and "
                    "ELEVENLABS_VOICE_ID."
                ),
                "text": text,
            }
        ), 503

    return jsonify({"text": text, "audio_data_url": audio_data_url})


@app.post("/api/habit-insight")
def get_habit_insight() -> Any:
    _, auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    habit = payload.get("habit", {})
    question = str(payload.get("question", "")).strip()
    history = payload.get("history", {})

    api_key = os.getenv("FEATHERLESS_API_KEY")
    model_id = os.getenv("FEATHERLESS_MODEL", "deepseek-ai/DeepSeek-V3-0324")
    if not api_key:
        return jsonify({"response": "AI unavailable - no API key set."})

    progress = ""
    if habit.get("type") == "dynamic":
        cur = habit.get("currentValue", 0)
        tgt = habit.get("targetValue", 1) or 1
        unit = habit.get("unit", "")
        progress = f"Current progress: {cur}/{tgt} {unit} ({round((cur / tgt) * 100)}%)"

    prompt = (
        f"You are a concise habit coach. The user has a habit called '{habit.get('text')}'.\n"
        f"Type: {habit.get('type', 'static')}. Streak: {habit.get('streak', 0)} days. {progress}\n"
        f"Weekly history: {json.dumps(history)}\n\n"
        f"User question: {question}\n\n"
        "Answer in 2-3 sentences. Be specific, practical, and encouraging."
    )

    try:
        response = requests.post(
            FEATHERLESS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
            },
            timeout=25,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        return jsonify({"response": content})
    except Exception:
        return jsonify({"response": "Couldn't reach AI right now. Try again shortly."})


@app.post("/api/weekly-review")
def get_weekly_review() -> Any:
    _, auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    habits = payload.get("habits", [])
    history = payload.get("history", {})

    api_key = os.getenv("FEATHERLESS_API_KEY")
    model_id = os.getenv("FEATHERLESS_MODEL", "deepseek-ai/DeepSeek-V3-0324")
    if not api_key:
        return jsonify({"review": "Set FEATHERLESS_API_KEY to enable weekly reviews."})

    habit_lines = "\n".join([f"- {h.get('text')}: {h.get('streak', 0)} day streak" for h in habits])
    history_lines = "\n".join([f"  {date}: {pct}%" for date, pct in list(history.items())[-7:]])

    prompt = (
        "You are a weekly habit coach giving a Sunday debrief. Be direct, warm, and specific.\n\n"
        f"User's habits:\n{habit_lines}\n\n"
        f"Completion % last 7 days:\n{history_lines}\n\n"
        "Write 3-4 sentences: one observation about their best habit, "
        "one thing to improve next week, and one motivational closer. No bullet points."
    )

    try:
        response = requests.post(
            FEATHERLESS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
            },
            timeout=25,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        return jsonify({"review": content})
    except Exception:
        return jsonify({"review": "Couldn't load weekly review right now."})



def _heuristic_category(habit_name: str) -> str:
    t = habit_name.lower()
    if any(k in t for k in ["read", "study", "homework", "learn", "flashcard", "class"]):
        return "Study"
    if any(k in t for k in ["gym", "run", "workout", "exercise", "walk", "steps", "protein", "sleep"]):
        return "Health"
    if any(k in t for k in ["code", "project", "build", "debug", "ship"]):
        return "Build"
    if any(k in t for k in ["journal", "meditate", "mind", "reflect"]):
        return "Mindset"
    return "General"


def _category_suggestions(habits: list[dict[str, Any]], existing: list[str] | None = None) -> dict[str, Any]:
    names = [str(h.get("text") or h.get("name") or "").strip() for h in habits]
    names = [n for n in names if n]
    if not names:
        return {"categories": existing or ["General"], "assignments": []}

    api_key = os.getenv("FEATHERLESS_API_KEY")
    model_id = os.getenv("FEATHERLESS_MODEL", "deepseek-ai/DeepSeek-V3-0324")

    if api_key and len(names) >= 5:
        prompt = (
            "Group these habits into practical categories. "
            "Return ONLY valid JSON with this schema: "
            "{\"categories\":[\"Study\"],\"assignments\":[{\"habit\":\"Read 20 pages\",\"category\":\"Study\"}]}. "
            "Use short category names and assign every habit exactly once.\n\n"
            f"Habits: {json.dumps(names)}\n"
            f"Existing categories: {json.dumps(existing or [])}"
        )
        try:
            response = requests.post(
                FEATHERLESS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=25,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            categories = [str(c).strip() for c in parsed.get("categories", []) if str(c).strip()]
            assignments = []
            for a in parsed.get("assignments", []):
                habit = str(a.get("habit", "")).strip()
                category = str(a.get("category", "")).strip()
                if habit and category:
                    assignments.append({"habit": habit, "category": category})
            if categories and assignments:
                return {"categories": categories, "assignments": assignments}
        except Exception:
            pass

    assignments = [{"habit": n, "category": _heuristic_category(n)} for n in names]
    categories = sorted({a["category"] for a in assignments} | set(existing or []) | {"General"})
    return {"categories": categories, "assignments": assignments}


@app.post("/api/category-suggestions")
def api_category_suggestions() -> Any:
    _, auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    habits = payload.get("habits", [])
    existing = payload.get("categories", [])
    if not isinstance(habits, list):
        habits = []
    if not isinstance(existing, list):
        existing = []

    result = _category_suggestions(habits, existing)
    return jsonify(result)
@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)







