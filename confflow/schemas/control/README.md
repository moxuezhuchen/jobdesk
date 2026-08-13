# Pinned control schema snapshot

These four files are a canonical-JSON snapshot of the producer bundle from
ConfFlow `v1.5.0` commit
`0fff6439a4614ec155959b1d0d3781fc5342d736`:

| file | canonical JSON SHA-256 |
| --- | --- |
| `common.schema.json` | `494983e47ba7570c73e0d72b77df32b3ec877a2122ded40818c6369054830bc1` |
| `requests.schema.json` | `72b0beab10e6cb380d66e11b5757a750efe1271d43be0098166e87b59af623c3` |
| `responses.schema.json` | `312e7b88047a20015080877903b63aa52df850c07a2a45fb023a30179e7d86b3` |
| `input-manifest.schema.json` | `b0a98bf2b758733de054c67baaf440d2839be37013ba40d09365d73f790daf97` |

The parity test compares canonical JSON, not formatting. Do not edit one file
in isolation; update the producer release reference, the snapshot, the
consumer parser, and the dual-repository CI gate together.
