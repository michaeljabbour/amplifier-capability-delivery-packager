# Delivery Packager

Package approved local files for a reviewer, client or teammate. The tool creates one ZIP containing only the selected editable/final assets and a SHA-256 manifest with credits, declared licenses, accessibility notes, owner, limits and next action. It never sends the package.

## Setup

Use Python 3.11+ on a POSIX host with descriptor-relative `O_NOFOLLOW` operations and same-filesystem hard links. No packages, provider or other capability is required. Start from a reviewed full Git commit. Keep `delivery_packager.py` and `LICENSE` together in a chosen project/tools directory, or run directly from the pinned checkout. The native CLI/library works across harnesses; there is no synthetic SKILL wrapper. Uninstall by removing only that chosen copy; archives remain independent.

## First task

The included repair-club files and plan are fictional. Create an empty destination outside the selected source tree, inspect the plan and run:

```sh
mkdir -p outputs
python3 delivery_packager.py preview --root examples/assets --plan examples/plan.json --destination outputs --name repair-club.zip
```

Independently approve every listed file, declaration and local destination. Copy the returned `approval_sha256` exactly; metadata is not permission:

```sh
python3 delivery_packager.py build --root examples/assets --plan examples/plan.json --destination outputs --name repair-club.zip --apply --expect-plan REVIEWED_SHA256
python3 delivery_packager.py verify outputs/repair-club.zip --expected-sha256 RETURNED_ARCHIVE_SHA256
```

A changed file, plan, root, destination, filename or limit requires a new preview. Existing output is refused. Successful JSON has exit0; blocked input has exit2. Use `--max-total-bytes` to lower the64 MiB default; hard maximum512 MiB, at most 128 files. Metadata has a separate256 KiB bound. Empty files are valid. Nothing is extracted or executed.

## Contract and limits

The [versioned plan](docs/CONTRACT.md) and [`examples/plan.json`](examples/plan.json) specify every field. `preview`, `build`, `verify` and `Limits` are importable standard-library APIs. The ZIP uses stored entries and fixed timestamps; this is not encryption, a digital signature, BagIt, RO-Crate or a large-media transfer service. POSIX protections do not promise protection against an arbitrary process running as the same user after creation. Windows native execution is unsupported; resulting ZIPs are conventional files.

Checksums establish byte integrity against the supplied digest. License, accessibility, ownership, approval and reported destination status are declarations, not verified rights, accessibility conformance, authority or receipt. A recipient needs a trusted external archive digest to detect replacement of both payload and manifest. Packaging is separate from human approval to distribute.

Optional practice is a pre/post checkpoint: the person selects the allowlist and explains a questionable inclusion before reviewing the preview. It never intercepts or loops around native execution. Stop at user stop, target, agreed cap, stalled gain, missing authority or critical unknown. Save no coaching record by default. See [optional guide](docs/coaching-session.v1.md).

[Related tools/research](docs/RELATED.md) · [shape and separation](docs/RIGHT-SHAPE.md) · [provenance](PROVENANCE.md). Run `make check` for local contract tests. Human usefulness and recipient acceptance have not been measured.
