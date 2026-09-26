import tempfile
import unittest
from pathlib import Path

from osai.connectors.rest_api import (
    APIParameter,
    APIOperation,
    ClientRESTConnector,
    RESTSource,
    ResponseField,
    RestAccessDenied,
    RestHTTPError,
    RestLimitExceeded,
    RestOperationNotAllowed,
    RestSchemaInvalid,
    StaticHeaderAuthProvider,
    client_api_table_manifest,
)
from osai.contracts import CapabilityRegistry, ExecutionContext, PolicyDenied, SchemaValidationError, ToolExecutor
from osai.storage import TenantSecurityStore


class FakeTransport:
    def __init__(self, payload=None, *, meta=None, error_status=None):
        self.payload = payload if payload is not None else {"data": []}
        self.meta = meta if meta is not None else {"etag": '"rev-1"'}
        self.error_status = error_status
        self.calls = []

    def get_json(self, url, *, headers, timeout_seconds, max_bytes):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
                "max_bytes": max_bytes,
            }
        )
        if self.error_status is not None:
            raise RestHTTPError(self.error_status)
        return self.payload, self.meta


class RecordingAuth:
    def __init__(self):
        self.contexts = []

    def headers(self, *, context, connector_id):
        self.contexts.append((context.tenant_id, context.actor_id, connector_id))
        return {"Authorization": f"Bearer tenant-{context.tenant_id}"}


def source():
    return RESTSource(
        alias="pilot-api",
        base_url="https://api.example.test/v1",
        allowed_hostname="api.example.test",
        operations=(
            APIOperation(
                alias="project-list",
                api_version="v1",
                path_template="/projects",
                parameters=(
                    APIParameter(name="status", location="query", enum=("active", "closed")),
                ),
                result_path=("data",),
                fields=(
                    ResponseField(output_name="ProjectCode", source_name="code"),
                    ResponseField(output_name="ProjectName", source_name="name"),
                    ResponseField(output_name="Customer", source_name="customer.name"),
                ),
            ),
            APIOperation(
                alias="project-get",
                api_version="v1",
                path_template="/projects/{project_id}",
                parameters=(APIParameter(name="project_id", location="path", required=True, max_length=64),),
                result_path=("data",),
                fields=(
                    ResponseField(output_name="ProjectCode", source_name="code"),
                    ResponseField(output_name="BudgetAFN", source_name="budget"),
                ),
            ),
        ),
    )


def context(tenant="alpha", actor="alpha-admin", source_alias="pilot-api"):
    return ExecutionContext.issue(
        tenant_id=tenant,
        actor_id=actor,
        resource_scope=(f"client_api:{source_alias}",),
    )


class RestConnectorTests(unittest.TestCase):
    def connector(self, transport, auth=None, max_rows=5000):
        return ClientRESTConnector(
            connector_id="rest-1",
            source=source(),
            auth_provider=auth or StaticHeaderAuthProvider({"Authorization": "Bearer test-only"}),
            transport=transport,
            max_rows=max_rows,
        )

    def test_registered_operation_builds_only_configured_get_url(self):
        transport = FakeTransport(
            {
                "data": [
                    {"code": "PRJ-001", "name": "North Office", "customer": {"name": "Aryana"}},
                    {"code": "PRJ-002", "name": "Warehouse", "customer": {"name": "Sadaf"}},
                ]
            }
        )
        connector = self.connector(transport)
        result = connector.invoke(
            capability="client_api.table.read",
            arguments={
                "source_alias": "pilot-api",
                "operation_alias": "project-list",
                "parameters": [{"name": "status", "value": "active"}],
            },
            context=context(),
        )
        self.assertEqual(transport.calls[0]["url"], "https://api.example.test/v1/projects?status=active")
        self.assertEqual(result.data["columns"], ["ProjectCode", "ProjectName", "Customer"])
        self.assertEqual(result.data["rows"][0], ["PRJ-001", "North Office", "Aryana"])
        self.assertEqual(result.provenance[0].source_id, "pilot-api")
        self.assertEqual(result.provenance[0].revision, '"rev-1"')

    def test_path_parameter_is_encoded_and_cannot_escape_route(self):
        transport = FakeTransport({"data": [{"code": "P1", "budget": 10}]})
        connector = self.connector(transport)
        with self.assertRaises(RestOperationNotAllowed):
            connector.invoke(
                capability="client_api.table.read",
                arguments={
                    "source_alias": "pilot-api",
                    "operation_alias": "project-get",
                    "parameters": [{"name": "project_id", "value": ".."}],
                },
                context=context(),
            )
        self.assertEqual(transport.calls, [])

    def test_unknown_operation_and_parameter_fail_before_network(self):
        transport = FakeTransport()
        connector = self.connector(transport)
        with self.assertRaises(RestOperationNotAllowed):
            connector.invoke(
                capability="client_api.table.read",
                arguments={"source_alias": "pilot-api", "operation_alias": "admin", "parameters": []},
                context=context(),
            )
        with self.assertRaises(RestOperationNotAllowed):
            connector.invoke(
                capability="client_api.table.read",
                arguments={
                    "source_alias": "pilot-api",
                    "operation_alias": "project-list",
                    "parameters": [{"name": "url", "value": "https://evil.test"}],
                },
                context=context(),
            )
        self.assertEqual(transport.calls, [])

    def test_tool_schema_rejects_raw_url_and_sql_fields(self):
        connector = self.connector(FakeTransport())
        registry = CapabilityRegistry()
        registry.register(client_api_table_manifest(), connector)
        executor = ToolExecutor(registry)
        for extra in (
            {"url": "https://evil.test"},
            {"sql": "select * from users"},
            {"method": "POST"},
        ):
            args = {"source_alias": "pilot-api", "operation_alias": "project-list", "parameters": []}
            args.update(extra)
            with self.subTest(extra=extra), self.assertRaises(SchemaValidationError):
                executor.execute(capability="client_api.table.read", arguments=args, context=context())

    def test_source_auth_is_derived_from_trusted_execution_context(self):
        auth = RecordingAuth()
        transport = FakeTransport({"data": []})
        connector = self.connector(transport, auth=auth)
        connector.invoke(
            capability="client_api.table.read",
            arguments={"source_alias": "pilot-api", "operation_alias": "project-list", "parameters": []},
            context=context(),
        )
        self.assertEqual(auth.contexts, [("alpha", "alpha-admin", "rest-1")])
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "Bearer tenant-alpha")

    def test_context_resource_scope_must_match_source(self):
        transport = FakeTransport()
        connector = self.connector(transport)
        with self.assertRaises(RestAccessDenied):
            connector.invoke(
                capability="client_api.table.read",
                arguments={"source_alias": "pilot-api", "operation_alias": "project-list", "parameters": []},
                context=context(source_alias="other-api"),
            )
        self.assertEqual(transport.calls, [])

    def test_source_401_403_404_fail_closed_without_schema_disclosure(self):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                connector = self.connector(FakeTransport(error_status=status))
                with self.assertRaises(RestAccessDenied):
                    connector.invoke(
                        capability="client_api.table.read",
                        arguments={"source_alias": "pilot-api", "operation_alias": "project-list", "parameters": []},
                        context=context(),
                    )

    def test_redirect_is_refused(self):
        connector = self.connector(FakeTransport(error_status=302))
        with self.assertRaises(RestOperationNotAllowed):
            connector.invoke(
                capability="client_api.table.read",
                arguments={"source_alias": "pilot-api", "operation_alias": "project-list", "parameters": []},
                context=context(),
            )

    def test_invalid_or_nested_collection_response_fails_closed(self):
        cases = [
            {"missing": []},
            {"data": {"not": "a-list"}},
            {"data": ["not-an-object"]},
            {"data": [{"code": "P1", "name": "X"}]},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                connector = self.connector(FakeTransport(payload))
                with self.assertRaises(RestSchemaInvalid):
                    connector.invoke(
                        capability="client_api.table.read",
                        arguments={"source_alias": "pilot-api", "operation_alias": "project-list", "parameters": []},
                        context=context(),
                    )

    def test_row_limit_is_enforced(self):
        connector = self.connector(
            FakeTransport(
                {
                    "data": [
                        {"code": "P1", "name": "A", "customer": {"name": "C1"}},
                        {"code": "P2", "name": "B", "customer": {"name": "C2"}},
                    ]
                }
            ),
            max_rows=1,
        )
        with self.assertRaises(RestLimitExceeded):
            connector.invoke(
                capability="client_api.table.read",
                arguments={"source_alias": "pilot-api", "operation_alias": "project-list", "parameters": []},
                context=context(),
            )

    def test_literal_ip_and_localhost_sources_are_rejected(self):
        for url, host in (
            ("https://127.0.0.1/v1", "127.0.0.1"),
            ("https://localhost/v1", "localhost"),
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                RESTSource(
                    alias="bad-api",
                    base_url=url,
                    allowed_hostname=host,
                    operations=source().operations,
                )

    def test_cross_tenant_policy_denies_before_source_call(self):
        transport = FakeTransport({"data": []})
        connector = self.connector(transport)
        registry = CapabilityRegistry()
        registry.register(client_api_table_manifest(), connector)
        with tempfile.TemporaryDirectory() as tmp:
            store = TenantSecurityStore(Path(tmp) / "policy.sqlite3")
            try:
                store.add_tenant("alpha")
                store.add_actor("alpha", "alpha-admin")
                store.add_tenant("beta")
                store.add_actor("beta", "beta-admin")
                store.grant("alpha", "alpha-admin", "client_api.table.read", "client_api:pilot-api")
                executor = ToolExecutor(registry, policy_check=store.authorize)
                forged = context(tenant="beta", actor="alpha-admin")
                with self.assertRaises(PolicyDenied):
                    executor.execute(
                        capability="client_api.table.read",
                        arguments={"source_alias": "pilot-api", "operation_alias": "project-list", "parameters": []},
                        context=forged,
                    )
                self.assertEqual(transport.calls, [])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
