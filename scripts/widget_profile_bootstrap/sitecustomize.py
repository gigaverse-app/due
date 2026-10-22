"""Opt-in Yappi bootstrap, placed on PYTHONPATH only by widget_profile.

Keep this separate from the application and the upstream checkout. A fork gets
fresh counters and its own writer; it cannot overwrite its parent's profile.
"""

import atexit
import json
import os
import sys
import threading
import time
from pathlib import Path


def collect_functions() -> list[dict]:
    """Read flat counters without constructing/sorting unused call graphs.

    Yappi is pinned to 1.7.6. Its documented raw enumerator preserves context IDs
    (including main-thread zero), unlike the unfiltered public aggregation.
    Pause/resume matches Yappi's public collector, with a much shorter pause.
    Exact parity with that collector is tested on real CPU and wall captures.
    """
    import _yappi  # pyrefly: ignore[missing-import]  # Pinned compiled Yappi extension.

    functions: dict[tuple, dict] = {}

    def append(entry: tuple) -> None:
        name, module, line, calls, _, builtin, total, own, _, _, context, context_name, _, _ = entry
        if context_name == "widget-profile-writer" or os.path.basename(module) == "yappi.py" or module == "_yappi":
            return
        # Public Yappi merges distinct code objects with the same source identity.
        key = (context, module, 0 if builtin else line, name)
        if key in functions:
            row = functions[key]
            row["calls"] += calls
            row["self_seconds"] += own
            row["inclusive_seconds"] += total
        else:
            functions[key] = dict(
                name=name,
                module=module,
                lineno=line,
                calls=calls,
                self_seconds=own,
                inclusive_seconds=total,
                thread=context,
            )

    _yappi._pause()
    try:
        _yappi.enum_func_stats(append, {})
    finally:
        _yappi._resume()
    return list(functions.values())


def start(directory: Path, mode: str, role: str) -> None:
    import yappi

    yappi.stop()
    yappi.clear_stats()
    yappi.set_clock_type(mode)
    yappi.set_context_name_callback(lambda: threading.current_thread().name)
    pid, parent_pid, started = os.getpid(), os.getppid(), time.time()
    lock = threading.Lock()

    def write() -> None:
        if not lock.acquire(blocking=False):
            return
        try:
            functions = collect_functions()
            result = {
                "pid": pid,
                "parent_pid": parent_pid,
                "role": role,
                "clock": mode,
                "started": started,
                "captured": time.time(),
                "functions": functions,
            }
            temporary = directory / f"{pid}.tmp"
            temporary.write_text(json.dumps(result))
            temporary.replace(directory / f"{pid}.json")
        except BaseException as error:
            (directory / f"error-{pid}.txt").write_text(repr(error))
        finally:
            lock.release()

    def background() -> None:
        while True:
            time.sleep(1)
            write()

    yappi.start(builtins=True, profile_threads=True)
    threading.Thread(target=background, name="widget-profile-writer", daemon=True).start()
    # Fork copies atexit callbacks. Remove the parent's writer in the child.
    atexit.unregister(globals().get("_last_write", write))
    globals()["_last_write"] = write
    atexit.register(write)


def activate() -> None:
    output = os.environ.get("WIDGET_PROFILE_OUTPUT")
    if not output:
        return
    command = " ".join(sys.orig_argv)
    if "widget_store.main" in command:
        role = "dbos"
    elif "django runserver" in command:
        role = "due_web"
    elif "celery" in command:
        role = "due_worker" if "worker" in command else "due_beat"
    else:
        return  # Never charge the controller/supervisor to an application.
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    mode = os.environ["WIDGET_PROFILE_CLOCK"]
    start(directory, mode, role)
    os.register_at_fork(after_in_child=lambda: start(directory, mode, role))


activate()
