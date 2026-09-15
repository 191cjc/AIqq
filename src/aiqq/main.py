"""AiQQ executable entry point."""

from __future__ import annotations

from dotenv import load_dotenv

from .bootstrap import build_application
from .config import AppConfig
from .logging_setup import configure_logging


def main() -> None:
    load_dotenv()
    config = AppConfig.from_env()
    configure_logging(
        config.logging.file_path,
        backup_days=config.logging.backup_days,
    )
    build_application(config).run()


if __name__ == "__main__":
    main()
