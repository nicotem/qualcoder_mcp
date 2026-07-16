"""XSD validation of export_refi_qda output (COMPAT X11).

Validates the project.qde produced by the export tool against the
official REFI-QDA Project.xsd (QDA-XML 1.0, vendored with provenance in
tests/fixtures/refi_qda/). QualCoder itself never validates its exports
against the schema — its xml_validation methods are stubs — so this
suite is where conformance is actually proven. NVivo/ATLAS.ti-class
importers are stricter than QualCoder; schema validity is the bar.
"""

import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server

xmlschema = pytest.importorskip(
    "xmlschema",
    reason="xmlschema is a dev dependency (pip install -e '.[dev]')",
)

XSD_PATH = Path(__file__).parent / "fixtures" / "refi_qda" / "Project.xsd"


@pytest.fixture(scope="module")
def refi_schema():
    return xmlschema.XMLSchema(str(XSD_PATH))


def _data_qda(project_path) -> Path:
    return Path(project_path) / "data.qda"


def _exec(project_path, query, args=()):
    conn = sqlite3.connect(str(_data_qda(project_path)))
    conn.execute(query, args)
    conn.commit()
    conn.close()


def _export_and_validate(refi_schema, tmp_path, name, session_id=None):
    """Run the export tool and schema-validate the project.qde inside."""
    out_file = tmp_path / f"{name}.qdpx"
    result = json.loads(server.export_refi_qda(
        str(out_file), session_id=session_id, overwrite=True))
    assert result.get("success") is True, result
    xml_text = zipfile.ZipFile(out_file).read("project.qde").decode("utf-8")
    # Raises XMLSchemaValidationError with a precise diagnosis on failure
    refi_schema.validate(xml_text)
    return result, out_file


class TestXsdSchemaFixture:

    def test_vendored_schema_is_the_official_one(self, refi_schema):
        assert refi_schema.target_namespace == "urn:QDA-XML:project:1.0"
        # The root element the standard mandates
        assert "Project" in refi_schema.elements


class TestExportValidatesAgainstXsd:

    def test_normal_project(self, setup_server, qualcoder_db_path,
                            refi_schema, tmp_path):
        """The conftest fixture project: 2 codes, 2 codings, memos."""
        _export_and_validate(refi_schema, tmp_path, "normal")

    def test_category_hierarchy(self, setup_server, qualcoder_db_path,
                                refi_schema, tmp_path):
        """Nested categories exercise the isCodable='false' nesting."""
        _exec(qualcoder_db_path,
              "INSERT INTO code_cat VALUES (2, 'Sub cat', 'child', 't', "
              "'2024', 1)")
        _exec(qualcoder_db_path,
              "UPDATE code_name SET catid = 2 WHERE cid = 1")
        _exec(qualcoder_db_path,
              "UPDATE code_name SET catid = 1 WHERE cid = 2")
        server.switch_project(qualcoder_db_path)
        result, out_file = _export_and_validate(
            refi_schema, tmp_path, "categories")
        # sanity: the hierarchy really is in the document
        xml_text = zipfile.ZipFile(out_file).read("project.qde").decode()
        assert 'isCodable="false"' in xml_text

    def test_unicode_heavy_project(self, setup_server, qualcoder_db_path,
                                   refi_schema, tmp_path):
        """Emoji/CJK/RTL/combining text in names, memos and fulltext."""
        text = "😀 emoji intro\n日本語のテキスト here\nمرحبا بالعالم end\ncafé"
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext, memo) "
              "VALUES (50, 'unicode 😀 file.txt', ?, 'memo 日本語')", (text,))
        _exec(qualcoder_db_path,
              "INSERT INTO code_name VALUES (60, 'código 😀', 'memo عربى', "
              "NULL, 't', '2024', '#3498DB')")
        # code-point-correct coding on the emoji file
        seg = "日本語のテキスト"
        p0 = text.find(seg)
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (60, 50, ?, ?, ?, 't')", (seg, p0, p0 + len(seg)))
        server.switch_project(qualcoder_db_path)
        _export_and_validate(refi_schema, tmp_path, "unicode")

    def test_control_char_poison_project(self, setup_server,
                                         qualcoder_db_path, refi_schema,
                                         tmp_path):
        """Sanitized control chars must still yield schema-valid XML."""
        _exec(qualcoder_db_path,
              "UPDATE code_name SET name = 'ctrl\x0bchar', "
              "memo = 'memo with \x0c feed' WHERE cid = 1")
        server.switch_project(qualcoder_db_path)
        _export_and_validate(refi_schema, tmp_path, "poison")

    def test_skip_and_disclose_output(self, setup_server, qualcoder_db_path,
                                      refi_schema, tmp_path):
        """The QA2-5 path: invalid legacy rows skipped, output still valid."""
        # GUI-drifted row (positions beyond the text) + NULL-position row
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (2, 1, 'drifted', 70, 999, 'gui_user')")
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (2, 1, NULL, NULL, NULL, 'gui_user')")
        server.switch_project(qualcoder_db_path)
        result, _ = _export_and_validate(refi_schema, tmp_path, "skipped")
        assert result["skipped_invalid_codings"] == 2

    def test_minimal_project(self, setup_server, qualcoder_db_path,
                             refi_schema, tmp_path):
        """Empty-ish: a single coding, no memos anywhere."""
        _exec(qualcoder_db_path, "DELETE FROM code_text")
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = '' WHERE 1=1")
        _exec(qualcoder_db_path, "UPDATE source SET memo = '' WHERE 1=1")
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (1, 1, 'This is in', 0, 10, 't')")
        server.switch_project(qualcoder_db_path)
        _export_and_validate(refi_schema, tmp_path, "minimal")

    def test_session_export(self, setup_server, qualcoder_db_path,
                            refi_schema, tmp_path):
        """Session mode (AI suggestions with confidence tags)."""
        out = server.analyze_for_coding([1])
        sid = out.split("Session ID: `")[1].split("`")[0]
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "segment_text": "I feel stressed about deadlines",
            "reasoning": "explicit stress statement", "confidence": 0.9,
        }]))
        assert rec["recorded_count"] == 1
        _export_and_validate(refi_schema, tmp_path, "session",
                             session_id=sid)
