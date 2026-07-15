from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fashion_cms.models import AnalysisMode, InputRow, JobStatus, WorkItemStatus
from fashion_cms.variant_service import (
    CacheContext,
    ImageAsset,
    PlannedWorkItem,
    VariantGroup,
    build_variant_groups,
)


SCHEMA_VERSION = 3
JOB_STATUSES = tuple(status.value for status in JobStatus)
ITEM_STATUSES = tuple(status.value for status in WorkItemStatus)
ANALYSIS_MODES = tuple(mode.value for mode in AnalysisMode)
ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.UPLOADED: frozenset({JobStatus.VALIDATING, JobStatus.FAILED}),
    JobStatus.VALIDATING: frozenset({JobStatus.READY, JobStatus.FAILED}),
    JobStatus.READY: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.REVIEW_REQUIRED,
            JobStatus.COMPLETED,
            JobStatus.PARTIAL_FAILURE,
            JobStatus.FAILED,
        }
    ),
    JobStatus.REVIEW_REQUIRED: frozenset(
        {JobStatus.RUNNING, JobStatus.COMPLETED, JobStatus.FAILED}
    ),
    JobStatus.PARTIAL_FAILURE: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.FAILED: frozenset({JobStatus.VALIDATING, JobStatus.RUNNING}),
    JobStatus.COMPLETED: frozenset(),
}


class DatabaseVersionError(RuntimeError):
    pass


class JobNotFoundError(KeyError):
    pass


class InvalidStateTransition(ValueError):
    pass


class InvalidJobEdit(ValueError):
    pass


@dataclass(frozen=True)
class JobRecord:
    id: str
    job_type: str
    attribute_set: str
    product_profile: str | None
    status: JobStatus
    context: CacheContext
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkItemRecord:
    job_id: str
    key: str
    position: int
    group_key: str
    analysis_mode: AnalysisMode
    represented_skus: tuple[str, ...]
    representative_sku: str
    status: WorkItemStatus
    error: str | None
    retry_count: int
    cache_key: str
    cache_payload_json: str
    result_ref: str | None
    request_metadata: dict[str, object] | None
    cache_hit: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class JobSummary:
    id: str
    job_type: str
    attribute_set: str
    created_at: str
    updated_at: str
    status: JobStatus
    completed_item_count: int
    failed_item_count: int
    review_required_count: int
    planned_request_count: int


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    job_id: str
    kind: str
    path: str
    created_at: str


MIGRATION_1 = (
    f"""
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        job_type TEXT NOT NULL,
        attribute_set TEXT NOT NULL,
        product_profile TEXT,
        status TEXT NOT NULL CHECK (status IN {JOB_STATUSES}),
        registry_version TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        model_identifier TEXT NOT NULL,
        image_detail TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE job_rows (
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        row_number INTEGER NOT NULL,
        sku TEXT NOT NULL,
        base_code TEXT,
        ean TEXT,
        shipping_weight_json TEXT NOT NULL,
        model_data TEXT,
        PRIMARY KEY (job_id, sku),
        UNIQUE (job_id, position)
    )
    """,
    f"""
    CREATE TABLE variant_groups (
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        group_key TEXT NOT NULL,
        position INTEGER NOT NULL,
        base_code TEXT,
        skus_json TEXT NOT NULL,
        analysis_mode TEXT NOT NULL CHECK (analysis_mode IN {ANALYSIS_MODES}),
        representative_sku TEXT NOT NULL,
        representative_override_sku TEXT,
        summary_json TEXT NOT NULL,
        PRIMARY KEY (job_id, group_key),
        UNIQUE (job_id, position)
    )
    """,
    """
    CREATE TABLE image_assets (
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        sku TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal > 0),
        source_name TEXT,
        filename TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        image_format TEXT,
        width INTEGER NOT NULL CHECK (width > 0),
        height INTEGER NOT NULL CHECK (height > 0),
        PRIMARY KEY (job_id, sku, ordinal),
        FOREIGN KEY (job_id, sku) REFERENCES job_rows(job_id, sku) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE work_items (
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        item_key TEXT NOT NULL,
        position INTEGER NOT NULL,
        group_key TEXT NOT NULL,
        analysis_mode TEXT NOT NULL CHECK (analysis_mode IN {ANALYSIS_MODES}),
        represented_skus_json TEXT NOT NULL,
        representative_sku TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN {ITEM_STATUSES}),
        error TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
        cache_key TEXT NOT NULL,
        cache_payload_json TEXT NOT NULL,
        result_ref TEXT,
        cache_hit INTEGER NOT NULL DEFAULT 0 CHECK (cache_hit IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (job_id, item_key),
        UNIQUE (job_id, position),
        FOREIGN KEY (job_id, group_key)
            REFERENCES variant_groups(job_id, group_key) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX work_items_job_status ON work_items(job_id, status)
    """,
    """
    CREATE TABLE result_cache (
        cache_key TEXT PRIMARY KEY,
        cache_payload_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE artifacts (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (job_id, kind, path)
    )
    """,
)
MIGRATION_2 = (
    "ALTER TABLE work_items ADD COLUMN request_metadata_json TEXT",
)
MIGRATION_3 = (
    """
    CREATE TABLE review_decisions (
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        sku TEXT NOT NULL,
        header TEXT NOT NULL,
        decision_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (job_id, sku, header),
        FOREIGN KEY (job_id, sku) REFERENCES job_rows(job_id, sku) ON DELETE CASCADE
    )
    """,
)
MIGRATIONS = (MIGRATION_1, MIGRATION_2, MIGRATION_3)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class JobDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._memory: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory = self._new_connection()
        else:
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            if self.path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            self._migrate(connection)

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._memory or self._new_connection()
        try:
            yield connection
        finally:
            if self._memory is None:
                connection.close()

    def close(self) -> None:
        if self._memory is not None:
            self._memory.close()
            self._memory = None

    def _migrate(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise DatabaseVersionError(
                f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}."
            )
        for target_version in range(version + 1, SCHEMA_VERSION + 1):
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in MIGRATIONS[target_version - 1]:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {target_version}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @property
    def schema_version(self) -> int:
        with self.connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def create_job(
        self,
        groups: Sequence[VariantGroup],
        context: CacheContext,
        *,
        job_type: str = "CMS_GENERATION",
        job_id: str | None = None,
    ) -> str:
        if not groups:
            raise ValueError("A job must contain at least one variant group.")
        identifier = job_id or uuid.uuid4().hex
        now = _now()
        rows = tuple(row for group in groups for row in group.rows)
        assets = tuple(asset for group in groups for asset in group.images)
        if len({row.sku for row in rows}) != len(rows):
            raise ValueError("A SKU may belong to only one variant group.")
        if len({(asset.sku, asset.ordinal) for asset in assets}) != len(assets):
            raise ValueError("Image ordinals must be unique within a SKU.")

        with self.connection() as connection, connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, job_type, attribute_set, product_profile, status,
                    registry_version, prompt_version, schema_version,
                    model_identifier, image_detail, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    job_type,
                    context.attribute_set,
                    context.product_profile,
                    JobStatus.UPLOADED.value,
                    context.registry_version,
                    context.prompt_version,
                    context.schema_version,
                    context.model_identifier,
                    context.image_detail,
                    now,
                    now,
                ),
            )
            for position, row in enumerate(rows):
                connection.execute(
                    """
                    INSERT INTO job_rows (
                        job_id, position, row_number, sku, base_code, ean,
                        shipping_weight_json, model_data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        position,
                        row.row_number,
                        row.sku,
                        row.base_code,
                        row.attributes__lulu_ean,
                        _json(row.attributes__shipping_weight),
                        row.model_code_input_data,
                    ),
                )
            for position, group in enumerate(groups):
                summary = {
                    "detected_colors": group.detected_colors,
                    "detected_sizes": group.detected_sizes,
                    "detected_patterns": group.detected_patterns,
                    "detected_product_types": group.detected_product_types,
                    "detected_pack_counts": group.detected_pack_counts,
                    "detected_model_codes": group.detected_model_codes,
                    "size_only_warnings": group.size_only_warnings,
                    "size_only_suggested": group.size_only_suggested,
                }
                connection.execute(
                    """
                    INSERT INTO variant_groups (
                        job_id, group_key, position, base_code, skus_json,
                        analysis_mode, representative_sku, representative_override_sku,
                        summary_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        group.key,
                        position,
                        group.base_code,
                        _json(group.skus),
                        group.analysis_mode.value,
                        group.representative_sku,
                        group.representative_sku
                        if group.user_selected_representative
                        else None,
                        _json(summary),
                    ),
                )
            for asset in assets:
                connection.execute(
                    """
                    INSERT INTO image_assets (
                        job_id, sku, ordinal, source_name, filename, sha256,
                        image_format, width, height
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        asset.sku,
                        asset.ordinal,
                        asset.source_name,
                        asset.filename,
                        asset.sha256,
                        asset.image_format,
                        asset.width,
                        asset.height,
                    ),
                )
        return identifier

    def get_job(self, job_id: str) -> JobRecord:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return JobRecord(
            id=row["id"],
            job_type=row["job_type"],
            attribute_set=row["attribute_set"],
            product_profile=row["product_profile"],
            status=JobStatus(row["status"]),
            context=CacheContext(
                attribute_set=row["attribute_set"],
                product_profile=row["product_profile"],
                registry_version=row["registry_version"],
                prompt_version=row["prompt_version"],
                schema_version=row["schema_version"],
                model_identifier=row["model_identifier"],
                image_detail=row["image_detail"],
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def transition_job(self, job_id: str, target: JobStatus | str) -> JobRecord:
        target_status = JobStatus(target)
        with self.connection() as connection, connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            current = JobStatus(row["status"])
            if target_status not in ALLOWED_TRANSITIONS[current]:
                raise InvalidStateTransition(
                    f"Cannot transition job {job_id} from {current.value} "
                    f"to {target_status.value}."
                )
            updated = connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (target_status.value, _now(), job_id, current.value),
            )
            if updated.rowcount != 1:
                raise InvalidStateTransition("Job status changed concurrently; retry the action.")
        return self.get_job(job_id)

    def load_rows(self, job_id: str) -> tuple[InputRow, ...]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM job_rows WHERE job_id = ? ORDER BY position", (job_id,)
            ).fetchall()
        return tuple(
            InputRow(
                row_number=row["row_number"],
                sku=row["sku"],
                base_code=row["base_code"],
                attributes__lulu_ean=row["ean"],
                attributes__shipping_weight=json.loads(row["shipping_weight_json"]),
                model_code_input_data=row["model_data"],
            )
            for row in rows
        )

    def load_image_assets(self, job_id: str) -> tuple[ImageAsset, ...]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM image_assets WHERE job_id = ?
                ORDER BY (SELECT position FROM job_rows
                          WHERE job_rows.job_id = image_assets.job_id
                            AND job_rows.sku = image_assets.sku), ordinal
                """,
                (job_id,),
            ).fetchall()
        return tuple(
            ImageAsset(
                sku=row["sku"],
                ordinal=row["ordinal"],
                filename=row["filename"],
                source_name=row["source_name"],
                image_format=row["image_format"],
                sha256=row["sha256"],
                width=row["width"],
                height=row["height"],
            )
            for row in rows
        )

    def load_groups(self, job_id: str) -> tuple[VariantGroup, ...]:
        rows = self.load_rows(job_id)
        assets = self.load_image_assets(job_id)
        with self.connection() as connection:
            stored = connection.execute(
                "SELECT * FROM variant_groups WHERE job_id = ? ORDER BY position",
                (job_id,),
            ).fetchall()
        if not stored and not rows:
            self.get_job(job_id)
            return ()
        modes = {row["group_key"]: row["analysis_mode"] for row in stored}
        built = {group.key: group for group in build_variant_groups(rows, assets, modes=modes)}
        groups = []
        for row in stored:
            summary = json.loads(row["summary_json"])
            values = built[row["group_key"]].model_dump()
            values.update(
                {
                    "representative_sku": row["representative_sku"],
                    "user_selected_representative": row[
                        "representative_override_sku"
                    ]
                    is not None,
                    **summary,
                }
            )
            groups.append(VariantGroup.model_validate(values))
        return tuple(groups)

    def update_group(
        self,
        job_id: str,
        group_key: str,
        *,
        analysis_mode: AnalysisMode | str | None = None,
        representative_sku: str | None = None,
    ) -> None:
        mode = AnalysisMode(analysis_mode) if analysis_mode is not None else None
        with self.connection() as connection, connection:
            self._require_editable(connection, job_id)
            group = connection.execute(
                """
                SELECT analysis_mode, representative_sku, skus_json
                FROM variant_groups WHERE job_id = ? AND group_key = ?
                """,
                (job_id, group_key),
            ).fetchone()
            if group is None:
                raise InvalidJobEdit(f"Unknown group {group_key!r}.")
            if representative_sku is not None and representative_sku not in json.loads(
                group["skus_json"]
            ):
                raise InvalidJobEdit("Representative SKU must belong to its variant group.")
            next_mode = mode.value if mode else group["analysis_mode"]
            next_representative = representative_sku or group["representative_sku"]
            changed = (
                next_mode != group["analysis_mode"]
                or next_representative != group["representative_sku"]
            )
            connection.execute(
                """
                UPDATE variant_groups
                SET analysis_mode = ?, representative_sku = ?,
                    representative_override_sku = COALESCE(?, representative_override_sku)
                WHERE job_id = ? AND group_key = ?
                """,
                (
                    next_mode,
                    next_representative,
                    representative_sku,
                    job_id,
                    group_key,
                ),
            )
            if changed:
                connection.execute("DELETE FROM work_items WHERE job_id = ?", (job_id,))
            connection.execute(
                "UPDATE jobs SET updated_at = ? WHERE id = ?", (_now(), job_id)
            )

    def bulk_update_mode(self, job_id: str, analysis_mode: AnalysisMode | str) -> None:
        mode = AnalysisMode(analysis_mode)
        with self.connection() as connection, connection:
            self._require_editable(connection, job_id)
            connection.execute(
                "UPDATE variant_groups SET analysis_mode = ? WHERE job_id = ?",
                (mode.value, job_id),
            )
            connection.execute("DELETE FROM work_items WHERE job_id = ?", (job_id,))
            connection.execute(
                "UPDATE jobs SET updated_at = ? WHERE id = ?", (_now(), job_id)
            )

    @staticmethod
    def _require_editable(connection: sqlite3.Connection, job_id: str) -> None:
        row = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        if JobStatus(row["status"]) not in {
            JobStatus.UPLOADED,
            JobStatus.VALIDATING,
            JobStatus.READY,
        }:
            raise InvalidJobEdit("Group selections cannot change after processing starts.")

    def replace_work_items(
        self, job_id: str, items: Sequence[PlannedWorkItem]
    ) -> None:
        now = _now()
        with self.connection() as connection, connection:
            self._require_editable(connection, job_id)
            connection.execute("DELETE FROM work_items WHERE job_id = ?", (job_id,))
            for position, item in enumerate(items):
                connection.execute(
                    """
                    INSERT INTO work_items (
                        job_id, item_key, position, group_key, analysis_mode,
                        represented_skus_json, representative_sku, status, error,
                        retry_count, cache_key, cache_payload_json, result_ref,
                        cache_hit, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, NULL, 0, ?, ?)
                    """,
                    (
                        job_id,
                        item.key,
                        position,
                        item.group_key,
                        item.analysis_mode.value,
                        _json(item.represented_skus),
                        item.representative_sku,
                        WorkItemStatus.PENDING.value,
                        item.cache_key,
                        item.cache_payload_json,
                        now,
                        now,
                    ),
                )
            connection.execute(
                "UPDATE jobs SET updated_at = ? WHERE id = ?", (now, job_id)
            )

    def list_work_items(
        self,
        job_id: str,
        statuses: Sequence[WorkItemStatus | str] | None = None,
    ) -> tuple[WorkItemRecord, ...]:
        parameters: list[object] = [job_id]
        query = "SELECT * FROM work_items WHERE job_id = ?"
        if statuses:
            values = tuple(WorkItemStatus(status).value for status in statuses)
            query += f" AND status IN ({','.join('?' for _ in values)})"
            parameters.extend(values)
        query += " ORDER BY position"
        with self.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(self._work_item(row) for row in rows)

    @staticmethod
    def _work_item(row: sqlite3.Row) -> WorkItemRecord:
        return WorkItemRecord(
            job_id=row["job_id"],
            key=row["item_key"],
            position=row["position"],
            group_key=row["group_key"],
            analysis_mode=AnalysisMode(row["analysis_mode"]),
            represented_skus=tuple(json.loads(row["represented_skus_json"])),
            representative_sku=row["representative_sku"],
            status=WorkItemStatus(row["status"]),
            error=row["error"],
            retry_count=row["retry_count"],
            cache_key=row["cache_key"],
            cache_payload_json=row["cache_payload_json"],
            result_ref=row["result_ref"],
            request_metadata=(
                json.loads(row["request_metadata_json"])
                if row["request_metadata_json"]
                else None
            ),
            cache_hit=bool(row["cache_hit"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def mark_item_running(self, job_id: str, item_key: str) -> None:
        with self.connection() as connection, connection:
            updated = connection.execute(
                """
                UPDATE work_items SET status = ?, updated_at = ?
                WHERE job_id = ? AND item_key = ? AND status IN (?, ?)
                """,
                (
                    WorkItemStatus.RUNNING.value,
                    _now(),
                    job_id,
                    item_key,
                    WorkItemStatus.PENDING.value,
                    WorkItemStatus.RUNNING.value,
                ),
            )
            if updated.rowcount != 1:
                raise InvalidJobEdit("Only pending or interrupted items can be started.")

    def complete_item_with_result(
        self,
        item: WorkItemRecord,
        result: Mapping[str, object],
        *,
        cache_hit: bool,
        review_required: bool = False,
    ) -> None:
        result_json = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        request_metadata = result.get("request_metadata")
        request_metadata_json = (
            json.dumps(
                request_metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if isinstance(request_metadata, Mapping)
            else None
        )
        now = _now()
        status = (
            WorkItemStatus.REVIEW_REQUIRED
            if review_required
            else WorkItemStatus.COMPLETED
        )
        with self.connection() as connection, connection:
            if not cache_hit:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO result_cache (
                        cache_key, cache_payload_json, result_json, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (item.cache_key, item.cache_payload_json, result_json, now),
                )
            updated = connection.execute(
                """
                UPDATE work_items
                SET status = ?, error = NULL, result_ref = ?, request_metadata_json = ?,
                    cache_hit = ?, updated_at = ?
                WHERE job_id = ? AND item_key = ? AND status = ?
                """,
                (
                    status.value,
                    f"cache:{item.cache_key}",
                    request_metadata_json,
                    int(cache_hit),
                    now,
                    item.job_id,
                    item.key,
                    WorkItemStatus.RUNNING.value,
                ),
            )
            if updated.rowcount != 1:
                raise InvalidJobEdit("Running item changed before its result was saved.")
            connection.execute(
                "UPDATE jobs SET updated_at = ? WHERE id = ?", (now, item.job_id)
            )

    def fail_item(
        self,
        item: WorkItemRecord,
        error: str,
        request_metadata: Mapping[str, object] | None = None,
    ) -> None:
        with self.connection() as connection, connection:
            updated = connection.execute(
                """
                UPDATE work_items
                SET status = ?, error = ?, request_metadata_json = ?,
                    cache_hit = 0, updated_at = ?
                WHERE job_id = ? AND item_key = ? AND status = ?
                """,
                (
                    WorkItemStatus.FAILED.value,
                    error,
                    _json(request_metadata) if request_metadata is not None else None,
                    _now(),
                    item.job_id,
                    item.key,
                    WorkItemStatus.RUNNING.value,
                ),
            )
            if updated.rowcount != 1:
                raise InvalidJobEdit("Running item changed before its failure was saved.")

    def prepare_failed_retry(self, job_id: str) -> int:
        now = _now()
        with self.connection() as connection, connection:
            updated = connection.execute(
                """
                UPDATE work_items
                SET status = ?, error = NULL, retry_count = retry_count + 1,
                    cache_hit = 0, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    WorkItemStatus.PENDING.value,
                    now,
                    job_id,
                    WorkItemStatus.FAILED.value,
                ),
            )
            if updated.rowcount:
                connection.execute(
                    "UPDATE jobs SET updated_at = ? WHERE id = ?", (now, job_id)
                )
            return updated.rowcount

    def get_cached_result(
        self, cache_key: str, cache_payload_json: str
    ) -> dict[str, object] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT result_json FROM result_cache
                WHERE cache_key = ? AND cache_payload_json = ?
                """,
                (cache_key, cache_payload_json),
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def delete_cached_result(self, cache_key: str, cache_payload_json: str) -> None:
        with self.connection() as connection, connection:
            connection.execute(
                "DELETE FROM result_cache WHERE cache_key = ? AND cache_payload_json = ?",
                (cache_key, cache_payload_json),
            )

    def get_work_item_result(self, item: WorkItemRecord) -> dict[str, object] | None:
        if item.result_ref != f"cache:{item.cache_key}":
            return None
        return self.get_cached_result(item.cache_key, item.cache_payload_json)

    def save_review_decision(
        self,
        job_id: str,
        sku: str,
        header: str,
        decision: Mapping[str, object],
    ) -> None:
        if not sku or not header:
            raise ValueError("Review decisions require a SKU and attribute header.")
        now = _now()
        with self.connection() as connection, connection:
            if connection.execute(
                "SELECT 1 FROM job_rows WHERE job_id = ? AND sku = ?",
                (job_id, sku),
            ).fetchone() is None:
                raise InvalidJobEdit("Review decision SKU does not belong to the job.")
            connection.execute(
                """
                INSERT INTO review_decisions (
                    job_id, sku, header, decision_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (job_id, sku, header) DO UPDATE SET
                    decision_json = excluded.decision_json,
                    updated_at = excluded.updated_at
                """,
                (job_id, sku, header, _json(decision), now),
            )
            connection.execute(
                "UPDATE jobs SET updated_at = ? WHERE id = ?", (now, job_id)
            )

    def load_review_decisions(
        self, job_id: str
    ) -> dict[tuple[str, str], dict[str, object]]:
        self.get_job(job_id)
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT sku, header, decision_json
                FROM review_decisions WHERE job_id = ? ORDER BY sku, header
                """,
                (job_id,),
            ).fetchall()
        return {
            (row["sku"], row["header"]): json.loads(row["decision_json"])
            for row in rows
        }

    def list_job_summaries(self) -> tuple[JobSummary, ...]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT jobs.*,
                       COUNT(work_items.item_key) AS planned,
                       COALESCE(SUM(work_items.status = 'COMPLETED'), 0) AS completed,
                       COALESCE(SUM(work_items.status = 'FAILED'), 0) AS failed,
                       COALESCE(SUM(work_items.status = 'REVIEW_REQUIRED'), 0) AS review
                FROM jobs
                LEFT JOIN work_items ON work_items.job_id = jobs.id
                GROUP BY jobs.id
                ORDER BY jobs.created_at DESC, jobs.id DESC
                """
            ).fetchall()
        return tuple(
            JobSummary(
                id=row["id"],
                job_type=row["job_type"],
                attribute_set=row["attribute_set"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                status=JobStatus(row["status"]),
                completed_item_count=row["completed"],
                failed_item_count=row["failed"],
                review_required_count=row["review"],
                planned_request_count=row["planned"],
            )
            for row in rows
        )

    list_jobs = list_job_summaries

    def add_artifact(self, job_id: str, kind: str, path: str) -> ArtifactRecord:
        self.get_job(job_id)
        identifier = uuid.uuid4().hex
        created = _now()
        with self.connection() as connection, connection:
            connection.execute(
                """
                INSERT INTO artifacts (id, job_id, kind, path, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (identifier, job_id, kind, path, created),
            )
        return ArtifactRecord(identifier, job_id, kind, path, created)

    def list_artifacts(self, job_id: str) -> tuple[ArtifactRecord, ...]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE job_id = ? ORDER BY created_at, id",
                (job_id,),
            ).fetchall()
        return tuple(
            ArtifactRecord(
                id=row["id"],
                job_id=row["job_id"],
                kind=row["kind"],
                path=row["path"],
                created_at=row["created_at"],
            )
            for row in rows
        )


Database = JobDatabase
