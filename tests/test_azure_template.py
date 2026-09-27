from __future__ import annotations

import json
from pathlib import Path


def test_generated_azure_foundation_is_private_by_default():
    template_path = Path(__file__).resolve().parents[1] / "infra" / "azure" / "main.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    resources = template["resources"]

    vaults = [item for item in resources if item["type"] == "Microsoft.KeyVault/vaults"]
    assert len(vaults) == 2
    assert all(item["properties"]["publicNetworkAccess"] == "Disabled" for item in vaults)
    assert all(item["properties"]["networkAcls"]["defaultAction"] == "Deny" for item in vaults)
    assert all(item["properties"]["networkAcls"]["bypass"] == "None" for item in vaults)

    postgres = [item for item in resources if item["type"] == "Microsoft.DBforPostgreSQL/flexibleServers"]
    assert len(postgres) == 1
    assert postgres[0]["properties"]["network"]["publicNetworkAccess"] == "Disabled"
    assert "postgresql-flexible-server" in postgres[0]["properties"]["network"]["delegatedSubnetResourceId"]

    app_environment = [item for item in resources if item["type"] == "Microsoft.App/managedEnvironments"]
    assert len(app_environment) == 1
    assert app_environment[0]["properties"]["publicNetworkAccess"] == "Disabled"
    assert app_environment[0]["properties"]["vnetConfiguration"]["internal"] is True

    private_endpoints = [item for item in resources if item["type"] == "Microsoft.Network/privateEndpoints"]
    assert len(private_endpoints) == 2
    secure_parameters = template["parameters"]
    assert secure_parameters["postgresAdministratorPassword"]["type"] == "securestring"

    client_vault_assignments = [
        item for item in resources
        if item["type"] == "Microsoft.Authorization/roleAssignments"
        and "parameters('clientVaultName')" in item["scope"]
    ]
    assert len(client_vault_assignments) == 1
    assert "'eve-executor'" in client_vault_assignments[0]["properties"]["principalId"]


def test_admin_key_vault_role_can_write_delete_and_recover_but_never_read():
    template_path = Path(__file__).resolve().parents[1] / "infra" / "azure" / "secret-writer-role.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    role = next(item for item in template["resources"] if item["type"] == "Microsoft.Authorization/roleDefinitions")
    permissions = role["properties"]["permissions"][0]
    assert permissions["dataActions"] == [
        "Microsoft.KeyVault/vaults/secrets/setSecret/action",
        "Microsoft.KeyVault/vaults/secrets/delete",
        "Microsoft.KeyVault/vaults/secrets/recover/action",
    ]
    assert permissions["actions"] == []
    assert permissions["notDataActions"] == []


def test_generated_azure_apps_split_control_and_executor_permissions():
    template_path = Path(__file__).resolve().parents[1] / "infra" / "azure" / "apps.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    resources = template["resources"]
    apps = [item for item in resources if item["type"] == "Microsoft.App/containerApps"]
    jobs = [item for item in resources if item["type"] == "Microsoft.App/jobs"]
    assert len(apps) == 1
    assert len(jobs) == 2

    control = next(item for item in apps if item["name"] == "[parameters('controlAppName')]")
    control_env = {item["name"]: item.get("value", item.get("secretRef")) for item in control["properties"]["template"]["containers"][0]["env"]}
    executor = next(item for item in jobs if item["name"] == "[parameters('executorJobName')]")
    executor_env = {item["name"]: item.get("value", item.get("secretRef")) for item in executor["properties"]["template"]["containers"][0]["env"]}

    assert control_env["AGENCY_MCP_AUTH_MODE"] == "[parameters('mcpAuthMode')]"
    assert template["parameters"]["mcpAuthMode"]["defaultValue"] == "entra"
    assert template["parameters"]["mcpAuthMode"]["allowedValues"] == ["entra", "auth0"]
    assert control_env["AUTH0_DOMAIN"] == "[parameters('auth0Domain')]"
    assert control_env["AUTH0_AUDIENCE"] == "[parameters('auth0Audience')]"
    assert control_env["AUTH0_ROLE_CLAIM"] == "[parameters('auth0RoleClaim')]"
    assert control_env["AGENCY_MCP_JSON_RESPONSE"] == "true"
    assert control_env["AGENCY_MCP_STATELESS_HTTP"] == "true"
    assert template["parameters"]["adsMode"]["defaultValue"] == "mock"
    assert control_env["ADS_MODE"] == "[parameters('adsMode')]"
    assert template["parameters"]["mutationsEnabled"]["defaultValue"] is False
    assert control_env["AGENCY_MUTATIONS_ENABLED"] == "[if(parameters('mutationsEnabled'), 'true', 'false')]"
    assert control_env["DATABASE_URL"] == "database-url"
    assert "AZURE_KEY_VAULT_URL" not in control_env
    assert control["properties"]["configuration"]["ingress"]["external"] is True
    assert control["properties"]["template"]["scale"]["minReplicas"] == 0

    assert executor_env["EVE_PROCESS_ROLE"] == "executor"
    assert executor_env["AZURE_KEY_VAULT_URL"]
    assert executor_env["AZURE_CLIENT_ID"]
    assert executor_env["DATABASE_URL"] == "database-url"
    assert executor["properties"]["configuration"]["triggerType"] == "Schedule"
    assert executor["properties"]["configuration"]["scheduleTriggerConfig"]["cronExpression"] == "*/1 * * * *"
    assert executor["properties"]["template"]["containers"][0]["args"] == ["executor-drain", "--limit", "10"]

    bootstrap = next(item for item in jobs if item["name"] == "[parameters('bootstrapJobName')]")
    assert bootstrap["condition"] == "[parameters('enableDatabaseBootstrap')]"
    assert bootstrap["properties"]["configuration"]["secrets"][0]["name"] == "database-admin-url"
    assert bootstrap["properties"]["template"]["containers"][0]["args"] == ["db-bootstrap-runtime"]
    assert template["parameters"]["enableDatabaseBootstrap"]["defaultValue"] is False

    acr_pull = [
        item for item in resources
        if item["type"] == "Microsoft.Authorization/roleAssignments"
        and "Microsoft.ContainerRegistry/registries" in item["scope"]
    ]
    assert len(acr_pull) == 2


def test_live_host_uses_public_https_ingress_but_private_database_and_vault_paths():
    template_path = Path(__file__).resolve().parents[1] / "infra" / "azure" / "mcp-host.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    resources = template["resources"]

    postgres = next(item for item in resources if item["type"] == "Microsoft.DBforPostgreSQL/flexibleServers")
    assert postgres["properties"]["network"]["publicNetworkAccess"] == "Disabled"

    environment = next(item for item in resources if item["type"] == "Microsoft.App/managedEnvironments")
    assert environment["properties"]["publicNetworkAccess"] == "Enabled"
    assert environment["properties"]["vnetConfiguration"]["internal"] is False

    private_endpoints = [item for item in resources if item["type"] == "Microsoft.Network/privateEndpoints"]
    assert len(private_endpoints) == 2
    assert all("private-endpoints" in item["properties"]["subnet"]["id"] for item in private_endpoints)
