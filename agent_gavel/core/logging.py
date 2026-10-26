"""进程日志：内部错误写文件，stdout 留给 MCP 协议。"""

import logging

from . import config


def setup():
    try:
        log_dir = config.data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "agent-gavel.log",
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(process)d %(levelname)s %(name)s %(message)s"))
        logger = logging.getLogger("agent_gavel")
        logger.setLevel(logging.ERROR)
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            logger.addHandler(handler)
    except Exception:
        pass
