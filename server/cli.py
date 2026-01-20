import os

import uvicorn


def main() -> None:
    host = os.getenv("YUXI_API_HOST", "0.0.0.0")
    port = int(os.getenv("YUXI_API_PORT", "5050"))
    reload = os.getenv("YUXI_API_RELOAD", "true").lower() == "true"
    uvicorn.run("server.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
