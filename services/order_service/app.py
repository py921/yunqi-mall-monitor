"""order-service —— 云启商城订单域。

职责：商品查询、下单、订单详情、订单状态回写。
下单链路会跨服务调用 user-service（校验用户）与 pay-service（创建支付单），
两次调用都会被 downstream_* 指标完整记录。
"""
import json
import logging
import os
import random
import time
import uuid

from flask import jsonify, request
from prometheus_client import Counter, Gauge

from common.db import query, redis_client, wait_for_mysql, wait_for_redis
from common.http_client import DownstreamError, call
from common.metrics import instrument
from common.web import create_app

SERVICE = "order-service"
VERSION = os.getenv("APP_VERSION", "1.0.0")
ENV = os.getenv("APP_ENV", "dev")
PORT = int(os.getenv("PORT", "8001"))

# 模拟真实业务里无法避免的失败：库存不足 / 风控拦截 / 下游超时
FAIL_RATE = float(os.getenv("ORDER_FAIL_RATE", "0.06"))
DOWNSTREAM_FAIL_RATE = float(os.getenv("DOWNSTREAM_FAIL_RATE", "0.02"))
# 模拟业务处理耗时，让 P95/P99 有真实分布而不是一条直线
MIN_LATENCY = float(os.getenv("ORDER_MIN_LATENCY", "0.015"))
MAX_LATENCY = float(os.getenv("ORDER_MAX_LATENCY", "0.09"))

# --------------------------------------------------------------------------- #
# 业务指标
# --------------------------------------------------------------------------- #
ORDERS_TOTAL = Counter(
    "orders_total", "下单总数", ["service", "status"]
)
ORDER_AMOUNT_YUAN_TOTAL = Counter(
    "order_amount_yuan_total", "累计成交金额（元）", ["service"]
)
ORDER_STOCK_SHORTAGE_TOTAL = Counter(
    "order_stock_shortage_total", "因库存不足导致的下单失败数", ["service", "product_id"]
)
ORDER_PROCESSING = Gauge(
    "order_processing_count", "当前正在处理的下单请求数", ["service"]
)

app = create_app(SERVICE, VERSION, ENV)
logger: logging.Logger = app.extensions["logger"]

# 容器启动时先等中间件就绪，避免 depends_on 的时序空档
wait_for_mysql()
wait_for_redis()
logger.info("order-service 就绪", extra={
    "extra_fields": {"version": VERSION, "env": ENV, "port": PORT}})


def _fake_work() -> None:
    time.sleep(random.uniform(MIN_LATENCY, MAX_LATENCY))


# --------------------------------------------------------------------------- #
# 商品
# --------------------------------------------------------------------------- #
@app.route("/api/order/products", methods=["GET"])
@instrument(SERVICE)
def list_products():
    """商品列表：优先读 Redis 缓存，未命中回源 MySQL。"""
    client = redis_client()
    cache_key = "mall:products:list"
    cached = client.get(cache_key)
    if cached:
        return jsonify(code=0, source="redis",
                       data=json.loads(cached))

    rows = query(
        "SELECT id, name, category, price_yuan, stock FROM t_product ORDER BY id"
    )
    for row in rows:
        row["price_yuan"] = float(row["price_yuan"])
    client.setex(cache_key, 30, json.dumps(rows, ensure_ascii=False))
    return jsonify(code=0, source="mysql", total=len(rows), data=rows)


@app.route("/api/order/product/<int:product_id>", methods=["GET"])
@instrument(SERVICE)
def product_detail(product_id: int):
    row = query(
        "SELECT id, name, category, price_yuan, stock FROM t_product WHERE id=%s",
        (product_id,), fetch="one",
    )
    if not row:
        return jsonify(code=404, message="商品不存在"), 404
    row["price_yuan"] = float(row["price_yuan"])
    return jsonify(code=0, data=row)


# --------------------------------------------------------------------------- #
# 下单（核心链路）
# --------------------------------------------------------------------------- #
@app.route("/api/order/create", methods=["POST"])
@instrument(SERVICE)
def create_order():
    body = request.get_json(silent=True) or {}
    user_id = int(body.get("user_id") or random.randint(1, 200))
    product_id = int(body.get("product_id") or random.randint(1, 20))
    quantity = max(1, int(body.get("quantity") or 1))

    ORDER_PROCESSING.labels(SERVICE).inc()
    try:
        _fake_work()
        product = query(
            "SELECT id, name, price_yuan, stock FROM t_product WHERE id=%s",
            (product_id,), fetch="one",
        )
        if product is None:
            ORDERS_TOTAL.labels(SERVICE, "failed").inc()
            return jsonify(code=404, message="商品不存在"), 404

        # ① 校验用户 —— 跨服务调用 user-service
        if random.random() < DOWNSTREAM_FAIL_RATE:
            ORDERS_TOTAL.labels(SERVICE, "failed").inc()
            return jsonify(code=503, message="用户服务响应超时，请稍后重试"), 503
        call(SERVICE, "user-service", "GET", f"/api/user/{user_id}")

        # ② 库存校验 —— 模拟真实缺货
        if product["stock"] < quantity or random.random() < FAIL_RATE:
            ORDERS_TOTAL.labels(SERVICE, "failed").inc()
            ORDER_STOCK_SHORTAGE_TOTAL.labels(SERVICE, str(product_id)).inc()
            return jsonify(code=400, message="库存不足或未通过风控，下单失败"), 400

        amount = round(float(product["price_yuan"]) * quantity, 2)
        order_no = "YQ{}{}".format(
            time.strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:6].upper()
        )

        # ③ 落库：订单 + 扣减库存
        query(
            "INSERT INTO t_order (order_no, user_id, product_id, quantity, "
            "amount_yuan, status) VALUES (%s, %s, %s, %s, %s, 'CREATED')",
            (order_no, user_id, product_id, quantity, amount), fetch="none",
        )
        query("UPDATE t_product SET stock = stock - %s WHERE id = %s",
              (quantity, product_id), fetch="none")

        # ④ 创建支付单 —— 跨服务调用 pay-service，超时不影响下单主流程
        pay_no = None
        try:
            pay = call(SERVICE, "pay-service", "POST", "/api/pay/create",
                       {"order_no": order_no, "amount_yuan": amount,
                        "user_id": user_id, "channel": random.choice(
                            ["ALIPAY", "WECHAT", "UNIONPAY"])})
            pay_no = (pay.get("data") or {}).get("pay_no")
        except DownstreamError as exc:
            logger.warning("支付单创建失败，订单已生成待重试", extra={
                "extra_fields": {"order_no": order_no, "reason": str(exc)}})

        ORDERS_TOTAL.labels(SERVICE, "success").inc()
        ORDER_AMOUNT_YUAN_TOTAL.labels(SERVICE).inc(amount)
        return jsonify(code=0, message="下单成功", data={
            "order_no": order_no, "user_id": user_id,
            "product_id": product_id, "product_name": product["name"],
            "quantity": quantity, "amount_yuan": amount,
            "status": "CREATED", "pay_no": pay_no,
        })
    finally:
        ORDER_PROCESSING.labels(SERVICE).dec()


@app.route("/api/order/detail/<order_no>", methods=["GET"])
@instrument(SERVICE)
def order_detail(order_no: str):
    row = query(
        "SELECT order_no, user_id, product_id, quantity, amount_yuan, status, "
        "created_at FROM t_order WHERE order_no=%s",
        (order_no,), fetch="one",
    )
    if not row:
        return jsonify(code=404, message="订单不存在"), 404
    row["amount_yuan"] = float(row["amount_yuan"])
    row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return jsonify(code=0, data=row)


@app.route("/api/order/<order_no>/status", methods=["PATCH"])
@instrument(SERVICE)
def update_order_status(order_no: str):
    """供 pay-service 支付成功后回写订单状态。"""
    body = request.get_json(silent=True) or {}
    status = str(body.get("status") or "").upper()
    if status not in {"CREATED", "PAID", "CANCELLED", "REFUNDED"}:
        return jsonify(code=400, message="非法的订单状态"), 400
    affected = query("UPDATE t_order SET status=%s WHERE order_no=%s",
                     (status, order_no), fetch="none")
    if not affected:
        return jsonify(code=404, message="订单不存在"), 404
    return jsonify(code=0, message="订单状态已更新",
                   data={"order_no": order_no, "status": status})


@app.route("/api/order/stats/today", methods=["GET"])
@instrument(SERVICE)
def today_stats():
    row = query(
        "SELECT COUNT(*) AS order_count, "
        "IFNULL(SUM(amount_yuan), 0) AS gmv_yuan "
        "FROM t_order WHERE DATE(created_at) = CURDATE()",
        fetch="one",
    )
    return jsonify(code=0, data={
        "order_count": row["order_count"],
        "gmv_yuan": float(row["gmv_yuan"]),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
