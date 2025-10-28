"""REFI-QDA XML export functionality for AI coding suggestions.

Generates REFI-QDA compliant XML files (.qdpx) that can be imported into
Qualcoder and other QDA software supporting the REFI-QDA standard.
"""

import logging
import xml.etree.ElementTree as ET
from xml.dom import minidom
import zipfile
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from .database import QualcoderDatabase
from .sessions import CodingSuggestion

logger = logging.getLogger(__name__)

# REFI-QDA XML namespaces
NAMESPACE = "urn:QDA-XML:project:1.0"
SCHEMA_LOCATION = "urn:QDA-XML:project:1.0 Project.xsd"


class RefiQdaExporter:
    """Generate REFI-QDA XML files from coding suggestions."""

    def __init__(self, db: QualcoderDatabase):
        """Initialize exporter with database connection.

        Args:
            db: QualcoderDatabase instance
        """
        self.db = db

    def create_project_xml(
        self,
        suggestions: List[CodingSuggestion],
        project_name: str = "AI Coding Suggestions",
        origin: str = "Qualcoder MCP AI Assistant"
    ) -> ET.Element:
        """Create the main REFI-QDA project XML structure.

        Args:
            suggestions: List of coding suggestions to export
            project_name: Name for the REFI-QDA project
            origin: Software origin identifier

        Returns:
            XML Element representing the project
        """
        # Register namespace
        ET.register_namespace('', NAMESPACE)

        # Create root element
        root = ET.Element(
            f"{{{NAMESPACE}}}Project",
            attrib={
                f"{{{NAMESPACE}}}name": project_name,
                "origin": origin,
                "creatingUserGUID": self.db.get_or_create_user_guid("ai_coder"),
                "creationDateTime": datetime.now().isoformat() + "Z"
            }
        )

        # Add schema location
        root.set(
            "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation",
            SCHEMA_LOCATION
        )

        # Add sections
        self._add_users_section(root)
        self._add_codebook_section(root, suggestions)
        self._add_sources_section(root, suggestions)

        return root

    def _add_users_section(self, root: ET.Element) -> None:
        """Add Users section to XML.

        Args:
            root: Root XML element
        """
        users_elem = ET.SubElement(root, f"{{{NAMESPACE}}}Users")

        # Add AI coder user
        ai_user = ET.SubElement(
            users_elem,
            f"{{{NAMESPACE}}}User",
            attrib={
                "guid": self.db.get_or_create_user_guid("ai_coder"),
                "name": "AI Coding Assistant"
            }
        )

    def _add_codebook_section(
        self,
        root: ET.Element,
        suggestions: List[CodingSuggestion]
    ) -> None:
        """Add CodeBook section with codes referenced in suggestions.

        Args:
            root: Root XML element
            suggestions: List of coding suggestions
        """
        codebook_elem = ET.SubElement(root, f"{{{NAMESPACE}}}CodeBook")
        codes_elem = ET.SubElement(codebook_elem, f"{{{NAMESPACE}}}Codes")

        # Get unique code IDs from suggestions
        code_ids = set(s.code_id for s in suggestions)

        # Get code GUIDs
        code_guids = self.db.get_code_guids()

        # Get code details
        for code_id in code_ids:
            try:
                code_details = self.db.get_code_details(code_id)
                if code_details:
                    code_elem = ET.SubElement(
                        codes_elem,
                        f"{{{NAMESPACE}}}Code",
                        attrib={
                            "guid": code_guids[code_id],
                            "name": code_details["name"],
                            "isCodable": "true"
                        }
                    )

                    # Add color if available
                    if code_details.get("color"):
                        code_elem.set("color", code_details["color"])

                    # Add description (memo)
                    if code_details.get("memo"):
                        desc_elem = ET.SubElement(code_elem, f"{{{NAMESPACE}}}Description")
                        desc_elem.text = code_details["memo"]

            except Exception as e:
                logger.warning(f"Could not export code {code_id}: {e}")
                continue

    def _add_sources_section(
        self,
        root: ET.Element,
        suggestions: List[CodingSuggestion]
    ) -> None:
        """Add Sources section with files and coded segments.

        Args:
            root: Root XML element
            suggestions: List of coding suggestions
        """
        sources_elem = ET.SubElement(root, f"{{{NAMESPACE}}}Sources")

        # Group suggestions by file
        file_suggestions = {}
        for suggestion in suggestions:
            if suggestion.file_id not in file_suggestions:
                file_suggestions[suggestion.file_id] = []
            file_suggestions[suggestion.file_id].append(suggestion)

        # Get file GUIDs
        file_guids = self.db.get_file_guids()

        # Get code GUIDs
        code_guids = self.db.get_code_guids()

        # Get user GUID
        user_guid = self.db.get_or_create_user_guid("ai_coder")

        # Create a TextSource for each file
        for file_id, file_sug_list in file_suggestions.items():
            try:
                # Get file content
                file_content = self.db.get_file_content(file_id)
                if not file_content:
                    logger.warning(f"Could not get content for file {file_id}")
                    continue

                # Create TextSource element
                source_elem = ET.SubElement(
                    sources_elem,
                    f"{{{NAMESPACE}}}TextSource",
                    attrib={
                        "guid": file_guids[file_id],
                        "name": file_content["name"],
                        "plainTextPath": f"internal://{file_content['name']}",
                        "creatingUser": user_guid,
                        "creationDateTime": datetime.now().isoformat() + "Z"
                    }
                )

                # Add description (memo) if available
                desc_elem = ET.SubElement(source_elem, f"{{{NAMESPACE}}}Description")
                if file_content.get("memo"):
                    desc_elem.text = file_content["memo"]

                # Add PlainTextSelection elements for each suggestion
                for suggestion in file_sug_list:
                    self._add_plain_text_selection(
                        source_elem,
                        suggestion,
                        code_guids,
                        user_guid
                    )

            except Exception as e:
                logger.error(f"Error adding source for file {file_id}: {e}")
                continue

    def _add_plain_text_selection(
        self,
        source_elem: ET.Element,
        suggestion: CodingSuggestion,
        code_guids: Dict[int, str],
        user_guid: str
    ) -> None:
        """Create PlainTextSelection element for a coded segment.

        Args:
            source_elem: Parent source element
            suggestion: CodingSuggestion to export
            code_guids: Mapping of code IDs to GUIDs
            user_guid: GUID of the creating user
        """
        # Create PlainTextSelection element
        selection_elem = ET.SubElement(
            source_elem,
            f"{{{NAMESPACE}}}PlainTextSelection",
            attrib={
                "guid": suggestion.guid,
                "startPosition": str(suggestion.start_pos),
                "endPosition": str(suggestion.end_pos),
                "creatingUser": user_guid,
                "creationDateTime": datetime.now().isoformat() + "Z"
            }
        )

        # Add description (AI memo)
        desc_elem = ET.SubElement(selection_elem, f"{{{NAMESPACE}}}Description")
        if suggestion.ai_memo:
            memo_text = suggestion.ai_memo
            # Add confidence score to memo
            memo_text += f" [AI confidence: {suggestion.confidence:.2f}]"
            desc_elem.text = memo_text

        # Add Coding element
        coding_elem = ET.SubElement(
            selection_elem,
            f"{{{NAMESPACE}}}Coding",
            attrib={
                "guid": self.db.generate_deterministic_guid(
                    "coding",
                    f"{suggestion.file_id}_{suggestion.code_id}_{suggestion.start_pos}"
                ),
                "creatingUser": user_guid,
                "creationDateTime": datetime.now().isoformat() + "Z"
            }
        )

        # Add CodeRef pointing to the code
        code_ref_elem = ET.SubElement(
            coding_elem,
            f"{{{NAMESPACE}}}CodeRef",
            attrib={
                "targetGUID": code_guids[suggestion.code_id]
            }
        )

    def prettify_xml(self, elem: ET.Element) -> str:
        """Convert XML element to pretty-printed string.

        Args:
            elem: XML element to prettify

        Returns:
            Pretty-printed XML string
        """
        # Convert to string
        rough_string = ET.tostring(elem, encoding='utf-8')

        # Parse and prettify
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ", encoding='utf-8').decode('utf-8')

    def export_to_qdpx(
        self,
        suggestions: List[CodingSuggestion],
        output_path: str,
        project_name: str = "AI Coding Suggestions"
    ) -> str:
        """Export suggestions as .qdpx file (ZIP with XML).

        Args:
            suggestions: List of coding suggestions to export
            output_path: Path where to save the .qdpx file
            project_name: Name for the REFI-QDA project

        Returns:
            Path to created .qdpx file
        """
        try:
            # Create output directory if needed
            output_file = Path(output_path).expanduser()
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # Generate XML
            logger.info("Generating REFI-QDA XML...")
            xml_root = self.create_project_xml(suggestions, project_name)

            # Pretty print XML
            xml_string = self.prettify_xml(xml_root)

            # Create .qdpx file (ZIP archive)
            logger.info(f"Creating .qdpx archive: {output_file}")
            with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add the project.qde file
                zipf.writestr('project.qde', xml_string)

            logger.info(f"Successfully exported {len(suggestions)} suggestions to {output_file}")
            return str(output_file)

        except Exception as e:
            logger.error(f"Failed to export to REFI-QDA: {e}")
            raise RuntimeError(f"REFI-QDA export failed: {e}") from None

    def validate_suggestions(self, suggestions: List[CodingSuggestion]) -> List[str]:
        """Validate suggestions before export.

        Args:
            suggestions: List of suggestions to validate

        Returns:
            List of validation warnings/errors (empty if all valid)
        """
        warnings = []

        # Check if we have any suggestions
        if not suggestions:
            warnings.append("No suggestions to export")
            return warnings

        # Get all codes and files from database for validation
        try:
            codes = self.db.list_codes()
            code_ids = {c["id"] for c in codes}

            files = self.db.list_files()
            file_ids = {f["id"] for f in files}

        except Exception as e:
            warnings.append(f"Could not load project data for validation: {e}")
            return warnings

        # Validate each suggestion
        for i, suggestion in enumerate(suggestions):
            # Check code exists
            if suggestion.code_id not in code_ids:
                warnings.append(
                    f"Suggestion {i}: Code ID {suggestion.code_id} not found in project"
                )

            # Check file exists
            if suggestion.file_id not in file_ids:
                warnings.append(
                    f"Suggestion {i}: File ID {suggestion.file_id} not found in project"
                )

            # Check positions are valid
            if suggestion.start_pos < 0:
                warnings.append(
                    f"Suggestion {i}: Invalid start position {suggestion.start_pos}"
                )

            if suggestion.end_pos <= suggestion.start_pos:
                warnings.append(
                    f"Suggestion {i}: End position {suggestion.end_pos} must be greater than start position {suggestion.start_pos}"
                )

            # Check confidence is in valid range
            if not (0.0 <= suggestion.confidence <= 1.0):
                warnings.append(
                    f"Suggestion {i}: Confidence {suggestion.confidence} outside valid range [0.0, 1.0]"
                )

        return warnings
