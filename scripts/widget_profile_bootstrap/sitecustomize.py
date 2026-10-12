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
            functions = []
            for thread in yappi.get_thread_stats():
                if thread.name == "widget-profile-writer":
                    continue
                # The ctx_id= keyword ignores zero in Yappi 1.7.6. The explicit
                # filter correctly selects the main thread instead of all threads.
                for function in yappi.get_func_stats(filter={"ctx_id": thread.id}):
                    functions.append(
                        {
                            "name": function.name,
                            "module": function.module,
                            "lineno": function.lineno,
                            "calls": function.ncall,
                            "self_seconds": function.tsub,
                            "inclusive_seconds": function.ttot,
                            "thread": thread.id,
                        }
                    )
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
