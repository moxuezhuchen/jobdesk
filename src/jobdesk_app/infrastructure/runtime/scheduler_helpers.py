"""Helpers to build scheduler adapter and resource spec from ServerConfig."""

from __future__ import annotations


def scheduler_from_server(server_config) -> object:
    """Build a SchedulerAdapter from a ServerConfig."""
    from ..remote.scheduler import make_adapter

    sched_cfg = getattr(server_config, "scheduler", None)
    sched_type = getattr(sched_cfg, "type", "nohup") if sched_cfg else "nohup"
    return make_adapter(sched_type)


def resources_from_server(server_config, overrides: dict | None = None):
    """Build a ResourceSpec from a ServerConfig's scheduler defaults + optional overrides."""
    from ..remote.scheduler import ResourceSpec

    sched_cfg = getattr(server_config, "scheduler", None)
    if sched_cfg is None:
        base = {}
    else:
        base = {
            "cpus": sched_cfg.default_cpus,
            "memory_mb": sched_cfg.default_memory_mb,
            "walltime_minutes": sched_cfg.default_walltime_minutes,
            "partition": sched_cfg.default_partition,
            "account": sched_cfg.default_account,
            "gpus": sched_cfg.default_gpus,
            "extra_directives": list(sched_cfg.extra_directives),
        }
    if overrides:
        base.update(overrides)
    return ResourceSpec.from_dict(base)
