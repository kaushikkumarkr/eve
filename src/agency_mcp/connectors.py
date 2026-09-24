from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import ExternalIntegration
from .service import ingest_text


SAFE_CRM_COLUMNS = {
    "company",
    "company_name",
    "role",
    "job_title",
    "industry",
    "stage",
    "status",
    "notes",
    "call_notes",
    "objection",
    "outcome",
    "product",
    "service",
    "location",
}
PII_COLUMNS = {"email", "phone", "mobile", "address", "first_name", "last_name", "name"}


@dataclass(frozen=True)
class CRMImportResult:
    source_id: str
    provider: str
    rows_read: int
    rows_stored: int
    excluded_columns: list[str]


def ingest_crm_csv(session: Session, client_id: str, provider: str, filename: str, csv_content: str) -> CRMImportResult:
    reader = csv.DictReader(io.StringIO(csv_content))
    fieldnames = [field.strip().lower() for field in (reader.fieldnames or [])]
    allowed = [field for field in fieldnames if field in SAFE_CRM_COLUMNS]
    excluded = [field for field in fieldnames if field not in SAFE_CRM_COLUMNS]
    rows = []
    for row in reader:
        safe = {key: (row.get(key) or "").strip() for key in allowed}
        values = [f"{key}: {value}" for key, value in safe.items() if value]
        if values:
            rows.append("; ".join(values))
    content = "CRM provider: " + provider + "\n" + "\n".join(rows)
    source = ingest_text(session, client_id, filename, content, "crm_csv")
    session.add(
        ExternalIntegration(
            client_id=client_id,
            provider=provider,
            status="imported",
            metadata_json={"filename": filename, "excluded_columns": excluded},
        )
    )
    session.commit()
    return CRMImportResult(source.id, provider, len(rows), len(rows), excluded)
