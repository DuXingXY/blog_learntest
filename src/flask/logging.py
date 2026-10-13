from __future__ import annotations

import logging
import sys
import typing as t

from werkzeug.local import LocalProxy

from .globals import request

if t.TYPE_CHECKING:  # pragma: no cover
    from .sansio.app import App


@LocalProxy
def wsgi_errors_stream() -> t.TextIO:
    """找到最适合应用程序的错误流。如果请求处于活动状态，则记录到 ``wsgi.errors`` ，否则
    使用 ``sys.stderr`` 。

    如果您配置自己的 :class:`logging.StreamHandler` ，您可能希望将其用于流。如果您使用文
    件或字典配置并且无法直接导入它，您可以将其称为
    ``ext://flask.logging.wsgi_errors_stream`` 。
    """
    if request:
        return request.environ["wsgi.errors"]  # type: ignore[no-any-return]

    return sys.stderr


def has_level_handler(logger: logging.Logger) -> bool:
    """检查日志记录链中是否有处理程序来处理给定记录器的
    :meth:`effective level <~logging.Logger.getEffectiveLevel>` 。
    """
    level = logger.getEffectiveLevel()
    current = logger

    while current:
        if any(handler.level <= level for handler in current.handlers):
            return True

        if not current.propagate:
            break

        current = current.parent  # type: ignore

    return False


#: 将消息记录到 :func:`~flask.logging.wsgi_errors_stream` ，格式为
#: ``[%(asctime)s] %(levelname)s in %(module)s: %(message)s`` 。
default_handler = logging.StreamHandler(wsgi_errors_stream)  # type: ignore
default_handler.setFormatter(
    logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
)


def create_logger(app: App) -> logging.Logger:
    """获取 Flask 应用程序的记录器并根据需要进行配置。

    记录器名称将与 :attr:`app.import_name <flask.Flask.name>` 相同。

    当启用 :attr:`~flask.Flask.debug` 时，如果未设置，则将记录器级别设置为
    :data:`logging.DEBUG` 。

    如果没有记录器有效级别的处理程序，请使用基本格式为
    :func:`~flask.logging.wsgi_errors_stream` 添加 :class:`~logging.StreamHandler` 。
    """
    logger = logging.getLogger(app.name)

    if app.debug and not logger.level:
        logger.setLevel(logging.DEBUG)

    if not has_level_handler(logger):
        logger.addHandler(default_handler)

    return logger
