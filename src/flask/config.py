from __future__ import annotations

import errno
import json
import os
import types
import typing as t

from werkzeug.utils import import_string

if t.TYPE_CHECKING:
    import typing_extensions as te

    from .sansio.app import App


T = t.TypeVar("T")


class ConfigAttribute(t.Generic[T]):
    """将属性转发到配置"""

    def __init__(
        self, name: str, get_converter: t.Callable[[t.Any], T] | None = None
    ) -> None:
        self.__name__ = name
        self.get_converter = get_converter

    @t.overload
    def __get__(self, obj: None, owner: None) -> te.Self: ...

    @t.overload
    def __get__(self, obj: App, owner: type[App]) -> T: ...

    def __get__(self, obj: App | None, owner: type[App] | None = None) -> T | te.Self:
        if obj is None:
            return self

        rv = obj.config[self.__name__]

        if self.get_converter is not None:
            rv = self.get_converter(rv)

        return rv  # type: ignore[no-any-return]

    def __set__(self, obj: App, value: t.Any) -> None:
        obj.config[self.__name__] = value


class Config(dict):  # type: ignore[type-arg]
    """工作原理与字典完全相同，但提供了从文件或特殊字典填充它的方法。  有两种常见的模式
    来填充配置。

    您可以从配置文件填充配置::

        app.config.from_pyfile('yourconfig.cfg')

    或者，您可以在调用 :meth:`from_object` 的模块中定义配置选项，或提供应加载的模块的导
    入路径。  还可以告诉它使用相同的模块，并在调用之前提供配置值::

        DEBUG = True
        SECRET_KEY = 'development key'
        app.config.from_object(__name__)

    在这两种情况下（从任何 Python 文件加载或从模块加载），仅将大写键添加到配置中。  这使
    得可以在配置文件中使用小写值作为未添加到配置中的临时值，或者在实现应用程序的同一文件
    中定义配置键。

    加载配置最有趣的方法可能是从指向文件的环境变量::

        app.config.from_envvar('YOURAPPLICATION_SETTINGS')

    在这种情况下，在启动应用程序之前，您必须将此环境变量设置为您要使用的文件。  在 Linux
    和 OS X 上使用导出语句::

        export YOURAPPLICATION_SETTINGS='/path/to/config/file'

    在 Windows 上使用 `set` 代替。

    :param root_path: 读取文件的相对路径。  当应用程序创建配置对象时，这是应用程序的
        :attr:`~flask.Flask.root_path` 。
    :param defaults: 可选的默认值字典
    """

    def __init__(
        self,
        root_path: str | os.PathLike[str],
        defaults: dict[str, t.Any] | None = None,
    ) -> None:
        super().__init__(defaults or {})
        self.root_path = root_path

    def from_envvar(self, variable_name: str, silent: bool = False) -> bool:
        """从指向配置文件的环境变量加载配置。  这基本上只是一个快捷方式，为这行代码提供
        了更好的错误消息::

            app.config.from_pyfile(os.environ['YOURAPPLICATION_SETTINGS'])

        :param variable_name: 环境变量的名称
        :param silent: 如果您希望丢失文件出现静默故障，请设置为 ``True`` 。
        :return: ``True`` 如果文件加载成功。
        """
        rv = os.environ.get(variable_name)
        if not rv:
            if silent:
                return False
            raise RuntimeError(
                f"The environment variable {variable_name!r} is not set"
                " and as such configuration could not be loaded. Set"
                " this variable and make it point to a configuration"
                " file"
            )
        return self.from_pyfile(rv, silent=silent)

    def from_prefixed_env(
        self, prefix: str = "FLASK", *, loads: t.Callable[[str], t.Any] = json.loads
    ) -> bool:
        """加载以 ``FLASK_`` 开头的任何环境变量，并从 env 键中删除配置键的前缀。值通过加
        载函数传递，以尝试将它们转换为比字符串更具体的类型。

        配置键按 :func:`sorted` 的排序结果加载。

        默认加载函数尝试将值解析为任何有效的 JSON 类型，包括字典和列表。

        嵌套字典中的特定项目可以通过用双下划线（ ``__`` ）分隔键来设置。如果中间键不存在
        ，它将被初始化为空字典。

        :param prefix: 加载以此前缀开头的环境变量，并用下划线 ( ``_`` ) 分隔。
        :param loads: 将每个字符串值传递给此函数并使用返回值作为配置值。如果出现任何错误
            ，该错误将被忽略并且该值仍为字符串。默认为 :func:`json.loads` 。

        .. versionadded:: 2.1
        """
        prefix = f"{prefix}_"

        for key in sorted(os.environ):
            if not key.startswith(prefix):
                continue

            value = os.environ[key]
            key = key.removeprefix(prefix)

            try:
                value = loads(value)
            except Exception:
                # 如果加载失败，则将该值保留为字符串。
                pass

            if "__" not in key:
                # 非嵌套键，直接设置。
                self[key] = value
                continue

            # 使用由 "__" 分隔的键遍历嵌套字典。
            current = self
            *parts, tail = key.split("__")

            for part in parts:
                # 如果中间字典不存在，则创建它。
                if part not in current:
                    current[part] = {}

                current = current[part]

            current[tail] = value

        return True

    def from_pyfile(
        self, filename: str | os.PathLike[str], silent: bool = False
    ) -> bool:
        """从 Python 文件更新配置中的值。  此函数的行为就像使用 :meth:`from_object` 函数
        将文件作为模块导入一样。

        :param filename: 配置的文件名。  这可以是绝对文件名，也可以是相对于根路径的文件
            名。
        :param silent: 如果您希望丢失文件出现静默故障，请设置为 ``True`` 。
        :return: ``True`` 如果文件加载成功。

        .. versionadded:: 0.7
           `silent` 参数。
        """
        filename = os.path.join(self.root_path, filename)
        d = types.ModuleType("config")
        d.__file__ = filename
        try:
            with open(filename, mode="rb") as config_file:
                exec(compile(config_file.read(), filename, "exec"), d.__dict__)
        except OSError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR, errno.ENOTDIR):
                return False
            e.strerror = f"Unable to load configuration file ({e.strerror})"
            raise
        self.from_object(d)
        return True

    def from_object(self, obj: object | str) -> None:
        """更新给定对象的值。  对象可以是以下两种类型之一：

        -   字符串：在这种情况下，将导入具有该名称的对象
        -   实际对象引用：直接使用该对象

        对象通常是模块或类。 :meth:`from_object` 仅加载模块/类的大写属性。 ``dict`` 对象
        不能与 :meth:`from_object` 一起使用，因为 ``dict`` 的键不是 ``dict`` 类的属性。

        基于模块的配置示例::

            app.config.from_object('yourapplication.default_config')
            from yourapplication import default_config
            app.config.from_object(default_config)

        加载之前不会对对象执行任何操作。如果对象是一个类并且具有 ``@property`` 属性，则
        需要在传递给此方法之前对其进行实例化。

        您不应该使用此函数来加载实际配置，而应该使用默认配置。  实际的配置应该使用
        :meth:`from_pyfile` 加载，并且最好从不在包内的位置加载，因为包可能安装在系统范围
        内。

        有关使用 :meth:`from_object` 的基于类的配置的示例，请参阅 :ref:`config-dev-prod`
        。

        :param obj: 导入名称或对象
        """
        if isinstance(obj, str):
            obj = import_string(obj)
        for key in dir(obj):
            if key.isupper():
                self[key] = getattr(obj, key)

    def from_file(
        self,
        filename: str | os.PathLike[str],
        load: t.Callable[[t.IO[t.Any]], t.Mapping[str, t.Any]],
        silent: bool = False,
        text: bool = True,
    ) -> bool:
        """从使用 ``load`` 参数加载的文件中更新配置中的值。加载的数据被传递给
        :meth:`from_mapping` 方法。

        .. code-block:: python

            import json
            app.config.from_file("config.json", load=json.load)

            import tomllib
            app.config.from_file("config.toml", load=tomllib.load, text=False)

        :param filename: 数据文件的路径。这可以是绝对路径或相对于配置根路径的路径。
        :param load: 一个可调用函数，它接受文件句柄并返回从文件加载的数据的映射。
        :type load: ``Callable[[Reader], Mapping]`` 其中 ``Reader`` 实现 ``read`` 方法。
        :param silent: 如果该文件不存在，则忽略该文件。
        :param text: 以文本或二进制模式打开文件。
        :return: ``True`` 如果文件加载成功。

        .. versionchanged:: 2.3
            添加了 ``text`` 参数。

        .. versionadded:: 2.0
        """
        filename = os.path.join(self.root_path, filename)

        try:
            with open(filename, "r" if text else "rb") as f:
                obj = load(f)
        except OSError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False

            e.strerror = f"Unable to load configuration file ({e.strerror})"
            raise

        return self.from_mapping(obj)

    def from_mapping(
        self, mapping: t.Mapping[str, t.Any] | None = None, **kwargs: t.Any
    ) -> bool:
        """更新配置，如 :meth:`update` 忽略具有非上部键的项目。

        :return: 始终返回 ``True`` 。

        .. versionadded:: 0.11
        """
        mappings: dict[str, t.Any] = {}
        if mapping is not None:
            mappings.update(mapping)
        mappings.update(kwargs)
        for key, value in mappings.items():
            if key.isupper():
                self[key] = value
        return True

    def get_namespace(
        self, namespace: str, lowercase: bool = True, trim_namespace: bool = True
    ) -> dict[str, t.Any]:
        """返回一个字典，其中包含与指定命名空间/前缀匹配的配置选项子集。用法示例::

            app.config['IMAGE_STORE_TYPE'] = 'fs'
            app.config['IMAGE_STORE_PATH'] = '/var/app/images'
            app.config['IMAGE_STORE_BASE_URL'] = 'http://img.website.com'
            image_store_config = app.config.get_namespace('IMAGE_STORE_')

        生成的字典 `image_store_config` 看起来像::

            {
                'type': 'fs',
                'path': '/var/app/images',
                'base_url': 'http://img.website.com'
            }

        当配置选项直接映射到函数或类构造函数中的关键字参数时，这通常很有用。

        :param namespace: 配置命名空间
        :param lowercase: 指示结果字典的键是否应为小写的标志
        :param trim_namespace: 指示结果字典的键是否不应包含名称空间的标志

        .. versionadded:: 0.11
        """
        rv = {}
        for k, v in self.items():
            if not k.startswith(namespace):
                continue
            if trim_namespace:
                key = k[len(namespace) :]
            else:
                key = k
            if lowercase:
                key = key.lower()
            rv[key] = v
        return rv

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {dict.__repr__(self)}>"
