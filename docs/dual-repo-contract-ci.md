# Dual-Repository Contract CI Design

ConfFlow is the sole owner of the control-protocol JSON Schema and the
capability/artifact contract constants exposed by its capability payload.
JobDesk consumes a release schema bundle and never hand-edits a second
authoritative protocol definition.

## Release artifacts

Every ConfFlow producer release carries the versioned control schema bundle
beside the wheel. Capability and artifact shapes are currently represented by
the producer's Python contract constants and capability payload, not by
separate JSON schema files. All of them are bound to the immutable tag, peeled
commit, wheel digest, and attestation subject.

JobDesk pins the exact producer tag and wheel digest. Its matrix installs that
release, checks the capability payload with the JobDesk parser, requires clean
build/producer provenance, compares the installed control schema bundle with
the checked snapshot, and runs consumer golden/negative fixtures. The release
and deployment gates additionally verify the external attestation and install
provenance; a plain pip install in the matrix deliberately reports a missing
install record as candidate-only. The producer bundle remains authoritative;
the JobDesk copy is a checked snapshot whose canonical content must match the
pinned release.

## Pull-request matrices

ConfFlow pull requests run `producer candidate × JobDesk main` compatibility
tests. The candidate wheel and schema bundle are installed in an isolated
environment, and JobDesk's parser, fixture, and contract suite run against
them.

JobDesk pull requests run `JobDesk candidate × current stable producer × next
producer candidate`. The stable producer protects released behavior; the next
candidate catches schema and capability drift before producer release.

## Automation

A release or PR automation job opens an update PR when the pinned producer tag,
peeled commit, wheel digest, or derived schema fixtures change. The update
includes the producer provenance, schema bundle digest, and compatibility
output. A manual one-sided version-string edit is not mergeable.

Breaking protocol/schema changes raise the major version. Forward-compatible
optional fields raise the minor version and retain at least one consumer
migration cycle. CI rejects a bundle whose declared protocol revision, tag,
commit, wheel digest, or schema digest is inconsistent.

## Required gates

1. Control-bundle examples pass producer validation and JobDesk golden
   fixtures; capability/artifact fields pass the shared parser contract.
2. Negative fixtures reject missing fields, wrong types, invalid enum values,
   unsafe paths, and extra fields.
3. Producer checkout HEAD equals the immutable peeled tag commit.
4. Wheel `__build__.COMMIT`, checksum manifest, attestation subject, and
   capability provenance identify the same release.
5. The matrix runs without WSL or Gaussian dependencies; real SSH/WSL
   acceptance remains a separate integration gate.

## Current execution boundary (2026-08-08)

The ConfFlow candidate workflow has a green run for PR #50 after installing the
Qt runtime libraries. The vendored JobDesk control-schema snapshot is checked
against the canonical v1.5.0 producer bundle locally, and both workflow files
now contain the candidate capability/schema-parity step. On 2026-08-08, the
isolated local matrix also ran the exact stable v1.5.0 and next v1.5.1 wheels
under Python 3.13: each capability/build/schema gate passed and each pinned
contract suite reported `88 passed`. The local Windows run used command-presence
stubs for the Unix-only capability fields and is not a substitute for Ubuntu
Actions. The JobDesk candidate matrix has not been published, so it still has
no remote CI result. Real WSL, launcher, and Gaussian/ORCA acceptance remain
separate gates. The unpublished producer candidate `2849dd4` additionally
contains a worker-handoff schema and real direct g16 evidence; that candidate
schema is intentionally outside the pinned v1.5.0 four-file matrix until the
producer release and consumer pin advance together.
