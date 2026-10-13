import sqlite3
from datetime import datetime

import click
from flask import current_app
from flask import g


def get_db():
    """连接到应用程序的配置数据库。该连接对于每个请求都是唯一的，如果再次调用，该连接将
    被重用。
    """
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"], detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row

    return g.db


def close_db(e=None):
    """如果此请求连接到数据库，则关闭连接。"""
    db = g.pop("db", None)

    if db is not None:
        db.close()


def init_db():
    """清除现有数据并创建新表。"""
    db = get_db()

    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf8"))


@click.command("init-db")
def init_db_command():
    """清除现有数据并创建新表。"""
    init_db()
    click.echo("Initialized the database.")


sqlite3.register_converter("timestamp", lambda v: datetime.fromisoformat(v.decode()))


def init_app(app):
    """使用 Flask 应用程序注册数据库函数。这是由应用程序工厂调用的。"""
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
