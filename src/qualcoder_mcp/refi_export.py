"""REFI-QDA XML export functionality for AI coding suggestions.

Generates REFI-QDA compliant XML files (.qdpx) that can be imported into
Qualcoder and other QDA software supporting the REFI-QDA standard
(QDA-XML 1.0, specification document v1.5).

Position convention (QualCoder's, shared by this database): character
offsets into the plain text as stored, 0-based, end-exclusive, newlines
are single \n characters. The exported .txt payloads are written verbatim
(UTF-8, no BOM) so positions remain valid on re-import.
"""

import re
import uuid
import logging
import xml.etree.ElementTree as ET
from xml.dom import minidom
import zipfile
from pathlib import Path
from typing import List, Dict, Optional, Set
from datetime import datetime, timezone

from .database import QualcoderDatabase
from .sessions import CodingSuggestion

logger = logging.getLogger(__name__)

# REFI-QDA XML namespace: "1.5" is the revision of the SPEC DOCUMENT; the
# wire format is QDA-XML 1.0 and this is its one and only namespace.
NAMESPACE = "urn:QDA-XML:project:1.0"
SCHEMA_LOCATION = (
    "urn:QDA-XML:project:1.0 "
    "http://schema.qdasoftware.org/versions/Project/v1.0/Project.xsd"
)

# GUIDType pattern from the spec (optionally brace-wrapped is also legal,
# but we always emit the bare lowercase form)
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# RGBType pattern from the spec
_COLOR_RE = re.compile(r"^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$")

# Spec §8.5: max internal file size
_MAX_INTERNAL_FILE_BYTES = 2_147_483_647


def _xml_safe(text) -> str:
    """Strip characters that are invalid in XML 1.0 from a string.

    ElementTree will happily serialize C0 control characters, but the
    result is not well-formed XML: minidom (and any conformant parser,
    including the QDA tools importing the .qdpx) rejects it. Database
    content (code names, memos, file names) is user-authored and may
    contain such characters, so every DB-derived string is filtered
    before it enters the XML tree.

    Valid XML 1.0 chars: #x9 #xA #xD, #x20-#xD7FF, #xE000-#xFFFD,
    #x10000-#x10FFFF (surrogates excluded by the D7FF/E000 bounds).
    """
    if text is None:
        return ""
    return "".join(
        ch for ch in str(text)
        if ch in ("\t", "\n", "\r")
        or 0x20 <= ord(ch) <= 0xD7FF
        or 0xE000 <= ord(ch) <= 0xFFFD
        or 0x10000 <= ord(ch) <= 0x10FFFF
    )


def _utc_now() -> str:
    """xsd:dateTime in actual UTC (not local time mislabeled as Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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

        # Create root element. NOTE: attributes are UNQUALIFIED in the
        # REFI-QDA schema (attributeFormDefault="unqualified") — a
        # namespaced name attribute would be schema-invalid.
        root = ET.Element(
            f"{{{NAMESPACE}}}Project",
            attrib={
                "name": _xml_safe(project_name),
                "origin": _xml_safe(origin),
                "creatingUserGUID": self.db.get_or_create_user_guid("ai_coder"),
                "creationDateTime": _utc_now()
            }
        )

        # Add schema location
        root.set(
            "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation",
            SCHEMA_LOCATION
        )

        # Add sections (Users -> CodeBook -> Sources: the XSD sequence with
        # the optional elements we don't emit skipped)
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
        ET.SubElement(
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

        Category hierarchy is preserved: REFI-QDA expresses categories as
        nested Code elements with isCodable="false" (QualCoder's own
        convention on both export and import), so referenced codes are
        emitted inside their full category chain.

        Args:
            root: Root XML element
            suggestions: List of coding suggestions
        """
        codebook_elem = ET.SubElement(root, f"{{{NAMESPACE}}}CodeBook")
        codes_elem = ET.SubElement(codebook_elem, f"{{{NAMESPACE}}}Codes")

        code_ids = set(s.code_id for s in suggestions)
        code_guids = self.db.get_code_guids()

        all_codes = {c["id"]: c for c in self.db.list_codes()}
        all_categories = {c["id"]: c for c in self.db.list_categories()}

        # Which categories are needed? Walk each referenced code's chain up
        needed_categories: Set[int] = set()
        for code_id in code_ids:
            code = all_codes.get(code_id)
            if not code:
                continue
            cat_id = code.get("category_id")
            seen: Set[int] = set()
            while cat_id is not None and cat_id in all_categories and cat_id not in seen:
                seen.add(cat_id)
                needed_categories.add(cat_id)
                cat_id = all_categories[cat_id].get("parent_id")

        category_elems: Dict[int, ET.Element] = {}

        def _category_elem(cat_id: int) -> ET.Element:
            """Get or create the (nested) element for a category."""
            if cat_id in category_elems:
                return category_elems[cat_id]
            cat = all_categories[cat_id]
            parent_id = cat.get("parent_id")
            if parent_id is not None and parent_id in needed_categories:
                parent_elem = _category_elem(parent_id)
            else:
                parent_elem = codes_elem
            elem = ET.SubElement(
                parent_elem,
                f"{{{NAMESPACE}}}Code",
                attrib={
                    "guid": self.db.generate_deterministic_guid("category", cat_id),
                    "name": _xml_safe(cat["name"]),
                    "isCodable": "false"
                }
            )
            if cat.get("memo"):
                desc = ET.SubElement(elem, f"{{{NAMESPACE}}}Description")
                desc.text = _xml_safe(cat["memo"])
            category_elems[cat_id] = elem
            return elem

        # Emit categories root-down (dict order does not matter — recursion
        # in _category_elem builds parents first)
        for cat_id in needed_categories:
            _category_elem(cat_id)

        # Emit the referenced codes inside their categories
        for code_id in sorted(code_ids):
            code = all_codes.get(code_id)
            if not code:
                logger.warning(f"Could not export code {code_id}: not found")
                continue

            cat_id = code.get("category_id")
            parent_elem = (category_elems.get(cat_id, codes_elem)
                           if cat_id is not None else codes_elem)

            code_elem = ET.SubElement(
                parent_elem,
                f"{{{NAMESPACE}}}Code",
                attrib={
                    "guid": code_guids[code_id],
                    "name": _xml_safe(code["name"]),
                    "isCodable": "true"
                }
            )

            # Add color only when it is a valid RGBType value
            color = code.get("color")
            if color and _COLOR_RE.match(str(color)):
                code_elem.set("color", str(color))

            # Add description (memo) only when non-empty
            if code.get("memo"):
                desc_elem = ET.SubElement(code_elem, f"{{{NAMESPACE}}}Description")
                desc_elem.text = _xml_safe(code["memo"])

    def _add_sources_section(
        self,
        root: ET.Element,
        suggestions: List[CodingSuggestion]
    ) -> None:
        """Add Sources section with files and coded segments.

        Sources are referenced with the internal:// URL scheme and GUID
        filenames per spec §8.3/8.4 — QualCoder's importer hard-depends on
        the internal:/ prefix (refi.py:880).

        Args:
            root: Root XML element
            suggestions: List of coding suggestions
        """
        sources_elem = ET.SubElement(root, f"{{{NAMESPACE}}}Sources")

        # Group suggestions by file
        file_suggestions: Dict[int, List[CodingSuggestion]] = {}
        for suggestion in suggestions:
            file_suggestions.setdefault(suggestion.file_id, []).append(suggestion)

        file_guids = self.db.get_file_guids()
        code_guids = self.db.get_code_guids()
        user_guid = self.db.get_or_create_user_guid("ai_coder")

        # GUID uniqueness within one document is mandatory (spec §3)
        used_guids: Set[str] = set()

        # Create a TextSource for each file
        for file_id, file_sug_list in file_suggestions.items():
            file_content = self.db.get_file_content(file_id)
            if not file_content or not (file_content.get("content") or ""):
                # Never emit a TextSource whose payload will not be written:
                # a dangling plainTextPath crashes QualCoder's importer.
                # export_to_qdpx validates this up front, so this is only a
                # defensive skip for direct callers.
                logger.warning(
                    f"Skipping file {file_id}: no text content to export"
                )
                continue

            source_elem = ET.SubElement(
                sources_elem,
                f"{{{NAMESPACE}}}TextSource",
                attrib={
                    "guid": file_guids[file_id],
                    "name": _xml_safe(file_content["name"]),
                    "plainTextPath": f"internal://{file_guids[file_id]}.txt",
                    "creatingUser": user_guid,
                    "creationDateTime": _utc_now()
                }
            )

            # Add description (memo) only when non-empty
            if file_content.get("memo"):
                desc_elem = ET.SubElement(source_elem, f"{{{NAMESPACE}}}Description")
                desc_elem.text = _xml_safe(file_content["memo"])

            # Add PlainTextSelection elements for each suggestion
            for suggestion in file_sug_list:
                self._add_plain_text_selection(
                    source_elem,
                    suggestion,
                    code_guids,
                    user_guid,
                    used_guids
                )

    def _add_plain_text_selection(
        self,
        source_elem: ET.Element,
        suggestion: CodingSuggestion,
        code_guids: Dict[int, str],
        user_guid: str,
        used_guids: Set[str]
    ) -> None:
        """Create PlainTextSelection element for a coded segment.

        Args:
            source_elem: Parent source element
            suggestion: CodingSuggestion to export
            code_guids: Mapping of code IDs to GUIDs
            user_guid: GUID of the creating user
            used_guids: GUIDs already used in this document (uniqueness is
                        mandatory within one REFI-QDA document)
        """
        # Selection GUID: session-supplied values are untrusted — validate
        # the format and document-uniqueness, minting a fresh one otherwise
        selection_guid = suggestion.guid
        if (not isinstance(selection_guid, str)
                or not _GUID_RE.match(selection_guid)
                or selection_guid in used_guids):
            selection_guid = str(uuid.uuid4())
        used_guids.add(selection_guid)

        selection_elem = ET.SubElement(
            source_elem,
            f"{{{NAMESPACE}}}PlainTextSelection",
            attrib={
                "guid": selection_guid,
                "startPosition": str(suggestion.start_pos),
                "endPosition": str(suggestion.end_pos),
                "creatingUser": user_guid,
                "creationDateTime": _utc_now()
            }
        )

        # Description: reasoning/memo, plus the AI confidence when one was
        # assigned (project exports of human codings carry confidence 0.0)
        memo_text = suggestion.reasoning or ""
        if suggestion.confidence > 0:
            tag = f"[AI confidence: {suggestion.confidence:.2f}]"
            memo_text = f"{memo_text} {tag}".strip()
        if memo_text:
            desc_elem = ET.SubElement(selection_elem, f"{{{NAMESPACE}}}Description")
            desc_elem.text = _xml_safe(memo_text)

        # Coding GUID: deterministic over file/code/BOTH positions (start
        # alone collided for same-start selections), deduped per document
        coding_guid = self.db.generate_deterministic_guid(
            "coding",
            f"{suggestion.file_id}_{suggestion.code_id}_"
            f"{suggestion.start_pos}_{suggestion.end_pos}"
        )
        if coding_guid in used_guids:
            coding_guid = str(uuid.uuid4())
        used_guids.add(coding_guid)

        coding_elem = ET.SubElement(
            selection_elem,
            f"{{{NAMESPACE}}}Coding",
            attrib={
                "guid": coding_guid,
                "creatingUser": user_guid,
                "creationDateTime": _utc_now()
            }
        )

        # Add CodeRef pointing to the code
        ET.SubElement(
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

        # Parse and prettify. Security note: this parses ONLY the string we
        # just serialized ourselves (never external/untrusted XML), so the
        # stdlib parser's XXE/entity-expansion caveats do not apply here.
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ", encoding='utf-8').decode('utf-8')

    def export_to_qdpx(
        self,
        suggestions: List[CodingSuggestion],
        output_path: str,
        project_name: str = "AI Coding Suggestions"
    ) -> str:
        """Export suggestions as .qdpx file (ZIP with XML).

        Container layout per spec §8: project.qde at the archive root plus
        a flat sources/ folder whose members are GUID-named .txt files
        (UTF-8, no BOM), referenced from the XML as internal://<guid>.txt.

        Suggestions are validated first — stale code/file references,
        out-of-bounds positions, or files without text content fail the
        export loudly instead of producing a .qdpx that crashes importers.

        Args:
            suggestions: List of coding suggestions to export
            output_path: Path where to save the .qdpx file
            project_name: Name for the REFI-QDA project

        Returns:
            Path to created .qdpx file

        Raises:
            ValueError: If any suggestion fails validation
            RuntimeError: If the export itself fails
        """
        # Validate BEFORE building anything (Gap 4: stale IDs used to
        # KeyError mid-export; empty-content files left dangling members)
        problems = self.validate_suggestions(suggestions)
        if problems:
            shown = "; ".join(problems[:10])
            more = f" (+{len(problems) - 10} more)" if len(problems) > 10 else ""
            raise ValueError(f"Export validation failed: {shown}{more}")

        try:
            # Create output directory if needed
            output_file = Path(output_path).expanduser()
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # Generate XML
            logger.info("Generating REFI-QDA XML...")
            xml_root = self.create_project_xml(suggestions, project_name)

            # Pretty print XML
            xml_string = self.prettify_xml(xml_root)

            file_guids = self.db.get_file_guids()

            # Create .qdpx file (ZIP archive)
            logger.info(f"Creating .qdpx archive: {output_file}")
            with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # project.qde at the root, lowercase (QualCoder's importer
                # hard-codes this name). Python str -> UTF-8, never a BOM:
                # a BOM would silently shift every position by one on
                # re-import.
                zipf.writestr('project.qde', xml_string)

                # Add source payloads under sources/<guid>.txt, verbatim
                # (positions are only meaningful against the exact text)
                file_ids = set(s.file_id for s in suggestions)
                logger.info(f"Adding {len(file_ids)} source files to archive...")

                for file_id in file_ids:
                    file_content = self.db.get_file_content(file_id)
                    content = (file_content or {}).get("content") or ""
                    if not content:
                        continue  # validated above; defensive only
                    member = f"sources/{file_guids[file_id]}.txt"
                    zipf.writestr(member, content)
                    logger.debug(f"Added source file: {member}")

            logger.info(f"Successfully exported {len(suggestions)} suggestions to {output_file}")
            return str(output_file)

        except ValueError:
            raise
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

        # File text lengths (also identifies files with no exportable text)
        content_lengths: Dict[int, int] = {}

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
            else:
                if suggestion.file_id not in content_lengths:
                    fc = self.db.get_file_content(suggestion.file_id)
                    content = (fc or {}).get("content") or ""
                    content_lengths[suggestion.file_id] = len(content)
                    if content and len(content.encode("utf-8")) > _MAX_INTERNAL_FILE_BYTES:
                        warnings.append(
                            f"File {suggestion.file_id} exceeds the REFI-QDA "
                            f"2 GiB internal file limit"
                        )
                length = content_lengths[suggestion.file_id]
                if length == 0:
                    warnings.append(
                        f"Suggestion {i}: File ID {suggestion.file_id} has no "
                        f"text content — selections cannot be exported for it"
                    )
                elif isinstance(suggestion.end_pos, int) and suggestion.end_pos > length:
                    warnings.append(
                        f"Suggestion {i}: End position {suggestion.end_pos} is "
                        f"beyond the file text (length {length})"
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
