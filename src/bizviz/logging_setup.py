"""Logging setup.

Privacy rule: never log amounts, merchant names, account names/masks, tickers, tokens, secrets
or Plaid error bodies. Log only IDs, counts, durations and error codes.
"""

import logging
import logging.handlers

from bizviz.config import AppConfig


def configure_logging(cfg: AppConfig, *, to_stderr: bool = False) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        if getattr(handler, "_bizviz", False):
            root.removeHandler(handler)
            handler.close()
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            cfg.log_dir / "service.log", maxBytes=5 * 1024 * 1024, backupCount=5
        )
    ]
    if to_stderr:
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(fmt)
        handler._bizviz = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("peewee").setLevel(logging.WARNING)
