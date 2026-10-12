"""traffic-generator —— 模拟真实用户行为，持续为监控平台产生数据。

没有流量的大屏就是一排空图。这个容器按设定并发循环执行
「登录 → 浏览商品 → 下单 → 支付回调」这条完整用户旅程，
让 Prometheus 的指标、Grafana 的曲线、Alertmanager 的告警
都有真实、连续的数据来源。

所有请求都走 Nginx 网关，因此网关的 QPS / 连接数指标同样是真实的。
"""
import logging
import os
import random
import signal
import sys
import threading
import time

import requests

GATEWAY = os.getenv("GATEWAY_URL", "http://api-gateway").rstrip("/")
TPS = float(os.getenv("TPS", "6"))
USER_POOL = int(os.getenv("USER_POOL", "200"))
PASSWORD = os.getenv("SEED_PASSWORD", "Mall@123456")
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5"))
BURST_EVERY = int(os.getenv("BURST_EVERY_SECONDS", "300"))
BURST_DURATION = int(os.getenv("BURST_DURATION_SECONDS", "40"))
BURST_MULTIPLIER = int(os.getenv("BURST_MULTIPLIER", "4"))

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","service":"traffic-generator","msg":"%(message)s"}',
)
log = logging.getLogger("traffic-generator")

stop_event = threading.Event()
_lock = threading.Lock()
_stats = {"journeys": 0, "requests": 0, "errors": 0}
_concurrency = [max(1, int(TPS))]


def _bump(field: str, delta: int = 1) -> None:
    with _lock:
        _stats[field] += delta


def user_journey(session: requests.Session) -> None:
    """一次完整的用户旅程。"""
    uid = random.randint(1, USER_POOL)
    username = "yunqi_user_{:04d}".format(uid)

    # ① 登录（写入 Redis 在线集合，影响 active_users 指标）
    session.post(f"{GATEWAY}/api/user/login",
                 json={"username": username, "password": PASSWORD},
                 timeout=TIMEOUT)
    _bump("requests")

    # ② 浏览商品（优先命中 Redis 缓存）
    resp = session.get(f"{GATEWAY}/api/order/products", timeout=TIMEOUT)
    _bump("requests")
    products = (resp.json().get("data") or []) if resp.ok else []
    if not products:
        return

    # ③ 下单（触发 order-service → user-service / pay-service 的跨服务调用）
    product = random.choice(products)
    order_resp = session.post(
        f"{GATEWAY}/api/order/create",
        json={"user_id": uid, "product_id": product["id"],
              "quantity": random.choice([1, 1, 1, 2])},
        timeout=TIMEOUT,
    )
    _bump("requests")

    # ④ 支付回调（支付成功后 pay-service 会回写订单状态）
    pay_no = None
    if order_resp.ok:
        pay_no = ((order_resp.json().get("data") or {}).get("pay_no"))
    if pay_no:
        session.post(f"{GATEWAY}/api/pay/callback",
                     json={"pay_no": pay_no}, timeout=TIMEOUT)
        _bump("requests")

    _bump("journeys")


def worker(idx: int) -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": f"yunqi-traffic-generator/{idx}"})
    while not stop_event.is_set():
        extra = _concurrency[0]
        for _ in range(extra):
            if stop_event.is_set():
                break
            try:
                user_journey(session)
            except Exception as exc:  # noqa: BLE001
                _bump("errors")
                if _stats["errors"] % 50 == 1:
                    log.warning("请求失败(%d)：%s", _stats["errors"], exc)
        time.sleep(random.uniform(0.15, 0.6))


def burst_loop() -> None:
    """周期性放大并发，模拟大促/秒杀流量，让告警有机会真实触发。"""
    while not stop_event.wait(BURST_EVERY):
        log.info("进入流量高峰模拟，并发放大 %d 倍，持续 %ds",
                 BURST_MULTIPLIER, BURST_DURATION)
        _concurrency[0] = max(1, int(TPS)) * BURST_MULTIPLIER
        time.sleep(BURST_DURATION)
        _concurrency[0] = max(1, int(TPS))
        log.info("流量恢复正常水位")


def reporter() -> None:
    while not stop_event.wait(60):
        with _lock:
            log.info("累计用户旅程 %d 次，请求 %d 次，异常 %d 次，当前并发系数 %d",
                     _stats["journeys"], _stats["requests"],
                     _stats["errors"], _concurrency[0])


def _shutdown(signum, _frame):
    log.info("收到信号 %s，正在退出", signum)
    stop_event.set()


def main() -> int:
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # 等网关就绪
    for _ in range(60):
        try:
            if requests.get(f"{GATEWAY}/health", timeout=3).ok:
                break
        except requests.RequestException:
            time.sleep(2)
    else:
        log.error("网关 %s 不可达，退出", GATEWAY)
        return 1

    log.info("流量生成器启动：目标 TPS=%.1f，用户池=%d，网关=%s",
             TPS, USER_POOL, GATEWAY)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True)
               for i in range(max(2, int(TPS)))]
    for thread in threads:
        thread.start()
    threading.Thread(target=burst_loop, daemon=True).start()
    threading.Thread(target=reporter, daemon=True).start()

    while not stop_event.is_set():
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
