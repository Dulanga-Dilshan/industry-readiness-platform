# Backend Project Structure

```text
D:\industry-readiness-platform\backend
├── .env                          # Local environment variables (gitignored)
├── .env.example                  # Template of required env variables
├── .gitignore
├── .python-version               # Python version managed by uv (e.g., 3.12)
├── pyproject.toml                # uv dependencies, build targets, & tool configs
├── uv.lock                       # Locked dependency versions (auto-generated)
├── README.md
├── alembic.ini                   # Alembic migration config (if using SQL DB)
├── alembic/                      # Database migration scripts
│   ├── env.py
│   └── versions/
├── tests/                        # Pytest suite mirroring src/
│   ├── __init__.py
│   ├── conftest.py               # Shared test fixtures (AsyncClient, test DB)
│   ├── api/
│   │   └── v1/
│   │       └── test_users.py
│   └── services/
│       └── test_user_service.py
└── src/
    ├── main.py                   # FastAPI entrypoint (app creation & lifespan)
    ├── api/                      # HTTP routing layer
    │   ├── __init__.py
    │   ├── deps.py               # Shared FastAPI dependencies (DB session, auth)
    │   └── v1/
    │       ├── __init__.py
    │       ├── router.py         # Combines all v1 endpoint routers
    │       └── endpoints/
    │           ├── __init__.py
    │           ├── health.py     # Health check & readiness probes
    │           ├── auth.py       # Login, token refresh, registration
    │           └── users.py      # User CRUD routes
    ├── core/                     # App-wide configuration & infrastructure
    │   ├── __init__.py
    │   ├── config.py             # Pydantic BaseSettings (.env validation)
    │   ├── database.py           # DB engine, session factory, Base class
    │   ├── security.py           # JWT creation/verification & password hashing
    │   └── exceptions.py         # Custom exception classes & global handlers
    ├── models/                   # Database ORM models (SQLAlchemy / SQLModel)
    │   ├── __init__.py
    │   └── user.py
    ├── schemas/                  # Pydantic request/response models (DTOs)
    │   ├── __init__.py
    │   ├── token.py
    │   └── user.py
    ├── services/                 # Core business logic & external integrations
    │   ├── __init__.py
    │   ├── user_service.py
    │   └── grpc_client.py        # Client to talk to your grpc_server service
    └── utils/                    # Shared helper utilities
        ├── __init__.py
        └── logger.py