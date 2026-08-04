# Dual-Repository Contract CI Design

ConfFlow is the sole owner of the control-protocol JSON Schema, capability schema, and artifact schema. JobDesk consumes a release schema bundle and never hand-edits a second authoritative protocol definition.

## Release artifacts

Every ConfFlow producer release carries a versioned schema bundle beside the wheel. The bundle contains the exact control, capability, and artifact schemas used by that release. It is bound to the immutable tag, peeled commit, wheel digest, and attestation subject.

JobDesk pins the exact producer tag and peeled commit. Its CI installs that release and generates typed fixtures from the bundled schemas. The generated fixtures are checked into the JobDesk test workspace only as derived test data; the producer bundle remains authoritative.

## Pull-request matrices

ConfFlow pull requests run `producer candidate × JobDesk main` compatibility tests. The candidate wheel and schema bundle are installed in an isolated environment, and JobDesk's parser, fixture, and contract suite run against them.

JobDesk pull requests run `JobDesk candidate × current stable producer × next producer candidate`. The stable producer protects released behavior; the next candidate catches schema and capability drift before producer release.

## Automation

A release or PR automation job opens an update PR when the pinned producer tag, peeled commit, wheel digest, or derived schema fixtures change. The update includes the producer provenance, schema bundle digest, and compatibility output. A manual one-sided version-string edit is not mergeable.

Breaking protocol/schema changes raise the major version. Forward-compatible optional fields raise the minor version and retain at least one consumer migration cycle. CI rejects a bundle whose declared protocol revision, tag, commit, wheel digest, or schema digest is inconsistent.

## Required gates

1. Bundle schema examples pass producer validation and JobDesk golden fixtures.
2. Negative fixtures reject missing fields, wrong types, invalid enum values, unsafe paths, and extra fields.
3. Producer checkout HEAD equals the immutable peeled tag commit.
4. Wheel `__build__.COMMIT`, checksum manifest, attestation subject, and capability provenance identify the same release.
5. The matrix runs without WSL or Gaussian dependencies; real SSH/WSL acceptance remains a separate integration gate.
