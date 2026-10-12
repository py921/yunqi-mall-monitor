# 云启商城（YunQi Mall）微服务监控平台

> 把「订单 / 支付 / 用户」三个微服务与 MySQL、Redis、Nginx 网关的全链路指标，  
> 统一采集到 Prometheus，用 Grafana 三个大屏分层呈现，并由 Alertmanager 按 P0/P1/P2 分级告警。  
> 所有服务用 Docker Compose 一键拉起，内置流量生成器保证指标持续有真实数据。

---

## 一、能力概览

| 能力       | 实现                                                                |
| -------- | ----------------------------------------------------------------- |
| 微服务架构    | order-service / pay-service / user-service 三个独立服务，各自暴露 `/metrics` |
| 统一入口     | Nginx 反向代理，微服务端口不对外暴露                                             |
| 服务间调用可观测 | 自研 `downstream_*` 指标，记录「谁调谁、成功与否、耗时多少」                            |
| 分层监控     | 接入层（Nginx）+ 微服务层（3 服务）+ 中间件层（MySQL / Redis）                       |
| 服务发现     | Prometheus `file_sd`，新增服务只改 JSON，无需重启                             |
| 可视化      | Grafana 3 个大屏：微服务总览、业务总览、中间件与网关                                   |
| 告警分级     | P0（10s 内发出）/ P1 / P2，含告警抑制规则防风暴                                   |
| 一键部署     | Docker Compose，14 个容器编排完成                                         |

---

## 二、架构

```text
                    ┌───────────────────────────────┐
                    │      traffic-generator        │
                    │   （模拟真实用户会话流量）      │
                    └───────────────┬───────────────┘
                                    │ HTTP
                    ┌───────────────▼───────────────┐
                    │     api-gateway  (Nginx :80)  │
                    │     统一入口 / 反代 / stub_status│
                    └───┬───────────┬───────────┬───┘
                        │           │           │
          ┌─────────────▼──┐  ┌─────▼──────┐  ┌─▼──────────────┐
          │ order-service  │  │ pay-service│  │  user-service  │
          │      :8001     │  │    :8002   │  │      :8003     │
          │  订单/商品/GMV  │  │  支付/回调  │  │ 登录/注册/在线  │
          └────────┬───────┘  └─────┬──────┘  └─┬──────────────┘
                   │  ①校验用户      │  ②回写订单状态
                   │  ──────────────►│  ──────────────►
                   │                │
          ┌────────▼────────────────▼────────────────────┐
          │            MySQL 8.0        Redis 7           │
          │      订单/支付/用户/商品      在线集合/商品缓存  │
          └────────┬────────────────────┬────────────────┘
                   │                    │
          ┌────────▼───────┐   ┌────────▼─────────┐   ┌──────────────────┐
          │ mysqld-exporter│   │  redis-exporter  │   │ nginx-exporter   │
          └────────┬───────┘   └────────┬─────────┘   └────────┬─────────┘
                   └──────────┬─────────┴──────────────────────┘
                              │  scrape（file_sd 服务发现）
              ┌───────────────▼────────────────────┐
              │        Prometheus  (:9090)         │
              │   采集 + 规则评估 + 15 天保留        │
              └───────┬────────────────────┬───────┘
                      │                    │ alerts
        ┌─────────────▼──────┐   ┌─────────▼──────────────┐
        │   Grafana (:3000)  │   │ Alertmanager (:9093)   │
        │   3 个分层大屏      │   │ P0 / P1 / P2 分级路由   │
        └────────────────────┘   └─────────┬──────────────┘
                                           │ webhook
                                 ┌─────────▼──────────┐
                                 │   alert-webhook    │
                                 │ 落地告警 / 可转钉钉 │
                                 └────────────────────┘
```

---

## 三、快速开始

```bash
# 1. 准备环境变量
cp .env.example .env

# 2. 构建并启动（首次构建约 3~8 分钟，取决于网络）
docker compose up -d --build

# 3. 查看状态（等 mysql / redis healthcheck 通过后其余服务才会启动）
docker compose ps

# 4. 自检
make verify
```

启动完成后访问：

| 入口             | 地址                             | 账号            |
| -------------- | ------------------------------ | ------------- |
| API 网关         | <http://localhost>             | -             |
| **Grafana 大屏** | <http://localhost:3000>        | admin / admin |
| Prometheus     | <http://localhost:9090>        | -             |
| Alertmanager   | <http://localhost:9093>        | -             |
| 告警接收记录         | <http://localhost:9090/alerts> | -             |

> Grafana 的数据源和三个大屏都是**自动加载**的，登录后左侧菜单 → Dashboards → 云启商城监控。

---

## 四、服务与端口

| 容器                      | 镜像                        | 端口        | 说明             |
| ----------------------- | ------------------------- | --------- | -------------- |
| yunqi-api-gateway       | nginx:1.27-alpine         | 80        | API 网关，微服务唯一入口 |
| yunqi-order-service     | 自建                        | 8001（仅内网） | 订单域            |
| yunqi-pay-service       | 自建                        | 8002（仅内网） | 支付域            |
| yunqi-user-service      | 自建                        | 8003（仅内网） | 用户域            |
| yunqi-mysql             | mysql:8.0                 | 3306（仅内网） | 业务数据库          |
| yunqi-redis             | redis:7.2-alpine          | 6379（仅内网） | 缓存 + 在线集合      |
| yunqi-mysqld-exporter   | prom/mysqld-exporter      | 9104（仅内网） | MySQL 指标       |
| yunqi-redis-exporter    | oliver006/redis_exporter  | 9121（仅内网） | Redis 指标       |
| yunqi-nginx-exporter    | nginx/prometheus-exporter | 9113（仅内网） | 网关指标           |
| yunqi-prometheus        | prom/prometheus           | 9090      | 采集与存储          |
| yunqi-alertmanager      | prom/alertmanager         | 9093      | 告警路由           |
| yunqi-alert-webhook     | 自建                        | 8080（仅内网） | 告警落地           |
| yunqi-grafana           | grafana/grafana           | 3000      | 可视化            |
| yunqi-traffic-generator | 自建                        | -         | 流量生成           |

**为什么微服务端口不对外暴露**：生产环境里外部流量只能经网关进入，内部服务直接暴露会绕过网关的鉴权、限流与审计。调试时用  
`docker compose exec order-service curl -s localhost:8001/metrics` 即可。

---

## 五、接口清单

| 方法    | 路径                             | 说明                  |
| ----- | ------------------------------ | ------------------- |
| GET   | `/api/order/products`          | 商品列表（优先读 Redis 缓存）  |
| GET   | `/api/order/product/<id>`      | 商品详情                |
| POST  | `/api/order/create`            | 下单（跨服务调 user + pay） |
| GET   | `/api/order/detail/<order_no>` | 订单详情                |
| PATCH | `/api/order/<order_no>/status` | 订单状态回写（供支付服务调用）     |
| GET   | `/api/order/stats/today`       | 今日订单量与 GMV          |
| POST  | `/api/pay/create`              | 创建支付单               |
| POST  | `/api/pay/callback`            | 接收支付回调，成功时回写订单      |
| GET   | `/api/pay/detail/<pay_no>`     | 支付单详情               |
| GET   | `/api/pay/channels`            | 各支付渠道统计             |
| POST  | `/api/user/login`              | 登录（写入在线集合）          |
| POST  | `/api/user/register`           | 注册                  |
| GET   | `/api/user/<id>`               | 用户详情（供订单服务校验）       |
| GET   | `/api/user/active/count`       | 当前在线用户数             |
| GET   | `/api/user/stats/today`        | 用户总量与今日新增           |

所有服务额外提供 `/health`（健康检查）与 `/metrics`（Prometheus 指标）。

---

## 六、监控指标设计

### 6.1 命名规范

所有 HTTP 指标**不带服务前缀**，靠 `service` 标签区分实例。这样一条 PromQL 就能覆盖全部服务：

```promql
# 各服务 QPS —— 一条查询搞定，不需要为每个服务写一遍
sum by (service) (rate(http_requests_total{layer="microservice"}[1m]))
```

### 6.2 指标清单

| 指标                                    | 类型        | 标签                                | 含义               |
| ------------------------------------- | --------- | --------------------------------- | ---------------- |
| `http_requests_total`                 | Counter   | service, method, endpoint, status | 请求总数             |
| `http_request_duration_seconds`       | Histogram | service, endpoint                 | 请求耗时（用于 P95/P99） |
| `http_requests_inflight`              | Gauge     | service                           | 正在处理的请求数         |
| `downstream_requests_total`           | Counter   | caller, target, status            | **服务间调用总数**      |
| `downstream_request_duration_seconds` | Histogram | caller, target                    | **服务间调用耗时**      |
| `service_info`                        | Gauge     | service, version, env             | 服务元信息（恒为 1）      |
| `orders_total`                        | Counter   | service, status                   | 下单总数（成功/失败）      |
| `order_amount_yuan_total`             | Counter   | service                           | 累计成交金额 GMV       |
| `order_stock_shortage_total`          | Counter   | service, product_id               | 库存不足失败数          |
| `order_processing_count`              | Gauge     | service                           | 处理中的下单数          |
| `payments_total`                      | Counter   | service, status                   | 支付单创建数           |
| `payment_callbacks_total`             | Counter   | service, status                   | 支付回调处理数          |
| `payment_amount_yuan_total`           | Counter   | service                           | 累计支付金额           |
| `payment_channel_total`               | Counter   | service, channel                  | 各渠道支付数           |
| `payment_pending_count`               | Gauge     | service                           | 待支付单数            |
| `logins_total`                        | Counter   | service, status                   | 登录总数             |
| `login_failures_total`                | Counter   | service, reason                   | 登录失败原因分布         |
| `registrations_total`                 | Counter   | service                           | 注册总数             |
| `active_users`                        | Gauge     | service                           | 当前在线用户数          |

### 6.3 为什么要有 `downstream_*`

这是微服务监控区别于单体监控的核心。单看 `order-service` 的错误率，你只知道「下单在失败」；  
有了 `downstream_requests_total{caller="order-service", target="pay-service"}`，  
你才知道是**订单自己坏了**，还是**它依赖的支付服务坏了**。

对应面板：微服务总览大屏的「服务间调用成功率」，图例直接显示 `order-service → pay-service`。

### 6.4 服务发现方式

Prometheus 采集目标全部走 `file_sd`，配置文件在 `prometheus/targets/`：

- `services.json` —— 三个微服务
- `middleware.json` —— MySQL / Redis exporter
- `gateway.json` —— Nginx exporter

新增一个微服务只需往 `services.json` 追加一条记录（30 秒内自动生效），  
**不需要重启 Prometheus，也不需要改主配置**。

---

## 七、Grafana 大屏

| 大屏     | UID                         | 面板数 | 内容                                                                       |
| ------ | --------------------------- | --- | ------------------------------------------------------------------------ |
| 微服务总览  | `yunqi-service-overview`    | 9   | 在线服务数、总 QPS、整体错误率、整体 P99、各服务 QPS / 错误率 / P99、服务间调用成功率、采集目标健康明细           |
| 业务总览   | `yunqi-business-overview`   | 10  | 累计订单、GMV、支付成功率、在线用户、每分钟订单量、每分钟支付量、在线用户趋势、成交额速率、登录成功率、渠道分布                |
| 中间件与网关 | `yunqi-middleware-overview` | 10  | MySQL / Redis / 网关状态、Redis 内存使用率、MySQL QPS、连接数与慢查询、Redis 命中率、命令速率、网关请求速率 |

大屏 JSON 由脚本生成，不要手改：

```bash
make dashboards     # 等价于 python3 tools/gen_dashboards.py
```

> 这样做的原因：Grafana dashboard schema 字段极多，手写极易写出已被废弃的枚举值  
> （例如把 `fieldConfig.defaults.color.mode` 写成旧版的 `"none"`，会导致整个大屏白屏）。  
> 脚本让所有面板共用同一套字段模板，保证一致性。

---

## 八、告警规则

配置文件：`prometheus/rules/`，共 3 组 14 条规则。

### 服务与依赖（service.yml）

| 告警                        | 条件                                    | 级别 |
| ------------------------- | ------------------------------------- | -- |
| MicroserviceDown          | `up{layer="microservice"} == 0` 持续 1m | P0 |
| GatewayDown               | `up{layer="gateway"} == 0` 持续 1m      | P0 |
| ServiceHighErrorRate      | 5xx 占比 > 5% 持续 2m                     | P1 |
| ServiceHighLatencyP99     | P99 > 1s 持续 3m                        | P1 |
| DownstreamCallFailureHigh | 服务间调用失败率 > 10% 持续 2m                  | P1 |

### 业务（business.yml）

| 告警                    | 条件                | 级别 |
| --------------------- | ----------------- | -- |
| PaymentSuccessRateLow | 支付成功率 < 95% 持续 3m | P0 |
| OrderSuccessRateLow   | 下单成功率 < 90% 持续 3m | P1 |
| LoginFailureRateHigh  | 登录失败率 > 10% 持续 3m | P1 |
| ActiveUsersDrop       | 在线用户 < 30 持续 5m   | P1 |
| OrderAmountStall      | GMV 计数停滞但订单在增长    | P2 |

### 中间件（middleware.yml）

| 告警                  | 条件                         | 级别 |
| ------------------- | -------------------------- | -- |
| MySQLDown           | `up{service="mysql"} == 0` | P0 |
| RedisDown           | `up{service="redis"} == 0` | P0 |
| MySQLSlowQueryBurst | 慢查询 > 0.2 次/秒 持续 5m        | P1 |
| MySQLConnectionHigh | 连接使用率 > 80%                | P1 |
| RedisMemoryHigh     | 内存使用率 > 85%                | P1 |
| RedisHitRateLow     | 命中率 < 80%                  | P2 |

### 告警路由与抑制

`deploy/alertmanager/alertmanager.yml` 里做了两件事：

1. **分级路由**：P0 在 `group_wait: 10s` 就发出、每小时重复；P1/P2 合并后低频发送。
2. **告警抑制**：服务整体挂了（P0）时，抑制它在质量、延迟、依赖维度产生的一堆 P1/P2，  
   避免一次故障刷出几十条告警。这是生产环境的标准做法。

---

## 九、资源要求

**这套平台有 14 个容器，比单体监控吃资源得多，务必按下面的规格给虚拟机。**

| 组件                           | 稳态内存               |
| ---------------------------- | ------------------ |
| Linux 系统 + dockerd           | 400 ~ 600 MB       |
| MySQL 8.0                    | 350 ~ 500 MB       |
| Grafana                      | 200 ~ 300 MB       |
| Prometheus                   | 200 ~ 300 MB       |
| 三个 Python 微服务                | 250 ~ 350 MB       |
| Redis                        | 30 ~ 60 MB         |
| Nginx + 三个 exporter          | 60 ~ 100 MB        |
| alert-webhook                | 50 ~ 80 MB         |
| Alertmanager                 | 40 ~ 60 MB         |
| traffic-generator            | 50 ~ 80 MB         |
| **稳态合计**                     | **约 1.7 ~ 2.4 GB** |
| 首次 `compose up --build` 额外峰值 | **+400 ~ 700 MB**  |

| 配置       | 结论                        |
| -------- | ------------------------- |
| 2 GB     | ❌ 首次构建几乎必定被 OOM Killer 打断 |
| **3 GB** | ⚠️ 可运行，但首次构建要逐个构建镜像       |
| **4 GB** | ✅ **推荐**，稳态余量充足           |
| 8 GB     | 舒适，可再加 Loki、SkyWalking    |

- **磁盘**：建议 **30 GB**（系统 8G + 镜像 2.5G + MySQL 数据 + Prometheus 15 天 TSDB 约 1G + 构建缓存）。
- **CPU**：2 核可用，4 核体验更好（首次构建会明显快）。

**低内存环境的两个技巧**

1. 加 swap，能挡掉绝大多数 OOM：
   ```bash
   sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
   sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```
2. 分步构建，不要一次性 `--build`：
   ```bash
   docker compose build order-service      # 三个服务共用同一镜像，构建一次即可
   docker compose build traffic-generator
   docker compose up -d
   ```


## 十一、接入钉钉告警

1. 钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义（Webhook）。
2. 安全设置勾选「加签」或「自定义关键词」（建议关键词设为 `告警`）。
3. 把 Webhook 地址填进 `.env`：
   ```dotenv
   DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxxxxx
   ```
4. 重启告警接收器：
   ```bash
   docker compose up -d alert-webhook
   ```

`services/alert_webhook/app.py` 中的 `_send_to_dingtalk()` 已经写好 Markdown 消息体，  
按等级加不同前缀即可。若不配置，告警会写入容器日志并通过 `GET /alerts` 暴露。

---

## 十二、故障演练与复盘（强烈建议做）

这是本项目**最有简历价值的部分**，大部分应届生交不出来。依次执行并记录数据：

| 演练     | 操作                                  | 观察点                                  |
| ------ | ----------------------------------- | ------------------------------------ |
| 服务宕机   | `docker compose stop pay-service`   | 多久触发 `MicroserviceDown`？订单服务报错率是否上升？ |
| 下游依赖故障 | 把 `PAY_CALLBACK_FAIL_RATE` 调到 `0.5` | `DownstreamCallFailureHigh` 是否触发？    |
| 中间件宕机  | `docker compose stop redis`         | `RedisDown` 是否触发？商品接口是否回源 MySQL？     |
| 网关故障   | `docker compose stop api-gateway`   | `GatewayDown` 是否触发？                  |
| 慢查询    | 手工执行 `SELECT SLEEP(1)` 循环           | `MySQLSlowQueryBurst` 是否触发？          |
| 流量洪峰   | 把 `GENERATOR_TPS` 调到 `60`           | P99 是否上升？告警是否被抑制规则正确收敛？              |

每次记录：**触发时间 / 恢复时间 / 是否漏报 / 告警是否刷屏**，整理成一份故障复盘文档放进仓库  
（`docs/fault-drills.md`），面试时可以主动拿出来讲。

---

## 十三、排查手册

**Q：`docker compose up` 卡在某个镜像拉取，报 `TLS handshake timeout`？**  
A：配置镜像加速，并**同时给 Docker 指定 DNS**（Ubuntu 宿主机的 `127.0.0.53` 在容器里不可用，  
会导致构建阶段解析超时）：

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": ["https://docker.1ms.run", "https://docker.1panel.live"],
  "dns": ["223.5.5.5", "119.29.29.29", "8.8.8.8"]
}
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
docker run --rm alpine cat /etc/resolv.conf     # 必须出现 223.5.5.5
```

**Q：`mysql` 容器一直 unhealthy？**  
A：首次初始化要建表 + 灌 200 条种子数据，需要 30~60 秒，属正常。若超过 2 分钟，  
`docker compose logs mysql` 查看。低内存机器容易被 OOM，参见第九节。

**Q：Grafana 大屏打开报 `An unexpected error happened`？**  
A：dashboard JSON 里有非法枚举值（最常见的是 `color.mode`）。  
合法取值只有 `fixed / shades / thresholds / palette-classic / palette-classic-by-name / continuous-*`。  
本项目的大屏由脚本生成，正常不会出现；若手工改过，用 `make dashboards` 重新生成。

**Q：Prometheus 里某个 target 是 DOWN？**  
A：访问 <http://localhost:9090/targets> 看具体报错。常见原因：该服务还没通过 healthcheck  
（等 30 秒）、容器名写错、端口写错。

**Q：大屏全是 No data？**  
A：① 确认 `traffic-generator` 在运行；② 确认 Prometheus targets 全绿；  
③ 指标名有拼写错误 —— 用 `docker compose exec order-service curl -s localhost:8001/metrics | head -50`  
核对真实指标名。

**Q：改了 `deploy/mysql/init/*.sql` 但没生效？**  
A：初始化脚本**只在数据卷为空时执行**。需要 `make reset` 后重新 `make up`。

---

## 十四、目录结构

```text
yunqi-mall-monitor/
├── docker-compose.yml              # 14 个容器的编排
├── .env.example                    # 环境变量模板
├── Makefile                        # 常用操作入口
├── services/                       # 微服务（共用一个镜像）
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── common/                     # 公共组件
│   │   ├── metrics.py              #   统一指标定义 + 视图装饰器
│   │   ├── db.py                   #   MySQL / Redis 连接
│   │   ├── http_client.py          #   带打点的服务间调用
│   │   ├── logging_conf.py         #   JSON 结构化日志
│   │   └── web.py                  #   健康检查 / 指标端点装配
│   ├── order_service/app.py
│   ├── pay_service/app.py
│   ├── user_service/app.py
│   └── alert_webhook/app.py        # 告警落地（可转钉钉）
├── deploy/
│   ├── nginx/                      # 网关配置
│   ├── mysql/init/01-schema.sql    # 建表 + 种子数据 + exporter 账号
│   └── alertmanager/alertmanager.yml
├── prometheus/
│   ├── prometheus.yml
│   ├── targets/                    # file_sd 服务发现
│   └── rules/                      # service / business / middleware 三组规则
├── grafana/
│   ├── provisioning/               # 数据源与大屏自动加载
│   └── dashboards/                 # 3 个大屏 JSON
├── traffic-generator/              # 流量生成器
├── tools/gen_dashboards.py         # 大屏生成脚本
└── docs/
    └── RESUME.md                   # 简历措辞与面试话术
```

---

## 十五、许可

本项目为个人学习与作品集用途，可自由用于简历展示与技术交流。
