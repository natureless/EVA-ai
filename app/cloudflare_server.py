"""Single-process, loopback-only entry point for the protected Cloudflare deployment."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

from runtime.cloudflare_access import AccessConfig

ROOT = Path(__file__).resolve().parents[1]


def configure(env_file: Path) -> AccessConfig:
    if not env_file.is_file():
        raise ValueError("Missing private deployment environment file")
    load_dotenv(ROOT / ".env")
    load_dotenv(env_file, override=True)
    config = AccessConfig.from_env()
    if config is None:
        raise ValueError("Cloudflare Access must be configured before deployment")
    if len(os.environ.get("EVA_API_TOKEN", "")) < 32:
        raise ValueError("Deployment requires a separate EVA_API_TOKEN of at least 32 characters")
    # Never expose the origin on the LAN or create several in-memory runtimes.
    os.environ.update(EVA_ENV="production", EVA_HOST="127.0.0.1", EVA_PORT="8000",
                      EVA_TRUST_PROXY_HEADERS="false")
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.cloudflare")
    parser.add_argument("--check", action="store_true", help="Validate configuration without opening the database")
    args = parser.parse_args()
    os.chdir(ROOT)
    config = configure(args.env_file.resolve())
    if args.check:
        print(f"Cloudflare configuration valid for {config.public_origin}; origin 127.0.0.1:8000")
        return
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, workers=1,
                proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
