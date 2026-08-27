#!/usr/bin/env python3
"""Verify an installed ConfFlow CLI through JobDesk's runtime contract client."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from typing import Any

from jobdesk_app.core.confflow_preflight import (
    parse_confflow_capabilities,
    validate_confflow_capabilities,
    validate_confflow_production_capability,
)
from jobdesk_app.remote.confflow_config_contract import parse_contract_response
from jobdesk_app.services.ssh_configuration_contract_client import SSHConfigurationContractClient


@dataclass(frozen=True)
class _Response:
    exit_code: int
    stdout: str
    stderr: str


class _RecordedSSH:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = list(responses)
        self.stdin: list[bytes | None] = []

    def run(
        self,
        command: str,
        timeout: int | None = None,
        check: bool = False,
        stdin_data: bytes | None = None,
    ) -> _Response:
        del command, timeout, check
        self.stdin.append(stdin_data)
        if not self._responses:
            raise AssertionError("runtime verifier exhausted recorded CLI responses")
        return self._responses.pop(0)


def _run(executable: str, *args: str, stdin: bytes | None = None) -> _Response:
    completed = subprocess.run(
        [executable, *args],
        input=stdin,
        capture_output=True,
        check=False,
    )
    return _Response(
        completed.returncode,
        completed.stdout.decode("utf-8", "strict"),
        completed.stderr.decode("utf-8", "replace"),
    )


def verify(executable: str, *, require_production: bool = True) -> dict[str, Any]:
    capabilities_response = _run(executable, "--capabilities", "--json")
    if capabilities_response.exit_code != 0:
        raise RuntimeError("installed ConfFlow capabilities command failed")
    capabilities = parse_confflow_capabilities(capabilities_response.stdout)
    if require_production:
        identity = validate_confflow_production_capability(capabilities)
    else:
        validate_confflow_capabilities(capabilities, require_dag=True)
        identity = capabilities.executable or {}
    raw_identity = capabilities.executable or {}
    configured_executable = raw_identity.get("path")
    if not isinstance(configured_executable, str) or not configured_executable:
        raise RuntimeError("installed ConfFlow capabilities omitted executable.path")

    contract_response = _run(executable, "config", "contract", "--json")
    if contract_response.exit_code != 0:
        raise RuntimeError("installed ConfFlow contract command failed")
    direct_contract = parse_contract_response(
        contract_response.stdout,
        server_id="installed-v2.1.6",
        configured_executable=configured_executable,
        capabilities=capabilities,
    )

    valid_bytes = b"global: {}\nsteps: []\n"
    invalid_bytes = b"steps:\n  - type: bad\n"
    valid_json = b'{"global":{},"steps":[]}'
    invalid_json = b'{"steps":[{"type":"bad"}]}'
    valid_response = _run(executable, "config", "validate", "--json", "--stdin", stdin=valid_json)
    invalid_response = _run(executable, "config", "validate", "--json", "--stdin", stdin=invalid_json)
    if valid_response.exit_code != 0 or invalid_response.exit_code != 1:
        raise RuntimeError("installed ConfFlow validation exit codes are not 0/1")

    ssh = _RecordedSSH([contract_response, valid_response, invalid_response])
    client = SSHConfigurationContractClient()
    contract = client.resolve(
        server_id="installed-v2.1.6",
        configured_executable=configured_executable,
        env_init_scripts=(),
        ssh=ssh,
        capabilities=capabilities,
    )
    valid = client.validate(contract, valid_bytes, env_init_scripts=(), ssh=ssh)
    invalid = client.validate(contract, invalid_bytes, env_init_scripts=(), ssh=ssh)
    if contract.cache_key != direct_contract.cache_key or not valid.valid or invalid.valid:
        raise RuntimeError("installed ConfFlow runtime contract results disagree")
    if ssh.stdin != [None, valid_json, invalid_json]:
        raise RuntimeError("runtime client did not transcode YAML to the producer canonical JSON ABI")

    return {
        "status": "compatible",
        "version": capabilities.version,
        "schema": contract.content_schema,
        "schema_sha256": contract.schema_sha256,
        "executable_sha256": identity["sha256"],
        "valid_exit_code": valid_response.exit_code,
        "invalid_exit_code": invalid_response.exit_code,
        "invalid_issue_count": len(invalid.diagnostics),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", default="confflow")
    parser.add_argument("--allow-candidate", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.executable, require_production=not args.allow_candidate), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
