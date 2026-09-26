import io
import unittest
import zipfile

from osai.connectors.google_drive import (
    GOOGLE_SHEET_MIME,
    XLSX_MIME,
    DriveAccessDenied,
    DriveLimitExceeded,
    DriveResource,
    DriveResourceNotAllowed,
    DriveSchemaAmbiguous,
    DriveTable,
    GoogleDriveConnector,
    GoogleHTTPError,
    StaticTokenProvider,
    drive_table_manifest,
    parse_xlsx_table,
)
from osai.contracts import CapabilityRegistry, ExecutionContext, ToolExecutor


class FakeTransport:
    def __init__(self, *, metadata, values=None, xlsx=b""):
        self.metadata = dict(metadata)
        self.values = values if values is not None else []
        self.xlsx = xlsx
        self.metadata_calls = 0
        self.values_calls = 0
        self.download_calls = 0
        self.fail_metadata_status = None

    def get_json(self, url, *, bearer_token, timeout_seconds):
        self.assert_token = bearer_token
        if "/drive/v3/files/" in url:
            self.metadata_calls += 1
            if self.fail_metadata_status is not None:
                raise GoogleHTTPError(self.fail_metadata_status)
            return dict(self.metadata)
        if "/v4/spreadsheets/" in url:
            self.values_calls += 1
            return {"values": self.values}
        raise AssertionError(f"unexpected URL: {url}")

    def get_bytes(self, url, *, bearer_token, timeout_seconds, max_bytes):
        self.download_calls += 1
        if len(self.xlsx) > max_bytes:
            raise DriveLimitExceeded("too large")
        return self.xlsx


def context(alias="pilot-finance-sheet"):
    return ExecutionContext.issue(
        tenant_id="alpha",
        actor_id="alpha-admin",
        resource_scope=(f"drive:{alias}",),
    )


def sheet_resource(*, parent="folder-1", mime=GOOGLE_SHEET_MIME):
    return DriveResource(
        alias="pilot-finance-sheet",
        file_id="sheet-123",
        kind="google_sheet",
        expected_parent_id=parent,
        tables=(DriveTable(alias="projects", sheet_name="Projects", a1_range="Projects!A1:F4"),),
    )


def xlsx_resource():
    return DriveResource(
        alias="pilot-finance-xlsx",
        file_id="xlsx-123",
        kind="xlsx",
        expected_parent_id="folder-1",
        tables=(DriveTable(alias="projects", sheet_name="Projects"),),
    )


def metadata(file_id, mime_type, *, parent="folder-1", size=None):
    data = {
        "id": file_id,
        "name": "pilot",
        "mimeType": mime_type,
        "modifiedTime": "2026-09-26T03:00:00Z",
        "parents": [parent],
        "capabilities": {"canDownload": True},
    }
    if size is not None:
        data["size"] = str(size)
    return data


def make_xlsx(*, duplicate_headers=False, formula=False, rows=2):
    workbook = """<?xml version='1.0' encoding='UTF-8'?>
    <workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
      xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>
      <sheets><sheet name='Projects' sheetId='1' r:id='rId1'/></sheets>
    </workbook>"""
    rels = """<?xml version='1.0' encoding='UTF-8'?>
    <Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>
      <Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet' Target='worksheets/sheet1.xml'/>
    </Relationships>"""
    h2 = "ProjectCode" if duplicate_headers else "BudgetAFN"
    body_rows = [
        "<row r='1'><c r='A1' t='inlineStr'><is><t>ProjectCode</t></is></c>"
        f"<c r='B1' t='inlineStr'><is><t>{h2}</t></is></c></row>"
    ]
    for idx in range(2, rows + 2):
        if formula and idx == 2:
            value = "<c r='B2'><f>1+6</f><v>7</v></c>"
        else:
            value = f"<c r='B{idx}'><v>{idx * 1000}</v></c>"
        body_rows.append(
            f"<row r='{idx}'><c r='A{idx}' t='inlineStr'><is><t>PRJ-{idx - 1:03d}</t></is></c>{value}</row>"
        )
    sheet = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData>"
        + "".join(body_rows)
        + "</sheetData></worksheet>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


class DriveConnectorTests(unittest.TestCase):
    def connector_for_sheet(self, transport):
        resource = sheet_resource()
        return GoogleDriveConnector(
            connector_id="drive-1",
            resources={resource.alias: resource},
            token_provider=StaticTokenProvider("test-token"),
            transport=transport,
        )

    def test_google_sheet_read_is_alias_only_and_source_grounded(self):
        transport = FakeTransport(
            metadata=metadata("sheet-123", GOOGLE_SHEET_MIME),
            values=[
                ["ProjectCode", "BudgetAFN"],
                ["PRJ-001", 500000],
                ["PRJ-002", 300000],
            ],
        )
        connector = self.connector_for_sheet(transport)
        registry = CapabilityRegistry()
        registry.register(drive_table_manifest(), connector)
        result = ToolExecutor(registry).execute(
            capability="drive.table.read",
            arguments={"resource_alias": "pilot-finance-sheet", "table_alias": "projects"},
            context=context(),
        )
        self.assertEqual(result.data["columns"], ["ProjectCode", "BudgetAFN"])
        self.assertEqual(result.data["rows"][0], ["PRJ-001", "500000"])
        self.assertEqual(result.data["row_count"], 2)
        self.assertEqual(result.provenance[0].source_id, "pilot-finance-sheet")
        self.assertEqual(result.provenance[0].revision, "2026-09-26T03:00:00Z")
        self.assertEqual(transport.metadata_calls, 1)
        self.assertEqual(transport.values_calls, 1)

    def test_raw_file_id_or_unknown_alias_cannot_be_addressed(self):
        transport = FakeTransport(metadata=metadata("sheet-123", GOOGLE_SHEET_MIME), values=[])
        connector = self.connector_for_sheet(transport)
        with self.assertRaises(DriveResourceNotAllowed):
            connector.invoke(
                capability="drive.table.read",
                arguments={"resource_alias": "sheet-123", "table_alias": "projects"},
                context=context(),
            )
        self.assertEqual(transport.metadata_calls, 0)

    def test_context_scope_must_match_resource_alias(self):
        transport = FakeTransport(metadata=metadata("sheet-123", GOOGLE_SHEET_MIME), values=[])
        connector = self.connector_for_sheet(transport)
        with self.assertRaises(DriveAccessDenied):
            connector.invoke(
                capability="drive.table.read",
                arguments={"resource_alias": "pilot-finance-sheet", "table_alias": "projects"},
                context=context("other-resource"),
            )
        self.assertEqual(transport.metadata_calls, 0)

    def test_parent_folder_is_rechecked_before_each_disclosure(self):
        transport = FakeTransport(
            metadata=metadata("sheet-123", GOOGLE_SHEET_MIME),
            values=[["A"], [1]],
        )
        connector = self.connector_for_sheet(transport)
        connector.invoke(
            capability="drive.table.read",
            arguments={"resource_alias": "pilot-finance-sheet", "table_alias": "projects"},
            context=context(),
        )
        transport.metadata["parents"] = ["different-folder"]
        with self.assertRaises(DriveAccessDenied):
            connector.invoke(
                capability="drive.table.read",
                arguments={"resource_alias": "pilot-finance-sheet", "table_alias": "projects"},
                context=context(),
            )
        self.assertEqual(transport.metadata_calls, 2)

    def test_google_acl_revocation_is_rechecked_before_each_disclosure(self):
        transport = FakeTransport(
            metadata=metadata("sheet-123", GOOGLE_SHEET_MIME),
            values=[["A"], [1]],
        )
        connector = self.connector_for_sheet(transport)
        connector.invoke(
            capability="drive.table.read",
            arguments={"resource_alias": "pilot-finance-sheet", "table_alias": "projects"},
            context=context(),
        )
        transport.fail_metadata_status = 403
        with self.assertRaises(DriveAccessDenied):
            connector.invoke(
                capability="drive.table.read",
                arguments={"resource_alias": "pilot-finance-sheet", "table_alias": "projects"},
                context=context(),
            )
        self.assertEqual(transport.metadata_calls, 2)

    def test_mime_type_mismatch_is_denied(self):
        transport = FakeTransport(metadata=metadata("sheet-123", XLSX_MIME), values=[["A"], [1]])
        connector = self.connector_for_sheet(transport)
        with self.assertRaises(DriveAccessDenied):
            connector.invoke(
                capability="drive.table.read",
                arguments={"resource_alias": "pilot-finance-sheet", "table_alias": "projects"},
                context=context(),
            )

    def test_duplicate_or_blank_headers_fail_closed(self):
        for headers in (["Code", "code"], ["Code", ""]):
            with self.subTest(headers=headers):
                transport = FakeTransport(
                    metadata=metadata("sheet-123", GOOGLE_SHEET_MIME),
                    values=[headers, ["P1", 1]],
                )
                connector = self.connector_for_sheet(transport)
                with self.assertRaises(DriveSchemaAmbiguous):
                    connector.invoke(
                        capability="drive.table.read",
                        arguments={"resource_alias": "pilot-finance-sheet", "table_alias": "projects"},
                        context=context(),
                    )

    def test_xlsx_read_is_bounded_and_normalized(self):
        payload = make_xlsx()
        transport = FakeTransport(
            metadata=metadata("xlsx-123", XLSX_MIME, size=len(payload)),
            xlsx=payload,
        )
        resource = xlsx_resource()
        connector = GoogleDriveConnector(
            connector_id="drive-1",
            resources={resource.alias: resource},
            token_provider=StaticTokenProvider("test-token"),
            transport=transport,
        )
        result = connector.invoke(
            capability="drive.table.read",
            arguments={"resource_alias": resource.alias, "table_alias": "projects"},
            context=context(resource.alias),
        )
        self.assertEqual(result.data["source_kind"], "xlsx")
        self.assertEqual(result.data["rows"][0], ["PRJ-001", "2000"])
        self.assertEqual(transport.download_calls, 1)

    def test_xlsx_formula_text_is_never_executed(self):
        values = parse_xlsx_table(
            make_xlsx(formula=True),
            sheet_name="Projects",
            max_rows=10,
            max_columns=10,
            max_uncompressed_bytes=1_000_000,
        )
        self.assertEqual(values[1][1], 7)
        self.assertNotIn("1+6", repr(values))

    def test_xlsx_duplicate_headers_fail_at_normalization(self):
        payload = make_xlsx(duplicate_headers=True)
        transport = FakeTransport(
            metadata=metadata("xlsx-123", XLSX_MIME, size=len(payload)),
            xlsx=payload,
        )
        resource = xlsx_resource()
        connector = GoogleDriveConnector(
            connector_id="drive-1",
            resources={resource.alias: resource},
            token_provider=StaticTokenProvider("test-token"),
            transport=transport,
        )
        with self.assertRaises(DriveSchemaAmbiguous):
            connector.invoke(
                capability="drive.table.read",
                arguments={"resource_alias": resource.alias, "table_alias": "projects"},
                context=context(resource.alias),
            )

    def test_xlsx_metadata_size_limit_blocks_download(self):
        payload = make_xlsx()
        transport = FakeTransport(
            metadata=metadata("xlsx-123", XLSX_MIME, size=9_000_000),
            xlsx=payload,
        )
        resource = xlsx_resource()
        connector = GoogleDriveConnector(
            connector_id="drive-1",
            resources={resource.alias: resource},
            token_provider=StaticTokenProvider("test-token"),
            transport=transport,
            max_download_bytes=1_000_000,
        )
        with self.assertRaises(DriveLimitExceeded):
            connector.invoke(
                capability="drive.table.read",
                arguments={"resource_alias": resource.alias, "table_alias": "projects"},
                context=context(resource.alias),
            )
        self.assertEqual(transport.download_calls, 0)

    def test_xlsx_row_limit_fails_closed(self):
        payload = make_xlsx(rows=4)
        with self.assertRaises(DriveLimitExceeded):
            parse_xlsx_table(
                payload,
                sheet_name="Projects",
                max_rows=3,
                max_columns=10,
                max_uncompressed_bytes=1_000_000,
            )


if __name__ == "__main__":
    unittest.main()
