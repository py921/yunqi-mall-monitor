"""user-service —— 云启商城用户域。

职责：注册、登录、用户查询、在线用户统计。
在线状态用 Redis ZSET 维护（score = 最近活跃时间戳），
这样「在线用户数」会随真实流量自然涨落，而不是一个写死的常量。
"""
import hashlib
import logging
import os
import random
import threading
import time
import uuid

from flask import jsonify, request
from prometheus_client import Counter, Gauge

from common.db import query, redis_client, wait_for_mysql, wait_for_redis
from common.metrics import instrument
from common.web import create_app

SERVICE = "user-service"
VERSION = os.getenv("APP_VERSION", "1.0.0")
ENV = os.getenv("APP_ENV", "dev")
PORT = int(os.getenv("PORT", "8003"))

ONLINE_KEY = "mall:online_users"
ONLINE_WINDOW = int(os.getenv("ONLINE_WINDOW_SECONDS", "300"))
DEFAULT_PASSWORD = os.getenv("SEED_PASSWORD", "Mall@123456")

LOGIN_FAIL_RATE = float(os.getenv("LOGIN_FAIL_RATE", "0.03"))
MIN_LATENCY = float(os.getenv("USER_MIN_LATENCY", "0.008"))
MAX_LATENCY = float(os.getenv("USER_MAX_LATENCY", "0.06"))

LOGINS_TOTAL = Counter(
    "logins_total", "登录总数", ["service", "status"]
)
LOGIN_FAILURES_TOTAL = Counter(
    "login_failures_total", "登录失败原因分布", ["service", "reason"]
)
REGISTRATIONS_TOTAL = Counter(
    "registrations_total", "注册总数", ["service"]
)
ACTIVE_USERS = Gauge(
    "active_users", "当前在线用户数", ["service"]
)

app = create_app(SERVICE, VERSION, ENV)
logger: logging.Logger = app.extensions["logger"]

wait_for_mysql()
wait_for_redis()
logger.info("user-service 就绪", extra={
    "extra_fields": {"version": VERSION, "env": ENV, "port": PORT}})


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _fake_work() -> None:
    time.sleep(random.uniform(MIN_LATENCY, MAX_LATENCY))


def _refresh_online_loop() -> None:
    """后台线程：清理过期在线记录并刷新 active_users 指标。"""
    client = redis_client()
    while True:
        try:
            threshold = time.time() - ONLINE_WINDOW
            client.zremrangebyscore(ONLINE_KEY, "-inf", threshold)
            ACTIVE_USERS.labels(SERVICE).set(client.zcard(ONLINE_KEY))
        except Exception as exc:  # noqa: BLE001
            logger.warning("在线用户刷新失败", extra={
                "extra_fields": {"reason": str(exc)}})
        time.sleep(10)


threading.Thread(target=_refresh_online_loop, daemon=True).start()


@app.route("/api/user/login", methods=["POST"])
@instrument(SERVICE)
def login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "")
    password = str(body.get("password") or DEFAULT_PASSWORD)

    _fake_work()
    row = query(
        "SELECT id, username, password_hash FROM t_user WHERE username=%s",
        (username,), fetch="one",
    )
    if row is None:
        LOGINS_TOTAL.labels(SERVICE, "failed").inc()
        LOGIN_FAILURES_TOTAL.labels(SERVICE, "user_not_found").inc()
        return jsonify(code=401, message="用户名或密码错误"), 401
    if row["password_hash"] != _hash(password) or random.random() < LOGIN_FAIL_RATE:
        LOGINS_TOTAL.labels(SERVICE, "failed").inc()
        LOGIN_FAILURES_TOTAL.labels(SERVICE, "bad_credentials").inc()
        return jsonify(code=401, message="用户名或密码错误"), 401

    redis_client().zadd(ONLINE_KEY, {str(row["id"]): time.time()})
    LOGINS_TOTAL.labels(SERVICE, "success").inc()
    return jsonify(code=0, message="登录成功", data={
        "user_id": row["id"], "username": row["username"],
        "token": uuid.uuid4().hex,
    })


@app.route("/api/user/register", methods=["POST"])
@instrument(SERVICE)
def register():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip()
    phone = str(body.get("phone") or "")
    if len(username) < 3:
        return jsonify(code=400, message="用户名至少 3 个字符"), 400

    _fake_work()
    exists = query("SELECT id FROM t_user WHERE username=%s", (username,), fetch="one")
    if exists:
        return jsonify(code=409, message="用户名已存在"), 409

    query(
        "INSERT INTO t_user (username, phone, password_hash) VALUES (%s, %s, %s)",
        (username, phone, _hash(DEFAULT_PASSWORD)), fetch="none",
    )
    REGISTRATIONS_TOTAL.labels(SERVICE).inc()
    row = query("SELECT id FROM t_user WHERE username=%s", (username,), fetch="one")
    return jsonify(code=0, message="注册成功",
                   data={"user_id": row["id"], "username": username})


@app.route("/api/user/<int:user_id>", methods=["GET"])
@instrument(SERVICE)
def user_detail(user_id: int):
    row = query(
        "SELECT id, username, phone, level, created_at FROM t_user WHERE id=%s",
        (user_id,), fetch="one",
    )
    if not row:
        return jsonify(code=404, message="用户不存在"), 404
    row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return jsonify(code=0, data=row)


@app.route("/api/user/active/count", methods=["GET"])
@instrument(SERVICE)
def active_count():
    client = redis_client()
    online = client.zcount(ONLINE_KEY, time.time() - ONLINE_WINDOW, "+inf")
    return jsonify(code=0, data={
        "active_users": online,
        "window_seconds": ONLINE_WINDOW,
    })


@app.route("/api/user/stats/today", methods=["GET"])
@instrument(SERVICE)
def today_stats():
    row = query(
        "SELECT COUNT(*) AS total_users, "
        "SUM(CASE WHEN DATE(created_at) = CURDATE() THEN 1 ELSE 0 END) AS new_users "
        "FROM t_user",
        fetch="one",
    )
    return jsonify(code=0, data={
        "total_users": row["total_users"],
        "new_users_today": int(row["new_users"] or 0),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
