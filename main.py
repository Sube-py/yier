from yier_web.app import app


if __name__ == "__main__":
    import asyncio

    from granian import Granian, loops
    from granian.constants import Interfaces

    @loops.register("auto")
    def build_loop():
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
        return asyncio.new_event_loop()

    Granian(
        f"{__name__}:app",
        address="0.0.0.0",
        port=9999,
        interface=Interfaces.ASGI,
    ).serve()

