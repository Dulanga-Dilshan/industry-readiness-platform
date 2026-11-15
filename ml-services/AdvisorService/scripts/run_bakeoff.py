"""Run the evaluation set once per provider and print a comparison table.

Needs the advisor running (docker compose up -d advisor) with real provider keys.
    uv run python scripts/run_bakeoff.py
Edit the constants below. The eval spends real quota: DELAY_S spaces the questions out.
"""
import time

import httpx

BASE = "http://127.0.0.1:8001/advisor"
SERVICE_SECRET = "put-your-SERVICE_SECRET-here"
ADMIN_KEY = "put-your-ADMIN_KEY-here"
PROVIDERS = ["groq-gpt-oss-20b", "gemini-flash-lite", "openrouter-free"]   # add local ones after Ollama is set up
DELAY_S = 2.0

H = {"X-Service-Secret": SERVICE_SECRET, "X-Admin-Key": ADMIN_KEY}
rows = []
with httpx.Client(headers=H, timeout=30) as c:
    for p in PROVIDERS:
        run = c.post(f"{BASE}/eval/run", json={"provider": p, "delay": DELAY_S}).json()["run_id"]
        while True:
            r = c.get(f"{BASE}/eval/runs/{run}").json()
            if r["state"] == "done":
                break
            time.sleep(5)
        s = r["summary"]
        avg = sum(x["seconds"] for x in r["results"]) / max(1, len(r["results"]))
        tokens = sum(t["tokens_in"] + t["tokens_out"] for t in s["tokens"] if t["provider"] == p)
        rows.append((p, f"{s['passed']}/{s['total']}", round(avg, 1), tokens, s["by_category"]))
print(f"{'provider':24} {'passed':8} {'avg s':7} {'tokens':8}")
for p, passed, avg, tokens, cats in rows:
    print(f"{p:24} {passed:8} {avg:<7} {tokens:<8}")
    print("   ", cats)
