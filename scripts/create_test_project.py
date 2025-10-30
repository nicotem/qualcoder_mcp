#!/usr/bin/env python3
"""Create a test Qualcoder project for AI coding development and testing."""

import sqlite3
import os
import shutil
from datetime import datetime
from pathlib import Path

def create_test_project(project_folder: str):
    """Create a test Qualcoder project with proper folder structure.

    Args:
        project_folder: Path to the project folder (e.g., ~/Documents/QDA Projects/test_project.qda)
    """

    project_path = Path(project_folder).expanduser()

    # Remove existing project folder if it exists
    if project_path.exists():
        print(f"Removing existing project at {project_path}")
        shutil.rmtree(project_path)

    # Create project folder structure
    print(f"Creating project folder: {project_path}")
    project_path.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    (project_path / "documents").mkdir(exist_ok=True)
    (project_path / "images").mkdir(exist_ok=True)
    (project_path / "audio").mkdir(exist_ok=True)
    (project_path / "video").mkdir(exist_ok=True)

    # Create the database file inside the folder
    db_path = project_path / "data.qda"
    print(f"Creating database: {db_path}")

    # Create database
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Create schema
    print("Creating Qualcoder schema...")

    # Project info table
    cur.execute("""
        CREATE TABLE project (
            databaseversion text,
            date text,
            memo text,
            about text
        )
    """)

    # Source files table
    cur.execute("""
        CREATE TABLE source (
            id integer primary key,
            name text,
            fulltext text,
            mediapath text,
            memo text,
            owner text,
            date text
        )
    """)

    # Code categories table
    cur.execute("""
        CREATE TABLE code_cat (
            catid integer primary key,
            name text,
            owner text,
            date text,
            memo text,
            supercatid integer
        )
    """)

    # Code names table
    cur.execute("""
        CREATE TABLE code_name (
            cid integer primary key,
            name text,
            memo text,
            owner text,
            date text,
            catid integer,
            color text
        )
    """)

    # Cases table
    cur.execute("""
        CREATE TABLE cases (
            caseid integer primary key,
            name text,
            memo text,
            owner text,
            date text
        )
    """)

    # Attribute types table
    cur.execute("""
        CREATE TABLE attribute_type (
            name text primary key,
            date text,
            owner text,
            memo text,
            caseOrFile text,
            valuetype text
        )
    """)

    # Attributes table
    cur.execute("""
        CREATE TABLE attribute (
            attrid integer primary key,
            name text,
            attr_type text,
            value text,
            id integer,
            date text,
            owner text
        )
    """)

    # Coded text table
    cur.execute("""
        CREATE TABLE code_text (
            ctid integer primary key,
            cid integer,
            fid integer,
            seltext text,
            pos0 integer,
            pos1 integer,
            owner text,
            date text,
            memo text,
            important integer
        )
    """)

    # Annotations table
    cur.execute("""
        CREATE TABLE annotation (
            anid integer primary key,
            fid integer,
            pos0 integer,
            pos1 integer,
            memo text,
            owner text,
            date text
        )
    """)

    # Journal table
    cur.execute("""
        CREATE TABLE journal (
            jid integer primary key,
            name text,
            jentry text,
            date text,
            owner text
        )
    """)

    conn.commit()

    # Insert test data
    print("Inserting test data...")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Project info
    cur.execute("""
        INSERT INTO project (databaseversion, date, memo, about)
        VALUES (?, ?, ?, ?)
    """, ("v8", now, "Test project for AI coding development", "AI Coding Test Project"))

    # Sample interview transcripts
    interview_1 = """
Interviewer: Can you tell me about your experience with workplace stress?

Participant: Well, it's been quite challenging lately. The main issue is the constant pressure from deadlines.
When multiple projects pile up and there's no support from management, that's when I really feel the pressure
building. It affects not just my work but also my personal life.

Interviewer: How do you cope with these stressful situations?

Participant: I've developed a few strategies. Exercise helps a lot - I try to go for a run after work to clear
my head. I also find that talking to colleagues who understand the situation is really valuable. We support
each other through the tough times.

Interviewer: What about work-life balance? How do you maintain it?

Participant: That's difficult. I try to set boundaries, like not checking emails after 7 PM. But sometimes the
workload makes it impossible. I've learned to say no to extra commitments when I'm already overwhelmed.

Interviewer: Do you feel your manager supports you?

Participant: To some extent, yes. My direct manager is understanding and tries to help distribute the workload.
But upper management doesn't always see the reality of what we're dealing with. There's a disconnect there.

Interviewer: What would improve the situation?

Participant: More realistic deadlines would help enormously. Also, better communication from leadership about
priorities. When everything is labeled "urgent," nothing really is. We need clearer guidance on what truly matters.
"""

    interview_2 = """
Interviewer: Tell me about your typical workday.

Participant: I usually start around 8 AM. The mornings are actually quite good - I can focus on deep work before
the meetings start. But by afternoon, it's just back-to-back meetings, which is exhausting.

Interviewer: What causes you the most stress at work?

Participant: Definitely the lack of resources. We're expected to deliver high-quality results, but we don't have
the tools or the staff to do it properly. It's frustrating because we care about the work, but we're set up to fail.

Interviewer: How does this affect your motivation?

Participant: It's draining, honestly. I used to be really excited about coming to work, but now it feels like
we're constantly firefighting. There's no time for innovation or improvement, just survival mode.

Interviewer: What keeps you going?

Participant: My team, really. We have great camaraderie. We joke that we're all in this together, sinking on the
same ship. But seriously, the relationships I've built here are what make it bearable.

Interviewer: Do you see yourself staying long-term?

Participant: I'm not sure. I'd like to, because I believe in the mission. But if things don't improve, I might
need to look for opportunities elsewhere. It's sad because I don't want to leave, but I also need to think about
my own wellbeing.

Interviewer: What would make you stay?

Participant: Recognition would help. Not just monetary, though that matters too. But acknowledgment of the effort
we put in. Sometimes it feels like our hard work is invisible to senior leadership.
"""

    interview_3 = """
Interviewer: How long have you been in your current role?

Participant: About three years now. It's been a journey with lots of ups and downs.

Interviewer: What are the ups?

Participant: The work itself is interesting and meaningful. I feel like I'm making a contribution. The projects
are challenging in a good way, and I've learned so much. My colleagues are brilliant, and I've grown professionally.

Interviewer: And the downs?

Participant: The organizational culture can be problematic. There's a lot of micromanagement, which stifles
creativity. You're expected to be innovative, but then you're questioned on every decision. It's contradictory.

Interviewer: How do you handle the micromanagement?

Participant: I document everything. I've learned to over-communicate, send frequent updates, get approvals in
writing. It's tedious, but it protects me. I wish I could just focus on the work instead of the politics.

Interviewer: What about professional development?

Participant: There are opportunities for training, which is good. I've taken several courses. But there's limited
room for advancement. The hierarchy is quite rigid, and promotions are rare. It feels like a dead end sometimes.

Interviewer: How does that impact your career plans?

Participant: I'm actively working on skills that will make me more marketable. I love my current work, but I'm
realistic about the future here. I'm building my network and keeping my options open.
"""

    # Insert source files
    files = [
        ("interview_001.txt", interview_1),
        ("interview_002.txt", interview_2),
        ("interview_003.txt", interview_3)
    ]

    for name, text in files:
        cur.execute("""
            INSERT INTO source (name, fulltext, mediapath, memo, owner, date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, text, None, f"Test interview transcript: {name}", "test_user", now))

    # Insert code categories
    categories = [
        (1, "Workplace Factors", None),
        (2, "Personal Responses", None),
        (3, "Management", None)
    ]

    for catid, name, supercatid in categories:
        cur.execute("""
            INSERT INTO code_cat (catid, name, owner, date, memo, supercatid)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (catid, name, "test_user", now, f"Category: {name}", supercatid))

    # Insert codes
    codes = [
        (1, "Workplace Stress - Causes", "Factors that create stress", 1, "#FF6B6B"),
        (2, "Stress Coping Strategies", "How participants manage stress", 2, "#4ECDC4"),
        (3, "Work-Life Balance", "Boundary setting and personal time", 2, "#95E1D3"),
        (4, "Management - Supportive", "Positive management behaviors", 3, "#6BCF7F"),
        (5, "Management - Micromanagement", "Controlling management behaviors", 3, "#FF8B94"),
        (6, "Workload Issues", "Too much work, unrealistic expectations", 1, "#FFA07A"),
        (7, "Team Relationships", "Colleague support and camaraderie", 2, "#98D8C8"),
        (8, "Career Development", "Growth and advancement opportunities", 1, "#B8A9E0"),
        (9, "Motivation - Declining", "Loss of enthusiasm and energy", 2, "#FFB6C1"),
        (10, "Organizational Culture", "Company values and environment", 1, "#87CEEB")
    ]

    for cid, name, memo, catid, color in codes:
        cur.execute("""
            INSERT INTO code_name (cid, name, memo, owner, date, catid, color)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (cid, name, memo, "test_user", now, catid, color))

    # Insert cases
    cases_data = [
        (1, "Participant_001", "Female, 35, 5 years experience"),
        (2, "Participant_002", "Male, 42, 8 years experience"),
        (3, "Participant_003", "Female, 29, 3 years experience")
    ]

    for caseid, name, memo in cases_data:
        cur.execute("""
            INSERT INTO cases (caseid, name, memo, owner, date)
            VALUES (?, ?, ?, ?, ?)
        """, (caseid, name, memo, "test_user", now))

    # Insert attribute types
    attributes = [
        ("age", "numeric", "case", "Participant age"),
        ("gender", "character", "case", "Participant gender"),
        ("experience_years", "numeric", "case", "Years in current role")
    ]

    for name, valuetype, caseorfile, memo in attributes:
        cur.execute("""
            INSERT INTO attribute_type (name, date, owner, memo, caseOrFile, valuetype)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, now, "test_user", memo, caseorfile, valuetype))

    # Insert attribute values
    attribute_values = [
        ("age", "case", "35", 1),
        ("gender", "case", "Female", 1),
        ("experience_years", "case", "5", 1),
        ("age", "case", "42", 2),
        ("gender", "case", "Male", 2),
        ("experience_years", "case", "8", 2),
        ("age", "case", "29", 3),
        ("gender", "case", "Female", 3),
        ("experience_years", "case", "3", 3)
    ]

    for attr_name, attr_type, value, case_id in attribute_values:
        cur.execute("""
            INSERT INTO attribute (name, attr_type, value, id, date, owner)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (attr_name, attr_type, value, case_id, now, "test_user"))

    # Insert a few example coded segments (just to show the project is functional)
    # These are manual codings - AI will add more
    example_codings = [
        (1, 1, "The main issue is the constant pressure from deadlines.", 150, 207, "Manual test coding"),
        (2, 1, "Exercise helps a lot - I try to go for a run after work to clear my head.", 523, 601, "Manual test coding"),
        (6, 2, "We're expected to deliver high-quality results, but we don't have the tools or the staff", 341, 430, "Manual test coding")
    ]

    for cid, fid, seltext, pos0, pos1, memo in example_codings:
        cur.execute("""
            INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo, important)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cid, fid, seltext, pos0, pos1, "test_user", now, memo, 0))

    # Insert a journal entry
    cur.execute("""
        INSERT INTO journal (name, jentry, date, owner)
        VALUES (?, ?, ?, ?)
    """, ("Research Notes", "Test project created for AI coding development. Contains 3 interview transcripts about workplace stress.", now, "test_user"))

    conn.commit()
    conn.close()

    print(f"✓ Test project database created: {db_path}")
    print(f"  - 3 interview transcripts")
    print(f"  - 10 codes in 3 categories")
    print(f"  - 3 cases with attributes")
    print(f"  - 3 example coded segments")
    return str(project_path)


if __name__ == "__main__":
    # Create project folder (not just the .qda file)
    project_folder = os.path.expanduser("~/Documents/QDA Projects/test_project.qda")
    create_test_project(project_folder)

    db_path = os.path.join(project_folder, "data.qda")
    print("\n✅ Test project created successfully!")
    print(f"📁 Project folder: {project_folder}")
    print(f"💾 Database file: {db_path}")
    print(f"\nYou can now open this project in Qualcoder.")
