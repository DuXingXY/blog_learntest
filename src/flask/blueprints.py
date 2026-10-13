from __future__ import annotations

import os
import typing as t
from datetime import timedelta

from .cli import AppGroup
from .globals import current_app
from .helpers import send_from_directory
from .sansio.blueprints import Blueprint as SansioBlueprint
from .sansio.blueprints import BlueprintSetupState as BlueprintSetupState  # noqa
from .sansio.scaffold import _sentinel

if t.TYPE_CHECKING:  # pragma: no cover
    from .wrappers import Response


class Blueprint(SansioBlueprint):
    def __init__(
        self,
        name: str,
        import_name: str,
        static_folder: str | os.PathLike[str] | None = None,
        static_url_path: str | None = None,
        template_folder: str | os.PathLike[str] | None = None,
        url_prefix: str | None = None,
        subdomain: str | None = None,
        url_defaults: dict[str, t.Any] | None = None,
        root_path: str | None = None,
        cli_group: str | None = _sentinel,  # type: ignore
    ) -> None:
        super().__init__(
            name,
            import_name,
            static_folder,
            static_url_path,
            template_folder,
            url_prefix,
            subdomain,
            url_defaults,
            root_path,
            cli_group,
        )

        #: 用于为此对象注册 CLI 命令的 Click 命令组。一旦发现应用程序并注册了蓝图，就可
        #: 以通过 ``flask`` 命令使用这些命令。
        self.cli = AppGroup()

        # 设置 Click 组的名称，以防有人想要将应用程序的命令添加到另一个 CLI 工具。
        self.cli.name = self.name

    def get_send_file_max_age(self, filename: str | None) -> int | None:
        """如果未传递给定文件路径，则由 :func:`send_file` 使用来确定 ``max_age`` 缓存值
        。

        默认情况下，这会从 :data:`~flask.current_app` 的配置中返回
        :data:`SEND_FILE_MAX_AGE_DEFAULT` 。默认为 ``None`` ，它告诉浏览器使用条件请求而
        不是定时缓存，这通常是更好的选择。

        请注意，这是 Flask 类中相同方法的重复。

        .. versionchanged:: 2.0
            默认配置是 ``None`` 而不是 12 小时。

        .. versionadded:: 0.9
        """
        value = current_app.config["SEND_FILE_MAX_AGE_DEFAULT"]

        if value is None:
            return None

        if isinstance(value, timedelta):
            return int(value.total_seconds())

        return value  # type: ignore[no-any-return]

    def send_static_file(self, filename: str) -> Response:
        """用于提供来自 :attr:`static_folder` 的文件的视图函数。如果设置了
        :attr:`static_folder` ，则会在 :attr:`static_url_path` 处自动为此视图注册一条路
        由。

        请注意，这是 Flask 类中相同方法的重复。

        .. versionadded:: 0.5

        """
        if not self.has_static_folder:
            raise RuntimeError("'static_folder' must be set to serve static_files.")

        # send_file 只知道在应用程序上调用 get_send_file_max_age ，在此处调用它，因此它
        # 也适用于蓝图。
        max_age = self.get_send_file_max_age(filename)
        return send_from_directory(
            t.cast(str, self.static_folder), filename, max_age=max_age
        )

    def open_resource(
        self, resource: str, mode: str = "rb", encoding: str | None = "utf-8"
    ) -> t.IO[t.AnyStr]:
        """打开相对于 :attr:`root_path` 的资源文件进行读取。应用程序的
        :meth:`~.Flask.open_resource` 方法的蓝图相关等效项。

        :param resource: 相对于 :attr:`root_path` 的资源路径。
        :param mode: 在此模式下打开文件。仅支持读取，有效值为 ``"r"`` （或 ``"rt"`` ）和
            ``"rb"`` 。
        :param encoding: 以文本模式打开时使用此编码打开文件。以二进制模式打开时会忽略这
            一点。

        .. versionchanged:: 3.1
            添加了 ``encoding`` 参数。
        """
        if mode not in {"r", "rt", "rb"}:
            raise ValueError("Resources can only be opened for reading.")

        path = os.path.join(self.root_path, resource)

        if mode == "rb":
            return open(path, mode)  # pyright: ignore

        return open(path, mode, encoding=encoding)
