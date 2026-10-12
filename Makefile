.PHONY: help init up down restart ps logs clean reset dashboards verify targets

SHELL   := /bin/bash
COMPOSE := docker compose

help:
	@echo "云启商城（YunQi Mall）微服务监控平台"
	@echo ""
	@echo "  make init       复制 .env.example 为 .env（首次使用）"
	@echo "  make up         构建并启动全部服务"
	@echo "  make down       停止全部服务（保留数据）"
	@echo "  make reset      停止并清空所有数据卷（危险）"
	@echo "  make ps         查看容器状态"
	@echo "  make logs       跟踪全部服务日志"
	@echo "  make dashboards 重新生成 Grafana 大屏 JSON"
	@echo "  make verify     健康检查与采集目标自检"
	@echo "  make targets    查看 Prometheus 抓取目标状态"

init:
	@test -f .env || cp .env.example .env
	@echo ".env 已就绪"

up: init
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

reset:
	$(COMPOSE) down -v

restart:
	$(COMPOSE) restart

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100

dashboards:
	python3 tools/gen_dashboards.py

verify:
	@echo "== 网关健康 =="
	@curl -s -o /dev/null -w "  gateway      %{http_code}\n" http://localhost/health || true
	@echo "== 业务接口（经网关）=="
	@for p in /api/order/products /api/user/active/count /api/pay/channels; do \
		printf "  %-26s %s\n" "$$p" "$$(curl -s -o /dev/null -w '%{http_code}' http://localhost$$p)"; \
	done
	@echo "== 采集目标 up 状态 =="
	@curl -s "http://localhost:9090/api/v1/query?query=up" \
		| python3 -c "import sys,json;d=json.load(sys.stdin);[print('  %-28s %s' % (r['metric'].get('instance','?'), r['value'][1])) for r in d['data']['result']]" \
		|| echo "  （Prometheus 尚未就绪）"

targets:
	@curl -s http://localhost:9090/api/v1/targets \
		| python3 -c "import sys,json;d=json.load(sys.stdin);[print('  %-10s %-28s %s' % (t['labels'].get('job'), t['scrapeUrl'], t['health'])) for t in d['data']['activeTargets']]"
