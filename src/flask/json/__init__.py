from __future__ import annotations

import json as _json
import typing as t

from ..globals import current_app
from .provider import _default

if t.TYPE_CHECKING:  # pragma: no cover
    from ..wrappers import Response


def dumps(obj: t.Any, **kwargs: t.Any) -> str:
    """将数据序列化为 JSON。

    如果 :data:`~flask.current_app` 可用，它将使用其
    :meth:`app.json.dumps() <flask.json.provider.JSONProvider.dumps>` 方法，否则将使用
    :func:`json.dumps` 。

    :param obj: 要序列化的数据。
    :param kwargs: 传递给 ``dumps`` 实现的参数。

    .. versionchanged:: 2.3
        ``app`` 参数已被删除。

    .. versionchanged:: 2.2
        调用 ``current_app.json.dumps`` ，允许应用程序覆盖该行为。

    .. versionchanged:: 2.0.2
        通过转换为字符串来支持 :class:`decimal.Decimal` 。

    .. versionchanged:: 2.0
        ``encoding`` 将在 Flask 2.1 中删除。

    .. versionchanged:: 1.0.3
        ``app`` 可以直接传递，而不需要应用程序上下文进行配置。
    """
    if current_app:
        return current_app.json.dumps(obj, **kwargs)

    kwargs.setdefault("default", _default)
    return _json.dumps(obj, **kwargs)


def dump(obj: t.Any, fp: t.IO[str], **kwargs: t.Any) -> None:
    """将数据序列化为 JSON 并写入文件。

    如果 :data:`~flask.current_app` 可用，它将使用其
    :meth:`app.json.dump() <flask.json.provider.JSONProvider.dump>` 方法，否则将使用
    :func:`json.dump` 。

    :param obj: 要序列化的数据。
    :param fp: 打开用于写入文本的文件。应使用 UTF-8 编码才有效 JSON。
    :param kwargs: 传递给 ``dump`` 实现的参数。

    .. versionchanged:: 2.3
        ``app`` 参数已被删除。

    .. versionchanged:: 2.2
        调用 ``current_app.json.dump`` ，允许应用程序覆盖该行为。

    .. versionchanged:: 2.0
        写入二进制文件和 ``encoding`` 参数将在 Flask 2.1 中删除。
    """
    if current_app:
        current_app.json.dump(obj, fp, **kwargs)
    else:
        kwargs.setdefault("default", _default)
        _json.dump(obj, fp, **kwargs)


def loads(s: str | bytes, **kwargs: t.Any) -> t.Any:
    """将数据反序列化为 JSON。

    如果 :data:`~flask.current_app` 可用，它将使用其
    :meth:`app.json.loads() <flask.json.provider.JSONProvider.loads>` 方法，否则将使用
    :func:`json.loads` 。

    :param s: 文本或 UTF-8 字节。
    :param kwargs: 传递给 ``loads`` 实现的参数。

    .. versionchanged:: 2.3
        ``app`` 参数已被删除。

    .. versionchanged:: 2.2
        调用 ``current_app.json.loads`` ，允许应用程序覆盖该行为。

    .. versionchanged:: 2.0
        ``encoding`` 将在 Flask 2.1 中删除。数据必须是字符串或 UTF-8 字节。

    .. versionchanged:: 1.0.3
        ``app`` 可以直接传递，而不需要应用程序上下文进行配置。
    """
    if current_app:
        return current_app.json.loads(s, **kwargs)

    return _json.loads(s, **kwargs)


def load(fp: t.IO[t.AnyStr], **kwargs: t.Any) -> t.Any:
    """将数据反序列化为 JSON 从文件中读取。

    如果 :data:`~flask.current_app` 可用，它将使用其
    :meth:`app.json.load() <flask.json.provider.JSONProvider.load>` 方法，否则将使用
    :func:`json.load` 。

    :param fp: 为读取文本或 UTF-8 字节而打开的文件。
    :param kwargs: 传递给 ``load`` 实现的参数。

    .. versionchanged:: 2.3
        ``app`` 参数已被删除。

    .. versionchanged:: 2.2
        调用 ``current_app.json.load`` ，允许应用程序覆盖该行为。

    .. versionchanged:: 2.2
        ``app`` 参数将在 Flask 2.3 中删除。

    .. versionchanged:: 2.0
        ``encoding`` 将在 Flask 2.1 中删除。文件必须是文本模式，或具有 UTF-8 字节的二进
        制模式。
    """
    if current_app:
        return current_app.json.load(fp, **kwargs)

    return _json.load(fp, **kwargs)


def jsonify(*args: t.Any, **kwargs: t.Any) -> Response:
    """将给定参数序列化为 JSON，并返回具有 ``application/json`` mimetype 的
    :class:`~flask.Response` 对象。从视图返回的字典或列表将自动转换为 JSON 响应，而无需
    调用它。

    这需要一个活动的应用程序上下文，并调用
    :meth:`app.json.response() <flask.json.provider.JSONProvider.response>` 。

    在调试模式下，输出采用缩进格式，以便于阅读。这也可能由提供商控制。

    可以给出位置参数或关键字参数，但不能同时给出两者。如果未给出参数，则序列化 ``None``
    。

    :param args: 要序列化的单个值，或将多个值视为要序列化的列表。
    :param kwargs: 视为要序列化的字典。

    .. versionchanged:: 2.2
        调用 ``current_app.json.response`` ，允许应用程序覆盖该行为。

    .. versionchanged:: 2.0.2
        通过转换为字符串来支持 :class:`decimal.Decimal` 。

    .. versionchanged:: 0.11
        添加了对序列化顶级数组的支持。这是古代浏览器中的一个安全风险。请参阅
        :ref:`security-json` 。

    .. versionadded:: 0.2
    """
    return current_app.json.response(*args, **kwargs)  # type: ignore[return-value]
