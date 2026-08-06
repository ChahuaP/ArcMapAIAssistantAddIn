#!/usr/bin/env python3
"""Run the frozen GeoPilot ablation matrix with one clean ArcMap per cell."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Sequence

import psutil

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from pair_workspace import (
    PairWorkspaceError,
    relocate_pair_workspace,
)
from source_provenance import repository_state


MODES = ("g0_direct", "g1_context", "g2_constrained", "g3_audited")
PAIRED_EXECUTION_UNIT = "g2_g3_paired"
PAIRED_MODES = ("g2_constrained", "g3_audited")
DEFAULT_GATEWAY = "http://127.0.0.1:8765"
DEFAULT_ARCMAP = Path(r"C:\Program Files (x86)\ArcGIS\Desktop10.2\bin\ArcMap.exe")
REPOSITORY = Path(__file__).resolve().parents[2]
DEFAULT_RUNNER = Path(__file__).with_name("run_formal_experiments.py")
BRIDGE_SOURCE = REPOSITORY / "ArcMapBridgeExternal" / "Program.cs"
RUNTIME_SOURCE = REPOSITORY / "arcmap_runtime_py2"
CATALOG_SOURCE = REPOSITORY / "operation_catalog"
INSTALL_CONFIG = Path(os.environ.get("APPDATA", "")) / "ArcMapAIAssistant" / "install.json"
EXECUTION_ASSET_SUFFIXES = {".py", ".json"}
ARCMAP_CLIENT_WIDTH = 1600
ARCMAP_CLIENT_HEIGHT = 900


class CampaignError(RuntimeError):
    pass


def execution_units_for(modes: Sequence[str]) -> tuple[str, ...]:
    """Keep G2/G3 adjacent as one proof unit while running two clean processes."""
    requested = tuple(modes)
    requested_set = set(requested)
    if ("g2_constrained" in requested_set) != ("g3_audited" in requested_set):
        raise ValueError("g2_constrained and g3_audited must be selected together")
    units: list[str] = []
    for mode in requested:
        if mode in PAIRED_MODES:
            if PAIRED_EXECUTION_UNIT not in units:
                units.append(PAIRED_EXECUTION_UNIT)
        else:
            units.append(mode)
    return tuple(units)


def runner_modes_for(execution_unit: str) -> tuple[str, ...]:
    if execution_unit == PAIRED_EXECUTION_UNIT:
        return PAIRED_MODES
    if execution_unit in MODES:
        return (execution_unit,)
    raise ValueError("Unknown execution unit: %s" % execution_unit)


def build_method_order(
    *,
    seeds: Sequence[int],
    repetitions: int,
    modes: Sequence[str] = MODES,
    order_seed: int,
) -> list[dict[str, Any]]:
    if not seeds or repetitions <= 0 or not modes:
        raise ValueError("seeds, repetitions, and modes must be non-empty and positive")
    if len(set(seeds)) != len(seeds) or len(set(modes)) != len(modes):
        raise ValueError("seeds and modes must be unique")
    rng = random.Random(order_seed)
    base = list(execution_units_for(modes))
    rng.shuffle(base)
    blocks = [(int(seed), repetition) for seed in seeds for repetition in range(1, repetitions + 1)]
    rng.shuffle(blocks)
    result: list[dict[str, Any]] = []
    for block_index, (seed, repetition) in enumerate(blocks):
        offset = block_index % len(base)
        block_modes = base[offset:] + base[:offset]
        for position, mode in enumerate(block_modes, start=1):
            result.append(
                {
                    "ordinal": len(result) + 1,
                    "block": block_index + 1,
                    "position": position,
                    "seed": seed,
                    "repetition": repetition,
                    "mode": mode,
                    "cell_id": "%s-seed-%d-rep-%02d" % (mode, seed, repetition),
                }
            )
    return result


def request_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise CampaignError("Gateway returned a non-object response: %s" % url)
    return payload


def post_json(url: str, payload: dict[str, Any], timeout: float = 3.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise CampaignError("Gateway returned a non-object response: %s" % url)
    return value


def listener_pids(port: int) -> set[int]:
    owners: set[int] = set()
    for connection in psutil.net_connections(kind="tcp"):
        if connection.status != psutil.CONN_LISTEN or not connection.laddr:
            continue
        if connection.laddr.port == port and connection.pid is not None:
            owners.add(connection.pid)
    return owners


def named_processes(names: Iterable[str]) -> list[tuple[int, str]]:
    expected = {name.lower() for name in names}
    found = []
    for process in psutil.process_iter(("pid", "name")):
        name = (process.info.get("name") or "").lower()
        if name in expected:
            found.append((int(process.info["pid"]), name))
    return found


def assert_clean_host(gateway: str) -> None:
    parsed = urllib.parse.urlparse(gateway)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    owners = listener_pids(port)
    if owners:
        raise CampaignError("Gateway port is already owned: %s" % sorted(owners))
    processes = named_processes(("ArcMap.exe", "ArcMapBridge.exe"))
    if processes:
        raise CampaignError("ArcMap experiment host is not clean: %s" % processes)


def wait_gateway(gateway: str, process: subprocess.Popen, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline and process.poll() is None:
        try:
            health = request_json(gateway.rstrip("/") + "/health")
            if health.get("ok") is True:
                return health
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise CampaignError("Gateway startup failed: %s" % (last_error or process.poll()))


def wait_bridge(gateway: str, arcmap_pid: int, process: subprocess.Popen, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline and process.poll() is None:
        try:
            payload = request_json(gateway.rstrip("/") + "/arcmap/bridges")
            matches = [
                bridge
                for bridge in payload.get("bridges", [])
                if bridge.get("arcmap_pid") == arcmap_pid
            ]
            if len(matches) == 1:
                bridge = matches[0]
                if not bridge.get("bridge_pid") or not bridge.get("hwnd"):
                    raise CampaignError("Bridge identity is incomplete")
                validate_bridge_source_identity(bridge)
                return bridge
            if len(matches) > 1:
                raise CampaignError("More than one Bridge owns the ArcMap process")
        except CampaignError:
            raise
        except Exception as exc:
            last_error = exc
        time.sleep(0.5)
    raise CampaignError("ArcMap Bridge startup failed: %s" % (last_error or process.poll()))


def validate_bridge_source_identity(bridge: dict[str, Any]) -> None:
    summary = bridge.get("summary")
    actual = summary.get("source_sha256") if isinstance(summary, dict) else None
    expected = hashlib.sha256(BRIDGE_SOURCE.read_bytes()).hexdigest()
    if actual != expected:
        raise CampaignError(
            "Running ArcMap Bridge source identity does not match the experiment repository"
        )


def _execution_tree_digest(root: Path) -> tuple[str, list[str]]:
    root = root.resolve()
    if not root.is_dir():
        raise CampaignError("ArcMap runtime directory is missing: %s" % root)
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in EXECUTION_ASSET_SUFFIXES
    )
    if not files:
        raise CampaignError("ArcMap runtime directory has no source files: %s" % root)
    digest = hashlib.sha256()
    relative_paths = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        relative_paths.append(relative)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), relative_paths


def _validate_execution_tree_identity(
    source: Path, installed: Path, label: str,
) -> dict[str, Any]:
    source_hash, source_files = _execution_tree_digest(source)
    installed_hash, installed_files = _execution_tree_digest(installed)
    if source_files != installed_files or source_hash != installed_hash:
        raise CampaignError(
            "Installed %s identity does not match the experiment repository" % label
        )
    return {
        "installed_path": str(installed.resolve()),
        "source_sha256": source_hash,
        "installed_sha256": installed_hash,
        "file_count": len(source_files),
    }


def validate_execution_deployment_identity(
    runtime_source: Path, catalog_source: Path, install_dir: Path,
) -> dict[str, Any]:
    install_dir = install_dir.resolve()
    return {
        "install_dir": str(install_dir),
        "runtime": _validate_execution_tree_identity(
            runtime_source, install_dir / "arcmap_runtime_py2", "ArcMap runtime",
        ),
        "catalog": _validate_execution_tree_identity(
            catalog_source, install_dir / "operation_catalog", "operation catalog",
        ),
    }


def installed_execution_deployment_identity() -> dict[str, Any]:
    if not INSTALL_CONFIG.is_file():
        raise CampaignError("GeoPilot install config is missing: %s" % INSTALL_CONFIG)
    try:
        config = json.loads(INSTALL_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CampaignError("GeoPilot install config is invalid: %s" % exc) from exc
    install_dir = config.get("install_dir")
    if not isinstance(install_dir, str) or not install_dir:
        raise CampaignError("GeoPilot install config has no install_dir")
    return validate_execution_deployment_identity(
        RUNTIME_SOURCE, CATALOG_SOURCE, Path(install_dir),
    )


def normalize_arcmap_window(hwnd: int, user32=None, settle=time.sleep) -> dict[str, int]:
    """Freeze ArcMap's client area before any map context is captured."""
    if not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
        raise CampaignError("ArcMap window identity is invalid.")
    api = user32 if user32 is not None else ctypes.windll.user32
    api.ShowWindow(hwnd, 9)  # SW_RESTORE

    def size(getter):
        rectangle = wintypes.RECT()
        if not getter(hwnd, ctypes.byref(rectangle)):
            raise CampaignError("ArcMap window geometry could not be observed.")
        return int(rectangle.right - rectangle.left), int(rectangle.bottom - rectangle.top)

    outer_width, outer_height = size(api.GetWindowRect)
    client_width, client_height = size(api.GetClientRect)
    for _ in range(2):
        width = ARCMAP_CLIENT_WIDTH + outer_width - client_width
        height = ARCMAP_CLIENT_HEIGHT + outer_height - client_height
        if not api.MoveWindow(hwnd, 0, 0, width, height, True):
            raise CampaignError("ArcMap window geometry could not be frozen.")
        settle(0.5)
        outer_width, outer_height = size(api.GetWindowRect)
        client_width, client_height = size(api.GetClientRect)
        if (client_width, client_height) == (ARCMAP_CLIENT_WIDTH, ARCMAP_CLIENT_HEIGHT):
            return {
                "client_width": client_width, "client_height": client_height,
                "outer_width": outer_width, "outer_height": outer_height,
            }
    raise CampaignError("ArcMap client geometry does not match the frozen protocol.")


def terminate_pid(pid: int) -> None:
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    descendants = process.children(recursive=True)
    for child in reversed(descendants):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        process.kill()
    except psutil.NoSuchProcess:
        pass


def cleanup_runtime(identity: dict[str, Any]) -> None:
    pids = {
        int(identity[key])
        for key in ("gateway_pid", "arcmap_pid", "bridge_pid")
        if identity.get(key)
    }
    for pid in pids:
        terminate_pid(pid)
    processes = []
    for pid in pids:
        try:
            processes.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(
        processes,
        timeout=10,
    )
    if alive:
        raise CampaignError("Owned experiment processes did not stop: %s" % [item.pid for item in alive])


def start_runtime(args: argparse.Namespace, log_dir: Path) -> tuple[dict[str, Any], list[Any]]:
    assert_clean_host(args.gateway)
    log_dir.mkdir(parents=True, exist_ok=False)
    handles = [
        (log_dir / "gateway.stdout.log").open("wb"),
        (log_dir / "gateway.stderr.log").open("wb"),
    ]
    gateway_process = subprocess.Popen(
        [str(args.python), "-m", "gateway_py3"],
        cwd=REPOSITORY,
        stdout=handles[0],
        stderr=handles[1],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    identity: dict[str, Any] = {"gateway_pid": gateway_process.pid}
    try:
        health = wait_gateway(args.gateway, gateway_process, args.gateway_startup_timeout)
        arcmap_process = subprocess.Popen([str(args.arcmap)], cwd=args.arcmap.parent)
        identity["arcmap_pid"] = arcmap_process.pid
        bridge = wait_bridge(args.gateway, arcmap_process.pid, arcmap_process, args.arcmap_startup_timeout)
        window_geometry = normalize_arcmap_window(int(bridge["hwnd"]))
        selected = post_json(
            args.gateway.rstrip("/") + "/arcmap/active",
            {"bridge_pid": bridge["bridge_pid"], "bridge_port": bridge["bridge_port"], "hwnd": bridge["hwnd"]},
        ).get("bridge")
        if not isinstance(selected, dict) or int(selected.get("arcmap_pid") or 0) != arcmap_process.pid:
            raise CampaignError("Campaign ArcMap target selection is not authoritative.")
        bridge = selected
        identity.update(
            {
                "bridge_pid": bridge["bridge_pid"],
                "bridge_port": bridge.get("bridge_port"),
                "hwnd": bridge["hwnd"],
                "window_geometry": window_geometry,
                "gateway_app_version": health.get("app_version"),
                "operation_count": health.get("operation_count"),
                "started_at": time.time(),
            }
        )
        return identity, handles
    except Exception:
        cleanup_runtime(identity)
        for handle in handles:
            handle.close()
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def copy_session_evidence(
    log_dir: Path, run_dir: Path, identity: dict[str, Any], phase: str | None = None,
) -> None:
    if not run_dir.is_dir():
        return
    suffix = "_%s" % phase if phase else ""
    atomic_write_json(run_dir / ("runtime_identity%s.json" % suffix), identity)
    target = run_dir / "logs"
    if phase:
        target = target / phase
    target.mkdir(parents=True, exist_ok=False)
    for source in sorted(log_dir.glob("*.log")):
        shutil.copy2(source, target / source.name)


def campaign_manifest(args: argparse.Namespace, order: list[dict[str, Any]]) -> dict[str, Any]:
    code_version, dirty, source_fingerprint = repository_state(REPOSITORY)
    execution_deployment = installed_execution_deployment_identity()
    return {
        "created_at": time.time(),
        "seeds": list(args.seeds),
        "repetitions": args.repetitions,
        "modes": list(args.modes),
        "case_ids": list(args.case_ids),
        "rounds": list(args.rounds),
        "replay_baseline_record": (
            str(args.replay_baseline_record.resolve()) if args.replay_baseline_record else None
        ),
        "paired_strategy": args.paired_strategy,
        "order_seed": args.order_seed,
        "dataset_template": args.dataset_template,
        "gateway": args.gateway,
        "provider": getattr(args, "provider", ""),
        "model": getattr(args, "model", ""),
        "timeout_seconds": args.timeout,
        "execution_cell_count": len(order),
        "runtime_session_count": sum(
            2 if item["mode"] == PAIRED_EXECUTION_UNIT else 1 for item in order
        ),
        "repository": str(REPOSITORY),
        "code_version": code_version,
        "dirty": dirty,
        "source_fingerprint": source_fingerprint,
        "execution_deployment": execution_deployment,
        "python": str(args.python),
        "python_version": platform.python_version(),
        "windows": platform.platform(),
        "arcmap": str(args.arcmap),
        "arcmap_window": {"client_width": ARCMAP_CLIENT_WIDTH, "client_height": ARCMAP_CLIENT_HEIGHT},
        "runner": str(args.runner),
    }


def prepare_campaign(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output = args.output.resolve()
    order = build_method_order(
        seeds=args.seeds,
        repetitions=args.repetitions,
        modes=args.modes,
        order_seed=args.order_seed,
    )
    if args.resume:
        if not output.is_dir():
            raise CampaignError("Resume output does not exist: %s" % output)
        existing = json.loads((output / "method_order.json").read_text(encoding="utf-8"))
        if existing != order:
            raise CampaignError("Resume parameters do not match the frozen method order")
        frozen_manifest = json.loads(
            (output / "campaign_manifest.json").read_text(encoding="utf-8")
        )
        current_manifest = campaign_manifest(args, order)
        for key, value in current_manifest.items():
            if key != "created_at" and frozen_manifest.get(key) != value:
                raise CampaignError("Resume parameter changed: %s" % key)
        state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
        return order, state
    if output.exists():
        raise CampaignError("Campaign output already exists: %s" % output)
    for seed in args.seeds:
        dataset = args.datasets_root / args.dataset_template.format(seed=seed)
        if not dataset.is_dir():
            raise CampaignError("Formal dataset is missing: %s" % dataset)
    manifest = campaign_manifest(args, order)
    output.mkdir(parents=True)
    (output / "logs").mkdir()
    atomic_write_json(output / "method_order.json", order)
    atomic_write_json(output / "campaign_manifest.json", manifest)
    state = {"status": "running", "completed": [], "attempts": [], "updated_at": time.time()}
    atomic_write_json(output / "campaign_state.json", state)
    return order, state


def _runner_command(
    args: argparse.Namespace, item: dict[str, Any], dataset: Path, run_dir: Path,
    paired_phase: str | None = None,
) -> list[str]:
    command = [
        str(args.python), str(args.runner),
        "--dataset", str(dataset),
        "--output", str(run_dir),
        "--gateway", args.gateway,
        "--repetitions", "1",
        "--timeout", str(args.timeout),
        "--modes", *runner_modes_for(item["mode"]),
    ]
    if paired_phase:
        command.extend(["--paired-phase", paired_phase])
        command.extend(["--paired-strategy", args.paired_strategy])
    provider = getattr(args, "provider", "")
    if provider:
        command.extend(["--provider", provider, "--model", args.model])
    command.extend([
        "--code-version", args.code_version,
        "--source-fingerprint", args.source_fingerprint,
        "--dirty", "true" if args.dirty else "false",
    ])
    if args.case_ids:
        command.extend(["--case-ids", *args.case_ids])
    if args.rounds:
        command.extend(["--rounds", *map(str, args.rounds)])
    if args.replay_baseline_record:
        command.extend(["--replay-baseline-record", str(args.replay_baseline_record)])
    return command


def _execute_runtime_phase(
    args: argparse.Namespace, run_dir: Path, log_dir: Path, command: list[str],
    phase: str | None = None,
) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    handles: list[Any] = []
    result: dict[str, Any]
    try:
        identity, handles = start_runtime(args, log_dir)
        runner_stdout = (log_dir / "runner.stdout.log").open("wb")
        runner_stderr = (log_dir / "runner.stderr.log").open("wb")
        handles.extend((runner_stdout, runner_stderr))
        completed = subprocess.run(
            command,
            cwd=REPOSITORY,
            stdout=runner_stdout,
            stderr=runner_stderr,
            timeout=args.session_timeout,
        )
        for handle in handles:
            handle.flush()
        identity["finished_at"] = time.time()
        identity["runner_exit_code"] = completed.returncode
        infrastructure_stop = run_dir / "infrastructure_stop.json"
        quota_stop = run_dir / "model_quota_stop.json"
        if completed.returncode == 0:
            status = "completed"
        elif quota_stop.is_file():
            status = "quota_exhausted"
        elif infrastructure_stop.is_file():
            status = "infrastructure_excluded"
        else:
            status = "failed"
        result = {"status": status, "runtime_identity": identity, "log_dir": str(log_dir)}
    except subprocess.TimeoutExpired as exc:
        identity["finished_at"] = time.time()
        result = {"status": "infrastructure_excluded", "error": "session timeout: %s" % exc,
                  "runtime_identity": identity, "log_dir": str(log_dir)}
    except (CampaignError, OSError) as exc:
        identity["finished_at"] = time.time()
        result = {"status": "infrastructure_excluded", "error": str(exc),
                  "runtime_identity": identity, "log_dir": str(log_dir)}
    finally:
        try:
            cleanup_runtime(identity)
        except CampaignError as exc:
            result = {"status": "infrastructure_excluded", "error": str(exc),
                      "runtime_identity": identity, "log_dir": str(log_dir)}
        try:
            for handle in handles:
                handle.close()
            copy_session_evidence(log_dir, run_dir, identity, phase)
        except OSError as exc:
            result = {"status": "infrastructure_excluded", "error": str(exc),
                      "runtime_identity": identity, "log_dir": str(log_dir)}
    return result


def finalize_paired_outputs(run_dir: Path) -> None:
    marker_path = run_dir / "paired_g3_complete.json"
    if not marker_path.is_file():
        raise CampaignError("Paired G3 completion marker is missing.")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("schema") != "geopilot-paired-g3-complete" or marker.get("version") != 1:
        raise CampaignError("Paired G3 completion marker is invalid.")
    try:
        for item in marker.get("pairs", []):
            source = run_dir / "pair-work" / item["pair_id"]
            target = run_dir / "g3_audited" / item["case_id"] / ("rep-%02d" % item["repetition"])
            relocate_pair_workspace(run_dir, source, target)
    except (KeyError, TypeError, PairWorkspaceError) as exc:
        raise CampaignError("Paired G3 result finalization failed: %s" % exc) from exc
    pair_root = run_dir / "pair-work"
    if pair_root.is_dir():
        try:
            pair_root.rmdir()
        except OSError as exc:
            raise CampaignError("Unexpected pair-work residue remains after G3 finalization.") from exc
    atomic_write_json(
        run_dir / "paired_complete.json",
        {"schema": "geopilot-paired-complete", "version": 1, "pairs": marker.get("pairs", [])},
    )


def execute_cell(args: argparse.Namespace, item: dict[str, Any], attempt: int) -> dict[str, Any]:
    session_name = "%s-attempt-%02d" % (item["cell_id"], attempt)
    log_root = args.output / "logs" / session_name
    run_dir = (
        args.output / "geopilot" / item["mode"] / ("seed-%d" % item["seed"])
        / ("rep-%02d" % item["repetition"]) / ("attempt-%02d" % attempt)
    )
    dataset = args.datasets_root / args.dataset_template.format(seed=item["seed"])
    phases = ("g2", "g3") if item["mode"] == PAIRED_EXECUTION_UNIT else (None,)
    phase_results = []
    for phase in phases:
        if installed_execution_deployment_identity() != args.execution_deployment:
            raise CampaignError("Installed ArcMap execution deployment changed during the frozen campaign")
        log_dir = log_root / phase if phase else log_root
        result = _execute_runtime_phase(
            args, run_dir, log_dir, _runner_command(args, item, dataset, run_dir, phase), phase,
        )
        phase_results.append({"phase": phase or item["mode"], **result})
        if result["status"] != "completed":
            return {
                **item, "attempt": attempt, "status": result["status"],
                "run_dir": str(run_dir), "log_dir": str(log_root), "phases": phase_results,
            }
    if item["mode"] == PAIRED_EXECUTION_UNIT:
        finalize_paired_outputs(run_dir)
    return {
        **item, "attempt": attempt, "status": "completed",
        "run_dir": str(run_dir), "log_dir": str(log_root), "phases": phase_results,
    }


def execute(args: argparse.Namespace) -> int:
    args.datasets_root = args.datasets_root.resolve()
    args.output = args.output.resolve()
    args.runner = args.runner.resolve()
    args.python = args.python.resolve()
    args.arcmap = args.arcmap.resolve()
    order, state = prepare_campaign(args)
    frozen_manifest = json.loads((args.output / "campaign_manifest.json").read_text(encoding="utf-8"))
    args.code_version = frozen_manifest["code_version"]
    args.source_fingerprint = frozen_manifest["source_fingerprint"]
    args.dirty = bool(frozen_manifest["dirty"])
    args.execution_deployment = frozen_manifest["execution_deployment"]
    completed_ids = set(state.get("completed", []))
    for item in order:
        if item["cell_id"] in completed_ids:
            continue
        for attempt in range(1, args.max_infrastructure_attempts + 1):
            result = execute_cell(args, item, attempt)
            state["attempts"].append(result)
            state["updated_at"] = time.time()
            atomic_write_json(args.output / "campaign_state.json", state)
            if result["status"] == "completed":
                state["completed"].append(item["cell_id"])
                completed_ids.add(item["cell_id"])
                atomic_write_json(args.output / "campaign_state.json", state)
                break
            if result["status"] == "quota_exhausted":
                state["status"] = "quota_exhausted"
                atomic_write_json(args.output / "campaign_state.json", state)
                raise CampaignError("Model quota exhausted: %s" % item["cell_id"])
            if result["status"] != "infrastructure_excluded":
                state["status"] = "failed"
                atomic_write_json(args.output / "campaign_state.json", state)
                raise CampaignError("Campaign cell failed: %s" % item["cell_id"])
        else:
            state["status"] = "failed"
            atomic_write_json(args.output / "campaign_state.json", state)
            raise CampaignError("Infrastructure attempts exhausted: %s" % item["cell_id"])
    state["status"] = "completed"
    state["updated_at"] = time.time()
    atomic_write_json(args.output / "campaign_state.json", state)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run clean-session GeoPilot G0-G3 ablation campaign.")
    parser.add_argument("--datasets-root", type=Path, required=True)
    parser.add_argument("--dataset-template", default="synthetic-city-formal-{seed}")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--case-ids", nargs="+", default=[])
    parser.add_argument("--rounds", nargs="+", type=int, default=[])
    parser.add_argument("--replay-baseline-record", type=Path)
    parser.add_argument("--paired-strategy", choices=("production", "artifact-replay"), default="production")
    parser.add_argument("--order-seed", type=int, required=True)
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--session-timeout", type=int, default=3600)
    parser.add_argument("--gateway-startup-timeout", type=int, default=30)
    parser.add_argument("--arcmap-startup-timeout", type=int, default=90)
    parser.add_argument("--max-infrastructure-attempts", type=int, default=3)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--arcmap", type=Path, default=DEFAULT_ARCMAP)
    parser.add_argument("--runner", type=Path, default=DEFAULT_RUNNER)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.repetitions <= 0 or args.timeout <= 0 or args.session_timeout <= 0:
        parser.error("repetitions and timeouts must be positive")
    if bool(args.provider) != bool(args.model):
        parser.error("--provider and --model must be supplied together")
    if args.max_infrastructure_attempts <= 0:
        parser.error("max-infrastructure-attempts must be positive")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(execute(parse_args()))
    except CampaignError as exc:
        print("Ablation campaign failed: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
