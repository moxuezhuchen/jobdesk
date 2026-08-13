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
the matching immutable snapshot under
`confflow/schemas/control/releases/v<version>/`, and runs consumer
golden/negative fixtures. The stable v2.0.0 path compares all five release
members, including `worker-handoff.schema.json`; historical v1.5.3 and v1.5.0
producers are explicitly labeled non-stable and checked fail-closed against
the current major-version window. Historical producers without
`control_worker` are checked against the four-file core. The release
and deployment gates additionally verify the external attestation and install
provenance; a plain pip install in the matrix deliberately reports a missing
install record as candidate-only. The producer bundle remains authoritative;
the versioned JobDesk copy is a checked snapshot whose canonical content must
match that pinned release. The files directly under
`confflow/schemas/control/` are the current candidate snapshot and are kept
separate so candidate semantics cannot silently rewrite an immutable release
contract.

## Pull-request matrices

The repository workflow currently runs the consumer contract checks against
the pinned stable v2.0.0 producer and retains v1.5.3/v1.5.0 as historical
fail-closed comparisons. It does not yet run a producer-candidate matrix or a
`JobDesk candidate x stable x next candidate` matrix. Those matrices remain a
future design item and must not be described as present CI coverage.

## Automation

The current workflow does not open update PRs or regenerate the pinned schema
snapshot automatically. A future release/PR automation job may do so when the
pinned producer tag, peeled commit, wheel digest, or derived schema fixtures
change; until then, provenance and schema updates require an explicit,
reviewed change to both repositories. A manual one-sided version-string edit
is not mergeable.

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

## Current execution boundary (2026-08-11)

The producer candidate was followed by the formally published v2.0.0 release.
The release main is at the normal merge commit `69819350`, tag `v2.0.0` is
immutable, and the formal release wheel digest is
`04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f`.
The JobDesk compatibility matrix labels v2.0.0 as `stable` and keeps v1.5.3
and v1.5.0 only as explicitly historical comparisons; each row uses its
matching immutable release snapshot. The current root snapshot may carry an
unreleased candidate semantic (including asynchronous cancel intent), but it
is not counted as stable and does not rewrite the v2.0.0 evidence. A formal
producer release and a matching reviewed JobDesk pin are required before that
candidate can become stable. Real WSL
launcher/control computation,
reconnect/cancel/resume/artifact integrity, and the complete compatibility
cycle remain separate gates; no candidate-only or historical run is counted.
