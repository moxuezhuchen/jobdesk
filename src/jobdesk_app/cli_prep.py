"""JobDesk Prep CLI — input building, molecule viewing, and SMILES conversion."""
from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


# ---- parser ---------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobdesk-prep")
    sub = parser.add_subparsers(dest="command", required=True)

    # -- input subcommand group --
    inp = sub.add_parser("input", help="Build Gaussian/ORCA input files")
    inp_sub = inp.add_subparsers(dest="inp_command", required=True)

    inp_list = inp_sub.add_parser("list-presets")
    inp_list.set_defaults(func=_cmd_input_list_presets)

    inp_build = inp_sub.add_parser("build")
    inp_build.add_argument("xyz_path", type=argparse.FileType("rb"))
    inp_build.add_argument("--preset", default=None)
    inp_build.add_argument("--method", default="B3LYP/6-31G(d)")
    inp_build.add_argument("--keywords", nargs="+", default=["opt", "freq"])
    inp_build.add_argument("--charge", type=int, default=0)
    inp_build.add_argument("--mult", type=int, default=1)
    inp_build.add_argument("--nproc", type=int, default=8)
    inp_build.add_argument("--mem", default="16GB")
    inp_build.add_argument("--output", type=argparse.FileType("wb"), default=None)
    inp_build.add_argument("--orca", action="store_true")
    inp_build.set_defaults(func=_cmd_input_build)

    # -- viewer subcommand --
    viewer = sub.add_parser("viewer", help="Open files in molecular viewers")
    viewer_sub = viewer.add_subparsers(dest="viewer_command", required=True)

    v_list = viewer_sub.add_parser("list")
    v_list.set_defaults(func=_cmd_viewer_list)

    v_open = viewer_sub.add_parser("open")
    v_open.add_argument("file_path", type=argparse.FileType("rb"))
    v_open.add_argument("--viewer", default="avogadro")
    v_open.add_argument("--exe", default=None)
    v_open.set_defaults(func=_cmd_viewer_open)

    # -- smiles subcommand --
    smiles = sub.add_parser("smiles", help="SMILES to 3D structure")
    smiles_sub = smiles.add_subparsers(dest="smiles_command", required=True)

    s_xyz = smiles_sub.add_parser("to-xyz")
    s_xyz.add_argument("smiles")
    s_xyz.add_argument("--output", type=argparse.FileType("wb"), default=None)
    s_xyz.add_argument("--title", default="")
    s_xyz.set_defaults(func=_cmd_smiles_to_xyz)

    s_gjf = smiles_sub.add_parser("to-gjf")
    s_gjf.add_argument("smiles")
    s_gjf.add_argument("--output", type=argparse.FileType("wb"), default=None)
    s_gjf.add_argument("--preset", default="b3lyp_631gd_opt_freq")
    s_gjf.add_argument("--title", default="")
    s_gjf.set_defaults(func=_cmd_smiles_to_gjf)

    return parser


# ---- input commands -------------------------------------------------------


def _cmd_input_list_presets(args) -> int:
    from .core.input_builder import list_presets
    for name, desc in sorted(list_presets().items()):
        print(f"{name}: {desc}")
    return 0


def _cmd_input_build(args) -> int:
    from .core.input_builder import (
        GaussianInputSpec,
        OrcaInputSpec,
        build_from_preset,
        build_gjf,
        build_inp,
    )
    if args.preset:
        content = build_from_preset(args.xyz_path.name, args.preset, None)
    elif args.orca:
        orca_spec = OrcaInputSpec(
            keywords=f"! {args.method} {' '.join(args.keywords)}",
            charge=args.charge,
            multiplicity=args.mult,
            nproc=args.nproc,
        )
        content = build_inp(args.xyz_path.name, orca_spec, None)
    else:
        gauss_spec = GaussianInputSpec(
            method_basis=args.method,
            job_keywords=args.keywords,
            charge=args.charge,
            multiplicity=args.mult,
            nproc=args.nproc,
            mem=args.mem,
        )
        content = build_gjf(args.xyz_path.name, gauss_spec, None)
    if args.output:
        args.output.write(content.encode("utf-8"))
        print(f"Written to {args.output.name}")
    else:
        print(content)
    return 0


# ---- viewer commands ------------------------------------------------------


def _cmd_viewer_list(args) -> int:
    from .core.viewer import list_available_viewers
    viewers = list_available_viewers()
    if not viewers:
        print("No molecular viewers found. Install Avogadro, GaussView, or ChemCraft.")
        return 0
    for name, path in sorted(viewers.items()):
        print(f"{name}: {path}")
    return 0


def _cmd_viewer_open(args) -> int:
    from .core.viewer import open_in_viewer
    if open_in_viewer(args.file_path.name, args.viewer, args.exe):
        print(f"Opened {args.file_path.name} in {args.viewer}")
        return 0
    print(f"Viewer not found: {args.viewer}. Use 'jobdesk-prep viewer list' to see available viewers.")
    return 2


# ---- smiles commands ------------------------------------------------------


def _cmd_smiles_to_xyz(args) -> int:
    from .core.viewer import is_rdkit_available, smiles_to_xyz
    if not is_rdkit_available():
        print("rdkit is required. Install with: pip install rdkit")
        return 2
    try:
        content = smiles_to_xyz(args.smiles, None, args.title)
        if args.output:
            args.output.write(content.encode("utf-8"))
            print(f"Written to {args.output.name}")
        else:
            print(content)
        return 0
    except ValueError as e:
        print(f"Error: {e}")
        return 2


def _cmd_smiles_to_gjf(args) -> int:
    from .core.viewer import is_rdkit_available, smiles_to_gjf
    if not is_rdkit_available():
        print("rdkit is required. Install with: pip install rdkit")
        return 2
    try:
        content = smiles_to_gjf(args.smiles, None, args.preset, args.title)
        if args.output:
            args.output.write(content.encode("utf-8"))
            print(f"Written to {args.output.name}")
        else:
            print(content)
        return 0
    except ValueError as e:
        print(f"Error: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
