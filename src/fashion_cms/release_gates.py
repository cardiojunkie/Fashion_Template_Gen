from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator


APPLICATION_NAME = "fashion-cms-upload-generator"
RELEASE_CANDIDATE_VERSION = "0.1.0-rc1"


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED_USER_DECISION = "BLOCKED_USER_DECISION"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


GATES: tuple[tuple[str, str, bool], ...] = (
    ("automated_tests", "Full automated test suite", True),
    ("lint", "Ruff lint", True),
    ("registry_validation", "Attribute registry validation", True),
    ("exact_export_headers", "Exact CMS export headers and order", True),
    ("attribute_set_workflows", "All seven attribute-set workflows", True),
    ("accessory_profile_isolation", "Men's Accessories profile isolation", True),
    ("evaluation_dataset_approval", "Human approval of frozen evaluation data", True),
    ("model_comparison", "Live comparison of at least two approved models", True),
    ("variant_leakage", "Variant-leakage evaluation", True),
    ("image_downloader_security", "Image downloader and URL security", True),
    ("workbook_zip_security", "Workbook and ZIP security", True),
    ("secrets_logging", "Secret handling and logging redaction", True),
    ("job_resume", "Partial failure resume", True),
    ("job_cancellation", "Job cancellation semantics", True),
    ("cost_visibility", "Request and cost visibility", True),
    ("backup_migrations", "Backup and migration documentation and checks", True),
    ("user_documentation", "Operator and user documentation", True),
    ("deployment_configuration", "Approved production deployment configuration", True),
    ("business_rule_signoff", "User business-rule sign-off", True),
)


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_id: str
    description: str
    status: GateStatus
    evidence: str
    artifact_path: str | None = None
    reason: str | None = None
    timestamp: datetime
    application: str
    version: str


class ReleaseGateReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_version: str = "1"
    application: str = APPLICATION_NAME
    version: str = RELEASE_CANDIDATE_VERSION
    generated_at: datetime
    results: tuple[GateResult, ...]

    @model_validator(mode="after")
    def exact_gate_membership(self) -> ReleaseGateReport:
        expected = [gate_id for gate_id, _, _ in GATES]
        actual = [result.gate_id for result in self.results]
        if actual != expected:
            raise ValueError("Release report must contain every gate exactly once in order")
        return self

    @property
    def production_ready(self) -> bool:
        mandatory = {gate_id for gate_id, _, required in GATES if required}
        return all(
            result.status == GateStatus.PASS
            for result in self.results
            if result.gate_id in mandatory
        )

    @property
    def verdict(self) -> str:
        if self.production_ready:
            return "READY_FOR_USER_ACCEPTANCE"
        if any(result.status == GateStatus.FAIL for result in self.results):
            return "NOT_READY"
        return "BLOCKED"


def build_report(
    results: dict[str, tuple[GateStatus | str, str, str | None, str | None]],
    *,
    timestamp: datetime | None = None,
    version: str = RELEASE_CANDIDATE_VERSION,
) -> ReleaseGateReport:
    now = timestamp or datetime.now(UTC)
    rows = []
    for gate_id, description, _ in GATES:
        status, evidence, artifact, reason = results.get(
            gate_id,
            (GateStatus.NOT_RUN, "Not executed.", None, "No evidence was recorded."),
        )
        rows.append(
            GateResult(
                gate_id=gate_id,
                description=description,
                status=status,
                evidence=evidence,
                artifact_path=artifact,
                reason=reason,
                timestamp=now,
                application=APPLICATION_NAME,
                version=version,
            )
        )
    return ReleaseGateReport(version=version, generated_at=now, results=tuple(rows))


def load_report(path: str | Path) -> ReleaseGateReport:
    return ReleaseGateReport.model_validate_json(Path(path).read_text(encoding="utf-8"))


def report_json(report: ReleaseGateReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2) + "\n"


def report_markdown(report: ReleaseGateReport) -> str:
    rows = [
        f"# Release gates — {report.version}",
        "",
        f"Verdict: **{report.verdict}**",
        "",
        "| Gate | Status | Evidence | Artifact | Blocker / failure |",
        "|---|---|---|---|---|",
    ]
    for result in report.results:
        evidence = result.evidence.replace("|", "\\|")
        artifact = (result.artifact_path or "").replace("|", "\\|")
        reason = (result.reason or "").replace("|", "\\|")
        rows.append(
            f"| {result.gate_id} | {result.status.value} | {evidence} | {artifact} | {reason} |"
        )
    return "\n".join(rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and summarize a release-gate report.")
    parser.add_argument("report", type=Path)
    arguments = parser.parse_args()
    report = load_report(arguments.report)
    print(f"{report.verdict}: {len(report.results)} gates, production_ready={report.production_ready}")


if __name__ == "__main__":
    main()
