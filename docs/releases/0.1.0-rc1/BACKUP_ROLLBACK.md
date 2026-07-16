# Backup, restore, upgrade, and rollback — 0.1.0-rc1

Default SQLite location is `data/fashion_cms.sqlite3`; override it with `FASHION_CMS_DB_PATH`. The parent directory and `data/artifacts` require persistent, private write access.

Create a consistent non-overwriting backup while the application is stopped or quiescent:

```bash
python -m fashion_cms.database backup data/fashion_cms.sqlite3 backups/fashion_cms-YYYYMMDD.sqlite3
python -m fashion_cms.database migrate backups/fashion_cms-YYYYMMDD.sqlite3
```

The backup uses SQLite's online backup API, writes a temporary file, runs `PRAGMA integrity_check`, then atomically renames it. An existing destination is never overwritten. Copy `data/artifacts` separately with filesystem metadata and access controls; do not place secrets in the backup set.

Restore only during a maintenance window: stop the app, preserve the failed database, copy the verified backup and matching artifacts into their configured paths, confirm ownership/permissions, run migration on a temporary copy, then start privately and smoke-test. Never reset or delete the current database to repair a migration.

For rollback, stop traffic, preserve logs and failed files, restore the pre-upgrade database/artifact snapshot, reinstall the last compatible application version, and execute `ROLLBACK_CHECKLIST.md`. A v6 database is not promised compatible with older code; restore the matching pre-upgrade backup instead of downgrading in place.

Schema v6 added provider configuration, route, capability, discovery-cache, and non-secret job
snapshot tables without deleting existing jobs. The fixed NVIDIA runtime preserves those rows as
inert audit history and does not read secrets from them. Rotate `NVIDIA_API_KEY` in the external
secret manager; never put a plaintext key in a database backup.
