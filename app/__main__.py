from app.config import load_settings
from app.logging_config import configure_logging
from app.main import create_app


def main() -> None:
    import uvicorn

    configure_logging()
    # Behind the TLS-terminating nginx ingress the app sees plain HTTP, so trust the
    # proxy's X-Forwarded-Proto. Without this the CSRF cookie is never marked Secure.
    # forwarded_allow_ips="*" is safe here: the pod is only reachable via the ingress
    # ClusterIP from inside the cluster.
    uvicorn.run(
        create_app(load_settings()),
        host="0.0.0.0",
        port=8080,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
