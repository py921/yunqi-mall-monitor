"""pay-service —— 云启商城支付域。

职责：创建支付单、接收第三方支付回调、回写订单状态。
支付成功后通过 HTTP 回调 order-service 更新订单，构成真实的服务依赖链，
这条链路的健康度完全由 downstream_* 指标刻画。
"""
import logging
import os
import random
import time
import uuid

from flask import jsonify, request
from prometheus_client import Counter, Gauge

from common.db import query, wait_for_mysql, wait_for_redis
from common.http_client import DownstreamError, call
from common.metrics import instrument
from common.web import create_app

SERVICE = "pay-service"
VERSION = os.getenv("APP_VERSION", "1.0.0")
ENV = os.getenv("APP_ENV", "dev")
PORT = int(os.getenv("PORT", "8002"))

# 创建支付单失败率（渠道初始化异常）
CREATE_FAIL_RATE = float(os.getenv("PAY_CREATE_FAIL_RATE", "0.012"))
# 回调失败率（银行/渠道侧失败），调高会让「支付成功率」告警真实触发
CALLBACK_FAIL_RATE = float(os.getenv("PAY_CALLBACK_FAIL_RATE", "0.035"))
MIN_LATENCY = float(os.getenv("PAY_MIN_LATENCY", "0.02"))
MAX_LATENCY = float(os.getenv("PAY_MAX_LATENCY", "0.12"))

PAYMENTS_TOTAL = Counter(
    "payments_total", "支付单创建总数", ["service", "status"]
)
PAYMENT_AMOUNT_YUAN_TOTAL = Counter(
    "payment_amount_yuan_total", "累计支付金额（元）", ["service"]
)
PAYMENT_CALLBACKS_TOTAL = Counter(
    "payment_callbacks_total", "支付回调处理总数", ["service", "status"]
)
PAYMENT_CHANNEL_TOTAL = Counter(
    "payment_channel_total", "各支付渠道下单数", ["service", "channel"]
)
PAYMENT_PENDING = Gauge(
    "payment_pending_count", "待支付的支付单数量", ["service"]
)

app = create_app(SERVICE, VERSION, ENV)
logger: logging.Logger = app.extensions["logger"]

wait_for_mysql()
wait_for_redis()
logger.info("pay-service 就绪", extra={
    "extra_fields": {"version": VERSION, "env": ENV, "port": PORT}})


def _fake_work() -> None:
    time.sleep(random.uniform(MIN_LATENCY, MAX_LATENCY))


@app.route("/api/pay/create", methods=["POST"])
@instrument(SERVICE)
def create_payment():
    """创建支付单，返回渠道支付单号。"""
    body = request.get_json(silent=True) or {}
    order_no = body.get("order_no")
    user_id = body.get("user_id")
    channel = str(body.get("channel") or "ALIPAY").upper()
    try:
        amount = float(body.get("amount_yuan") or 0)
    except (TypeError, ValueError):
        amount = 0.0

    if not order_no or amount <= 0:
        PAYMENTS_TOTAL.labels(SERVICE, "failed").inc()
        return jsonify(code=400, message="订单号或金额非法"), 400

    _fake_work()
    if random.random() < CREATE_FAIL_RATE:
        PAYMENTS_TOTAL.labels(SERVICE, "failed").inc()
        return jsonify(code=500, message="支付渠道初始化失败，请重试"), 500

    pay_no = "PAY{}{}".format(
        time.strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:6].upper()
    )
    query(
        "INSERT INTO t_payment (pay_no, order_no, user_id, amount_yuan, channel, "
        "status) VALUES (%s, %s, %s, %s, %s, 'PENDING')",
        (pay_no, order_no, user_id, amount, channel), fetch="none",
    )

    PAYMENTS_TOTAL.labels(SERVICE, "success").inc()
    PAYMENT_AMOUNT_YUAN_TOTAL.labels(SERVICE).inc(amount)
    PAYMENT_CHANNEL_TOTAL.labels(SERVICE, channel).inc()
    PAYMENT_PENDING.labels(SERVICE).inc()

    return jsonify(code=0, message="支付单已创建", data={
        "pay_no": pay_no, "order_no": order_no, "amount_yuan": amount,
        "channel": channel, "status": "PENDING",
    })


@app.route("/api/pay/callback", methods=["POST"])
@instrument(SERVICE)
def pay_callback():
    """接收第三方支付结果回调。成功时回写订单状态。"""
    body = request.get_json(silent=True) or {}
    pay_no = body.get("pay_no")
    row = query(
        "SELECT pay_no, order_no, amount_yuan, status FROM t_payment WHERE pay_no=%s",
        (pay_no,), fetch="one",
    )
    if not row:
        PAYMENT_CALLBACKS_TOTAL.labels(SERVICE, "failed").inc()
        return jsonify(code=404, message="支付单不存在"), 404

    _fake_work()
    if random.random() < CALLBACK_FAIL_RATE:
        query("UPDATE t_payment SET status='FAILED' WHERE pay_no=%s",
              (pay_no,), fetch="none")
        PAYMENT_CALLBACKS_TOTAL.labels(SERVICE, "failed").inc()
        PAYMENT_PENDING.labels(SERVICE).dec()
        return jsonify(code=200, message="支付失败", data={
            "pay_no": pay_no, "order_no": row["order_no"], "status": "FAILED"})

    query("UPDATE t_payment SET status='SUCCESS' WHERE pay_no=%s",
          (pay_no,), fetch="none")
    PAYMENT_CALLBACKS_TOTAL.labels(SERVICE, "success").inc()
    PAYMENT_PENDING.labels(SERVICE).dec()

    # 回写订单状态 —— 跨服务调用 order-service
    order_synced = False
    try:
        call(SERVICE, "order-service", "PATCH",
             f"/api/order/{row['order_no']}/status", {"status": "PAID"})
        order_synced = True
    except DownstreamError as exc:
        logger.warning("订单状态回写失败，等待对账补偿", extra={
            "extra_fields": {"order_no": row["order_no"], "reason": str(exc)}})

    return jsonify(code=0, message="支付成功", data={
        "pay_no": pay_no, "order_no": row["order_no"],
        "amount_yuan": float(row["amount_yuan"]),
        "status": "SUCCESS", "order_synced": order_synced,
    })


@app.route("/api/pay/detail/<pay_no>", methods=["GET"])
@instrument(SERVICE)
def payment_detail(pay_no: str):
    row = query(
        "SELECT pay_no, order_no, user_id, amount_yuan, channel, status, "
        "created_at FROM t_payment WHERE pay_no=%s",
        (pay_no,), fetch="one",
    )
    if not row:
        return jsonify(code=404, message="支付单不存在"), 404
    row["amount_yuan"] = float(row["amount_yuan"])
    row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return jsonify(code=0, data=row)


@app.route("/api/pay/stats/today", methods=["GET"])
@instrument(SERVICE)
def today_stats():
    row = query(
        "SELECT COUNT(*) AS pay_count, "
        "IFNULL(SUM(CASE WHEN status='SUCCESS' THEN amount_yuan END), 0) AS paid_yuan "
        "FROM t_payment WHERE DATE(created_at) = CURDATE()",
        fetch="one",
    )
    return jsonify(code=0, data={
        "pay_count": row["pay_count"],
        "paid_yuan": float(row["paid_yuan"]),
    })


@app.route("/api/pay/channels", methods=["GET"])
@instrument(SERVICE)
def channel_breakdown():
    rows = query(
        "SELECT channel, COUNT(*) AS total FROM t_payment "
        "GROUP BY channel ORDER BY total DESC"
    )
    return jsonify(code=0, data=rows)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
