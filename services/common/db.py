"""MySQL / Redis 连接封装。

容器启动顺序靠 depends_on + healthcheck 保证，这里再叠一层等待重试，
避免服务先于中间件就绪时直接崩掉。
"""
import os
import time

import pymysql
import redis


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "mysql"),
    "port": _env_int("MYSQL_PORT", 3306),
    "user": os.getenv("MYSQL_USER", "mall"),
    "password": os.getenv("MYSQL_PASSWORD", "Mall@2024"),
    "database": os.getenv("MYSQL_DATABASE", "yunqi_mall"),
    "charset": "utf8mb4",
    "autocommit": True,
    "connect_timeout": 5,
    "cursorclass": pymysql.cursors.DictCursor,
}

_redis_client = None


def wait_for_mysql(retries: int = 60, delay: int = 2) -> bool:
    last_error = None
    for _ in range(retries):
        try:
            pymysql.connect(**MYSQL_CONFIG).close()
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(f"MySQL 在 {retries * delay}s 内不可用：{last_error}")


def query(sql: str, args=None, fetch: str = "all"):
    """执行 SQL。

    fetch: all -> list[dict] | one -> dict | none -> 影响行数
    """
    conn = pymysql.connect(**MYSQL_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            if fetch == "one":
                return cur.fetchone()
            if fetch == "none":
                return cur.rowcount
            return cur.fetchall()
    finally:
        conn.close()


def redis_client():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=_env_int("REDIS_PORT", 6379),
            db=_env_int("REDIS_DB", 0),
            decode_responses=True,
            socket_connect_timeout=5,
        )
    return _redis_client


def wait_for_redis(retries: int = 60, delay: int = 1) -> bool:
    client = redis_client()
    last_error = None
    for _ in range(retries):
        try:
            client.ping()
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(f"Redis 不可用：{last_error}")
