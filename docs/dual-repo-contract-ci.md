# Dual-Repository Contract CI Design

## Current candidate pairing (2026-08-12)

The current isolated pairing is JobDesk `a63f2e9` against ConfFlow `0037c04`.
The released comparison remains JobDesk `e4d8f74` / v0.6.0 against ConfFlow
`6981935` / v2.0.0. Candidate compatibility is evidence only; it does not
authorize publication, installation over the stable environment, endpoint
switching, or production promotion.

The Phase 4 consumer gate additionally checks the per-server
`confflow.config.contract.v1` response, the packaged workflow-schema digest
`87991f09a0edbd56aed354bdd03b012775a2f2b98504297ab459e524f4542427`, and the
binding to the selected executable identity. The v2.0.0 stable producer uses
the explicit approved-identity compatibility path because it has no additive
config-contract command; unknown identity or hash values fail closed.

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
the checked snapshot, and runs consumer golden/negative fixtures. The stable
v2.0.0 path compares all five release members, including
`worker-handoff.schema.json`; historical v1.5.3 and v1.5.0 producers are
explicitly labeled non-stable and checked fail-closed against the current
major-version window. Historical producers without `control_worker` are
checked against the four-file core. The release
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

The candidate-side two-direction gate is exposed by
`.github/workflows/post-phase-f-contract.yml`. It is intentionally a manual
workflow: dispatch it from the exact JobDesk candidate ref and pass the exact
ConfFlow final-candidate ref as `confflow_ref`; the stable matrix rows always
checkout the released `v2.0.0` tag. It runs the
`base` and `chem` installations against both the released v2.0.0 wheel and the
selected candidate, checks capability/configuration-contract provenance and
schema bindings, verifies non-editable installed-wheel package data and
`pip check`, and runs the saved-workflow/resume/worker fixture corpus.
It does not run Gaussian, ORCA, SSH, or a production endpoint. A local run or
an unpushed candidate is not remote CI evidence.

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

## Current execution boundary (2026-08-11)

The producer candidate was followed by the formally published v2.0.0 release.
Main is now at the normal merge commit `69819350`, tag `v2.0.0` is immutable,
and the formal release wheel digest is
`04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f`.
The JobDesk compatibility matrix labels v2.0.0 as `stable` and keeps v1.5.3
and v1.5.0 only as explicitly historical comparisons; old candidate digests
are not referenced. The JobDesk consumer pin and the formal five-member
worker-handoff schema contract are advanced together. Real WSL
launcher/control computation,
reconnect/cancel/resume/artifact integrity, and the complete compatibility
cycle remain separate gates; no candidate-only or historical run is counted.
