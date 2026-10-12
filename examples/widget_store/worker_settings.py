"""Settings for Celery's serial prefork children; web threads use settings.py."""

from examples.widget_store.settings import *  # noqa: F403
from examples.widget_store.settings import DATABASES as WEB_DATABASES  # noqa: E402

# Each prefork child runs one task at a time and owns its own pool. Keep socket
# validation and the pool's normal reconnect backoff; avoid spare stale sockets.
DATABASES = {
    "default": {
        **WEB_DATABASES["default"],
        "OPTIONS": {
            **WEB_DATABASES["default"]["OPTIONS"],
            "pool": {**WEB_DATABASES["default"]["OPTIONS"]["pool"], "max_size": 1},
        },
    }
}
