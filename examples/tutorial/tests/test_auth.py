import pytest
from flask import g
from flask import session

from flaskr.db import get_db


def test_register(client, app):
    # 测试查看页面呈现时是否没有模板错误
    assert client.get("/auth/register").status_code == 200

    # 测试注册成功是否重定向到登录页面
    response = client.post("/auth/register", data={"username": "a", "password": "a"})
    assert response.headers["Location"] == "/auth/login"

    # 测试用户是否已插入数据库
    with app.app_context():
        assert (
            get_db().execute("SELECT * FROM user WHERE username = 'a'").fetchone()
            is not None
        )


@pytest.mark.parametrize(
    ("username", "password", "message"),
    (
        ("", "", b"Username is required."),
        ("a", "", b"Password is required."),
        ("test", "test", b"already registered"),
    ),
)
def test_register_validate_input(client, username, password, message):
    response = client.post(
        "/auth/register", data={"username": username, "password": password}
    )
    assert message in response.data


def test_login(client, auth):
    # 测试查看页面呈现时是否没有模板错误
    assert client.get("/auth/login").status_code == 200

    # 测试成功登录重定向到索引页
    response = auth.login()
    assert response.headers["Location"] == "/"

    # 登录请求在会话中设置 user_id 检查用户是否从会话中加载
    with client:
        client.get("/")
        assert session["user_id"] == 1
        assert g.user["username"] == "test"


@pytest.mark.parametrize(
    ("username", "password", "message"),
    (("a", "test", b"Incorrect username."), ("test", "a", b"Incorrect password.")),
)
def test_login_validate_input(auth, username, password, message):
    response = auth.login(username, password)
    assert message in response.data


def test_logout(client, auth):
    auth.login()

    with client:
        auth.logout()
        assert "user_id" not in session
