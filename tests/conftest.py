"""Pytest configuration and shared fixtures."""

import pytest
import sqlite3
import tempfile
import shutil
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.sessions import SessionManager, AICodingSession, CodingSuggestion


# =============================================================================
# SESSION FIXTURES (used by test_sessions.py, test_integration_ai_coding.py)
# =============================================================================

@pytest.fixture
def temp_session_dir():
    """Create a temporary directory for session storage."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_suggestion_data():
    """Sample data for creating CodingSuggestion instances."""
    return {
        "file_id": 1,
        "file_name": "interview_01.txt",
        "code_id": 10,
        "code_name": "Workplace Stress",
        "start_pos": 100,
        "end_pos": 250,
        "segment_text": "I often feel overwhelmed with the workload and tight deadlines.",
        "reasoning": "Clear expression of stress related to workload",
        "confidence": 0.85,
        "status": "pending"
    }


@pytest.fixture
def sample_session_data():
    """Sample data for creating AICodingSession instances."""
    return {
        "project_path": "/home/user/test_project.qda",
        "description": "Test coding session",
        "file_ids": [1, 2, 3],
        "code_names": ["Workplace Stress", "Coping Strategies"],
        "instruction": "Code all relevant segments",
        "min_confidence": 0.6
    }


# =============================================================================
# DATABASE FIXTURES (used by test_server_tools.py, test_resources.py,
#                    test_security.py)
# =============================================================================

@pytest.fixture
def qualcoder_db_path(tmp_path):
    """Create a complete QualCoder-compatible SQLite database.

    This is the most comprehensive fixture with all 13 tables and realistic
    test data. Used by server tool tests, resource tests, and security tests.
    """
    project_folder = tmp_path / "test_project.qda"
    project_folder.mkdir()
    db_file = project_folder / "data.qda"

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE project (
            databaseversion TEXT, date TEXT, memo TEXT, about TEXT,
            bookmarkfile INTEGER, bookmarkpos INTEGER, codername TEXT,
            recently_used_codes TEXT
        )
    """)
    cursor.execute("""INSERT INTO project (databaseversion, date, memo, about, codername)
        VALUES ('v14', '2024-01-15', 'Test project', 'About', 'TestCoder')""")

    cursor.execute("""
        CREATE TABLE code_cat (
            catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT,
            owner TEXT, date TEXT, supercatid INTEGER
        )
    """)
    cursor.execute("INSERT INTO code_cat VALUES (1, 'Category A', '', 'TestCoder', '2024-01-15', NULL)")

    cursor.execute("""
        CREATE TABLE code_name (
            cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT,
            catid INTEGER, owner TEXT, date TEXT, color TEXT
        )
    """)
    codes = [
        (1, "Stress", "Stress code", 1, "TestCoder", "2024-01-15", "#FF0000"),
        (2, "Coping", "Coping code", 1, "TestCoder", "2024-01-15", "#00FF00"),
    ]
    cursor.executemany("INSERT INTO code_name VALUES (?, ?, ?, ?, ?, ?, ?)", codes)

    cursor.execute("""
        CREATE TABLE source (
            id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, mediapath TEXT,
            memo TEXT, owner TEXT, date TEXT, av_text_id INTEGER, risid INTEGER,
            UNIQUE(name)
        )
    """)
    cursor.execute("""INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date)
        VALUES (1, 'interview.txt',
        'This is interview text. I feel stressed about deadlines. I cope by exercising.',
        NULL, 'Test memo', 'TestCoder', '2024-01-15')""")
    cursor.execute("""INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date)
        VALUES (2, 'notes.txt',
        'Field notes from observation session.',
        NULL, '', 'TestCoder', '2024-01-16')""")

    cursor.execute("""
        CREATE TABLE code_text (
            ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER,
            seltext TEXT, pos0 INTEGER, pos1 INTEGER,
            owner TEXT, date TEXT, memo TEXT, avid INTEGER,
            important INTEGER,
            UNIQUE(cid, fid, pos0, pos1, owner)
        )
    """)
    cursor.execute("""INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo, important)
        VALUES (1, 1, 1,
        'I feel stressed about deadlines', 24, 55, 'TestCoder', '2024-01-15', 'key passage', 1)""")
    cursor.execute("""INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo, important)
        VALUES (2, 2, 1,
        'I cope by exercising', 57, 77, 'TestCoder', '2024-01-15', '', 0)""")

    cursor.execute("""
        CREATE TABLE cases (
            caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT,
            CONSTRAINT ucm UNIQUE(name)
        )
    """)
    cursor.execute("INSERT INTO cases VALUES (1, 'Case A', 'First case', 'TestCoder', '2024-01-15')")

    cursor.execute("""
        CREATE TABLE case_text (
            id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER,
            pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT
        )
    """)
    cursor.execute("INSERT INTO case_text VALUES (1, 1, 1, 0, 100, '', 'TestCoder', '2024-01-15')")

    cursor.execute("""
        CREATE TABLE annotation (
            anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER,
            pos1 INTEGER, memo TEXT, owner TEXT, date TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE journal (
            jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT
        )
    """)
    cursor.execute("CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL, visibility INTEGER NOT NULL DEFAULT 1 CHECK (visibility IN (0, 1)))")
    cursor.execute("INSERT INTO journal VALUES (1, 'Entry 1', 'Some notes', '2024-01-15', 'TestCoder')")

    cursor.execute("""
        CREATE TABLE attribute_type (
            name TEXT PRIMARY KEY, date TEXT, owner TEXT,
            memo TEXT, caseOrFile TEXT, valuetype TEXT
        )
    """)
    cursor.execute("INSERT INTO attribute_type VALUES ('Age', '2024-01-15', 'TestCoder', '', 'case', 'numeric')")

    cursor.execute("""
        CREATE TABLE attribute (
            attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT,
            value TEXT, id INTEGER, date TEXT, owner TEXT
        )
    """)
    cursor.execute("INSERT INTO attribute VALUES (1, 'Age', 'case', '30', 1, '2024-01-15', 'TestCoder')")

    cursor.execute("""
        CREATE TABLE code_image (
            imid INTEGER PRIMARY KEY, id INTEGER,
            x1 INTEGER, y1 INTEGER, width INTEGER, height INTEGER,
            cid INTEGER, memo TEXT, date TEXT, owner TEXT,
            important INTEGER, pdf_page INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE code_av (
            avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER,
            pos0 INTEGER, pos1 INTEGER,
            memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()

    yield str(project_folder)


@pytest.fixture
def empty_db_path(tmp_path):
    """Create an empty QualCoder database (schema only, no data rows except project).

    Used for testing empty-state behavior in resources and tools.
    """
    project_folder = tmp_path / "empty_project.qda"
    project_folder.mkdir()
    db_file = project_folder / "data.qda"

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    cursor.execute("CREATE TABLE project (databaseversion TEXT, date TEXT, memo TEXT, about TEXT, bookmarkfile INTEGER, bookmarkpos INTEGER, codername TEXT, recently_used_codes TEXT)")
    cursor.execute("INSERT INTO project (databaseversion, date, memo, about, codername) VALUES ('v14', '2024-01-15', '', '', 'TestCoder')")
    cursor.execute("CREATE TABLE code_cat (catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, owner TEXT, date TEXT, supercatid INTEGER)")
    cursor.execute("CREATE TABLE code_name (cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, catid INTEGER, owner TEXT, date TEXT, color TEXT)")
    cursor.execute("CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, mediapath TEXT, memo TEXT, owner TEXT, date TEXT, av_text_id INTEGER, risid INTEGER, UNIQUE(name))")
    cursor.execute("CREATE TABLE code_text (ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER, seltext TEXT, pos0 INTEGER, pos1 INTEGER, owner TEXT, date TEXT, memo TEXT, avid INTEGER, important INTEGER, UNIQUE(cid, fid, pos0, pos1, owner))")
    cursor.execute("CREATE TABLE cases (caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT, CONSTRAINT ucm UNIQUE(name))")
    cursor.execute("CREATE TABLE case_text (id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT)")
    cursor.execute("CREATE TABLE annotation (anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT)")
    cursor.execute("CREATE TABLE journal (jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT)")
    cursor.execute("CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL, visibility INTEGER NOT NULL DEFAULT 1 CHECK (visibility IN (0, 1)))")
    cursor.execute("CREATE TABLE attribute_type (name TEXT PRIMARY KEY, date TEXT, owner TEXT, memo TEXT, caseOrFile TEXT, valuetype TEXT)")
    cursor.execute("CREATE TABLE attribute (attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT, value TEXT, id INTEGER, date TEXT, owner TEXT)")
    cursor.execute("CREATE TABLE code_image (imid INTEGER PRIMARY KEY, id INTEGER, x1 INTEGER, y1 INTEGER, width INTEGER, height INTEGER, cid INTEGER, memo TEXT, date TEXT, owner TEXT, important INTEGER, pdf_page INTEGER)")
    cursor.execute("CREATE TABLE code_av (avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0)")

    conn.commit()
    conn.close()

    yield str(project_folder)


# =============================================================================
# SERVER FIXTURES (used by test_server_tools.py, test_resources.py,
#                  test_security.py)
# =============================================================================

@pytest.fixture
def setup_server(qualcoder_db_path, tmp_path):
    """Set up server globals for testing.

    Saves and restores original server state to avoid cross-test contamination.
    """
    original_db = server.db
    original_path = server.current_project_path
    original_sm = server.session_manager

    server.db = QualcoderDatabase(qualcoder_db_path)
    server.current_project_path = qualcoder_db_path
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    yield server

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = original_db
    server.current_project_path = original_path
    server.session_manager = original_sm


@pytest.fixture
def setup_empty_server(empty_db_path, tmp_path):
    """Set up server with empty database.

    Used for testing empty-state behavior in resources.
    """
    original_db = server.db
    original_path = server.current_project_path
    original_sm = server.session_manager

    server.db = QualcoderDatabase(empty_db_path)
    server.current_project_path = empty_db_path
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    yield server

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = original_db
    server.current_project_path = original_path
    server.session_manager = original_sm


@pytest.fixture
def session_with_suggestions(setup_server, qualcoder_db_path):
    """Create a session with pre-populated suggestions.

    Used for testing review, update, and apply workflows.
    """
    session = AICodingSession(
        project_path=qualcoder_db_path,
        description="Test session",
        file_ids=[1],
        code_names=["Stress"],
        instruction="Test",
        min_confidence=0.6
    )

    s1 = CodingSuggestion(
        file_id=1, file_name="interview.txt",
        code_id=1, code_name="Stress",
        start_pos=0, end_pos=10,
        segment_text="This is in",
        reasoning="Test reasoning", confidence=0.85,
        status="pending"
    )
    s2 = CodingSuggestion(
        file_id=1, file_name="interview.txt",
        code_id=2, code_name="Coping",
        start_pos=57, end_pos=77,
        segment_text="I cope by exercising",
        reasoning="Coping behavior", confidence=0.9,
        status="pending"
    )
    session.add_suggestion(s1)
    session.add_suggestion(s2)

    setup_server.session_manager.save_session(session)
    return session
