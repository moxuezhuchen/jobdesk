# Pinned control schema snapshot

These five files are a canonical-JSON snapshot of the producer bundle from
the published ConfFlow `v2.1.1` tag, peeled commit
`338b53b3a34593271b926fc9e96010186141a386`:

| file | canonical JSON SHA-256 |
| --- | --- |
| `common.schema.json` | `494983e47ba7570c73e0d72b77df32b3ec877a2122ded40818c6369054830bc1` |
| `requests.schema.json` | `72b0beab10e6cb380d66e11b5757a750efe1271d43be0098166e87b59af623c3` |
| `responses.schema.json` | `11c70a0d40063409e1f6aff3a74a3951cda0c573fe0ea7f4850c38c000dd886b` |
| `input-manifest.schema.json` | `b0a98bf2b758733de054c67baaf440d2839be37013ba40d09365d73f790daf97` |
| `worker-handoff.schema.json` | `8c8bed4cc9550a466bc8fc7b010bd2857d4d34efc6b381f5a7a62573f3169459` |

The parity test compares canonical JSON, not formatting. Do not edit one file
in isolation; update the producer release reference, the snapshot, the
consumer parser, and the dual-repository CI gate together.
