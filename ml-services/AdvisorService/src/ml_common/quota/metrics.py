"""Prometheus text exposition without extra dependencies."""
from __future__ import annotations


def render_metrics(ledger, health, quota, provider_names: list[str], reroutes: int = 0) -> str:
    lines = ["# TYPE advisor_requests_total counter", "# TYPE advisor_tokens_total counter",
             "# TYPE advisor_errors_total counter", "# TYPE advisor_reroutes_total counter",
             "# TYPE advisor_answers_left gauge"]
    for row in ledger.aggregate():
        lab = f'provider="{row["provider"]}",agent="{row["agent"]}"'
        lines.append(f"advisor_requests_total{{{lab}}} {row['requests']}")
        lines.append(f'advisor_tokens_total{{{lab},dir="in"}} {row["tokens_in"]}')
        lines.append(f'advisor_tokens_total{{{lab},dir="out"}} {row["tokens_out"]}')
        lines.append(f"advisor_errors_total{{{lab}}} {row['errors']}")
    lines.append(f"advisor_reroutes_total {reroutes}")
    for p, info in quota.report(provider_names).items():
        if info["answers_left_estimate"] is not None:
            lines.append(f'advisor_answers_left{{provider="{p}"}} {info["answers_left_estimate"]}')
    return "\n".join(lines) + "\n"
