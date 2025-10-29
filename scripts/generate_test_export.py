#!/usr/bin/env python3
"""
Generate a test REFI-QDA export for manual import testing.

This script creates a simple AI coding session and exports it as a .qdpx file
that you can import into Qualcoder to verify the import process works correctly.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.sessions import CodingSuggestion, AICodingSession
from qualcoder_mcp.refi_export import RefiQdaExporter


def main():
    """Generate test export for manual import verification."""

    # Path to test database
    test_db_path = Path.home() / "Documents" / "qualcoder_mcp_test" / "test_project.qda"

    if not test_db_path.exists():
        print(f"❌ Test database not found at: {test_db_path}")
        print("\nPlease ensure the test project exists.")
        print("You can create it by running:")
        print("  python scripts/create_test_project.py")
        return 1

    print(f"✓ Found test database: {test_db_path}")

    # Open database
    print("\n📂 Opening database...")
    db = QualcoderDatabase(str(test_db_path))

    # Get files and codes
    files = db.list_files()
    codes = db.list_codes()

    if not files:
        print("❌ No files found in test database")
        db.close()
        return 1

    if not codes:
        print("❌ No codes found in test database")
        db.close()
        return 1

    print(f"✓ Found {len(files)} files and {len(codes)} codes")

    # Display what we're working with
    print(f"\n📄 First file: {files[0]['name']} (ID: {files[0]['id']})")
    print(f"🏷️  First code: {codes[0]['name']} (ID: {codes[0]['id']})")

    # Create test suggestions
    print("\n🤖 Creating test AI coding suggestions...")

    suggestions = []

    # Suggestion 1: Simple example
    suggestions.append(CodingSuggestion(
        file_id=files[0]["id"],
        file_name=files[0]["name"],
        code_id=codes[0]["id"],
        code_name=codes[0]["name"],
        start_pos=0,
        end_pos=100,
        segment_text="Sample text segment for testing import",
        ai_memo="This is a test suggestion to verify REFI-QDA import works correctly. Claude identified this segment as relevant.",
        confidence=0.85,
        status="approved"
    ))

    # Suggestion 2: Different code if available
    if len(codes) > 1:
        suggestions.append(CodingSuggestion(
            file_id=files[0]["id"],
            file_name=files[0]["name"],
            code_id=codes[1]["id"],
            code_name=codes[1]["name"],
            start_pos=200,
            end_pos=300,
            segment_text="Another sample segment with different code",
            ai_memo="Second test suggestion to verify multiple codings can be imported.",
            confidence=0.92,
            status="approved"
        ))

    # Suggestion 3: Different file if available
    if len(files) > 1:
        suggestions.append(CodingSuggestion(
            file_id=files[1]["id"],
            file_name=files[1]["name"],
            code_id=codes[0]["id"],
            code_name=codes[0]["name"],
            start_pos=0,
            end_pos=150,
            segment_text="Test segment in second file",
            ai_memo="Third test suggestion in a different file to verify multi-file import.",
            confidence=0.78,
            status="approved"
        ))

    print(f"✓ Created {len(suggestions)} test suggestions")

    # Export to REFI-QDA
    print("\n📦 Exporting to REFI-QDA format...")

    output_path = Path.home() / "Desktop" / "test_import.qdpx"

    exporter = RefiQdaExporter(db)
    result_path = exporter.export_to_qdpx(
        suggestions,
        str(output_path),
        "Manual Import Test"
    )

    db.close()

    print(f"✓ Export created successfully!")
    print(f"\n📍 Location: {result_path}")

    # Provide import instructions
    print("\n" + "="*70)
    print("MANUAL IMPORT TEST INSTRUCTIONS")
    print("="*70)

    print("\n1️⃣  Open Qualcoder on your Mac")
    print("    - Launch Qualcoder application")
    print(f"    - Open the test project at: {test_db_path}")

    print("\n2️⃣  Navigate to Import function")
    print("    - Click: File > Import > REFI-QDA Project")

    print("\n3️⃣  Select the export file")
    print(f"    - Browse to: {result_path}")
    print("    - Click Open")

    print("\n4️⃣  Review and confirm import")
    print("    - Check that codes and files are recognized")
    print("    - Click Import/OK to proceed")

    print("\n5️⃣  Verify import success")
    print("    ✓ Check that coded segments appear in the files")
    print("    ✓ Verify code assignments are correct")
    print("    ✓ Check that AI memos are visible")
    print("    ✓ Confirm coder is 'AI Coding Assistant'")
    print("    ✓ Verify segment positions look correct")

    print("\n" + "="*70)
    print("WHAT TO CHECK")
    print("="*70)

    print(f"\n📄 File 1: {files[0]['name']}")
    print(f"   - Should have {1 if len(codes) == 1 else 2} coded segment(s)")
    print(f"   - Code: {codes[0]['name']}")
    if len(codes) > 1:
        print(f"   - Code: {codes[1]['name']}")
    print("   - Positions: 0-100 and 200-300")
    print("   - Memos should show confidence scores")

    if len(files) > 1:
        print(f"\n📄 File 2: {files[1]['name']}")
        print("   - Should have 1 coded segment")
        print(f"   - Code: {codes[0]['name']}")
        print("   - Position: 0-150")

    print("\n" + "="*70)
    print("EXPECTED RESULTS")
    print("="*70)

    print("\n✅ PASS if:")
    print("   - Import completes without errors")
    print("   - All coded segments appear correctly")
    print("   - Segment boundaries match expected positions")
    print("   - AI memos are visible with confidence scores")
    print("   - Coder attribution is 'AI Coding Assistant'")

    print("\n❌ FAIL if:")
    print("   - Import fails with error message")
    print("   - Segments missing or in wrong locations")
    print("   - Code assignments incorrect")
    print("   - Memos missing or malformed")

    print("\n" + "="*70)
    print("TROUBLESHOOTING")
    print("="*70)

    print("\nIf import fails, check:")
    print("   1. Qualcoder version supports REFI-QDA (v3.0+)")
    print("   2. Test project is actually open in Qualcoder")
    print("   3. Codes exist in the project (they should)")
    print("   4. Files exist in the project (they should)")
    print("   5. Check Qualcoder logs for error details")

    print("\nFor detailed import help, see:")
    print("   IMPORT_INSTRUCTIONS.md")

    print("\n" + "="*70)
    print("✨ Ready to test! Good luck!")
    print("="*70 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
