from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..domain.models.execution import AttemptOutcome, CostEvidenceKind, ProviderAttempt
from ..infrastructure.telemetry.request_log import RequestTrace, RouteTrace
from ..utils.time import utc_now
from .harness import BenchmarkReport

SCHEMA_VERSION = 5


@dataclass(frozen=True)
class ProviderUsageRecord:
    """Queryable request-level provider usage with explicit cost completeness."""

    request_id: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None
    cost_evidence_complete: bool
    pricing_table_version: str
    cache_hit: bool
    provider_attempt_count: int
    provider_retry_count: int
    provider_attempts: tuple[ProviderAttempt, ...]
    error_type: str | None
    timestamp: str


@dataclass(frozen=True)
class ProviderUsageSummary:
    """Aggregated provider usage for one benchmark run."""

    run_id: str
    request_count: int
    success_count: int
    failure_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None
    cost_evidence_complete: bool
    provider_attempt_count: int
    provider_retry_count: int
    cost_by_model: dict[str, float | None]
    tokens_by_model: dict[str, int]


_TRACE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS benchmark_traces (
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    estimated_cost_usd REAL,
    cost_evidence_complete INTEGER NOT NULL DEFAULT 1,
    pricing_table_version TEXT NOT NULL,
    cache_hit INTEGER NOT NULL,
    error_type TEXT,
    error_message TEXT,
    quality_passed INTEGER,
    quality_score REAL,
    quality_reason TEXT,
    eval_type TEXT,
    provider_attempt_count INTEGER NOT NULL DEFAULT 1,
    provider_retry_count INTEGER NOT NULL DEFAULT 0,
    provider_attempts_json TEXT NOT NULL DEFAULT '[]',
    timestamp TEXT NOT NULL,
    PRIMARY KEY (run_id, request_id),
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id) ON DELETE CASCADE
)
"""

_USAGE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS benchmark_provider_usage (
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    estimated_cost_usd REAL,
    cost_evidence_complete INTEGER NOT NULL DEFAULT 1,
    pricing_table_version TEXT NOT NULL,
    cache_hit INTEGER NOT NULL,
    provider_attempt_count INTEGER NOT NULL,
    provider_retry_count INTEGER NOT NULL,
    provider_attempts_json TEXT NOT NULL DEFAULT '[]',
    error_type TEXT,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (run_id, request_id),
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id) ON DELETE CASCADE
)
"""


class SQLiteBenchmarkLedger:
    """Small local SQLite ledger for reproducible benchmark run comparisons."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    workload_path TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    report_json TEXT NOT NULL
                )
                """
            )

            connection.execute(_TRACE_TABLE_SQL)
            _ensure_columns(
                connection,
                table_name="benchmark_traces",
                columns={
                    "quality_passed": "INTEGER",
                    "quality_score": "REAL",
                    "quality_reason": "TEXT",
                    "eval_type": "TEXT",
                    "provider_attempt_count": "INTEGER NOT NULL DEFAULT 1",
                    "provider_retry_count": "INTEGER NOT NULL DEFAULT 0",
                    "cost_evidence_complete": "INTEGER NOT NULL DEFAULT 1",
                    "provider_attempts_json": "TEXT NOT NULL DEFAULT '[]'",
                },
            )
            if _column_is_not_null(connection, "benchmark_traces", "estimated_cost_usd"):
                _rebuild_trace_table_with_nullable_cost(connection)

            connection.execute(_USAGE_TABLE_SQL)
            _ensure_columns(
                connection,
                table_name="benchmark_provider_usage",
                columns={
                    "cost_evidence_complete": "INTEGER NOT NULL DEFAULT 1",
                    "provider_attempts_json": "TEXT NOT NULL DEFAULT '[]'",
                },
            )
            if _column_is_not_null(
                connection,
                "benchmark_provider_usage",
                "estimated_cost_usd",
            ):
                _rebuild_usage_table_with_nullable_cost(connection)

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_routes (
                    run_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    selected_model TEXT NOT NULL,
                    estimated_cost_usd REAL NOT NULL,
                    estimated_latency_ms INTEGER NOT NULL,
                    decision_reason TEXT NOT NULL,
                    considered_models_json TEXT NOT NULL,
                    fallback_models_json TEXT NOT NULL,
                    max_estimated_cost_usd REAL,
                    budget_violation INTEGER NOT NULL,
                    budget_violation_reason TEXT,
                    timestamp TEXT NOT NULL,
                    PRIMARY KEY (run_id, request_id),
                    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id) ON DELETE CASCADE
                )
                """
            )

            connection.execute(
                """
                INSERT OR IGNORE INTO benchmark_provider_usage (
                    run_id,
                    request_id,
                    provider,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    cost_evidence_complete,
                    pricing_table_version,
                    cache_hit,
                    provider_attempt_count,
                    provider_retry_count,
                    provider_attempts_json,
                    error_type,
                    timestamp
                )
                SELECT
                    run_id,
                    request_id,
                    provider,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    cost_evidence_complete,
                    pricing_table_version,
                    cache_hit,
                    provider_attempt_count,
                    provider_retry_count,
                    provider_attempts_json,
                    error_type,
                    timestamp
                FROM benchmark_traces
                """
            )
            _downgrade_ambiguous_legacy_costs(connection, "benchmark_traces")
            _downgrade_ambiguous_legacy_costs(connection, "benchmark_provider_usage")
            _downgrade_legacy_attempt_provenance(connection, "benchmark_traces")
            _downgrade_legacy_attempt_provenance(connection, "benchmark_provider_usage")
            connection.execute(
                """
                INSERT OR REPLACE INTO ledger_metadata (key, value)
                VALUES ('schema_version', ?)
                """,
                (str(SCHEMA_VERSION),),
            )

    def record_run(
        self,
        *,
        run_id: str,
        report: BenchmarkReport,
        traces: list[RequestTrace],
        route_traces: list[RouteTrace] | None = None,
    ) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                """
                INSERT OR REPLACE INTO benchmark_runs (
                    run_id, created_at, workload_path, strategy, provider, model, report_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    utc_now().isoformat(),
                    report.workload_path,
                    report.strategy,
                    report.provider,
                    report.model,
                    json.dumps(asdict(report), sort_keys=True),
                ),
            )
            connection.execute("DELETE FROM benchmark_traces WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM benchmark_routes WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM benchmark_provider_usage WHERE run_id = ?", (run_id,))
            connection.executemany(
                """
                INSERT INTO benchmark_traces (
                    run_id,
                    request_id,
                    provider,
                    model,
                    latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    cost_evidence_complete,
                    pricing_table_version,
                    cache_hit,
                    error_type,
                    error_message,
                    quality_passed,
                    quality_score,
                    quality_reason,
                    eval_type,
                    provider_attempt_count,
                    provider_retry_count,
                    provider_attempts_json,
                    timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_trace_storage_row(run_id, trace) for trace in traces],
            )
            connection.executemany(
                """
                INSERT INTO benchmark_provider_usage (
                    run_id,
                    request_id,
                    provider,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    cost_evidence_complete,
                    pricing_table_version,
                    cache_hit,
                    provider_attempt_count,
                    provider_retry_count,
                    provider_attempts_json,
                    error_type,
                    timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_usage_storage_row(run_id, trace) for trace in traces],
            )
            connection.executemany(
                """
                INSERT INTO benchmark_routes (
                    run_id,
                    request_id,
                    strategy,
                    selected_model,
                    estimated_cost_usd,
                    estimated_latency_ms,
                    decision_reason,
                    considered_models_json,
                    fallback_models_json,
                    max_estimated_cost_usd,
                    budget_violation,
                    budget_violation_reason,
                    timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        route.request_id,
                        route.strategy,
                        route.selected_model,
                        route.estimated_cost_usd,
                        route.estimated_latency_ms,
                        route.decision_reason,
                        json.dumps(route.considered_models, sort_keys=True),
                        json.dumps(route.fallback_models, sort_keys=True),
                        route.max_estimated_cost_usd,
                        1 if route.budget_violation else 0,
                        route.budget_violation_reason,
                        route.timestamp,
                    )
                    for route in (route_traces or [])
                ],
            )

    def get_report(self, run_id: str) -> BenchmarkReport:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM benchmark_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            trace_cost_state = connection.execute(
                """
                SELECT
                    COUNT(*) AS trace_count,
                    SUM(
                        CASE
                            WHEN cost_evidence_complete = 0 OR estimated_cost_usd IS NULL THEN 1
                            ELSE 0
                        END
                    ) AS incomplete_cost_count
                FROM benchmark_traces
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown benchmark run_id: {run_id}")
        raw = json.loads(str(row["report_json"]))
        raw.setdefault("workload_sha256", None)
        raw.setdefault("route_count", 0)
        raw.setdefault("budget_violation_count", 0)
        raw.setdefault("model_distribution", {})
        raw.setdefault("route_reason_distribution", {})
        raw.setdefault("observed_latency_ms_by_model", {})
        raw.setdefault("provider_attempt_count", raw.get("request_count", 0))
        raw.setdefault("provider_retry_count", 0)
        raw.setdefault("quality_count", 0)
        raw.setdefault("quality_pass_count", 0)
        raw.setdefault("quality_pass_rate", None)
        raw.setdefault("quality_score_avg", None)

        trace_count = int(trace_cost_state["trace_count"]) if trace_cost_state is not None else 0
        incomplete_cost_count = (
            int(trace_cost_state["incomplete_cost_count"] or 0)
            if trace_cost_state is not None
            else 0
        )
        if trace_count > 0 and incomplete_cost_count > 0:
            raw["cost_evidence_complete"] = False
            raw["estimated_cost_usd"] = None
            limitations = list(raw.get("limitations", []))
            reconciliation_note = (
                "Stored trace evidence contains unknown execution cost; historical aggregate cost "
                "is suppressed."
            )
            if reconciliation_note not in limitations:
                limitations.append(reconciliation_note)
            raw["limitations"] = limitations
        elif "cost_evidence_complete" not in raw:
            complete = (
                int(raw.get("failure_count", 0)) == 0
                and int(raw.get("provider_retry_count", 0)) == 0
            )
            raw["cost_evidence_complete"] = complete
            if not complete:
                raw["estimated_cost_usd"] = None
        return BenchmarkReport(**raw)

    def get_traces(self, run_id: str) -> list[RequestTrace]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    request_id,
                    provider,
                    model,
                    latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    cost_evidence_complete,
                    pricing_table_version,
                    cache_hit,
                    error_type,
                    error_message,
                    quality_passed,
                    quality_score,
                    quality_reason,
                    eval_type,
                    provider_attempt_count,
                    provider_retry_count,
                    provider_attempts_json,
                    timestamp
                FROM benchmark_traces
                WHERE run_id = ?
                ORDER BY timestamp, request_id
                """,
                (run_id,),
            ).fetchall()
        return [_trace_from_row(row) for row in rows]

    def get_provider_usage(self, run_id: str) -> list[ProviderUsageRecord]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    request_id,
                    provider,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    cost_evidence_complete,
                    pricing_table_version,
                    cache_hit,
                    provider_attempt_count,
                    provider_retry_count,
                    provider_attempts_json,
                    error_type,
                    timestamp
                FROM benchmark_provider_usage
                WHERE run_id = ?
                ORDER BY timestamp, request_id
                """,
                (run_id,),
            ).fetchall()
        return [_usage_from_row(row) for row in rows]

    def get_provider_usage_summary(self, run_id: str) -> ProviderUsageSummary:
        usage = self.get_provider_usage(run_id)
        if not usage:
            self.get_report(run_id)

        cost_evidence_complete = all(
            record.cost_evidence_complete and record.estimated_cost_usd is not None
            for record in usage
        )
        cost_by_model: dict[str, float | None] = {}
        tokens_by_model: dict[str, int] = {}
        for record in usage:
            tokens_by_model[record.model] = tokens_by_model.get(record.model, 0) + record.total_tokens
            if record.model not in cost_by_model:
                cost_by_model[record.model] = 0.0
            if not record.cost_evidence_complete or record.estimated_cost_usd is None:
                cost_by_model[record.model] = None
            elif cost_by_model[record.model] is not None:
                known_cost = cost_by_model[record.model]
                assert known_cost is not None
                cost_by_model[record.model] = known_cost + record.estimated_cost_usd

        return ProviderUsageSummary(
            run_id=run_id,
            request_count=len(usage),
            success_count=sum(1 for record in usage if record.error_type is None),
            failure_count=sum(1 for record in usage if record.error_type is not None),
            prompt_tokens=sum(record.prompt_tokens for record in usage),
            completion_tokens=sum(record.completion_tokens for record in usage),
            total_tokens=sum(record.total_tokens for record in usage),
            estimated_cost_usd=(
                sum(record.estimated_cost_usd or 0.0 for record in usage)
                if cost_evidence_complete
                else None
            ),
            cost_evidence_complete=cost_evidence_complete,
            provider_attempt_count=sum(record.provider_attempt_count for record in usage),
            provider_retry_count=sum(record.provider_retry_count for record in usage),
            cost_by_model=dict(sorted(cost_by_model.items())),
            tokens_by_model=dict(sorted(tokens_by_model.items())),
        )

    def get_routes(self, run_id: str) -> list[RouteTrace]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    request_id,
                    strategy,
                    selected_model,
                    estimated_cost_usd,
                    estimated_latency_ms,
                    decision_reason,
                    considered_models_json,
                    fallback_models_json,
                    max_estimated_cost_usd,
                    budget_violation,
                    budget_violation_reason,
                    timestamp
                FROM benchmark_routes
                WHERE run_id = ?
                ORDER BY timestamp, request_id
                """,
                (run_id,),
            ).fetchall()
        return [
            RouteTrace(
                request_id=str(row["request_id"]),
                strategy=str(row["strategy"]),
                selected_model=str(row["selected_model"]),
                estimated_cost_usd=float(row["estimated_cost_usd"]),
                estimated_latency_ms=int(row["estimated_latency_ms"]),
                decision_reason=str(row["decision_reason"]),
                considered_models=[
                    str(item) for item in json.loads(str(row["considered_models_json"]))
                ],
                fallback_models=[str(item) for item in json.loads(str(row["fallback_models_json"]))],
                max_estimated_cost_usd=(
                    float(row["max_estimated_cost_usd"])
                    if row["max_estimated_cost_usd"] is not None
                    else None
                ),
                budget_violation=bool(row["budget_violation"]),
                budget_violation_reason=(
                    str(row["budget_violation_reason"])
                    if row["budget_violation_reason"] is not None
                    else None
                ),
                timestamp=str(row["timestamp"]),
            )
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _trace_storage_row(run_id: str, trace: RequestTrace) -> tuple[object, ...]:
    return (
        run_id,
        trace.request_id,
        trace.provider,
        trace.model,
        trace.latency_ms,
        trace.prompt_tokens,
        trace.completion_tokens,
        trace.total_tokens,
        trace.estimated_cost_usd,
        1 if trace.cost_evidence_complete else 0,
        trace.pricing_table_version,
        1 if trace.cache_hit else 0,
        trace.error_type,
        trace.error_message,
        _optional_bool_to_int(trace.quality_passed),
        trace.quality_score,
        trace.quality_reason,
        trace.eval_type,
        trace.provider_attempt_count,
        trace.provider_retry_count,
        _attempts_json(trace.provider_attempts),
        trace.timestamp,
    )


def _usage_storage_row(run_id: str, trace: RequestTrace) -> tuple[object, ...]:
    return (
        run_id,
        trace.request_id,
        trace.provider,
        trace.model,
        trace.prompt_tokens,
        trace.completion_tokens,
        trace.total_tokens,
        trace.estimated_cost_usd,
        1 if trace.cost_evidence_complete else 0,
        trace.pricing_table_version,
        1 if trace.cache_hit else 0,
        trace.provider_attempt_count,
        trace.provider_retry_count,
        _attempts_json(trace.provider_attempts),
        trace.error_type,
        trace.timestamp,
    )


def _trace_from_row(row: sqlite3.Row) -> RequestTrace:
    attempts = _attempts_from_json(str(row["provider_attempts_json"]))
    complete = bool(row["cost_evidence_complete"])
    if attempts and not all(attempt.cost_is_known for attempt in attempts):
        complete = False
    return RequestTrace(
        request_id=str(row["request_id"]),
        provider=str(row["provider"]),
        model=str(row["model"]),
        latency_ms=int(row["latency_ms"]),
        prompt_tokens=int(row["prompt_tokens"]),
        completion_tokens=int(row["completion_tokens"]),
        total_tokens=int(row["total_tokens"]),
        estimated_cost_usd=_known_cost_from_row(row, complete),
        cost_evidence_complete=complete,
        pricing_table_version=str(row["pricing_table_version"]),
        cache_hit=bool(row["cache_hit"]),
        error_type=_optional_str(row["error_type"]),
        error_message=_optional_str(row["error_message"]),
        timestamp=str(row["timestamp"]),
        quality_passed=_optional_int_to_bool(row["quality_passed"]),
        quality_score=_optional_float(row["quality_score"]),
        quality_reason=_optional_str(row["quality_reason"]),
        eval_type=_optional_str(row["eval_type"]),
        provider_attempt_count=int(row["provider_attempt_count"]),
        provider_retry_count=int(row["provider_retry_count"]),
        provider_attempts=attempts,
    )


def _usage_from_row(row: sqlite3.Row) -> ProviderUsageRecord:
    attempts = _attempts_from_json(str(row["provider_attempts_json"]))
    complete = bool(row["cost_evidence_complete"])
    if attempts and not all(attempt.cost_is_known for attempt in attempts):
        complete = False
    return ProviderUsageRecord(
        request_id=str(row["request_id"]),
        provider=str(row["provider"]),
        model=str(row["model"]),
        prompt_tokens=int(row["prompt_tokens"]),
        completion_tokens=int(row["completion_tokens"]),
        total_tokens=int(row["total_tokens"]),
        estimated_cost_usd=_known_cost_from_row(row, complete),
        cost_evidence_complete=complete,
        pricing_table_version=str(row["pricing_table_version"]),
        cache_hit=bool(row["cache_hit"]),
        provider_attempt_count=int(row["provider_attempt_count"]),
        provider_retry_count=int(row["provider_retry_count"]),
        provider_attempts=attempts,
        error_type=_optional_str(row["error_type"]),
        timestamp=str(row["timestamp"]),
    )


def _known_cost_from_row(row: sqlite3.Row, complete: bool) -> float | None:
    value = row["estimated_cost_usd"]
    if not complete:
        return None
    if value is None:
        raise ValueError("complete stored cost evidence is missing estimated_cost_usd")
    return float(value)


def _attempts_json(attempts: tuple[ProviderAttempt, ...]) -> str:
    return json.dumps([asdict(attempt) for attempt in attempts], sort_keys=True)


def _attempts_from_json(raw: str) -> tuple[ProviderAttempt, ...]:
    values = json.loads(raw)
    if not isinstance(values, list):
        raise ValueError("provider_attempts_json must contain a list")
    return tuple(_attempt_from_dict(value) for value in values)


def _attempt_from_dict(raw: Any) -> ProviderAttempt:
    if not isinstance(raw, dict):
        raise ValueError("provider attempt must be an object")
    normalized, _ = _normalize_attempt_pricing_provenance(raw)
    return ProviderAttempt(
        attempt_index=int(normalized["attempt_index"]),
        provider=str(normalized["provider"]),
        model=str(normalized["model"]),
        outcome=AttemptOutcome(str(normalized["outcome"])),
        latency_ms=int(normalized["latency_ms"]),
        prompt_tokens=_optional_int(normalized.get("prompt_tokens")),
        completion_tokens=_optional_int(normalized.get("completion_tokens")),
        total_tokens=_optional_int(normalized.get("total_tokens")),
        cached_tokens=_optional_int(normalized.get("cached_tokens")),
        calculated_cost_usd=_optional_float(normalized.get("calculated_cost_usd")),
        cost_evidence=CostEvidenceKind(
            str(normalized.get("cost_evidence", CostEvidenceKind.UNKNOWN.value))
        ),
        pricing_table_version=_optional_str(normalized.get("pricing_table_version")),
        pricing_record_id=_optional_str(normalized.get("pricing_record_id")),
        pricing_observed_at=_optional_str(normalized.get("pricing_observed_at")),
        pricing_source_url=_optional_str(normalized.get("pricing_source_url")),
        error_type=_optional_str(normalized.get("error_type")),
        status_code=_optional_int(normalized.get("status_code")),
    )


def _normalize_attempt_pricing_provenance(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    normalized = dict(raw)
    cost_evidence = CostEvidenceKind(
        str(normalized.get("cost_evidence", CostEvidenceKind.UNKNOWN.value))
    )
    if cost_evidence != CostEvidenceKind.CALCULATED_FROM_USAGE:
        return normalized, False

    required = (
        normalized.get("pricing_table_version"),
        normalized.get("pricing_record_id"),
        normalized.get("pricing_observed_at"),
        normalized.get("pricing_source_url"),
    )
    if all(value is not None and str(value).strip() for value in required):
        return normalized, False

    normalized["calculated_cost_usd"] = None
    normalized["cost_evidence"] = CostEvidenceKind.UNKNOWN.value
    normalized["pricing_table_version"] = None
    normalized["pricing_record_id"] = None
    normalized["pricing_observed_at"] = None
    normalized["pricing_source_url"] = None
    return normalized, True


def _downgrade_ambiguous_legacy_costs(connection: sqlite3.Connection, table_name: str) -> None:
    connection.execute(
        f"""
        UPDATE {table_name}
        SET cost_evidence_complete = 0,
            estimated_cost_usd = NULL
        WHERE provider_attempts_json = '[]'
          AND provider_attempt_count > 0
          AND (error_type IS NOT NULL OR provider_retry_count > 0 OR provider_attempt_count > 1)
        """
    )


def _downgrade_legacy_attempt_provenance(
    connection: sqlite3.Connection,
    table_name: str,
) -> None:
    rows = connection.execute(
        f"SELECT rowid, provider_attempts_json FROM {table_name} WHERE provider_attempts_json != '[]'"
    ).fetchall()
    for row in rows:
        raw_attempts = json.loads(str(row["provider_attempts_json"]))
        if not isinstance(raw_attempts, list):
            raise ValueError(f"{table_name}.provider_attempts_json must contain a list")

        changed = False
        normalized_attempts: list[dict[str, Any]] = []
        for raw_attempt in raw_attempts:
            if not isinstance(raw_attempt, dict):
                raise ValueError(f"{table_name}.provider_attempts_json contains a non-object attempt")
            normalized, attempt_changed = _normalize_attempt_pricing_provenance(raw_attempt)
            normalized_attempts.append(normalized)
            changed = changed or attempt_changed

        if changed:
            connection.execute(
                f"""
                UPDATE {table_name}
                SET provider_attempts_json = ?,
                    cost_evidence_complete = 0,
                    estimated_cost_usd = NULL
                WHERE rowid = ?
                """,
                (json.dumps(normalized_attempts, sort_keys=True), int(row["rowid"])),
            )


def _column_is_not_null(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    for row in rows:
        if str(row["name"]) == column_name:
            return bool(row["notnull"])
    raise ValueError(f"{table_name}.{column_name} does not exist")


def _rebuild_trace_table_with_nullable_cost(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE benchmark_traces RENAME TO benchmark_traces_legacy_cost")
    connection.execute(_TRACE_TABLE_SQL)
    connection.execute(
        """
        INSERT INTO benchmark_traces (
            run_id,
            request_id,
            provider,
            model,
            latency_ms,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            estimated_cost_usd,
            cost_evidence_complete,
            pricing_table_version,
            cache_hit,
            error_type,
            error_message,
            quality_passed,
            quality_score,
            quality_reason,
            eval_type,
            provider_attempt_count,
            provider_retry_count,
            provider_attempts_json,
            timestamp
        )
        SELECT
            run_id,
            request_id,
            provider,
            model,
            latency_ms,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            CASE WHEN cost_evidence_complete = 1 THEN estimated_cost_usd ELSE NULL END,
            cost_evidence_complete,
            pricing_table_version,
            cache_hit,
            error_type,
            error_message,
            quality_passed,
            quality_score,
            quality_reason,
            eval_type,
            provider_attempt_count,
            provider_retry_count,
            provider_attempts_json,
            timestamp
        FROM benchmark_traces_legacy_cost
        """
    )
    connection.execute("DROP TABLE benchmark_traces_legacy_cost")


def _rebuild_usage_table_with_nullable_cost(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE benchmark_provider_usage RENAME TO benchmark_provider_usage_legacy_cost"
    )
    connection.execute(_USAGE_TABLE_SQL)
    connection.execute(
        """
        INSERT INTO benchmark_provider_usage (
            run_id,
            request_id,
            provider,
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            estimated_cost_usd,
            cost_evidence_complete,
            pricing_table_version,
            cache_hit,
            provider_attempt_count,
            provider_retry_count,
            provider_attempts_json,
            error_type,
            timestamp
        )
        SELECT
            run_id,
            request_id,
            provider,
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            CASE WHEN cost_evidence_complete = 1 THEN estimated_cost_usd ELSE NULL END,
            cost_evidence_complete,
            pricing_table_version,
            cache_hit,
            provider_attempt_count,
            provider_retry_count,
            provider_attempts_json,
            error_type,
            timestamp
        FROM benchmark_provider_usage_legacy_cost
        """
    )
    connection.execute("DROP TABLE benchmark_provider_usage_legacy_cost")


def _optional_bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _optional_int_to_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (bool, int)):
        return int(value)
    if isinstance(value, (str, bytes, bytearray, float)):
        return int(value)
    raise TypeError(f"expected integer-compatible value, got {type(value).__name__}")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return float(value)
    if isinstance(value, (str, bytes, bytearray)):
        return float(value)
    raise TypeError(f"expected float-compatible value, got {type(value).__name__}")


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _ensure_columns(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    columns: dict[str, str],
) -> None:
    existing = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, column_type in columns.items():
        if column_name not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
