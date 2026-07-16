# Dependency and environment summary — 0.1.0-rc1

- Verified: Python 3.12.1 on Linux x86_64, `start.sh` mode 755.
- Runtime: cryptography 47.0.0, httpx 0.28.1, openpyxl 3.1.5, Pillow 12.3.0, Pydantic 2.13.4, Streamlit 1.59.2.
- Development verification: pytest 8.4.2 and Ruff 0.15.21.
- Supported dependency ranges remain authoritative in `pyproject.toml`; `python -m pip check` must pass after installation.
- Legacy live variables: `OPENAI_API_KEY`, `OPENAI_MODEL`, and optional `OPENAI_IMAGE_DETAIL`.
  Custom providers use session memory, an environment reference, or guarded encrypted storage;
  see `.env.example` and `docs/LLM_PROVIDERS.md`. Fake/offline mode needs no secret.
- Writable paths: configured SQLite parent and `data/artifacts`. Registry, thresholds, pricing, application code, and release artifacts should be read-only to the service account.
- No formatter, type checker, security scanner, container target, host, authentication package, or external queue is configured.
