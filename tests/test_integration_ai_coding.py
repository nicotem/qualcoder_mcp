"""Integration tests for complete AI coding workflow.

These tests verify that all components work together correctly:
- Database GUID generation
- Session management and persistence
- REFI-QDA export with actual database data
- End-to-end workflow from suggestions to import-ready file

Spans here are deliberately short. They used to run to position 150 in
a project that no longer exists for anyone; they now sit inside the
shortest source of the tmp_path fixture, which is what the REFI export
validator checks before it builds anything.
"""

import pytest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
import tempfile
import shutil

from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.sessions import CodingSuggestion, AICodingSession, SessionManager
from qualcoder_mcp.refi_export import RefiQdaExporter, NAMESPACE


@pytest.fixture
def test_db(qualcoder_db_path):
    """Read-only connection to a fixture project built in tmp_path.

    This used to be `Path.home() / "Documents" / "QDA Projects" /
    "test_project.qda"`, computed at import and skipped when absent.
    Forty-four of the suite's forty-six skips were these two modules,
    which means they ran for exactly one person and nobody else, on no
    CI job, on no platform, ever. Worse, on a machine where the
    researcher DOES have a project by that name, forty-four tests
    silently start reading their live data, which is the same
    home-derived import-bound binding the workspace escape came from.

    `qualcoder_db_path` (tests/conftest.py) builds the full v14 schema
    in tmp_path with two codes, two sources, two codings, a category, a
    case, a case text span and the attribute tables, which satisfies
    every assertion here, including the ones that require a non-empty
    codebook and more than one file.
    """
    db = QualcoderDatabase(qualcoder_db_path)
    yield db
    db.close()


@pytest.fixture
def temp_dir():
    """Create temporary directory for test outputs."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def session_manager(temp_dir):
    """Create session manager with temp storage."""
    return SessionManager(temp_dir)


class TestCompleteAICodingWorkflow:
    """Integration tests for complete AI coding workflow."""

    def test_create_suggestions_from_real_database(self, test_db):
        """Test creating coding suggestions using actual database data."""
        # Get actual codes and files from database
        codes = test_db.list_codes()
        files = test_db.list_files()

        assert len(codes) > 0, "Test database should have codes"
        assert len(files) > 0, "Test database should have files"

        # Create a suggestion using real data
        code = codes[0]
        file = files[0]

        suggestion = CodingSuggestion(
            file_id=file["id"],
            file_name=file["name"],
            code_id=code["id"],
            code_name=code["name"],
            start_pos=0,
            end_pos=30,
            segment_text="Sample coded segment",
            reasoning="AI identified this segment",
            confidence=0.85,
            status="approved"
        )

        # Verify suggestion is valid
        assert suggestion.file_id == file["id"]
        assert suggestion.code_id == code["id"]
        assert suggestion.confidence == 0.85

    def test_session_creation_and_persistence(self, test_db, session_manager, qualcoder_db_path):
        """Test creating a session, adding suggestions, and persisting to disk."""
        # Get data from database
        codes = test_db.list_codes()
        files = test_db.list_files()

        # Create session
        session = AICodingSession(
            project_path=qualcoder_db_path,
            description="Integration test session",
            file_ids=[f["id"] for f in files[:2]],
            code_names=[c["name"] for c in codes[:2]],
            instruction="Test coding instruction",
            min_confidence=0.6
        )

        # Add suggestions
        for i, (file, code) in enumerate(zip(files[:2], codes[:2])):
            suggestion = CodingSuggestion(
                file_id=file["id"],
                file_name=file["name"],
                code_id=code["id"],
                code_name=code["name"],
                start_pos=i * 5,
                end_pos=(i * 5) + 10,
                segment_text=f"Test segment {i}",
                reasoning=f"Test memo {i}",
                confidence=0.8 + (i * 0.05)
            )
            session.add_suggestion(suggestion)

        # Save session
        session_manager.save_session(session)

        # Verify saved
        assert session_manager.session_exists(session.session_id)

        # Load and verify
        loaded = session_manager.load_session(session.session_id)
        assert loaded.session_id == session.session_id
        assert len(loaded.suggestions) == 2
        assert loaded.project_path == qualcoder_db_path

    def test_export_to_refi_qda(self, test_db, temp_dir):
        """Test exporting suggestions to REFI-QDA format."""
        # Get data from database
        codes = test_db.list_codes()
        files = test_db.list_files()

        # Create suggestions
        suggestions = []
        for i, (file, code) in enumerate(zip(files[:2], codes[:2])):
            suggestions.append(CodingSuggestion(
                file_id=file["id"],
                file_name=file["name"],
                code_id=code["id"],
                code_name=code["name"],
                start_pos=i * 5,
                end_pos=(i * 5) + 10,
                segment_text=f"Test segment {i}",
                reasoning=f"Test memo {i}",
                confidence=0.85,
                status="approved"
            ))

        # Export to REFI-QDA
        exporter = RefiQdaExporter(test_db)
        output_file = Path(temp_dir) / "test_export.qdpx"

        result = exporter.export_to_qdpx(
            suggestions,
            str(output_file),
            "Integration Test Export"
        )

        # Verify file was created
        assert Path(result).exists()
        assert zipfile.is_zipfile(result)

        # Verify contents
        with zipfile.ZipFile(result, 'r') as zipf:
            assert "project.qde" in zipf.namelist()
            xml_content = zipf.read("project.qde")

        # Parse and validate XML structure
        root = ET.fromstring(xml_content)
        assert root.tag == f"{{{NAMESPACE}}}Project"

        # Check has expected sections
        assert root.find(f".//{{{NAMESPACE}}}Users") is not None
        assert root.find(f".//{{{NAMESPACE}}}CodeBook") is not None
        assert root.find(f".//{{{NAMESPACE}}}Sources") is not None

    def test_full_workflow_suggestions_to_export(self, test_db, session_manager, temp_dir, qualcoder_db_path):
        """Test complete workflow: create suggestions, save session, export to REFI-QDA."""
        # Step 1: Get database data
        codes = test_db.list_codes()
        files = test_db.list_files()

        # Step 2: Create session
        session = AICodingSession(
            project_path=qualcoder_db_path,
            description="Full workflow test",
            file_ids=[f["id"] for f in files],
            code_names=[c["name"] for c in codes],
            instruction="Code all relevant segments",
            min_confidence=0.7
        )

        # Step 3: Add multiple suggestions
        for i in range(3):
            file = files[i % len(files)]
            code = codes[i % len(codes)]

            suggestion = CodingSuggestion(
                file_id=file["id"],
                file_name=file["name"],
                code_id=code["id"],
                code_name=code["name"],
                start_pos=i * 5,
                end_pos=(i * 5) + 10,
                segment_text=f"Sample text segment {i}",
                reasoning=f"AI analysis memo {i}",
                confidence=0.75 + (i * 0.05),
                status="approved"
            )
            session.add_suggestion(suggestion)

        # Step 4: Save session
        session_manager.save_session(session)
        assert session_manager.session_exists(session.session_id)

        # Step 5: Load session
        loaded_session = session_manager.load_session(session.session_id)
        assert len(loaded_session.suggestions) == 3

        # Step 6: Get statistics
        stats = loaded_session.get_statistics()
        assert stats["total_suggestions"] == 3
        assert stats["approved"] == 3

        # Step 7: Export to REFI-QDA
        exporter = RefiQdaExporter(test_db)
        output_file = Path(temp_dir) / "full_workflow_export.qdpx"

        result = exporter.export_to_qdpx(
            loaded_session.suggestions,
            str(output_file),
            "Full Workflow Test"
        )

        # Step 8: Verify export
        assert Path(result).exists()

        # Step 9: Validate export contents
        with zipfile.ZipFile(result, 'r') as zipf:
            xml_content = zipf.read("project.qde")

        root = ET.fromstring(xml_content)

        # Count PlainTextSelection elements (should match suggestion count)
        selections = root.findall(f".//{{{NAMESPACE}}}PlainTextSelection")
        assert len(selections) == 3

        # Verify each selection has coding
        for selection in selections:
            coding = selection.find(f"{{{NAMESPACE}}}Coding")
            assert coding is not None
            code_ref = coding.find(f"{{{NAMESPACE}}}CodeRef")
            assert code_ref is not None

    def test_validation_before_export(self, test_db):
        """Test that validation catches issues before export."""
        # Create invalid suggestion (code doesn't exist)
        invalid_suggestion = CodingSuggestion(
            file_id=1,
            file_name="test.txt",
            code_id=9999,  # Doesn't exist
            code_name="Invalid Code",
            start_pos=0,
            end_pos=30,
            segment_text="test",
            confidence=0.8
        )

        # Validate
        exporter = RefiQdaExporter(test_db)
        warnings = exporter.validate_suggestions([invalid_suggestion])

        # Should have warning about invalid code
        assert len(warnings) > 0
        assert any("Code ID 9999" in w for w in warnings)

    def test_validation_reports_a_span_past_the_end_of_the_file(self, test_db):
        """The validator branch the shortened spans stopped exercising.

        Shortening the spans in this module was right: they ran to
        position 150 in files of 78 and 37 characters, which the
        validator refuses. But that span was the only thing in the suite
        reaching `refi_export.validate_suggestions`'s "beyond the file
        text" branch, measured by running the whole suite under coverage
        (the line was reported unexecuted afterwards). So the branch gets
        a test of its own, which says what it is checking instead of
        exercising it by accident, and the length is read from the
        fixture rather than hard-coded so that resizing a source cannot
        quietly vacate it.
        """
        files = test_db.list_files()
        codes = test_db.list_codes()
        file_id = files[0]["id"]
        length = len((test_db.get_file_content(file_id) or {}).get(
            "content") or "")
        assert length > 0, "the fixture source must have text"

        def suggestion(end_pos):
            return CodingSuggestion(
                file_id=file_id,
                file_name=files[0]["name"],
                code_id=codes[0]["id"],
                code_name=codes[0]["name"],
                start_pos=0,
                end_pos=end_pos,
                segment_text="Test",
                confidence=0.8
            )

        exporter = RefiQdaExporter(test_db)

        past_the_end = exporter.validate_suggestions(
            [suggestion(length + 50)])
        assert any(f"End position {length + 50} is beyond the file text"
                   in w for w in past_the_end), past_the_end

        # The same span, ending exactly at the last character: clean, so
        # the warning above is the span's doing and not the fixture's.
        assert exporter.validate_suggestions([suggestion(length)]) == []

    def test_guid_consistency_in_export(self, test_db, temp_dir):
        """Test that GUIDs are consistent when exporting same data multiple times."""
        # Get data
        codes = test_db.list_codes()
        files = test_db.list_files()

        # Create suggestion
        suggestion = CodingSuggestion(
            file_id=files[0]["id"],
            file_name=files[0]["name"],
            code_id=codes[0]["id"],
            code_name=codes[0]["name"],
            start_pos=0,
            end_pos=30,
            segment_text="Test",
            confidence=0.8,
            guid="test-guid-123"  # Fixed GUID
        )

        # Export twice
        exporter = RefiQdaExporter(test_db)

        output1 = Path(temp_dir) / "export1.qdpx"
        output2 = Path(temp_dir) / "export2.qdpx"

        exporter.export_to_qdpx([suggestion], str(output1))
        exporter.export_to_qdpx([suggestion], str(output2))

        # Extract and compare
        with zipfile.ZipFile(output1, 'r') as zipf:
            xml1 = zipf.read("project.qde")
        with zipfile.ZipFile(output2, 'r') as zipf:
            xml2 = zipf.read("project.qde")

        root1 = ET.fromstring(xml1)
        root2 = ET.fromstring(xml2)

        # Get code GUIDs from both exports
        codes1 = root1.findall(f".//{{{NAMESPACE}}}Code")
        codes2 = root2.findall(f".//{{{NAMESPACE}}}Code")

        # Same code should have same GUID in both exports.
        #
        # Asserted rather than guarded: `if codes1 and codes2:` put the
        # test's ONLY claim behind a condition, so an export that wrote
        # no Code element at all vacated the test silently and it passed
        # green. The emptiness is now the first thing checked, and the
        # comparison covers every code rather than the first one.
        assert codes1, "the first export wrote no Code element"
        assert len(codes2) == len(codes1)
        guids1 = [c.attrib["guid"] for c in codes1]
        guids2 = [c.attrib["guid"] for c in codes2]
        assert guids1 == guids2

    def test_multiple_files_grouped_correctly(self, test_db, temp_dir):
        """Test that suggestions for multiple files are grouped correctly in export."""
        codes = test_db.list_codes()
        files = test_db.list_files()

        # Create suggestions for different files
        suggestions = []
        for i, file in enumerate(files[:2]):  # Use first 2 files
            for j in range(2):  # 2 suggestions per file
                suggestions.append(CodingSuggestion(
                    file_id=file["id"],
                    file_name=file["name"],
                    code_id=codes[0]["id"],
                    code_name=codes[0]["name"],
                    start_pos=j * 5,
                    end_pos=(j * 5) + 10,
                    segment_text=f"Text {i}-{j}",
                    confidence=0.8
                ))

        # Export
        exporter = RefiQdaExporter(test_db)
        output_file = Path(temp_dir) / "grouped_export.qdpx"
        exporter.export_to_qdpx(suggestions, str(output_file))

        # Verify grouping
        with zipfile.ZipFile(output_file, 'r') as zipf:
            xml_content = zipf.read("project.qde")

        root = ET.fromstring(xml_content)
        sources = root.findall(f".//{{{NAMESPACE}}}TextSource")

        # Should have 2 TextSource elements (one per file)
        assert len(sources) == 2

        # Each should have 2 PlainTextSelection elements
        for source in sources:
            selections = source.findall(f"{{{NAMESPACE}}}PlainTextSelection")
            assert len(selections) == 2

    def test_session_list_and_cleanup(self, test_db, session_manager, qualcoder_db_path):
        """Test session listing and cleanup functionality."""
        # Create multiple sessions
        sessions = []
        for i in range(3):
            session = AICodingSession(
                project_path=qualcoder_db_path,
                description=f"Test session {i}",
                file_ids=[1],
                code_names=["Test"]
            )
            session_manager.save_session(session)
            sessions.append(session)

        # List sessions
        all_sessions = session_manager.list_sessions()
        assert len(all_sessions) >= 3

        # List sessions for this project
        project_sessions = session_manager.list_sessions(
            project_path=qualcoder_db_path
        )
        assert len(project_sessions) >= 3

        # Delete one session
        deleted = session_manager.delete_session(sessions[0].session_id)
        assert deleted is True

        # Verify deleted
        assert not session_manager.session_exists(sessions[0].session_id)

        # Other sessions still exist
        assert session_manager.session_exists(sessions[1].session_id)
        assert session_manager.session_exists(sessions[2].session_id)

    def test_confidence_filtering(self, test_db, qualcoder_db_path):
        """Test that confidence scores work correctly throughout workflow."""
        codes = test_db.list_codes()
        files = test_db.list_files()

        # Create suggestions with different confidence scores
        suggestions = [
            CodingSuggestion(
                file_id=files[0]["id"],
                file_name=files[0]["name"],
                code_id=codes[0]["id"],
                code_name=codes[0]["name"],
                start_pos=0,
                end_pos=30,
                segment_text="High confidence",
                confidence=0.95,
                status="approved"
            ),
            CodingSuggestion(
                file_id=files[0]["id"],
                file_name=files[0]["name"],
                code_id=codes[0]["id"],
                code_name=codes[0]["name"],
                start_pos=15,
                end_pos=30,
                segment_text="Low confidence",
                confidence=0.55,
                status="pending"
            )
        ]

        # Session with min_confidence = 0.6 should show only high confidence
        session = AICodingSession(
            project_path=qualcoder_db_path,
            min_confidence=0.6
        )

        for sugg in suggestions:
            session.add_suggestion(sugg)

        # Check that low confidence suggestion is still there but identifiable
        assert len(session.suggestions) == 2
        high_conf = [s for s in session.suggestions if s.confidence >= 0.6]
        low_conf = [s for s in session.suggestions if s.confidence < 0.6]

        assert len(high_conf) == 1
        assert len(low_conf) == 1

    def test_the_reasoning_is_preserved_in_the_export(
            self, test_db, temp_dir, qualcoder_db_path):
        """Test that AI memos and confidence scores are preserved in export."""
        codes = test_db.list_codes()
        files = test_db.list_files()

        suggestion = CodingSuggestion(
            file_id=files[0]["id"],
            file_name=files[0]["name"],
            code_id=codes[0]["id"],
            code_name=codes[0]["name"],
            start_pos=0,
            end_pos=30,
            segment_text="Test",
            reasoning="Important AI insight here",
            confidence=0.92
        )

        # Export
        exporter = RefiQdaExporter(test_db)
        output_file = Path(temp_dir) / "memo_test.qdpx"
        exporter.export_to_qdpx([suggestion], str(output_file))

        # Check memo is in XML
        with zipfile.ZipFile(output_file, 'r') as zipf:
            xml_content = zipf.read("project.qde").decode('utf-8')

        # Should contain memo text and confidence
        assert "Important AI insight here" in xml_content
        assert "0.92" in xml_content
        assert "AI confidence" in xml_content
