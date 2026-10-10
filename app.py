"""Flask server: serves the dashboard and the diagnosis API.

Run:  python backend/app.py   then open http://127.0.0.1:5000
"""
import os

from flask import Flask, jsonify, request, send_from_directory

import checks
import database
import diagnose

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")

SYMPTOMS = {
    "slow": ["slow", "lag", "buffer", "delay", "stutter"],
    "offline": ["no internet", "offline", "not connect", "disconnect",
                "can't connect", "cannot connect", "not working", "no network"],
    "web": ["website", "web page", "webpage", "browser", "not open", "won't open",
            "wont open", "not loading", "won't load"],
}


def detect_symptom(message):
    text = (message or "").lower()
    for symptom, words in SYMPTOMS.items():
        if any(w in text for w in words):
            return symptom
    return None


def build_reply(symptom, verdict):
    """Return (intro, note): a line shown above the verdict and an optional extra note."""
    intro = {
        "slow": "You mentioned slowness, so latency and packet loss were checked closely.",
        "offline": "You mentioned the connection is down, so each step from your device to the web was checked.",
        "web": "You mentioned a website problem, so the connection, DNS and web access were checked.",
        None: "Each step from your device to the web was checked.",
    }[symptom]
    note = ""
    if symptom == "slow" and verdict["severity"] == "ok":
        note = ("Your own connection looks fine. The slowness may come from the website "
                "itself, a busy Wi-Fi channel or other devices using bandwidth.")
    return intro, note


def run_and_store():
    result = diagnose.run_diagnosis()
    database.save_run(result)
    return result


@app.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/api/diagnose", methods=["GET", "POST"])
def api_diagnose():
    return jsonify(run_and_store())


@app.route("/api/ask", methods=["POST"])
def api_ask():
    message = (request.get_json(silent=True) or {}).get("message", "")
    result = run_and_store()
    result["intro"], result["note"] = build_reply(detect_symptom(message), result["verdict"])
    return jsonify(result)


@app.route("/api/traceroute")
def api_traceroute():
    host = diagnose.INTERNET_HOST
    return jsonify({"host": host, "lines": checks.traceroute(host)})


@app.route("/api/history")
def api_history():
    return jsonify(database.get_history(10))


if __name__ == "__main__":
    database.init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
