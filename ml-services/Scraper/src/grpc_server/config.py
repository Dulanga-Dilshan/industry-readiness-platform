"""Server settings. Override with environment variables, no code change needed."""
import os

GRPC_HOST = os.getenv("GRPC_HOST", "0.0.0.0")
GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))
MAX_MESSAGE_MB = int(os.getenv("MAX_MESSAGE_MB", "16"))

# The REST test API runs in the same process. Turn it off in production.
ENABLE_REST = os.getenv("ENABLE_REST", "1") == "1"
REST_HOST = os.getenv("REST_HOST", "127.0.0.1")   # local only; it is a testing tool
REST_PORT = int(os.getenv("REST_PORT", "8000"))

# Per-source options for the 24-hour run: it only needs recent postings.
# For a one-off backfill, pass larger values through scrape_latest(options=...).
LATEST_OPTIONS = {
    "linkedin": {"max_age_days": 3},
    "itpro": {"all_pages": False},
}
