from granian.constants import Interfaces

from main import build_server


def test_build_server_configures_granian_shutdown_timeout() -> None:
    server = build_server()

    assert server.bind_addr == "0.0.0.0"
    assert server.bind_port == 9999
    assert server.interface == Interfaces.ASGI
    assert server.workers_kill_timeout == 5
