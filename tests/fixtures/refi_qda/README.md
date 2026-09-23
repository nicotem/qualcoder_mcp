# REFI-QDA Project.xsd (vendored, test-only)

`Project.xsd` is the official XML schema of the REFI-QDA Project Exchange
standard (QDA-XML library version 1.0, module release date 18 March 2019,
authored by the specification editor Fred van Blommestein). It is used
ONLY by the test suite (`tests/test_refi_xsd_validation.py`) to validate
`.qdpx` archives produced by `export_refi_qda`; it is not part of the
installed package.

## Provenance

- Retrieved: 2026-07-15
- Source: https://raw.githubusercontent.com/openqda/refi-tools/main/docs/schemas/project/v1.0/Project.xsd
  (the openqda/refi-tools repository, a public mirror of the REFI-QDA
  materials published at https://www.qdasoftware.org/, the official
  download on qdasoftware.org's "Project Implementation Files" page is
  served through a Tresorit share that does not allow direct retrieval;
  the canonical schema URL referenced by the specification,
  http://schema.qdasoftware.org/versions/Project/v1.0/Project.xsd, no
  longer resolves)
- SHA-256 as retrieved: 77608205d5c17c6a60f75e771dd825d8e36eb7db24f8fe7cb86d47e70f83c0f5
- REFI's copyright notice restored (2026-09-23). The refi-tools copy
  omits the "Copyright Notice" comment that QualCoder's copy of the same
  schema keeps. The 27 lines were taken from QualCoder 3.6's embedded
  copy (`qualcoder/xsd.py`, lines 93-119, where the schema is
  a Python string), removing only the string escapes that end each line
  (a trailing `\n\` or `\`), and placed where QualCoder's copy has them:
  after the header comments, before `<xsd:schema>`. The notice's words
  are character for character those of QualCoder's string, including
  REFI's own spelling; nothing else in the file changed.
- SHA-256 now: 891d1ee5e25613dbf038b8b02c03eafc6c509044e7aa49c6eb8ab1a6d9e5ce85
- Target namespace: `urn:QDA-XML:project:1.0`

## Licence and copyright

The schema is copyright 2019 REFI (www.qdasoftware.org) and licensed by
REFI under the MIT licence, whose terms require the copyright and
permission notice in every copy; the notice is in the file, as a comment
before `<xsd:schema>`. The schema is used here solely for conformance
testing against the published standard, per the standard's stated
purpose of enabling interoperable implementations. See
https://www.qdasoftware.org/ for the authoritative specification.
