from app.config import load_settings
from app.logging_config import configure_logging
from app.main import create_app


def main() -> None:
    import uvicorn

    configure_logging()
    uvicorn.run(create_app(load_settings()), host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
