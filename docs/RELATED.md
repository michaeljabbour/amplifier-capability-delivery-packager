# Related tools and research

Reviewed 2026-11-01. These relationships do not imply adoption, partnership or measured benefits.

- [BagIt (RFC 8493)](https://www.rfc-editor.org/rfc/rfc8493) — inspiration. Separates payload bytes and descriptive metadata with checksums. This package is a small ZIP delivery contract, not a BagIt implementation. No specification text or implementation copied; no conformance claim.
- [Python zipfile](https://docs.python.org/3/library/zipfile.html) — actual engine reuse. The existing Python standard library writes and reads stored ZIP entries. Delivery Packager adds bounded allowlists, declarations and approval binding. Imported from the caller runtime; no CPython implementation is vendored. Python runtime has its own license.
- [Library of Congress bagit-python](https://github.com/LibraryOfCongress/bagit-python) — alternative. Choose this for creating or validating BagIt directory packages. No bagit-python dependency or interoperability is implemented here. No code copied or installed; project metadata declares Public Domain, not an adoption or legal clearance claim.
- [RO-Crate](https://www.researchobject.org/ro-crate/) — complement. Research-data metadata interchange may be useful for a future explicit export. This archive does not implement the RO-Crate format. Research only; no specification text or code copied.
