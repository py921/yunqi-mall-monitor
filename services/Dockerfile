# 三个微服务共用同一个镜像，靠启动命令区分角色
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 国内构建加速 + 重试，避免网络抖动导致构建失败
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --trusted-host pypi.tuna.tsinghua.edu.cn \
        --retries 5 --timeout 60 \
        -r /app/requirements.txt

COPY . /app

EXPOSE 8001 8002 8003

CMD ["python", "-m", "order_service.app"]
