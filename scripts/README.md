# JobDesk smoke scripts

This matrix lists the eight supported smoke entry points. The timeout values below are the **code-configured upper bounds**, not measured runtimes.

| Script | Execution model | Timeout / constraint |
|---|---|---|
| `smoke_confflow_wsl.py` | Windows Python driver targeting WSL | 120s |
| `smoke_confflow_real_g16_wsl.py` | Windows Python driver targeting WSL | 600s |
| `smoke_confflow_real_g16_chk_wsl.py` | Windows Python driver targeting WSL | 600s |
| `smoke_confflow_real_g16_ts_wsl.py` | Windows Python driver targeting WSL | 900s |
| `smoke_confflow_dual_mol_release.py` | Windows Python driver targeting WSL | 900s |
| `smoke_confflow_dag_round_trip.py` | Local pure Python | No explicit timeout; no SSH or g16 |
| `smoke_gui_offscreen.py` | Local pure Python | No explicit timeout |
| `smoke_workflow_spec.py` | Local pure Python | No explicit timeout |

The three real-g16 scripts require `/opt/g16/g16`. Run them only under the project’s `.cursor/rules/wsl-g16-safety.mdc` procedure. This documentation change neither runs nor modifies the g16 installation.
