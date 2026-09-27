from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_stdio_mcp_discovers_ads_only_tools_and_creates_no_spend_client(tmp_path):
    async def smoke() -> None:
        env = dict(os.environ)
        env.update(
            {
                "DATABASE_URL": f"sqlite:///{tmp_path / 'mcp.db'}",
                "ADS_MODE": "mock",
                "AGENCY_MUTATIONS_ENABLED": "false",
                "AGENCY_MCP_TRANSPORT": "stdio",
                "AGENCY_ROLE": "admin",
                "AGENCY_OPERATOR_ID": "mcp-smoke-admin",
            }
        )
        subprocess.run(
            [sys.executable, "-c", "from agency_mcp.cli import app; app()", "db-init"],
            env=env,
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
            text=True,
        )
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agency_mcp.mcp_server"],
            env=env,
            cwd=str(tmp_path),
        )
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as client:
                await client.initialize()
                tool_names = {tool.name for tool in (await client.list_tools()).tools}
                expected = {
                    "client_create",
                    "source_list",
                    "evidence_search",
                    "hint_set_draft",
                    "campaign_blueprint_compile",
                    "change_platform_validate",
                    "controlled_experiment_register",
                    "controlled_experiments_list",
                    "controlled_experiment_evaluate",
                    "ads_report_generate",
                    "ads_reports_list",
                    "ads_report_get",
                    "ads_report_review",
                    "ads_report_mark_sent",
                    "executor_jobs_list",
                    "audit_chain_verify",
                    "insights_sync",
                    "ads_manager_access_record",
                    "ads_manager_access_get",
                    "ads_manager_action_record",
                    "ads_manager_actions_list",
                    "ads_manager_insights_import",
                }
                assert expected <= tool_names
                assert "run_visibility_audit_tool" not in tool_names
                assert "ads_api_request" not in tool_names
                assert "client_secret_intake" not in tool_names
                assert not any("secret_set" in name or "secret_value" in name for name in tool_names)
                assert "executor_run" not in tool_names
                result = await client.call_tool(
                    "client_create",
                    {"name": "MCP Smoke", "vertical": "digital_products", "website": "https://example.com"},
                )
                assert not result.isError
                response = json.loads(result.content[0].text)
                assert response["name"] == "MCP Smoke"
                manager_access = await client.call_tool(
                    "ads_manager_access_record",
                    {"client_id": response["id"], "ad_account_id": "mock_account", "access_confirmed": True},
                )
                assert json.loads(manager_access.content[0].text)["verification"] == "operator_attested_not_platform_verified"
                manager_import = await client.call_tool(
                    "ads_manager_insights_import",
                    {
                        "client_id": response["id"], "ad_account_id": "mock_account",
                        "source_name": "chatgpt-ads-export.csv", "source_reference": "ticket-123",
                        "snapshots": [{"aggregation_level": "campaign", "period": "2026-09-01/2026-09-07", "timezone": "America/New_York", "metrics": {"clicks": 9, "spend": 3.25}}],
                    },
                )
                manager_import_data = json.loads(manager_import.content[0].text)
                assert manager_import_data["provider"] == "chatgpt_ads_manager_operator_import"
                assert manager_import_data["duplicate"] is False
                manager_action = await client.call_tool(
                    "ads_manager_action_record",
                    {
                        "client_id": response["id"], "action_type": "edit", "entity_type": "campaign",
                        "external_reference": "ticket-123-action-1", "outcome": "completed",
                        "performed_at": "2026-09-26T12:00:00Z", "entity_external_id": "cmp-123",
                        "details": {"platform_status": "paused"},
                    },
                )
                assert json.loads(manager_action.content[0].text)["verification"] == "operator_reported_not_api_verified"
                manager_actions = await client.call_tool("ads_manager_actions_list", {"client_id": response["id"]})
                assert len(json.loads(manager_actions.content[0].text)["actions"]) == 1
                audit_check = await client.call_tool("audit_chain_verify", {})
                assert json.loads(audit_check.content[0].text)["status"] == "verified"

                configured = await client.call_tool(
                    "ads_workspace_configure",
                    {"client_id": response["id"], "ad_account_id": "mock_account"},
                )
                assert not configured.isError
                metrics_job = await client.call_tool(
                    "insights_sync",
                    {
                        "client_id": response["id"],
                        "entity_external_id": "adgrp_mock",
                        "aggregation_level": "ad_group",
                        "period": "2026-09-01/2026-09-07",
                        "timezone": "America/New_York",
                    },
                )
                assert not metrics_job.isError
                assert json.loads(metrics_job.content[0].text)["status"] == "queued"

                source = await client.call_tool(
                    "source_ingest",
                    {
                        "client_id": response["id"],
                        "name": "authorized-operator-notes",
                        "content": "Operators compare workflow tools before changing their process.",
                        "source_url": "https://example.com/research",
                    },
                )
                assert not source.isError
                pending = await client.call_tool(
                    "evidence_search",
                    {"client_id": response["id"], "query": "workflow"},
                )
                assert not pending.isError
                assert json.loads(pending.content[0].text)["count"] == 0

                all_evidence = await client.call_tool(
                    "evidence_search",
                    {"client_id": response["id"], "query": "workflow", "review_status": "unreviewed"},
                )
                evidence_id = json.loads(all_evidence.content[0].text)["evidence"][0]["id"]
                reviewed = await client.call_tool(
                    "evidence_review",
                    {"client_id": response["id"], "evidence_id": evidence_id, "decision": "approved"},
                )
                assert not reviewed.isError
                approved = await client.call_tool(
                    "evidence_search",
                    {"client_id": response["id"], "query": "workflow"},
                )
                approved_result = json.loads(approved.content[0].text)
                assert approved_result["count"] == 1
                assert approved_result["evidence"][0]["source_name"] == "authorized-operator-notes"

                generated_report = await client.call_tool(
                    "ads_report_generate",
                    {"client_id": response["id"]},
                )
                report_data = json.loads(generated_report.content[0].text)
                assert report_data["review_status"] == "pending_review"
                assert report_data["readiness_status"] == "reviewable"
                listed_reports = await client.call_tool(
                    "ads_reports_list",
                    {"client_id": response["id"]},
                )
                assert json.loads(listed_reports.content[0].text)["reports"][0]["id"] == report_data["id"]
                loaded_report = await client.call_tool(
                    "ads_report_get",
                    {"report_id": report_data["id"]},
                )
                assert json.loads(loaded_report.content[0].text)["content_sha256"] == report_data["content_sha256"]
                approved_report = await client.call_tool(
                    "ads_report_review",
                    {"report_id": report_data["id"], "decision": "approve"},
                )
                assert not approved_report.isError
                assert json.loads(approved_report.content[0].text)["review_status"] == "client_ready"

    asyncio.run(smoke())
