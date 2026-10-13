import os
import tempfile

import pytest

from flaskr import create_app
from flaskr.db import get_db
from flaskr.db import init_db

# 读入 SQL 以填充测试数据
with open(os.path.join(os.path.dirname(__file__), "data.sql"), "rb") as f:
    _data_sql = f.read().decode("utf8")


@pytest.fixture
def app():
    """为每个测试创建并配置一个新的应用程序实例。"""
    # 创建一个临时文件来隔离每个测试的数据库
    db_fd, db_path = tempfile.mkstemp()
    # 使用通用测试配置创建应用程序
    app = create_app({"TESTING": True, "DATABASE": db_path})

    # 创建数据库并加载测试数据
    with app.app_context():
        init_db()
        get_db().executescript(_data_sql)

    yield app

    # 关闭并删除临时数据库
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    """该应用程序的测试客户端。"""
    return app.test_client()


@pytest.fixture
def runner(app):
    """应用程序 Click 命令的测试运行程序。"""
    return app.test_cli_runner()


class AuthActions:
    def __init__(self, client):
        self._client = client

    def login(self, username="test", password="test"):
        return self._client.post(
            "/auth/login", data={"username": username, "password": password}
        )

    def logout(self):
        return self._client.get("/auth/logout")


@pytest.fixture
def auth(client):
    return AuthActions(client)
