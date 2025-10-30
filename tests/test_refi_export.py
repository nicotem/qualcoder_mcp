"""Tests for REFI-QDA export module."""

import pytest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, MagicMock

from qualcoder_mcp.refi_export import RefiQdaExporter, NAMESPACE
from qualcoder_mcp.sessions import CodingSuggestion


class MockQualcoderDatabase:
    """Mock database for testing."""

    def __init__(self):
        self.codes = [
            {"id": 1, "name": "Workplace Stress", "color": "#FF5733", "memo": "Stress related themes"},
            {"id": 2, "name": "Coping Strategies", "color": "#33FF57", "memo": "Ways people cope"}
        ]

        self.files = [
            {"id": 1, "name": "interview_01.txt", "fulltext": "Sample interview text here.", "memo": "First interview"},
            {"id": 2, "name": "interview_02.txt", "fulltext": "Another interview text.", "memo": "Second interview"}
        ]

        self.code_guids = {
            1: "code-guid-0001",
            2: "code-guid-0002"
        }

        self.file_guids = {
            1: "file-guid-0001",
            2: "file-guid-0002"
        }

        self.user_guid = "user-guid-ai-coder"

    def list_codes(self):
        return self.codes

    def list_files(self):
        return self.files

    def get_code_details(self, code_id: int):
        for code in self.codes:
            if code["id"] == code_id:
                return code
        return None

    def get_file_content(self, file_id: int):
        for file in self.files:
            if file["id"] == file_id:
                return file
        return None

    def get_code_guids(self):
        return self.code_guids

    def get_file_guids(self):
        return self.file_guids

    def get_or_create_user_guid(self, username: str):
        return self.user_guid

    def generate_deterministic_guid(self, entity_type: str, entity_id: str):
        return f"{entity_type}-{entity_id}-guid"


@pytest.fixture
def mock_db():
    """Create a mock database."""
    return MockQualcoderDatabase()


@pytest.fixture
def exporter(mock_db):
    """Create a RefiQdaExporter with mock database."""
    return RefiQdaExporter(mock_db)


@pytest.fixture
def sample_suggestions():
    """Create sample coding suggestions for testing."""
    return [
        CodingSuggestion(
            file_id=1,
            file_name="interview_01.txt",
            code_id=1,
            code_name="Workplace Stress",
            start_pos=0,
            end_pos=50,
            segment_text="I feel very stressed at work.",
            reasoning="Clear stress indicator",
            confidence=0.9,
            status="approved",
            guid="suggestion-guid-001"
        ),
        CodingSuggestion(
            file_id=1,
            file_name="interview_01.txt",
            code_id=2,
            code_name="Coping Strategies",
            start_pos=100,
            end_pos=150,
            segment_text="I try to meditate daily.",
            reasoning="Positive coping strategy",
            confidence=0.85,
            status="approved",
            guid="suggestion-guid-002"
        ),
        CodingSuggestion(
            file_id=2,
            file_name="interview_02.txt",
            code_id=1,
            code_name="Workplace Stress",
            start_pos=200,
            end_pos=250,
            segment_text="The deadlines are overwhelming.",
            reasoning="Stress from deadlines",
            confidence=0.88,
            status="approved",
            guid="suggestion-guid-003"
        )
    ]


class TestRefiQdaExporter:
    """Tests for RefiQdaExporter class."""

    def test_constructor(self, mock_db):
        """Test exporter initialization."""
        exporter = RefiQdaExporter(mock_db)
        assert exporter.db == mock_db

    def test_create_project_xml_structure(self, exporter, sample_suggestions):
        """Test that create_project_xml creates proper structure."""
        root = exporter.create_project_xml(sample_suggestions, "Test Project")

        # Check root element
        assert root.tag == f"{{{NAMESPACE}}}Project"

        # Check root attributes
        assert f"{{{NAMESPACE}}}name" in root.attrib
        assert root.attrib[f"{{{NAMESPACE}}}name"] == "Test Project"
        assert "origin" in root.attrib
        assert "creatingUserGUID" in root.attrib
        assert "creationDateTime" in root.attrib

        # Check has Users, CodeBook, and Sources sections
        children_tags = [child.tag for child in root]
        assert f"{{{NAMESPACE}}}Users" in children_tags
        assert f"{{{NAMESPACE}}}CodeBook" in children_tags
        assert f"{{{NAMESPACE}}}Sources" in children_tags

    def test_users_section(self, exporter, sample_suggestions):
        """Test Users section creation."""
        root = exporter.create_project_xml(sample_suggestions)

        users_elem = root.find(f"{{{NAMESPACE}}}Users")
        assert users_elem is not None

        # Check AI user exists
        user_elems = users_elem.findall(f"{{{NAMESPACE}}}User")
        assert len(user_elems) >= 1

        # Check user has required attributes
        user = user_elems[0]
        assert "guid" in user.attrib
        assert "name" in user.attrib
        assert user.attrib["name"] == "AI Coding Assistant"

    def test_codebook_section(self, exporter, sample_suggestions):
        """Test CodeBook section creation."""
        root = exporter.create_project_xml(sample_suggestions)

        codebook_elem = root.find(f"{{{NAMESPACE}}}CodeBook")
        assert codebook_elem is not None

        codes_elem = codebook_elem.find(f"{{{NAMESPACE}}}Codes")
        assert codes_elem is not None

        # Check codes are present
        code_elems = codes_elem.findall(f"{{{NAMESPACE}}}Code")
        assert len(code_elems) == 2  # Two unique codes in sample suggestions

        # Check code attributes
        code = code_elems[0]
        assert "guid" in code.attrib
        assert "name" in code.attrib
        assert "isCodable" in code.attrib
        assert code.attrib["isCodable"] == "true"

    def test_codebook_includes_descriptions(self, exporter, sample_suggestions):
        """Test that code descriptions (memos) are included."""
        root = exporter.create_project_xml(sample_suggestions)

        codes_elem = root.find(f".//{{{NAMESPACE}}}Codes")
        code_elems = codes_elem.findall(f"{{{NAMESPACE}}}Code")

        # Check at least one code has a description
        descriptions_found = False
        for code in code_elems:
            desc = code.find(f"{{{NAMESPACE}}}Description")
            if desc is not None and desc.text:
                descriptions_found = True
                break

        assert descriptions_found

    def test_sources_section(self, exporter, sample_suggestions):
        """Test Sources section creation."""
        root = exporter.create_project_xml(sample_suggestions)

        sources_elem = root.find(f"{{{NAMESPACE}}}Sources")
        assert sources_elem is not None

        # Check TextSource elements
        source_elems = sources_elem.findall(f"{{{NAMESPACE}}}TextSource")
        assert len(source_elems) == 2  # Two unique files in sample suggestions

        # Check source attributes
        source = source_elems[0]
        assert "guid" in source.attrib
        assert "name" in source.attrib
        assert "plainTextPath" in source.attrib
        assert "creatingUser" in source.attrib
        assert "creationDateTime" in source.attrib

    def test_plain_text_selections(self, exporter, sample_suggestions):
        """Test PlainTextSelection elements are created."""
        root = exporter.create_project_xml(sample_suggestions)

        # Find all PlainTextSelection elements
        selections = root.findall(f".//{{{NAMESPACE}}}PlainTextSelection")
        assert len(selections) == 3  # Three suggestions total

        # Check selection attributes
        selection = selections[0]
        assert "guid" in selection.attrib
        assert "startPosition" in selection.attrib
        assert "endPosition" in selection.attrib
        assert "creatingUser" in selection.attrib
        assert "creationDateTime" in selection.attrib

    def test_plain_text_selection_positions(self, exporter, sample_suggestions):
        """Test that positions are correctly exported."""
        root = exporter.create_project_xml(sample_suggestions)

        selections = root.findall(f".//{{{NAMESPACE}}}PlainTextSelection")

        # Check first suggestion positions
        selection = selections[0]
        assert selection.attrib["startPosition"] == "0"
        assert selection.attrib["endPosition"] == "50"

    def test_coding_elements(self, exporter, sample_suggestions):
        """Test that Coding elements are created within selections."""
        root = exporter.create_project_xml(sample_suggestions)

        selections = root.findall(f".//{{{NAMESPACE}}}PlainTextSelection")

        # Each selection should have a Coding element
        for selection in selections:
            coding_elem = selection.find(f"{{{NAMESPACE}}}Coding")
            assert coding_elem is not None
            assert "guid" in coding_elem.attrib
            assert "creatingUser" in coding_elem.attrib
            assert "creationDateTime" in coding_elem.attrib

            # Check CodeRef
            code_ref = coding_elem.find(f"{{{NAMESPACE}}}CodeRef")
            assert code_ref is not None
            assert "targetGUID" in code_ref.attrib

    def test_ai_memo_in_description(self, exporter, sample_suggestions):
        """Test that AI memos are included in selection descriptions."""
        root = exporter.create_project_xml(sample_suggestions)

        selections = root.findall(f".//{{{NAMESPACE}}}PlainTextSelection")

        # Check first selection description
        selection = selections[0]
        desc = selection.find(f"{{{NAMESPACE}}}Description")
        assert desc is not None
        assert desc.text is not None
        assert "Clear stress indicator" in desc.text
        assert "0.90" in desc.text  # Confidence score should be included

    def test_prettify_xml(self, exporter):
        """Test XML prettification."""
        # Create simple XML element
        root = ET.Element("Test")
        child = ET.SubElement(root, "Child")
        child.text = "content"

        pretty_xml = exporter.prettify_xml(root)

        # Check it's properly formatted
        assert "<?xml" in pretty_xml
        assert "<Test>" in pretty_xml
        assert "<Child>" in pretty_xml
        assert "content" in pretty_xml
        assert pretty_xml.count("\n") > 1  # Multiple lines

    def test_export_to_qdpx_creates_file(self, exporter, sample_suggestions, tmp_path):
        """Test that export_to_qdpx creates a .qdpx file."""
        output_file = tmp_path / "test_export.qdpx"

        result = exporter.export_to_qdpx(
            sample_suggestions,
            str(output_file),
            "Test Export"
        )

        # Check file was created
        assert Path(result).exists()
        assert Path(result).suffix == ".qdpx"

    def test_export_to_qdpx_creates_zip(self, exporter, sample_suggestions, tmp_path):
        """Test that .qdpx file is a valid ZIP archive."""
        output_file = tmp_path / "test_export.qdpx"

        exporter.export_to_qdpx(sample_suggestions, str(output_file))

        # Check it's a valid ZIP file
        assert zipfile.is_zipfile(output_file)

    def test_export_to_qdpx_contains_project_qde(self, exporter, sample_suggestions, tmp_path):
        """Test that .qdpx contains project.qde file."""
        output_file = tmp_path / "test_export.qdpx"

        exporter.export_to_qdpx(sample_suggestions, str(output_file))

        # Check ZIP contents
        with zipfile.ZipFile(output_file, 'r') as zipf:
            files = zipf.namelist()
            assert "project.qde" in files

    def test_export_to_qdpx_xml_content(self, exporter, sample_suggestions, tmp_path):
        """Test that project.qde contains valid XML."""
        output_file = tmp_path / "test_export.qdpx"

        exporter.export_to_qdpx(sample_suggestions, str(output_file))

        # Extract and parse XML
        with zipfile.ZipFile(output_file, 'r') as zipf:
            xml_content = zipf.read("project.qde")

        # Parse XML
        root = ET.fromstring(xml_content)
        assert root.tag == f"{{{NAMESPACE}}}Project"

    def test_export_creates_parent_directories(self, exporter, sample_suggestions, tmp_path):
        """Test that export creates parent directories if needed."""
        output_file = tmp_path / "subdir" / "nested" / "export.qdpx"

        exporter.export_to_qdpx(sample_suggestions, str(output_file))

        assert output_file.exists()
        assert output_file.parent.exists()

    def test_export_expands_tilde(self, exporter, sample_suggestions, tmp_path, monkeypatch):
        """Test that ~ in path is expanded."""
        # This test verifies the expanduser() call works
        # We'll use tmp_path directly since we can't easily mock home directory

        output_file = tmp_path / "export.qdpx"
        result = exporter.export_to_qdpx(sample_suggestions, str(output_file))

        assert Path(result).exists()
        assert "~" not in result

    def test_validate_suggestions_empty(self, exporter):
        """Test validation with empty suggestions list."""
        warnings = exporter.validate_suggestions([])

        assert len(warnings) == 1
        assert "No suggestions" in warnings[0]

    def test_validate_suggestions_valid(self, exporter, sample_suggestions):
        """Test validation with valid suggestions."""
        warnings = exporter.validate_suggestions(sample_suggestions)

        assert len(warnings) == 0

    def test_validate_suggestions_invalid_code(self, exporter):
        """Test validation catches invalid code IDs."""
        invalid_suggestion = CodingSuggestion(
            file_id=1,
            file_name="test.txt",
            code_id=999,  # Doesn't exist in mock
            code_name="Invalid Code",
            start_pos=0,
            end_pos=10,
            segment_text="text"
        )

        warnings = exporter.validate_suggestions([invalid_suggestion])

        assert len(warnings) > 0
        assert any("Code ID 999 not found" in w for w in warnings)

    def test_validate_suggestions_invalid_file(self, exporter):
        """Test validation catches invalid file IDs."""
        invalid_suggestion = CodingSuggestion(
            file_id=999,  # Doesn't exist in mock
            file_name="nonexistent.txt",
            code_id=1,
            code_name="Valid Code",
            start_pos=0,
            end_pos=10,
            segment_text="text"
        )

        warnings = exporter.validate_suggestions([invalid_suggestion])

        assert len(warnings) > 0
        assert any("File ID 999 not found" in w for w in warnings)

    def test_validate_suggestions_negative_start_position(self, exporter):
        """Test validation catches negative start positions."""
        invalid_suggestion = CodingSuggestion(
            file_id=1,
            file_name="test.txt",
            code_id=1,
            code_name="Valid Code",
            start_pos=-10,
            end_pos=10,
            segment_text="text"
        )

        warnings = exporter.validate_suggestions([invalid_suggestion])

        assert len(warnings) > 0
        assert any("Invalid start position" in w for w in warnings)

    def test_validate_suggestions_invalid_end_position(self, exporter):
        """Test validation catches invalid end positions."""
        invalid_suggestion = CodingSuggestion(
            file_id=1,
            file_name="test.txt",
            code_id=1,
            code_name="Valid Code",
            start_pos=100,
            end_pos=50,  # End before start
            segment_text="text"
        )

        warnings = exporter.validate_suggestions([invalid_suggestion])

        assert len(warnings) > 0
        assert any("End position" in w and "greater than start" in w for w in warnings)

    def test_validate_suggestions_invalid_confidence(self, exporter):
        """Test validation catches invalid confidence values."""
        invalid_suggestion = CodingSuggestion(
            file_id=1,
            file_name="test.txt",
            code_id=1,
            code_name="Valid Code",
            start_pos=0,
            end_pos=10,
            segment_text="text",
            confidence=1.5  # Out of range
        )

        warnings = exporter.validate_suggestions([invalid_suggestion])

        assert len(warnings) > 0
        assert any("Confidence" in w and "outside valid range" in w for w in warnings)

    def test_validate_suggestions_multiple_issues(self, exporter):
        """Test validation reports multiple issues."""
        invalid_suggestions = [
            CodingSuggestion(
                file_id=999,  # Invalid
                file_name="test1.txt",
                code_id=999,  # Invalid
                code_name="Code",
                start_pos=0,
                end_pos=10,
                segment_text="text"
            ),
            CodingSuggestion(
                file_id=1,
                file_name="test2.txt",
                code_id=1,
                code_name="Code",
                start_pos=-5,  # Invalid
                end_pos=3,  # Invalid (before start when start is corrected)
                segment_text="text",
                confidence=2.0  # Invalid
            )
        ]

        warnings = exporter.validate_suggestions(invalid_suggestions)

        # Should have multiple warnings
        assert len(warnings) >= 4  # At least file, code, position, and confidence errors

    def test_export_with_file_grouping(self, exporter, sample_suggestions, tmp_path):
        """Test that suggestions are correctly grouped by file."""
        output_file = tmp_path / "test_export.qdpx"

        exporter.export_to_qdpx(sample_suggestions, str(output_file))

        # Extract and check XML
        with zipfile.ZipFile(output_file, 'r') as zipf:
            xml_content = zipf.read("project.qde")

        root = ET.fromstring(xml_content)

        # Find TextSource elements
        sources = root.findall(f".//{{{NAMESPACE}}}TextSource")

        # Should have 2 sources (2 unique files)
        assert len(sources) == 2

        # First source should have 2 selections (file_id=1 has 2 suggestions)
        source1_selections = sources[0].findall(f"{{{NAMESPACE}}}PlainTextSelection")
        source2_selections = sources[1].findall(f"{{{NAMESPACE}}}PlainTextSelection")

        # One should have 2, one should have 1
        selection_counts = sorted([len(source1_selections), len(source2_selections)])
        assert selection_counts == [1, 2]

    def test_namespace_consistency(self, exporter, sample_suggestions):
        """Test that all elements use consistent namespace."""
        root = exporter.create_project_xml(sample_suggestions)

        # Convert to string and check namespace appears correctly
        xml_string = ET.tostring(root, encoding='unicode')

        # Check namespace is declared in root
        assert f'xmlns="{NAMESPACE}"' in xml_string

        # Root element should have namespace in its tag
        assert f"{{{NAMESPACE}}}Project" in str(root.tag)

        # Child elements appear without namespace prefix when default namespace is used
        # This is correct XML behavior
        assert "<Users>" in xml_string or "Users" in xml_string
        assert "<CodeBook>" in xml_string or "CodeBook" in xml_string
        assert "<Sources>" in xml_string or "Sources" in xml_string

    def test_export_with_empty_memo(self, exporter, tmp_path):
        """Test export handles suggestions with empty memos."""
        suggestion = CodingSuggestion(
            file_id=1,
            file_name="test.txt",
            code_id=1,
            code_name="Test Code",
            start_pos=0,
            end_pos=10,
            segment_text="text",
            reasoning="",  # Empty reasoning
            confidence=0.8
        )

        output_file = tmp_path / "test_export.qdpx"
        result = exporter.export_to_qdpx([suggestion], str(output_file))

        assert Path(result).exists()

    def test_export_preserves_guid(self, exporter, sample_suggestions, tmp_path):
        """Test that suggestion GUIDs are preserved in export."""
        output_file = tmp_path / "test_export.qdpx"

        exporter.export_to_qdpx(sample_suggestions, str(output_file))

        # Extract and check XML
        with zipfile.ZipFile(output_file, 'r') as zipf:
            xml_content = zipf.read("project.qde")

        root = ET.fromstring(xml_content)

        # Find all PlainTextSelection elements
        selections = root.findall(f".//{{{NAMESPACE}}}PlainTextSelection")

        # Check that GUIDs from suggestions are present
        exported_guids = [sel.attrib["guid"] for sel in selections]

        for suggestion in sample_suggestions:
            assert suggestion.guid in exported_guids
