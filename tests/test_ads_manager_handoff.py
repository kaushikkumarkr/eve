from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agency_mcp.ads_manager import import_ads_manager_snapshots, record_ads_manager_access, record_external_action
from agency_mcp.db import Base
from agency_mcp.models import AdsInsightSnapshot, AdsManagerConnection
from agency_mcp.service import create_client


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'manager-handoff.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db
    engine.dispose()


def test_ads_manager_access_is_operator_attestation_not_verified(session):
    client = create_client(session, "Client A")
    row = record_ads_manager_access(session, client.id, "acct-123", "operator-a")
    assert row.status == "operator_attested"
    assert row.connected_by == "operator-a"


def test_external_action_is_audited_and_idempotency_reference_is_unique(session):
    client = create_client(session, "Client A")
    record_ads_manager_access(session, client.id, "acct-123", "operator-a")
    kwargs = dict(
        client_id=client.id, action_type="edit", entity_type="campaign",
        entity_external_id="cmp-1", external_reference="chatgpt-response-1",
        outcome="completed", details={"platform_status": "paused"},
        evidence_reference="operator-note-1", approved_payload_sha256=None,
        performed_at=datetime.now(timezone.utc), actor_id="operator-a",
    )
    action = record_external_action(session, **kwargs)
    assert action.ad_account_id == "acct-123"
    with pytest.raises(ValueError, match="already been recorded"):
        record_external_action(session, **kwargs)


def test_ads_manager_import_is_validated_provenanced_and_deduplicated(session):
    client = create_client(session, "Client A")
    record_ads_manager_access(session, client.id, "acct-123", "operator-a")
    rows = [{
        "entity_external_id": "cmp-1", "aggregation_level": "campaign",
        "period": "2026-09-01/2026-09-07", "timezone": "America/New_York",
        "freshness_state": "operator_reported",
        "metrics": {"impressions": 1200, "clicks": 36, "spend": 28.50, "conversions": None},
    }]
    first = import_ads_manager_snapshots(session, client_id=client.id, account_id="acct-123", source_name="ads-manager-export.csv", snapshots=rows, actor_id="operator-a")
    second = import_ads_manager_snapshots(session, client_id=client.id, account_id="acct-123", source_name="ads-manager-export.csv", snapshots=rows, actor_id="operator-a")
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    snapshot = session.scalar(select(AdsInsightSnapshot).where(AdsInsightSnapshot.client_id == client.id))
    assert snapshot.provider == "chatgpt_ads_manager_operator_import"
    assert snapshot.freshness_state == "operator_reported"
    assert snapshot.raw_response == {}


@pytest.mark.parametrize("bad_metrics", [{"email": 3}, {"clicks": True}, {"clicks": float("nan")}])
def test_ads_manager_import_rejects_unsafe_or_invalid_metrics(session, bad_metrics):
    client = create_client(session, "Client A")
    record_ads_manager_access(session, client.id, "acct-123", "operator-a")
    with pytest.raises(ValueError):
        import_ads_manager_snapshots(session, client_id=client.id, account_id="acct-123", source_name="export", snapshots=[{"aggregation_level": "campaign", "period": "2026-09-01/2026-09-02", "metrics": bad_metrics}], actor_id="operator-a")


def test_manager_import_requires_exact_attested_account(session):
    client = create_client(session, "Client A")
    record_ads_manager_access(session, client.id, "acct-123", "operator-a")
    with pytest.raises(ValueError, match="match"):
        import_ads_manager_snapshots(session, client_id=client.id, account_id="acct-other", source_name="export", snapshots=[{"aggregation_level": "campaign", "period": "2026-09-01/2026-09-02", "metrics": {"clicks": 1}}], actor_id="operator-a")
