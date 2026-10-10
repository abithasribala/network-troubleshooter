# Network Troubleshooter

A web tool that checks a computer's network connection step by step and tells the user where the problem is and how to fix it.

## How it works

The checks run in the order a technician would use:

| Step | Check | How |
|---|---|---|
| 1 | Network adapter and IP address | `psutil` |
| 2 | Router (default gateway) | `ping` |
| 3 | Internet reachability | `ping 8.8.8.8` |
| 4 | DNS lookup | `socket.gethostbyname("google.com")` |
| 5 | Web access | HTTPS request to google.com |
| 6 | Connection quality | latency and packet loss from step 3 |

A rule-based engine (`backend/diagnose.py`) reads the results and names the problem:
no adapter or IP, router problem, ISP problem, DNS problem, blocked web access, high packet loss or high latency.
Each diagnosis comes with fixes and copyable commands. Every run is saved in SQLite.

## Run on Windows

```
cd network-troubleshooter
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python backend\app.py
```

Open http://127.0.0.1:5000 in the browser.

## Folders

```
backend/
  checks.py      ping, DNS, HTTP, port, traceroute, adapter checks
  diagnose.py    rule-based diagnosis
  database.py    SQLite history
  app.py         Flask server and API
frontend/
  index.html, style.css, script.js
data/            troubleshooter.db is created here on first run
```

## API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Dashboard |
| POST | `/api/ask` | Run all checks; body `{"message": "internet is slow"}` |
| GET | `/api/diagnose` | Run all checks |
| GET | `/api/traceroute` | Trace the route to 8.8.8.8 |
| GET | `/api/history` | Last 10 diagnoses |

## Limits

- Ping and route output is read in English format (Windows language matters).
- Some routers ignore ping; the tool treats that as a warning when the internet still works.
- Thresholds (100 ms, 200 ms, 20%, 50%) are fixed and set at the top of `diagnose.py`.
