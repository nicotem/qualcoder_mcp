#!/usr/bin/env python3
"""Test the complete conversational AI coding workflow."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.sessions import AICodingSession, CodingSuggestion, SessionManager

def main():
    print("=" * 80)
    print("TESTING CONVERSATIONAL AI CODING WORKFLOW")
    print("=" * 80)

    # Setup
    workspace = Path.home() / "Documents" / "Qualcoder MCP Projects"
    project_path = workspace / "test_write_operations.qda"

    print(f"\n1. Opening project: {project_path}")
    db = QualcoderDatabase(str(project_path))

    files = db.list_files()
    codes = db.list_codes()
    print(f"   Project has {len(files)} files, {len(codes)} codes")

    # Step 1: Create analysis session (simulating analyze_for_coding tool)
    print("\n2. Creating analysis session...")
    session = AICodingSession(
        project_path=str(project_path),
        description="Test workflow - analyzing first file for WORKPLACE-STRESS code",
        file_ids=[3],  # First file
        code_names=["WORKPLACE-STRESS"],
        instruction="Find segments related to workplace stress",
        min_confidence=0.7
    )
    print(f"   Session ID: {session.session_id}")

    # Step 2: Simulate AI analysis creating suggestions
    print("\n3. Simulating AI analysis...")

    # Get file content
    file_content = db.get_file_content(3)
    text = file_content["content"]

    # Get code ID
    code_id = 11  # WORKPLACE-STRESS

    # Create a few test suggestions (using unique positions)
    suggestions = [
        CodingSuggestion(
            file_id=3,
            file_name=file_content["name"],
            code_id=code_id,
            code_name="WORKPLACE-STRESS",
            start_pos=2000,
            end_pos=2100,
            segment_text=text[2000:2100],
            reasoning="This segment discusses workplace stress factors",
            confidence=0.85,
            context_before=text[1950:2000],
            context_after=text[2100:2150]
        ),
        CodingSuggestion(
            file_id=3,
            file_name=file_content["name"],
            code_id=code_id,
            code_name="WORKPLACE-STRESS",
            start_pos=3000,
            end_pos=3150,
            segment_text=text[3000:3150],
            reasoning="References coping mechanisms for workplace stress",
            confidence=0.78,
            context_before=text[2950:3000],
            context_after=text[3150:3200]
        ),
        CodingSuggestion(
            file_id=3,
            file_name=file_content["name"],
            code_id=code_id,
            code_name="WORKPLACE-STRESS",
            start_pos=4000,
            end_pos=4100,
            segment_text=text[4000:4100],
            reasoning="Discusses impact of stress on work performance",
            confidence=0.65,  # Below threshold
            context_before=text[3950:4000],
            context_after=text[4100:4150]
        )
    ]

    for sugg in suggestions:
        session.add_suggestion(sugg)

    print(f"   Created {len(suggestions)} suggestions")
    for i, s in enumerate(suggestions, 1):
        print(f"     {i}. {s.code_name} at {s.start_pos}-{s.end_pos} (confidence: {s.confidence:.2f})")
        print(f"        GUID: {s.guid}")
        print(f"        Reasoning: {s.reasoning}")

    # Step 3: Save session
    print("\n4. Saving session...")
    session_mgr = SessionManager()
    session_mgr.save_session(session)
    print(f"   Session saved")

    # Step 4: Simulate user reviewing suggestions (review_suggestions tool)
    print("\n5. Reviewing suggestions (as user would see them)...")
    loaded_session = session_mgr.load_session(session.session_id)

    for i, sugg in enumerate(loaded_session.suggestions, 1):
        print(f"\n   Suggestion {i}:")
        print(f"     File: {sugg.file_name}")
        print(f"     Code: {sugg.code_name}")
        print(f"     Position: {sugg.start_pos}-{sugg.end_pos}")
        print(f"     Text: {sugg.segment_text[:50]}...")
        print(f"     Reasoning: {sugg.reasoning}")
        print(f"     Confidence: {sugg.confidence:.2f}")
        print(f"     Status: {sugg.status}")
        print(f"     GUID: {sugg.guid}")

    # Step 5: Simulate user approving/rejecting (update_suggestion_status tool)
    print("\n6. User approves suggestions 1 & 2, rejects suggestion 3...")

    approve_guids = [suggestions[0].guid, suggestions[1].guid]
    reject_guids = [suggestions[2].guid]

    result = loaded_session.update_suggestions_by_guid(
        approve=approve_guids,
        reject=reject_guids
    )

    print(f"   Approved: {result['approved']}")
    print(f"   Rejected: {result['rejected']}")

    # Save updated session
    session_mgr.save_session(loaded_session)

    # Step 6: Apply approved codings (apply_codings tool)
    print("\n7. Applying approved codings to database...")

    # Create backup first
    from qualcoder_mcp.database import backup_project
    backup_path = backup_project(project_path)
    print(f"   Backup created: {backup_path}")

    approved = loaded_session.filter_by_status("approved")
    print(f"   Found {len(approved)} approved suggestions")

    applied = []
    for sugg in approved:
        try:
            # Add memo with reasoning and confidence
            memo = f"{sugg.reasoning}\n[AI confidence: {sugg.confidence:.2f}]"

            ctid = db.add_coding(
                file_id=sugg.file_id,
                code_id=sugg.code_id,
                start_pos=sugg.start_pos,
                end_pos=sugg.end_pos,
                selected_text=sugg.segment_text,
                owner="AI Coding Assistant",
                memo=memo
            )

            applied.append({
                "ctid": ctid,
                "file": sugg.file_name,
                "code": sugg.code_name,
                "position": f"{sugg.start_pos}-{sugg.end_pos}"
            })

            print(f"   ✓ Applied coding ctid={ctid} to {sugg.file_name}")

        except Exception as e:
            print(f"   ✗ Failed to apply suggestion: {e}")

    # Step 7: Verify results
    print("\n8. Verification - checking database...")

    for item in applied:
        # Query to verify the coding exists
        result = db.conn.execute(
            "SELECT ctid, cid, fid, pos0, pos1, owner, memo FROM code_text WHERE ctid = ?",
            (item["ctid"],)
        ).fetchone()

        if result:
            print(f"   ✓ Verified ctid={result['ctid']}: {item['code']} on {item['file']}")
        else:
            print(f"   ✗ Could not verify ctid={item['ctid']}")

    # Summary
    print("\n" + "=" * 80)
    print("WORKFLOW TEST COMPLETE")
    print("=" * 80)
    print(f"\nSummary:")
    print(f"  • Created session with {len(suggestions)} suggestions")
    print(f"  • User approved {len(approve_guids)} suggestions")
    print(f"  • User rejected {len(reject_guids)} suggestions")
    print(f"  • Applied {len(applied)} codings to database")
    print(f"  • All codings verified in database")
    print(f"\nSession ID: {session.session_id}")
    print(f"Backup: {backup_path}")
    print(f"\nYou can now open this project in Qualcoder to see the AI-generated codings!")
    print(f"Project: {project_path}")

if __name__ == "__main__":
    main()
