import asyncio

from granian import Granian, loops
from granian.constants import Interfaces

from yier_web.app import app


@loops.register("auto")
def build_loop():
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    return asyncio.new_event_loop()


def build_server() -> Granian:
    return Granian(
        f"{__name__}:app",
        address="0.0.0.0",
        port=9999,
        interface=Interfaces.ASGI,
        # Some workers do not always exit promptly after SIGINT/SIGTERM on macOS.
        # Without a timeout, the main process can block forever waiting on join().
        workers_kill_timeout=5,
    )


if __name__ == "__main__":
    build_server().serve()
