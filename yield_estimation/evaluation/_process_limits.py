"""Optional POSIX process-group limits for the existing experiment entry point."""
from contextlib import contextmanager
import os
import signal
import subprocess
import threading
import time


@contextmanager
def forward_termination(enabled):
    previous = None
    if enabled:
        if os.name != "posix" or threading.current_thread() is not threading.main_thread():
            raise ValueError("Bounded experiments run on the POSIX main thread (including WSL)")
        previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        yield
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def _interrupt(signum, frame):
    raise KeyboardInterrupt("Experiment received termination")


def stop_group(process, grace):
    def send(value):
        try:
            os.killpg(process.pid, value)
        except ProcessLookupError:
            pass
    send(signal.SIGTERM)
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The leader can exit while a grandchild ignores TERM. Clean that group too.
        send(signal.SIGKILL)
        process.wait()


def run_limited(command, stream, cwd, limits, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 124, "not_started_after_experiment_timeout"
    limit = min(limits["task_timeout_seconds"], remaining)
    timeout_state = "experiment_timeout" if remaining <= limits["task_timeout_seconds"] else "task_timeout"
    process = subprocess.Popen(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                               start_new_session=True)
    try:
        code = process.wait(timeout=limit)
        return code, "complete" if code == 0 else "process_failed"
    except subprocess.TimeoutExpired:
        key = "experiment_kill_grace_seconds" if timeout_state == "experiment_timeout" else "task_kill_grace_seconds"
        stop_group(process, limits[key])
        return 124, timeout_state
    except BaseException:
        stop_group(process, limits["task_kill_grace_seconds"])
        raise
