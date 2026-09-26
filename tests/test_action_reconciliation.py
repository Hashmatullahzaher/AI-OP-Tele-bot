import unittest

from osai.connectors.rest_write import (
    ClientRESTWriteAdapter,
    StaticWriteAuthProvider,
    UrllibWriteTransport,
    WriteHTTPError,
    WriteOperation,
    WriteSchemaInvalid,
    WriteSource,
    WriteUnavailable,
)
from osai.contracts import ExecutionContext

SOURCE = "pilot-client"


class ErrorWriteTransport:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def send_json(self, url, *, method, headers, payload, timeout_seconds, max_response_bytes):
        self.calls += 1
        raise self.error


class FakeWriteTransport:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def send_json(self, url, *, method, headers, payload, timeout_seconds, max_response_bytes):
        self.calls += 1
        return dict(self.response)


class TimeoutOpener:
    def open(self, request, timeout):
        raise TimeoutError("timed out")


class ReconciliationTransportTests(unittest.TestCase):
    def operation(self):
        return WriteOperation(
            action="customer.create",
            method="POST",
            path="/v1/customers",
            allowed_fields=("name", "customer_type"),
            required_fields=("name", "customer_type"),
            record_id_field="record.id",
            state_field="record.state",
            tenant_field="authorization.tenant_id",
            actor_field="authorization.actor_id",
            idempotency_field="idempotency_key",
            revision_field="record.version",
        )

    def source(self):
        return WriteSource(
            alias=SOURCE,
            base_url="https://sandbox.example.test",
            allowed_hostname="sandbox.example.test",
            operations=(self.operation(),),
        )

    def context(self):
        return ExecutionContext.issue(
            tenant_id="alpha",
            actor_id="owner",
            resource_scope=(f"client_api:{SOURCE}",),
        )

    def adapter(self, transport):
        return ClientRESTWriteAdapter(
            connector_id="client-1",
            source=self.source(),
            auth_provider=StaticWriteAuthProvider({"X-Unit-Auth": "unit"}),
            transport=transport,
        )

    def test_http_5xx_is_classified_as_uncertain(self):
        transport = ErrorWriteTransport(WriteHTTPError(500))
        with self.assertRaises(WriteUnavailable) as raised:
            self.adapter(transport).execute(
                action="customer.create",
                payload={"name": "A", "customer_type": "COMPANY"},
                context=self.context(),
                idempotency_key="idem:12345678",
            )
        self.assertTrue(getattr(raised.exception, "outcome_uncertain", False))
        self.assertEqual(transport.calls, 1)

    def test_invalid_receipt_is_classified_as_uncertain(self):
        transport = FakeWriteTransport(
            {
                "record": {"id": "CUS-1", "state": "CREATED", "version": "7"},
                "idempotency_key": "idem:12345678",
            }
        )
        with self.assertRaises(WriteSchemaInvalid) as raised:
            self.adapter(transport).execute(
                action="customer.create",
                payload={"name": "A", "customer_type": "COMPANY"},
                context=self.context(),
                idempotency_key="idem:12345678",
            )
        self.assertTrue(getattr(raised.exception, "outcome_uncertain", False))
        self.assertEqual(transport.calls, 1)

    def test_default_transport_timeout_is_uncertain(self):
        transport = UrllibWriteTransport(allowed_hostname="sandbox.example.test")
        transport._opener = TimeoutOpener()
        with self.assertRaises(WriteUnavailable) as raised:
            transport.send_json(
                "https://sandbox.example.test/v1/customers",
                method="POST",
                headers={"X-Unit-Auth": "unit"},
                payload={"name": "A"},
                timeout_seconds=5,
                max_response_bytes=1024,
            )
        self.assertTrue(getattr(raised.exception, "outcome_uncertain", False))


if __name__ == "__main__":
    unittest.main()
