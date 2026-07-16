# Rollback checklist — 0.1.0-rc1

- [ ] Stop new jobs and access; record the incident/change identifier.
- [ ] Preserve the current database, artifacts, sanitized logs, version manifest, and release-gate report.
- [ ] Confirm the target application version and matching pre-upgrade backup.
- [ ] Verify backup integrity on a copy; never downgrade the live SQLite schema in place.
- [ ] Restore database and matching artifacts with private ownership/permissions.
- [ ] Reinstall the last compatible dependencies/application without reusing the failed environment.
- [ ] Start privately; verify health, registry, representative offline workflow, review/history, CMS/QC reopen, and downloader.
- [ ] Restore approved access only after data-owner confirmation.
- [ ] Document cause, affected jobs/artifacts, provider calls that may have completed, and follow-up gates.
