from __future__ import annotations

import dataclasses
import decimal
import json
import typing as t
import uuid
import weakref
from datetime import date

from werkzeug.http import http_date

if t.TYPE_CHECKING:  # pragma: no cover
    from werkzeug.sansio.response import Response

    from ..sansio.app import App


class JSONProvider:
    """应用程序的一组标准 JSON 操作。其子类可用于自定义 JSON 行为或使用不同的 JSON 库。

    要实现特定库的提供程序，请子类化该基类并至少实现 :meth:`dumps` 和 :meth:`loads` 。所
    有其他方法都有默认实现。

    要使用不同的提供程序，请子类化 ``Flask`` 并将
    :attr:`~flask.Flask.json_provider_class` 设置为提供程序类，或者将
    :attr:`app.json <flask.Flask.json>` 设置为该类的实例。

    :param app: 一个应用程序实例。这将作为 :class:`weakref.proxy` 存储在 :attr:`_app` 属
        性上。

    .. versionadded:: 2.2
    """

    def __init__(self, app: App) -> None:
        self._app: App = weakref.proxy(app)

    def dumps(self, obj: t.Any, **kwargs: t.Any) -> str:
        """将数据序列化为 JSON。

        :param obj: 要序列化的数据。
        :param kwargs: 可以传递给底层 JSON 库。
        """
        raise NotImplementedError

    def dump(self, obj: t.Any, fp: t.IO[str], **kwargs: t.Any) -> None:
        """将数据序列化为 JSON 并写入文件。

        :param obj: 要序列化的数据。
        :param fp: 打开用于写入文本的文件。应使用 UTF-8 编码才有效 JSON。
        :param kwargs: 可以传递给底层 JSON 库。
        """
        fp.write(self.dumps(obj, **kwargs))

    def loads(self, s: str | bytes, **kwargs: t.Any) -> t.Any:
        """将数据反序列化为 JSON。

        :param s: 文本或 UTF-8 字节。
        :param kwargs: 可以传递给底层 JSON 库。
        """
        raise NotImplementedError

    def load(self, fp: t.IO[t.AnyStr], **kwargs: t.Any) -> t.Any:
        """将数据反序列化为 JSON 从文件中读取。

        :param fp: 为读取文本或 UTF-8 字节而打开的文件。
        :param kwargs: 可以传递给底层 JSON 库。
        """
        return self.loads(fp.read(), **kwargs)

    def _prepare_response_obj(
        self, args: tuple[t.Any, ...], kwargs: dict[str, t.Any]
    ) -> t.Any:
        if args and kwargs:
            raise TypeError("app.json.response() takes either args or kwargs, not both")

        if not args and not kwargs:
            return None

        if len(args) == 1:
            return args[0]

        return args or kwargs

    def response(self, *args: t.Any, **kwargs: t.Any) -> Response:
        """将给定参数序列化为 JSON，并返回具有 ``application/json`` mimetype 的
        :class:`~flask.Response` 对象。

        :func:`~flask.json.jsonify` 函数为当前应用程序调用此方法。

        可以给出位置参数或关键字参数，但不能同时给出两者。如果未给出参数，则序列化
        ``None`` 。

        :param args: 要序列化的单个值，或将多个值视为要序列化的列表。
        :param kwargs: 视为要序列化的字典。
        """
        obj = self._prepare_response_obj(args, kwargs)
        return self._app.response_class(self.dumps(obj), mimetype="application/json")


def _default(o: t.Any) -> t.Any:
    if isinstance(o, date):
        return http_date(o)

    if isinstance(o, (decimal.Decimal, uuid.UUID)):
        return str(o)

    if dataclasses and dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)  # type: ignore[arg-type]

    if hasattr(o, "__html__"):
        return str(o.__html__())

    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


class DefaultJSONProvider(JSONProvider):
    """使用 Python 内置的 :mod:`json` 库提供 JSON 操作，并序列化以下额外数据类型：

    -   :class:`datetime.datetime` 和 :class:`datetime.date` 被序列化为 :rfc:`822` 字符
        串。这与 HTTP 日期格式相同。
    -   :class:`uuid.UUID` 被序列化为字符串。
    -   :class:`dataclasses.dataclass` 传递给 :func:`dataclasses.asdict` 。
    -   :class:`~markupsafe.Markup` （或任何具有 ``__html__`` 方法的对象）将调用
        ``__html__`` 方法来获取字符串。
    """

    default: t.Callable[[t.Any], t.Any] = staticmethod(_default)
    """将此函数应用于 :meth:`json.dumps` 无法序列化的对象。它应返回有效的 JSON 类型，或
    引发 ``TypeError`` 。
    """

    ensure_ascii = True
    """将非 ASCII 字符替换为转义序列。这可以改善某些客户端的兼容性；禁用此选项可提高性能
    并减小输出体积。
    """

    sort_keys = True
    """对序列化字典的键排序。这对某些缓存场景有用；禁用此选项可提高性能。启用时，所有键
    都必须是字符串，排序前不会进行类型转换。
    """

    compact: bool | None = None
    """设为 ``True`` ，或在非调试模式下设为 ``None`` 时， :meth:`response` 的输出不会包
    含缩进、换行或空格。设为 ``False`` ，或在调试模式下设为 ``None`` 时，会使用非紧凑格
    式。
    """

    mimetype = "application/json"
    """:meth:`response` 中设置的 mimetype。"""

    def dumps(self, obj: t.Any, **kwargs: t.Any) -> str:
        """将数据作为 JSON 序列化为字符串。

        关键字参数传递给 :func:`json.dumps` 。从 :attr:`default` 、 :attr:`ensure_ascii`
        和 :attr:`sort_keys` 属性设置一些参数默认值。

        :param obj: 要序列化的数据。
        :param kwargs: 传递给 :func:`json.dumps` 。
        """
        kwargs.setdefault("default", self.default)
        kwargs.setdefault("ensure_ascii", self.ensure_ascii)
        kwargs.setdefault("sort_keys", self.sort_keys)
        return json.dumps(obj, **kwargs)

    def loads(self, s: str | bytes, **kwargs: t.Any) -> t.Any:
        """将字符串或字节中的数据反序列化为 JSON。

        :param s: 文本或 UTF-8 字节。
        :param kwargs: 传递给 :func:`json.loads` 。
        """
        return json.loads(s, **kwargs)

    def response(self, *args: t.Any, **kwargs: t.Any) -> Response:
        """将给定参数序列化为 JSON，并返回一个 :class:`~flask.Response` 对象。响应
        mimetype 将为 "application/json" 并且可以使用 :attr:`mimetype` 进行更改。

        如果 :attr:`compact` 是 ``False`` 或启用了调试模式，输出将被格式化为更易于阅读。

        可以给出位置参数或关键字参数，但不能同时给出两者。如果未给出参数，则序列化
        ``None`` 。

        :param args: 要序列化的单个值，或将多个值视为要序列化的列表。
        :param kwargs: 视为要序列化的字典。
        """
        obj = self._prepare_response_obj(args, kwargs)
        dump_args: dict[str, t.Any] = {}

        if (self.compact is None and self._app.debug) or self.compact is False:
            dump_args.setdefault("indent", 2)
        else:
            dump_args.setdefault("separators", (",", ":"))

        return self._app.response_class(
            f"{self.dumps(obj, **dump_args)}\n", mimetype=self.mimetype
        )
