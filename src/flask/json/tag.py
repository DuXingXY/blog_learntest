"""
带标签的 JSON
~~~~~~~~~~~~~

非标准 JSON 类型的无损序列化的紧凑表示。
:class:`~flask.sessions.SecureCookieSessionInterface` 使用它来序列化会话数据，但它在其他
地方可能很有用。它可以扩展以支持其他类型。

.. autoclass:: TaggedJSONSerializer
    :members:

.. autoclass:: JSONTag
    :members:

让我们看一个添加对 :class:`~collections.OrderedDict` 支持的示例。字典在 JSON 中没有顺序
，因此为了处理这个问题，我们将把这些项目转储为 ``[key, value]`` 对的列表。子类
:class:`JSONTag` 并为其赋予新标签键 ``' od'`` 来标识类型。
会话序列化程序首先处理字典，因此应将新标签插入顺序表开头，
以便在 ``dict`` 之前处理 ``OrderedDict``。

.. code-block:: python

    from flask.json.tag import JSONTag

    class TagOrderedDict(JSONTag):
        __slots__ = ('serializer',)
        key = ' od'

        def check(self, value):
            return isinstance(value, OrderedDict)

        def to_json(self, value):
            return [[k, self.serializer.tag(v)] for k, v in iteritems(value)]

        def to_python(self, value):
            return OrderedDict(value)

    app.session_interface.serializer.register(TagOrderedDict, index=0)
"""

from __future__ import annotations

import typing as t
from base64 import b64decode
from base64 import b64encode
from datetime import datetime
from uuid import UUID

from markupsafe import Markup
from werkzeug.http import http_date
from werkzeug.http import parse_date

from ..json import dumps
from ..json import loads


class JSONTag:
    """用于定义 :class:`TaggedJSONSerializer` 类型标记的基类。"""

    __slots__ = ("serializer",)

    #: 用于标记序列化对象的标签。如果为空，则此标记仅用作标记过程中的中间步骤。
    key: str = ""

    def __init__(self, serializer: TaggedJSONSerializer) -> None:
        """为给定的序列化器创建一个标记器。"""
        self.serializer = serializer

    def check(self, value: t.Any) -> bool:
        """检查给定值是否应使用此标签进行标记。"""
        raise NotImplementedError

    def to_json(self, value: t.Any) -> t.Any:
        """将 Python 对象转换为有效 JSON 类型的对象。稍后将添加该标签。"""
        raise NotImplementedError

    def to_python(self, value: t.Any) -> t.Any:
        """将 JSON 表示形式转换回正确的类型。该标签已被删除。"""
        raise NotImplementedError

    def tag(self, value: t.Any) -> dict[str, t.Any]:
        """将值转换为有效的 JSON 类型并在其周围添加标记结构。"""
        return {self.key: self.to_json(value)}


class TagDict(JSONTag):
    """唯一键与注册标签匹配的 1 项字典的标签。

    在内部，dict key 以 `__` 为后缀，反序列化时将后缀删除。
    """

    __slots__ = ()
    key = " di"

    def check(self, value: t.Any) -> bool:
        return (
            isinstance(value, dict)
            and len(value) == 1
            and next(iter(value)) in self.serializer.tags
        )

    def to_json(self, value: t.Any) -> t.Any:
        key = next(iter(value))
        return {f"{key}__": self.serializer.tag(value[key])}

    def to_python(self, value: t.Any) -> t.Any:
        key = next(iter(value))
        return {key[:-2]: value[key]}


class PassDict(JSONTag):
    __slots__ = ()

    def check(self, value: t.Any) -> bool:
        return isinstance(value, dict)

    def to_json(self, value: t.Any) -> t.Any:
        # JSON 对象可能只有字符串键，因此不必在此处标记键。
        return {k: self.serializer.tag(v) for k, v in value.items()}

    tag = to_json


class TagTuple(JSONTag):
    __slots__ = ()
    key = " t"

    def check(self, value: t.Any) -> bool:
        return isinstance(value, tuple)

    def to_json(self, value: t.Any) -> t.Any:
        return [self.serializer.tag(item) for item in value]

    def to_python(self, value: t.Any) -> t.Any:
        return tuple(value)


class PassList(JSONTag):
    __slots__ = ()

    def check(self, value: t.Any) -> bool:
        return isinstance(value, list)

    def to_json(self, value: t.Any) -> t.Any:
        return [self.serializer.tag(item) for item in value]

    tag = to_json


class TagBytes(JSONTag):
    __slots__ = ()
    key = " b"

    def check(self, value: t.Any) -> bool:
        return isinstance(value, bytes)

    def to_json(self, value: t.Any) -> t.Any:
        return b64encode(value).decode("ascii")

    def to_python(self, value: t.Any) -> t.Any:
        return b64decode(value)


class TagMarkup(JSONTag):
    """通过使用 ``__html__`` 方法来序列化与 :class:`~markupsafe.Markup` API 匹配的任何内
    容到该方法的结果。始终反序列化为 :class:`~markupsafe.Markup` 的实例。"""

    __slots__ = ()
    key = " m"

    def check(self, value: t.Any) -> bool:
        return callable(getattr(value, "__html__", None))

    def to_json(self, value: t.Any) -> t.Any:
        return str(value.__html__())

    def to_python(self, value: t.Any) -> t.Any:
        return Markup(value)


class TagUUID(JSONTag):
    __slots__ = ()
    key = " u"

    def check(self, value: t.Any) -> bool:
        return isinstance(value, UUID)

    def to_json(self, value: t.Any) -> t.Any:
        return value.hex

    def to_python(self, value: t.Any) -> t.Any:
        return UUID(value)


class TagDateTime(JSONTag):
    __slots__ = ()
    key = " d"

    def check(self, value: t.Any) -> bool:
        return isinstance(value, datetime)

    def to_json(self, value: t.Any) -> t.Any:
        return http_date(value)

    def to_python(self, value: t.Any) -> t.Any:
        return parse_date(value)


class TaggedJSONSerializer:
    """使用标签系统来紧凑地表示非 JSON 类型的对象的序列化程序。作为中间序列化器传递给
    :class:`itsdangerous.Serializer` 。

    支持以下额外类型：

    * :class:`dict`
    * :class:`tuple`
    * :class:`bytes`
    * :class:`~markupsafe.Markup`
    * :class:`~uuid.UUID`
    * :class:`~datetime.datetime`
    """

    __slots__ = ("tags", "order")

    #: 创建序列化器时要绑定的标记类。稍后可以使用 :meth:`~register` 添加其他标签。
    default_tags = [
        TagDict,
        PassDict,
        TagTuple,
        PassList,
        TagBytes,
        TagMarkup,
        TagUUID,
        TagDateTime,
    ]

    def __init__(self) -> None:
        self.tags: dict[str, JSONTag] = {}
        self.order: list[JSONTag] = []

        for cls in self.default_tags:
            self.register(cls)

    def register(
        self,
        tag_class: type[JSONTag],
        force: bool = False,
        index: int | None = None,
    ) -> None:
        """使用此序列化器注册一个新标签。

        :param tag_class: 要注册的标签类。将使用此序列化器实例进行实例化。
        :param force: 覆盖现有标签。如果为 false（默认），则会引发 :exc:`KeyError` 。
        :param index: 用于按标签顺序插入新标签的索引。当新标签是现有标签的特例时很有用。
            如果 ``None`` （默认），则标记将附加到顺序末尾。

        :raise KeyError: 如果标签键已注册且 ``force`` 不为 true。
        """
        tag = tag_class(self)
        key = tag.key

        if key:
            if not force and key in self.tags:
                raise KeyError(f"Tag '{key}' is already registered.")

            self.tags[key] = tag

        if index is None:
            self.order.append(tag)
        else:
            self.order.insert(index, tag)

    def tag(self, value: t.Any) -> t.Any:
        """如有必要，将值转换为标记表示。"""
        for tag in self.order:
            if tag.check(value):
                return tag.tag(value)

        return value

    def untag(self, value: dict[str, t.Any]) -> t.Any:
        """将标记的表示形式转换回原始类型。"""
        if len(value) != 1:
            return value

        key = next(iter(value))

        if key not in self.tags:
            return value

        return self.tags[key].to_python(value[key])

    def _untag_scan(self, value: t.Any) -> t.Any:
        if isinstance(value, dict):
            # 递归地取消每个项目的标记
            value = {k: self._untag_scan(v) for k, v in value.items()}
            # 取消字典本身的标签
            value = self.untag(value)
        elif isinstance(value, list):
            # 递归地取消每个项目的标记
            value = [self._untag_scan(item) for item in value]

        return value

    def dumps(self, value: t.Any) -> str:
        """标记该值并将其转储到紧凑的 JSON 字符串。"""
        return dumps(self.tag(value), separators=(",", ":"))

    def loads(self, value: str) -> t.Any:
        """从 JSON 字符串加载数据并反序列化任何标记的对象。"""
        return self._untag_scan(loads(value))
