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
  materials published at https://www.qdasoftware.org/ — the official
  download on qdasoftware.org's "Project Implementation Files" page is
  served through a Tresorit share that does not allow direct retrieval;
  the canonical schema URL referenced by the specification,
  http://schema.qdasoftware.org/versions/Project/v1.0/Project.xsd, no
  longer resolves)
- SHA-256: 77608205d5c17c6a60f75e771dd825d8e36eb7db24f8fe7cb86d47e70f83c0f5
- Target namespace: `urn:QDA-XML:project:1.0`

## License / copyright

The REFI-QDA standard and its related definitions and documents are
copyright REFI / qdasoftware.org. The schema is redistributed here
unmodified, solely to enable conformance testing against the published
standard, per the standard's stated purpose of enabling interoperable
implementations. See https://www.qdasoftware.org/ for the authoritative
specification and licensing terms.
