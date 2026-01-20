import os

import uvicorn


def main() -> None:
    host = os.getenv("YUXI_API_HOST", "0.0.0.0")
    port_value = os.getenv("YUXI_API_PORT")
    port = int(port_value) if port_value and port_value.isdigit() else 5050
    if not 1 <= port <= 65535:
        port = 5050
    reload = os.getenv("YUXI_API_RELOAD", "true").lower() == "true"
    uvicorn.run("server.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
