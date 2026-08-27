# Control schema snapshots

ConfFlow owns the control-protocol schemas. The JSON files directly in this
directory are the current JobDesk candidate snapshot. They intentionally may
contain unreleased compatibility work, including the asynchronous cancel
intent response states; they are not a claim that the immutable v2.0.0
release has changed.

The compatibility matrix uses the immutable, wheel-derived snapshots under
`releases/` instead:

| release | snapshot | wheel SHA-256 |
| --- | --- | --- |
| `v2.1.3` | `releases/v2.1.3/` | `10dab012cc8dafea9de2279bddfea3e978807cb0d526111dbe5eaee26cf542fe` |
| `v2.0.0` | `releases/v2.0.0/` | `04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f` |
| `v1.5.3` | `releases/v1.5.3/` | `213eba551b344c7146450fa1135a884e3c00896371507a1edbf2eb18c7c0c5d6` |
| `v1.5.0` | `releases/v1.5.0/` | `d9ac87410f1b73b91e19eb740298431663ee5f07bd4ffaeb19779c3a53c2e8dc` |

Each release directory is extracted from the exact wheel named by the matrix
and records canonical JSON hashes in its README. Do not edit a release
snapshot or use the candidate root as a release substitute. A producer schema
change requires a new producer release and a reviewed update to the matching
release snapshot and matrix pin.
