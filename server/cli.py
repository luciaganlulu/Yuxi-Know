import os

import uvicorn


def main() -> None:
    host = os.getenv("YUXI_API_HOST", "0.0.0.0")
    port_value = os.getenv("YUXI_API_PORT")
    port_candidate = 5050
    if port_value:
        try:
            port_candidate = int(port_value)
        except ValueError:
            pass
    port = port_candidate if 1 <= port_candidate <= 65535 else 5050
    reload = os.getenv("YUXI_API_RELOAD", "true").lower() == "true"
    uvicorn.run("server.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
