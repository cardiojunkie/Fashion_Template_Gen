# Development and deployment — 0.1.0-rc1

## Codespaces development

Use Python 3.12, install with `python -m pip install -e ".[dev]"`, and start with `./start.sh 8501`. Codespaces exposes the port through the Ports panel; keep it private. The health endpoint is `/_stcore/health` and must return `ok`.

## Linux production requirements

- Python 3.12 and the locked-compatible dependencies documented in `ENVIRONMENT.md`.
- A non-root service account, writable persistent directory for `FASHION_CMS_DB_PATH` and `data/artifacts`, and read-only application/config files.
- Server-side `NVIDIA_API_KEY`; never a browser/UI value. Allow outbound HTTPS only to the fixed
  `integrate.api.nvidia.com` runtime as required, and configure validated `FASHION_CMS_*` limits
  plus approved pricing/threshold files. Back up before migration and rotate the key in the
  deployment secret manager; see `docs/LLM_PROVIDERS.md`.
- An HTTPS reverse proxy with authentication, request-body/time limits, private-network egress denial, security headers, access-log redaction, and health checks.
- Monitoring for health, disk, database errors, failed/cancelled jobs, provider errors, call/cost limits, backup age, and dependency vulnerabilities.
- Scheduled verified backups and, only after retention approval, dry-run-reviewed cleanup.

Do not run Streamlit's development listener as an unauthenticated public service. Production host, authentication, resource sizing, storage, and monitoring providers remain user decisions, so no container or host-specific service file is included.

No private/local or operator-configured model endpoint is supported. Network egress policy must
still block metadata, private networks, redirects, and unapproved destinations.

## Upgrade

1. Stop new work and wait for active requests.
2. Back up SQLite and artifacts; verify the backup.
3. Install the release into a clean environment and run the complete release checklist against a temporary database copy.
4. Apply migrations with `python -m fashion_cms.database migrate PATH_TO_COPY` first, then the production path during the maintenance window.
5. Start privately, verify health/dashboard/offline workflow/export/history/release gates, then restore approved access.

Rollback uses `BACKUP_ROLLBACK.md`. Database schema compatibility is forward-only within this candidate; older code must not open a v6 database.
