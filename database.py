"""SQLite storage for diagnosis history (single file, no server needed)."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "troubleshooter.db")


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS diagnosis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                result TEXT,
                severity TEXT,
                failed_step TEXT,
                latency REAL,
                packet_loss REAL
            )
            """
        )


def save_run(result):
    verdict = result["verdict"]
    internet = next(s for s in result["steps"] if s["id"] == "internet")
    data = internet["data"]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO diagnosis_history "
            "(timestamp, result, severity, failed_step, latency, packet_loss) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                result["timestamp"],
                verdict["title"],
                verdict["severity"],
                verdict["failed_step"],
                data.get("avg"),
                data.get("loss"),
            ),
        )


def get_history(limit=10):
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT timestamp, result, severity, failed_step, latency, packet_loss "
            "FROM diagnosis_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
if __name__ == "__main__":
    init_db()
    print("===== Diagnosis History (last 10) =====")
    rows = get_history(10)
    if not rows:
        print("No records yet. Run a diagnosis first.")
    for r in rows:
        print(r["timestamp"], "|", r["result"], "|", r["latency"], "ms |", r["packet_loss"], "% loss")