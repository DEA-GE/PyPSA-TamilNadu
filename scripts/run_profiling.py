"""Run profiling for the 9-BA model: phase timings, resources and solver logs.

Every run started through ``run_selection`` writes a ``profile/`` folder next to
its results. It records where wall-clock time, CPU time and memory go, so that
run-time and efficiency improvements can be targeted and measured:

``run.log``
    Timestamped log of the runner, PyPSA and Linopy messages, and one line for
    every completed phase longer than ``PHASE_LOG_THRESHOLD_SECONDS``.
``events.jsonl``
    One JSON record per completed phase: wall and CPU seconds, average cores,
    process memory at the end and the sampled peak during the phase, and the
    context (window, week, day, solve attempt) it belongs to.
``solves.jsonl`` / ``solves.csv``
    One record per optimization attempt, splitting ``network.optimize`` into
    PyPSA model build, custom constraints, LP-file write, HiGHS read, HiGHS
    run, solution transfer, and PyPSA solution/dual assignment. Model sizes
    before and after presolve, iteration counts, MIP statistics and the
    largest constraint groups are included.
``highs/<tag>.log``
    The complete HiGHS log of every attempt.
``resources.csv``
    A resource timeline sampled every ``sample_interval`` seconds: process
    memory, process CPU cores in use, threads, system CPU and available
    memory, and the phase that was active.
``environment.json``
    Package versions, hardware, Git revision and the run settings.
``summary.json`` / ``summary.txt``
    Phase totals and shares of the run, the solve-time breakdown and the
    slowest solves. Rebuild or compare with
    ``python scripts/summarize_run_profile.py <run folder> [...]``.

Worker processes (parallel daily and selected-week solves) write their own
``events-w<pid>.jsonl``, ``solves-w<pid>.jsonl``, ``run-w<pid>.log`` and
``resources-w<pid>.csv`` files; the summary merges them.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as _dt
import functools
import importlib.metadata
import json
import logging
import logging.handlers
import os
import platform
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import psutil

PROFILE_DIRNAME = "profile"
PHASE_LOG_THRESHOLD_SECONDS = 1.0
RUN_LOGGER_NAME = "tn_run"
_LIBRARY_LOGGERS = ("pypsa", "linopy")
_PACKAGES = (
    "pypsa", "linopy", "highspy", "numpy", "pandas", "xarray", "scipy",
    "netCDF4", "psutil", "pyarrow",
)
_REPO_ROOT = Path(__file__).resolve().parents[1]
_MB = 1_000_000

_run_logger = logging.getLogger(RUN_LOGGER_NAME)
_run_logger.propagate = False
_run_logger.setLevel(logging.DEBUG)


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _json_default(value: Any) -> Any:
    """Serialize NumPy/pandas scalars and paths in profile records."""
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (Path, _dt.date, _dt.datetime)):
        return str(value)
    return repr(value)


def _cpu_seconds(process: psutil.Process) -> float:
    times = process.cpu_times()
    return float(times.user + times.system)


def _windows_peak_rss(process: psutil.Process) -> int | None:
    """Return the OS-tracked lifetime peak working set where available."""
    info = process.memory_info()
    return getattr(info, "peak_wset", None)


class _PhaseRecord(dict):
    """Mutable phase record; callers may add fields while the phase runs."""


class RunProfiler:
    """Collect phase timings, resource samples and solve records for one run.

    Use :func:`activate` to make a profiler visible to the runner modules via
    :func:`get_profiler`; inactive code paths receive a no-op profiler.
    """

    enabled = True

    def __init__(
        self,
        *,
        run_label: str = "",
        settings: dict[str, Any] | None = None,
        output_dir: Path | None = None,
        worker: bool = False,
        sample_interval: float = 1.0,
        keep_solver_logs: bool = True,
        log_level: str | int = "INFO",
    ) -> None:
        self.run_label = run_label
        self.settings = dict(settings or {})
        self.worker = worker
        self.sample_interval = float(sample_interval)
        self.keep_solver_logs = keep_solver_logs
        self.log_level = (
            logging.getLevelName(log_level.upper()) if isinstance(log_level, str) else log_level
        )
        self.pid = os.getpid()
        self.suffix = f"-w{self.pid}" if worker else ""
        self.process = psutil.Process()
        self.started_perf = time.perf_counter()
        self.started_cpu = _cpu_seconds(self.process)
        self.started_iso = _now_iso()
        self.status = "running"
        self.profile_dir: Path | None = None
        self.events: list[dict[str, Any]] = []
        self.solves: list[dict[str, Any]] = []
        self.last_solve: dict[str, Any] | None = None
        self._pending_events: list[dict[str, Any]] = []
        self._pending_solves: list[dict[str, Any]] = []
        self._pending_samples: list[list[Any]] = []
        self._stack: list[_PhaseRecord] = []
        self._context: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._sampler: threading.Thread | None = None
        self._last_sample_perf = self.started_perf
        self._last_sample_cpu = self.started_cpu
        self._events_file = None
        self._solves_file = None
        self._samples_file = None
        self._samples_writer = None
        self._file_handler: logging.Handler | None = None
        self._memory_handler = logging.handlers.MemoryHandler(
            capacity=100_000, flushLevel=logging.CRITICAL + 1, target=None, flushOnClose=False
        )
        self._memory_handler.setLevel(self.log_level)
        self._saved_library_logging: dict[str, tuple[int, bool, list[logging.Handler]]] = {}
        self._console_handler: logging.Handler | None = None
        self._closed = False
        self._setup_logging()
        self._start_sampler()
        if output_dir is not None:
            self.attach(output_dir)

    # ------------------------------------------------------------------ setup
    def _setup_logging(self) -> None:
        _run_logger.addHandler(self._memory_handler)
        # Route PyPSA and Linopy INFO messages to the run log without changing
        # what the console shows: warnings stay visible, INFO stays quiet.
        self._console_handler = logging.StreamHandler(sys.stderr)
        self._console_handler.setLevel(logging.WARNING)
        self._console_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        for name in _LIBRARY_LOGGERS:
            logger = logging.getLogger(name)
            self._saved_library_logging[name] = (
                logger.level, logger.propagate, list(logger.handlers)
            )
            logger.setLevel(min(self.log_level, logging.INFO))
            logger.propagate = False
            logger.addHandler(self._memory_handler)
            logger.addHandler(self._console_handler)

    def attach(self, output_dir: Path) -> Path:
        """Start writing files in ``output_dir/profile``; flush buffered records."""
        with self._lock:
            if self.profile_dir is not None:
                return self.profile_dir
            profile_dir = Path(output_dir) / PROFILE_DIRNAME
            if not self.worker and profile_dir.is_dir() and any(profile_dir.iterdir()):
                # Keep an earlier session's profile (for example a failed or
                # resumed run) instead of mixing it with or deleting it.
                stamp = _dt.datetime.fromtimestamp(profile_dir.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
                history = Path(output_dir) / "profile_history"
                history.mkdir(exist_ok=True)
                target = history / stamp
                number = 2
                while target.exists():
                    target = history / f"{stamp}-{number}"
                    number += 1
                with contextlib.suppress(OSError):
                    profile_dir.rename(target)
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "highs").mkdir(exist_ok=True)
            self.profile_dir = profile_dir
            self._events_file = open(profile_dir / f"events{self.suffix}.jsonl", "a", encoding="utf-8")
            self._solves_file = open(profile_dir / f"solves{self.suffix}.jsonl", "a", encoding="utf-8")
            self._samples_file = open(
                profile_dir / f"resources{self.suffix}.csv", "a", encoding="utf-8", newline=""
            )
            self._samples_writer = csv.writer(self._samples_file)
            self._samples_writer.writerow([
                "time", "elapsed_s", "pid", "rss_mb", "process_cpu_cores", "threads",
                "system_cpu_pct", "system_available_mb", "phase",
            ])
            handler = logging.FileHandler(profile_dir / f"run{self.suffix}.log", encoding="utf-8")
            handler.setLevel(self.log_level)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)-7s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            self._memory_handler.setTarget(handler)
            self._memory_handler.flush()
            self._swap_memory_handler(handler)
            self._file_handler = handler
            for event in self._pending_events:
                self._write_line(self._events_file, event)
            for solve in self._pending_solves:
                self._write_line(self._solves_file, solve)
            for sample in self._pending_samples:
                self._samples_writer.writerow(sample)
            self._pending_events.clear()
            self._pending_solves.clear()
            self._pending_samples.clear()
            if not self.worker:
                self._write_environment()
            return profile_dir

    def _swap_memory_handler(self, handler: logging.Handler) -> None:
        for logger in [_run_logger, *(logging.getLogger(name) for name in _LIBRARY_LOGGERS)]:
            if self._memory_handler in logger.handlers:
                logger.removeHandler(self._memory_handler)
                logger.addHandler(handler)

    def _write_environment(self) -> None:
        assert self.profile_dir is not None
        versions = {}
        for package in _PACKAGES:
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        memory = psutil.virtual_memory()
        frequency = None
        with contextlib.suppress(Exception):
            current = psutil.cpu_freq()
            frequency = None if current is None else {"current_mhz": current.current, "max_mhz": current.max}
        environment = {
            "run_label": self.run_label,
            "started": self.started_iso,
            "pid": self.pid,
            "python": sys.version,
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": psutil.cpu_count(logical=True),
            "physical_cpus": psutil.cpu_count(logical=False),
            "cpu_frequency": frequency,
            "memory_total_gb": memory.total / 1e9,
            "memory_available_gb_at_start": memory.available / 1e9,
            "packages": versions,
            "git": _git_state(),
            "settings": self.settings,
        }
        (self.profile_dir / "environment.json").write_text(
            json.dumps(environment, indent=2, default=_json_default), encoding="utf-8"
        )

    # ---------------------------------------------------------------- sampler
    def _start_sampler(self) -> None:
        with contextlib.suppress(Exception):
            psutil.cpu_percent(None)  # prime the system-wide counter
        self._sampler = threading.Thread(target=self._sample_loop, name="run-profiler", daemon=True)
        self._sampler.start()

    def _sample_loop(self) -> None:
        while not self._stop.wait(self.sample_interval):
            self._sample()

    def _sample(self) -> None:
        try:
            rss = self.process.memory_info().rss
            cpu = _cpu_seconds(self.process)
            threads = self.process.num_threads() - 1  # exclude the sampler
            system_cpu = psutil.cpu_percent(None)
            available = psutil.virtual_memory().available
        except psutil.Error:
            return
        now = time.perf_counter()
        elapsed = now - self._last_sample_perf
        cores = (cpu - self._last_sample_cpu) / elapsed if elapsed > 0 else 0.0
        self._last_sample_perf, self._last_sample_cpu = now, cpu
        with self._lock:
            for record in self._stack:
                record["_peak_rss"] = max(record.get("_peak_rss", 0), rss)
                record["_peak_cores"] = max(record.get("_peak_cores", 0.0), cores)
                record["_peak_threads"] = max(record.get("_peak_threads", 0), threads)
                record["_min_available"] = min(record.get("_min_available", available), available)
            phase = self._stack[-1]["path"] if self._stack else ""
            row = [
                _now_iso(), round(now - self.started_perf, 3), self.pid, round(rss / _MB, 1),
                round(cores, 3), threads, system_cpu, round(available / _MB, 1), phase,
            ]
            if self._samples_writer is None:
                self._pending_samples.append(row)
            else:
                self._samples_writer.writerow(row)
                self._samples_file.flush()

    # ----------------------------------------------------------------- phases
    def _context_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for item in self._context:
            fields.update(item)
        return fields

    @contextlib.contextmanager
    def context(self, **labels: Any) -> Iterator[None]:
        """Attach labels (window, week, day, attempt) to nested records."""
        self._context.append(labels)
        try:
            yield
        finally:
            self._context.pop()

    @contextlib.contextmanager
    def phase(self, name: str, **fields: Any) -> Iterator[_PhaseRecord]:
        """Time a block; yields a dict whose extra keys are stored with it."""
        parent = self._stack[-1]["path"] if self._stack else ""
        rss = self.process.memory_info().rss
        record = _PhaseRecord(
            name=name,
            path=f"{parent}/{name}" if parent else name,
            depth=len(self._stack),
            pid=self.pid,
            started=_now_iso(),
            start_offset_s=time.perf_counter() - self.started_perf,
            **self._context_fields(),
            **fields,
        )
        record["_t0"] = time.perf_counter()
        record["_cpu0"] = _cpu_seconds(self.process)
        record["_rss0"] = rss
        record["_peak_rss"] = rss
        with self._lock:
            self._stack.append(record)
        status = "ok"
        try:
            yield record
        except BaseException as error:
            status = "error"
            record["error"] = f"{type(error).__name__}: {error}"[:500]
            raise
        finally:
            with self._lock:
                self._stack[:] = [item for item in self._stack if item is not record]
            self._finish_phase(record, status)

    def _finish_phase(self, record: _PhaseRecord, status: str) -> None:
        wall = time.perf_counter() - record.pop("_t0")
        cpu = _cpu_seconds(self.process) - record.pop("_cpu0")
        rss_end = self.process.memory_info().rss
        rss_start = record.pop("_rss0")
        peak = max(record.pop("_peak_rss", rss_end), rss_end)
        record.update(
            status=status,
            wall_s=round(wall, 4),
            cpu_s=round(cpu, 4),
            avg_cores=round(cpu / wall, 3) if wall > 0 else None,
            peak_cores_sampled=round(record.pop("_peak_cores", 0.0), 3),
            peak_threads_sampled=record.pop("_peak_threads", None),
            rss_start_mb=round(rss_start / _MB, 1),
            rss_end_mb=round(rss_end / _MB, 1),
            rss_delta_mb=round((rss_end - rss_start) / _MB, 1),
            rss_peak_mb_sampled=round(peak / _MB, 1),
            system_available_mb_min=(
                round(record.pop("_min_available") / _MB, 1) if "_min_available" in record else None
            ),
        )
        event = dict(record)
        with self._lock:
            self.events.append(event)
            if self._events_file is None:
                self._pending_events.append(event)
            else:
                self._write_line(self._events_file, event)
        if wall >= PHASE_LOG_THRESHOLD_SECONDS or status != "ok":
            labels = ", ".join(
                f"{key}={event[key]}" for key in ("window", "week", "day", "attempt") if key in event
            )
            _run_logger.log(
                logging.INFO if status == "ok" else logging.WARNING,
                "phase %s%s %s in %.2f s (cpu %.2f s, %.2f cores avg, peak RSS %.0f MB, RSS %+.0f MB)",
                event["path"], f" [{labels}]" if labels else "",
                "done" if status == "ok" else "FAILED", wall, cpu,
                event["avg_cores"] or 0.0, event["rss_peak_mb_sampled"], event["rss_delta_mb"],
            )

    def event(self, name: str, **fields: Any) -> None:
        """Record a point-in-time event, such as a checkpoint skip or retry."""
        with self.phase(name, kind="event", **fields):
            pass

    # ----------------------------------------------------------------- solves
    def solver_log_path(self, tag: str) -> Path | None:
        if not self.keep_solver_logs or self.profile_dir is None:
            return None
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", tag).strip("_") or "solve"
        path = self.profile_dir / "highs" / f"{safe}.log"
        number = 2
        while path.exists():
            path = self.profile_dir / "highs" / f"{safe}.{number}.log"
            number += 1
        return path

    def record_solve(self, record: dict[str, Any]) -> None:
        record = {**self._context_fields(), "pid": self.pid, "recorded": _now_iso(), **record}
        with self._lock:
            self.solves.append(record)
            self.last_solve = record
            if self._solves_file is None:
                self._pending_solves.append(record)
            else:
                self._write_line(self._solves_file, record)
        _run_logger.info("solve %s: %s", record.get("tag"), format_solve_breakdown(record))

    @staticmethod
    def _write_line(handle, record: dict[str, Any]) -> None:
        handle.write(json.dumps(record, default=_json_default) + "\n")
        handle.flush()

    # ---------------------------------------------------------------- logging
    def log(self, message: str, level: int = logging.INFO) -> None:
        _run_logger.log(level, message)

    # ------------------------------------------------------------------ close
    def close(self, status: str = "completed", error: BaseException | None = None) -> dict | None:
        """Stop sampling, write the summary and restore library logging."""
        if self._closed:
            return None
        self._closed = True
        self.status = status
        self._stop.set()
        if self._sampler is not None:
            self._sampler.join(timeout=5)
        self._sample()
        wall = time.perf_counter() - self.started_perf
        cpu = _cpu_seconds(self.process) - self.started_cpu
        peak = _windows_peak_rss(self.process)
        if error is not None:
            _run_logger.error("run %s: %s: %s", status, type(error).__name__, error)
        _run_logger.info(
            "run %s after %.1f s wall, %.1f s CPU%s", status, wall, cpu,
            f", lifetime peak working set {peak / _MB:.0f} MB" if peak else "",
        )
        summary = None
        if self.profile_dir is not None and not self.worker:
            run_record = {
                "run_label": self.run_label,
                "pid": self.pid,
                "status": status,
                "error": None if error is None else f"{type(error).__name__}: {error}"[:2000],
                "started": self.started_iso,
                "finished": _now_iso(),
                "wall_s": wall,
                "cpu_s": cpu,
                "avg_cores": cpu / wall if wall > 0 else None,
                "lifetime_peak_working_set_mb": None if peak is None else peak / _MB,
            }
            (self.profile_dir / "run_status.json").write_text(
                json.dumps(run_record, indent=2, default=_json_default), encoding="utf-8"
            )
            try:
                summary = write_summary(self.profile_dir)
            except Exception as summary_error:  # never hide the run's own result
                _run_logger.exception("could not write the profile summary: %s", summary_error)
        for handle in (self._events_file, self._solves_file, self._samples_file):
            if handle is not None:
                handle.close()
        self._restore_logging()
        return summary

    def _restore_logging(self) -> None:
        handlers = [self._memory_handler, self._file_handler, self._console_handler]
        for handler in handlers:
            if handler is None:
                continue
            for logger in [_run_logger, *(logging.getLogger(name) for name in _LIBRARY_LOGGERS)]:
                if handler in logger.handlers:
                    logger.removeHandler(handler)
            handler.close()
        for name, (level, propagate, saved_handlers) in self._saved_library_logging.items():
            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.propagate = propagate
            for handler in saved_handlers:
                if handler not in logger.handlers:
                    logger.addHandler(handler)


class _NullProfiler:
    """No-op stand-in used when profiling is disabled or inactive."""

    enabled = False
    profile_dir = None
    last_solve = None

    @contextlib.contextmanager
    def phase(self, name: str, **fields: Any) -> Iterator[dict]:
        yield {}

    @contextlib.contextmanager
    def context(self, **labels: Any) -> Iterator[None]:
        yield

    def event(self, name: str, **fields: Any) -> None:
        pass

    def solver_log_path(self, tag: str) -> None:
        return None

    def record_solve(self, record: dict[str, Any]) -> None:
        self.last_solve = record

    def log(self, message: str, level: int = logging.INFO) -> None:
        _run_logger.log(level, message)

    def attach(self, output_dir: Path) -> None:
        return None

    def close(self, status: str = "completed", error: BaseException | None = None) -> None:
        return None


NULL_PROFILER = _NullProfiler()
_ACTIVE: RunProfiler | _NullProfiler = NULL_PROFILER


def get_profiler() -> RunProfiler | _NullProfiler:
    """Return the active profiler, or a no-op profiler."""
    return _ACTIVE


@contextlib.contextmanager
def activate(profiler: RunProfiler | _NullProfiler) -> Iterator[RunProfiler | _NullProfiler]:
    """Make ``profiler`` the active one for the duration of the block."""
    global _ACTIVE
    previous = _ACTIVE
    _ACTIVE = profiler
    try:
        yield profiler
    finally:
        _ACTIVE = previous


def worker_profiler_config(profiler: RunProfiler | _NullProfiler) -> dict[str, Any] | None:
    """Return picklable settings for starting a profiler in a worker process."""
    if not profiler.enabled or profiler.profile_dir is None:
        return None
    return {
        "output_dir": str(profiler.profile_dir.parent),
        "run_label": profiler.run_label,
        "sample_interval": profiler.sample_interval,
        "keep_solver_logs": profiler.keep_solver_logs,
        "log_level": profiler.log_level,
    }


def start_worker_profiler(config: dict[str, Any] | None) -> None:
    """Activate a per-process profiler in a pool worker (initializer hook)."""
    global _ACTIVE
    if not config:
        return
    _ACTIVE = RunProfiler(
        run_label=config["run_label"],
        output_dir=Path(config["output_dir"]),
        worker=True,
        sample_interval=config["sample_interval"],
        keep_solver_logs=config["keep_solver_logs"],
        log_level=config["log_level"],
    )


def report(message: str, level: int = logging.INFO) -> None:
    """Print a progress message and copy it, timestamped, to the run log."""
    print(message, flush=True)
    _run_logger.log(level, message)


def _git_state() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=10,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain")
    return {
        "commit": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": None if status is None else bool(status),
        "changed_files": None if not status else status.splitlines()[:50],
    }


# ---------------------------------------------------------------------------
# Profiled network.optimize
# ---------------------------------------------------------------------------

def _stepwise_supported(network: Any) -> bool:
    """The stepwise path mirrors PyPSA 1.4's ``Network.optimize.__call__``."""
    try:
        import pypsa
    except ImportError:
        return False
    accessor = network.optimize
    return pypsa.__version__.startswith("1.4.") and all(
        hasattr(accessor, name)
        for name in ("create_model", "assign_solution", "assign_duals", "post_processing")
    )


class _LinopyProbes:
    """Time Linopy's LP-file write, HiGHS model read and HiGHS run.

    The probes wrap three library callables for the duration of one solve and
    restore them afterwards. They record time only; arguments and results are
    passed through unchanged.
    """

    def __init__(self) -> None:
        self.timings: dict[str, float] = {}
        self.lp_file_mb: float | None = None
        self._restore: list[tuple[Any, str, Any]] = []

    def _wrap(self, owner: Any, attribute: str, key: str, after: Callable | None = None) -> None:
        original = getattr(owner, attribute, None)
        if original is None:
            return

        @functools.wraps(original)
        def timed(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                result = original(*args, **kwargs)
            finally:
                self.timings[key] = self.timings.get(key, 0.0) + time.perf_counter() - started
            if after is not None:
                with contextlib.suppress(Exception):
                    after(result)
            return result

        self._restore.append((owner, attribute, original))
        setattr(owner, attribute, timed)

    def _record_lp_file(self, path: Any) -> None:
        if path is not None and Path(path).exists():
            self.lp_file_mb = Path(path).stat().st_size / _MB

    def __enter__(self) -> "_LinopyProbes":
        try:
            import linopy
            import linopy.solvers as linopy_solvers
            import highspy
        except ImportError:
            return self
        self._wrap(linopy.Model, "to_file", "lp_write_s", after=self._record_lp_file)
        self._wrap(linopy_solvers, "_run_highs_with_keyboard_interrupt", "highs_run_s")
        self._wrap(highspy.Highs, "readModel", "lp_read_s")
        self._wrap(highspy.Highs, "passModel", "pass_model_s")
        return self

    def __exit__(self, *_exc: object) -> None:
        for owner, attribute, original in reversed(self._restore):
            setattr(owner, attribute, original)
        self._restore.clear()


def _constraint_group_sizes(model: Any, top: int = 15) -> tuple[int, list[tuple[str, int]]]:
    """Return the total active constraint rows and the largest groups."""
    sizes = []
    for name in model.constraints:
        try:
            labels = model.constraints[name].labels.values
            sizes.append((str(name), int((labels != -1).sum())))
        except Exception:
            continue
    sizes.sort(key=lambda item: item[1], reverse=True)
    return sum(size for _, size in sizes), sizes[:top]


def _variable_count(model: Any) -> int | None:
    total = 0
    try:
        for name in model.variables:
            labels = model.variables[name].labels.values
            total += int((labels != -1).sum())
    except Exception:
        return None
    return total


def _highs_details(model: Any) -> dict[str, Any]:
    """Read size and iteration statistics from the native HiGHS object."""
    solver_model = getattr(model, "solver_model", None)
    if solver_model is None:
        return {}
    details: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        details["highs_rows"] = int(solver_model.getNumRow())
        details["highs_cols"] = int(solver_model.getNumCol())
        details["highs_nonzeros"] = int(solver_model.getNumNz())
    with contextlib.suppress(Exception):
        details["highs_reported_run_time_s"] = float(solver_model.getRunTime())
    with contextlib.suppress(Exception):
        details["highs_model_status"] = solver_model.modelStatusToString(solver_model.getModelStatus())
    with contextlib.suppress(Exception):
        info = solver_model.getInfo()
        for field in (
            "simplex_iteration_count", "ipm_iteration_count", "crossover_iteration_count",
            "pdlp_iteration_count", "mip_node_count", "mip_gap", "mip_dual_bound",
            "objective_function_value", "primal_solution_status", "dual_solution_status",
            "num_primal_infeasibilities", "max_primal_infeasibility",
            "num_dual_infeasibilities", "max_dual_infeasibility", "max_integrality_violation",
            "primal_dual_integral",
        ):
            value = getattr(info, field, None)
            if value is not None:
                details[f"highs_{field}"] = value if isinstance(value, (int, float)) else str(value)
    return details


_LOG_PATTERNS = {
    "log_original_rows": re.compile(r"^(?:LP|MIP|QP)\s+\S+\s+has\s+(\d+)\s+rows;\s+(\d+)\s+cols;\s+(\d+)\s+nonzeros(?:;\s+(\d+)\s+integer)?"),
    "log_presolved": re.compile(r"^Presolve reductions:\s+rows\s+(\d+)\(-(\d+)\);\s+columns\s+(\d+)\(-(\d+)\);\s+(?:elements|nonzeros)\s+(\d+)\(-(\d+)\)"),
    "log_presolve_to_empty": re.compile(r"Presolve.*reduced.*to empty|Presolve: Optimal"),
    "log_ipx_runtime": re.compile(r"^\s*Runtime:\s+([\d.]+)s"),
    "log_ipm_status": re.compile(r"^\s*Status interior point solve:\s+(\S+)"),
    "log_crossover_status": re.compile(r"^\s*Status crossover:\s+(\S+)"),
    "log_run_time": re.compile(r"^HiGHS run time\s*:\s*([\d.]+)"),
    "log_model_status": re.compile(r"^Model status\s*:\s*(.+?)\s*$"),
    "log_mip_timing": re.compile(r"^\s*Timing\s+([\d.]+)"),
    "log_mip_nodes": re.compile(r"^\s*Nodes\s+(\d+)"),
    "log_mip_lp_iterations": re.compile(r"^\s*LP iterations\s+(\d+)"),
    "log_mip_gap": re.compile(r"^\s*Gap\s+(\S+)"),
    "log_mip_primal_bound": re.compile(r"^\s*Primal bound\s+(\S+)"),
    "log_mip_dual_bound": re.compile(r"^\s*Dual bound\s+(\S+)"),
}
# Indented continuation lines of the MIP "Solving report" block.
_MIP_TIMING_PART = re.compile(r"^\s+([\d.]+)\s+\((Presolve|Solve|Postsolve)\)\s*$")
_MIP_ITERATION_PART = re.compile(r"^\s+(\d+)\s+\((strong br\.|separation|heuristics)\)\s*$")
_MIP_ITERATION_KEYS = {
    "strong br.": "log_mip_lp_iterations_strong_branching",
    "separation": "log_mip_lp_iterations_separation",
    "heuristics": "log_mip_lp_iterations_heuristics",
}
# B&B progress rows: "<src> nodes inqueue | leaves expl% | bestbound bestsol gap | ... | lpiters time"
_MIP_ROW = re.compile(
    r"^\s*([A-Za-z]?)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+(\S+)\s+(\S+)\s+(\S+).*?\s([\d.]+)s\s*$"
)


def parse_highs_log(path: Path | None) -> dict[str, Any]:
    """Extract presolve, interior-point, crossover and MIP figures from a HiGHS log."""
    if path is None or not Path(path).exists():
        return {}
    result: dict[str, Any] = {}
    first_incumbent: tuple[float, str] | None = None
    incumbent_updates = 0
    last_best: str | None = None
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n")
            for key, pattern in _LOG_PATTERNS.items():
                match = pattern.search(line)
                if not match:
                    continue
                if key == "log_original_rows" and "log_original_rows" not in result:
                    result["log_original_rows"] = int(match.group(1))
                    result["log_original_cols"] = int(match.group(2))
                    result["log_original_nonzeros"] = int(match.group(3))
                    if match.group(4):
                        result["log_original_integer_cols"] = int(match.group(4))
                elif key == "log_presolved":
                    result["log_presolved_rows"] = int(match.group(1))
                    result["log_presolve_removed_rows"] = int(match.group(2))
                    result["log_presolved_cols"] = int(match.group(3))
                    result["log_presolve_removed_cols"] = int(match.group(4))
                    result["log_presolved_nonzeros"] = int(match.group(5))
                    result["log_presolve_removed_nonzeros"] = int(match.group(6))
                elif key == "log_presolve_to_empty":
                    result["log_presolve_solved"] = True
                elif key in {"log_ipx_runtime", "log_run_time", "log_mip_timing"}:
                    result[key] = float(match.group(1))
                elif key in {"log_mip_nodes", "log_mip_lp_iterations"}:
                    result[key] = int(match.group(1))
                elif key != "log_original_rows":
                    result[key] = match.group(1)
            part = _MIP_TIMING_PART.match(line)
            if part:
                result[f"log_mip_{part.group(2).lower()}_s"] = float(part.group(1))
            part = _MIP_ITERATION_PART.match(line)
            if part:
                result[_MIP_ITERATION_KEYS[part.group(2)]] = int(part.group(1))
            row = _MIP_ROW.match(line)
            if row:
                best_solution = row.group(7)
                if best_solution.lower() not in {"inf", "-inf"}:
                    if first_incumbent is None:
                        first_incumbent = (float(row.group(9)), best_solution)
                    if best_solution != last_best:
                        incumbent_updates += 1
                        last_best = best_solution
                result["log_mip_progress_rows"] = result.get("log_mip_progress_rows", 0) + 1
    if first_incumbent is not None:
        result["log_mip_first_incumbent_s"] = first_incumbent[0]
        result["log_mip_first_incumbent"] = first_incumbent[1]
        result["log_mip_incumbent_updates"] = incumbent_updates
    return result


def format_solve_breakdown(record: dict[str, Any]) -> str:
    """One-line description of where an optimization's time went."""
    parts = []
    for key, label in (
        ("build_model_s", "build"),
        ("extra_functionality_s", "custom constraints"),
        ("lp_write_s", "LP write"),
        ("lp_read_s", "HiGHS read"),
        ("highs_run_s", "HiGHS"),
        ("linopy_other_s", "linopy other"),
        ("assign_s", "assign"),
    ):
        value = record.get(key)
        if value is not None:
            parts.append(f"{label} {value:.1f} s")
    text = ", ".join(parts)
    total = record.get("optimize_wall_s")
    if total is not None:
        text = f"{total:.1f} s total ({text})"
    size = record.get("log_original_rows") or record.get("highs_rows")
    if size:
        presolved = record.get("log_presolved_rows")
        text += f"; {size:,} rows"
        if presolved is not None:
            text += f" -> {presolved:,} after presolve"
    for key, label in (
        ("highs_ipm_iteration_count", "IPM it"),
        ("highs_crossover_iteration_count", "crossover it"),
        ("highs_simplex_iteration_count", "simplex it"),
        ("highs_mip_node_count", "nodes"),
    ):
        value = record.get(key)
        # HiGHS reports -1 for iteration counts of algorithms that did not run.
        if value is not None and value > 0:
            text += f", {label} {value:,}"
    gap = record.get("highs_mip_gap")
    if record.get("log_original_integer_cols") and gap is not None and 0 <= gap < 1e10:
        text += f", gap {gap:.2%}"
    if record.get("log_mip_first_incumbent_s") is not None:
        text += f", first incumbent at {record['log_mip_first_incumbent_s']:.1f} s"
    return text


def optimize_profiled(
    network: Any,
    *,
    solver_name: str,
    solver_options: dict[str, Any],
    extra_functionality: Callable | None = None,
    tag: str = "solve",
    assign_all_duals: bool = False,
    **create_kwargs: Any,
) -> tuple[str, str]:
    """Run ``network.optimize`` as separately timed steps.

    With PyPSA 1.4 this performs the same calls as ``Network.optimize``:
    consistency check, ``create_model``, ``extra_functionality``,
    ``Model.solve`` and, for an ``ok`` status, solution and dual assignment
    and post-processing. Each step is a profile phase, and a solve record with
    model size, HiGHS statistics and the parsed HiGHS log is added. Other
    PyPSA versions, or a disabled profiler, fall back to ``network.optimize``.
    """
    profiler = get_profiler()
    profiler.last_solve = None
    if not profiler.enabled or not _stepwise_supported(network):
        with profiler.phase("optimize", tag=tag, stepwise=False) as phase:
            status, condition = network.optimize(
                solver_name=solver_name,
                solver_options=solver_options,
                extra_functionality=extra_functionality,
                assign_all_duals=assign_all_duals,
                **create_kwargs,
            )
            phase.update(status=str(status), condition=str(condition))
        return status, condition

    import pypsa

    log_path = profiler.solver_log_path(tag)
    record: dict[str, Any] = {
        "tag": tag,
        "snapshots": int(len(network.snapshots)),
        "solver_name": solver_name,
        "solver_options": json.dumps(solver_options, default=_json_default, sort_keys=True),
        "lp_solver": solver_options.get("solver"),
        "threads": solver_options.get("threads"),
        "time_limit": solver_options.get("time_limit"),
        "linearized_unit_commitment": bool(create_kwargs.get("linearized_unit_commitment", False)),
        "committable_generators": int(network.generators.committable.sum()),
        "highs_log": None if log_path is None else log_path.name,
    }
    with profiler.phase("optimize", tag=tag, stepwise=True) as optimize_phase:
        started = time.perf_counter()
        n = network
        n._multi_invest = int(create_kwargs.get("multi_investment_periods", False))
        n._linearized_uc = create_kwargs.get("linearized_unit_commitment", False)
        options = pypsa.options.params.optimize
        model_kwargs = create_kwargs.pop("model_kwargs", None)
        if model_kwargs is None:
            model_kwargs = options.model_kwargs.copy()
        with profiler.phase("consistency_check") as phase:
            n.consistency_check(strict=["unknown_buses", "maintenance", "phase_shift_bounds"])
        record["consistency_check_s"] = phase["wall_s"]
        with profiler.phase("build_model") as phase:
            model = n.optimize.create_model(consistency_check=False, **create_kwargs, **model_kwargs)
        record["build_model_s"] = phase["wall_s"]
        record["build_model_rss_delta_mb"] = phase["rss_delta_mb"]
        with profiler.phase("model_size") as phase:
            record["pypsa_constraint_rows"], _ = _constraint_group_sizes(model)
            record["model_variables"] = _variable_count(model)
        if extra_functionality is not None:
            with profiler.phase("extra_functionality") as phase:
                extra_functionality(n, n.optimize._window.model_index)
            record["extra_functionality_s"] = phase["wall_s"]
        with profiler.phase("model_size") as phase:
            rows, top_groups = _constraint_group_sizes(model)
            record["model_constraint_rows"] = rows
            record["custom_constraint_rows"] = rows - record["pypsa_constraint_rows"]
            record["model_variables"] = _variable_count(model)
            record["constraint_groups"] = len(model.constraints)
            record["largest_constraint_groups"] = json.dumps(top_groups)
        solve_kwargs = dict(solver_options)
        if options.log_to_console is not None and "log_to_console" not in solve_kwargs:
            solve_kwargs["log_to_console"] = options.log_to_console
        if log_path is not None:
            solve_kwargs["log_fn"] = log_path
        with profiler.phase("solve") as phase:
            with _LinopyProbes() as probes:
                status, condition = model.solve(solver_name=solver_name, **solve_kwargs)
            phase.update(status=str(status), condition=str(condition))
        record["solve_call_s"] = phase["wall_s"]
        record["solve_peak_rss_mb"] = phase["rss_peak_mb_sampled"]
        record["solve_avg_cores"] = phase["avg_cores"]
        record["solve_peak_cores_sampled"] = phase["peak_cores_sampled"]
        record.update({key: round(value, 4) for key, value in probes.timings.items()})
        record["lp_file_mb"] = None if probes.lp_file_mb is None else round(probes.lp_file_mb, 1)
        record["io_api"] = getattr(getattr(model, "solver", None), "io_api", None)
        measured = sum(probes.timings.get(key, 0.0) for key in ("lp_write_s", "lp_read_s", "pass_model_s", "highs_run_s"))
        record["linopy_other_s"] = round(max(0.0, phase["wall_s"] - measured), 4)
        record.update(_highs_details(model))
        assign = 0.0
        if status == "ok":
            for step, call in (
                ("assign_solution", lambda: n.optimize.assign_solution()),
                ("assign_duals", lambda: n.optimize.assign_duals(assign_all_duals)),
                ("post_processing", lambda: n.optimize.post_processing()),
            ):
                with profiler.phase(step) as phase:
                    call()
                record[f"{step}_s"] = phase["wall_s"]
                assign += phase["wall_s"]
        record["assign_s"] = round(assign, 4)
        record["status"] = str(status)
        record["condition"] = str(condition)
        record["optimize_wall_s"] = round(time.perf_counter() - started, 4)
        optimize_phase.update(status=str(status), condition=str(condition))
    record.update(parse_highs_log(log_path))
    with contextlib.suppress(Exception):
        record["objective"] = float(model.objective.value)
    profiler.record_solve(record)
    return status, condition


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d+")


def _read_jsonl(paths: list[Path]) -> list[dict[str, Any]]:
    records = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    with contextlib.suppress(json.JSONDecodeError):
                        records.append(json.loads(line))
    return records


def summarize_profile(profile_dir: Path, top: int = 10) -> dict[str, Any]:
    """Aggregate a run's phase and solve records."""
    import pandas as pd

    profile_dir = Path(profile_dir)
    events = _read_jsonl(sorted(profile_dir.glob("events*.jsonl")))
    solves = _read_jsonl(sorted(profile_dir.glob("solves*.jsonl")))
    status_file = profile_dir / "run_status.json"
    run_status = json.loads(status_file.read_text(encoding="utf-8")) if status_file.exists() else {}
    summary: dict[str, Any] = {"profile_dir": str(profile_dir), "run": run_status}
    run_wall = run_status.get("wall_s")
    if events:
        frame = pd.DataFrame(events)
        frame = frame.loc[frame.get("kind", pd.Series(index=frame.index, dtype=object)).ne("event")]
        workers = frame["pid"].nunique() if "pid" in frame else 1
        by_path = frame.groupby("path").agg(
            count=("wall_s", "size"),
            total_wall_s=("wall_s", "sum"),
            mean_wall_s=("wall_s", "mean"),
            max_wall_s=("wall_s", "max"),
            total_cpu_s=("cpu_s", "sum"),
            max_rss_peak_mb=("rss_peak_mb_sampled", "max"),
            max_rss_delta_mb=("rss_delta_mb", "max"),
            errors=("status", lambda values: int((values != "ok").sum())),
        )
        by_path["avg_cores"] = by_path.total_cpu_s / by_path.total_wall_s.where(by_path.total_wall_s > 0)
        if run_wall:
            # Summed over processes, so parallel worker phases can exceed 100 %.
            by_path["share_of_run_pct"] = 100.0 * by_path.total_wall_s / run_wall
        by_path = by_path.sort_values("total_wall_s", ascending=False)
        summary["parallel_processes"] = int(workers)
        summary["phases"] = by_path.reset_index().round(4).to_dict(orient="records")
        main_pid = run_status.get("pid", frame["pid"].iloc[0])
        top_level = frame.loc[frame["depth"].eq(0) & frame["pid"].eq(main_pid)]
        summary["top_level_wall_s"] = float(top_level.wall_s.sum())
        if run_wall:
            summary["unprofiled_wall_s"] = max(0.0, run_wall - summary["top_level_wall_s"])
    if solves:
        frame = pd.DataFrame(solves)
        components = [
            key for key in (
                "consistency_check_s", "build_model_s", "extra_functionality_s", "lp_write_s",
                "lp_read_s", "pass_model_s", "highs_run_s", "linopy_other_s", "assign_s",
            ) if key in frame
        ]
        totals = {key: float(frame[key].fillna(0).sum()) for key in components}
        optimize_total = float(frame["optimize_wall_s"].sum()) if "optimize_wall_s" in frame else None
        summary["solves"] = {
            "attempts": int(len(frame)),
            "accepted_or_ok": int(frame["status"].eq("ok").sum()) if "status" in frame else None,
            "conditions": frame["condition"].value_counts().to_dict() if "condition" in frame else {},
            "optimize_wall_s": optimize_total,
            "breakdown_s": totals,
            "breakdown_pct": (
                {key: 100.0 * value / optimize_total for key, value in totals.items()}
                if optimize_total else {}
            ),
        }
        for key in (
            "highs_simplex_iteration_count", "highs_ipm_iteration_count",
            "highs_crossover_iteration_count", "highs_mip_node_count",
        ):
            if key in frame:
                # HiGHS reports -1 for algorithms that did not run.
                summary["solves"][f"total_{key}"] = int(
                    pd.to_numeric(frame[key], errors="coerce").fillna(0).clip(lower=0).sum()
                )
        columns = [
            column for column in (
                "tag", "window", "week", "day", "attempt", "condition", "optimize_wall_s",
                "build_model_s", "extra_functionality_s", "lp_write_s", "lp_read_s",
                "highs_run_s", "assign_s", "log_original_rows", "log_presolved_rows",
                "highs_ipm_iteration_count", "highs_crossover_iteration_count",
                "highs_simplex_iteration_count", "highs_mip_node_count", "highs_mip_gap",
                "log_mip_first_incumbent_s", "solve_peak_rss_mb", "solve_avg_cores",
            ) if column in frame
        ]
        sort_key = "highs_run_s" if "highs_run_s" in frame else "optimize_wall_s"
        slowest = frame.sort_values(sort_key, ascending=False).head(top)[columns]
        summary["slowest_solves"] = json.loads(slowest.to_json(orient="records"))
        frame.to_csv(profile_dir / "solves.csv", index=False)
    return summary


def format_summary(summary: dict[str, Any], top_phases: int = 25) -> str:
    """Render :func:`summarize_profile` output as plain text."""
    lines = []
    run = summary.get("run", {})
    if run:
        lines.append(
            f"Run {run.get('status')}: {run.get('wall_s', 0):.1f} s wall, "
            f"{run.get('cpu_s', 0):.1f} s CPU ({(run.get('avg_cores') or 0):.2f} cores avg)"
            + (f", peak working set {run['lifetime_peak_working_set_mb']:.0f} MB"
               if run.get("lifetime_peak_working_set_mb") else "")
        )
        if run.get("error"):
            lines.append(f"Error: {run['error']}")
    if "unprofiled_wall_s" in summary:
        lines.append(f"Time outside profiled phases: {summary['unprofiled_wall_s']:.1f} s")
    solves = summary.get("solves")
    if solves:
        lines.append("")
        lines.append(
            f"Optimizations: {solves['attempts']} attempt(s), {solves['optimize_wall_s'] or 0:.1f} s "
            f"in total; conditions {solves['conditions']}"
        )
        for key, value in solves["breakdown_s"].items():
            pct = solves["breakdown_pct"].get(key, 0.0)
            lines.append(f"  {key[:-2]:<24}{value:>12.1f} s {pct:>6.1f} %")
        for key in ("simplex", "ipm", "crossover", "mip_node"):
            total = solves.get(f"total_highs_{key}_iteration_count" if key != "mip_node" else "total_highs_mip_node_count")
            if total:
                lines.append(f"  total {key.replace('_', ' ')} {'count' if key == 'mip_node' else 'iterations'}: {total:,}")
    phases = summary.get("phases")
    if phases:
        lines.append("")
        lines.append(f"{'Phase':<58}{'n':>6}{'total s':>11}{'mean s':>9}{'max s':>9}{'cores':>7}{'% run':>7}{'peak MB':>9}")
        for row in phases[:top_phases]:
            share = row.get("share_of_run_pct")
            lines.append(
                f"{row['path'][:57]:<58}{row['count']:>6}{row['total_wall_s']:>11.1f}"
                f"{row['mean_wall_s']:>9.2f}{row['max_wall_s']:>9.1f}"
                f"{(row.get('avg_cores') or 0):>7.2f}"
                f"{(share if share is not None else float('nan')):>7.1f}"
                f"{(row.get('max_rss_peak_mb') or 0):>9.0f}"
            )
    slowest = summary.get("slowest_solves")
    if slowest:
        lines.append("")
        lines.append("Slowest solves:")
        for row in slowest:
            lines.append("  " + format_solve_breakdown(row) + f"  [{row.get('tag')}, {row.get('condition')}]")
    return "\n".join(lines)


def write_summary(profile_dir: Path) -> dict[str, Any]:
    """Write ``summary.json`` and ``summary.txt`` for a profile folder."""
    summary = summarize_profile(profile_dir)
    (Path(profile_dir) / "summary.json").write_text(
        json.dumps(summary, indent=2, default=_json_default), encoding="utf-8"
    )
    (Path(profile_dir) / "summary.txt").write_text(format_summary(summary) + "\n", encoding="utf-8")
    return summary
