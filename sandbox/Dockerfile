FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt && mkdir -m 0711 /jobs
COPY server.py /app/server.py
# 服务进程以 root 运行，只为给每个任务切换到独立的 uid；脚本本身一律以 10001+ 的无权限 uid 执行。
# compose 里去掉了全部 capability，只加回 CHOWN / DAC_OVERRIDE / SETUID / SETGID
EXPOSE 8901
CMD ["python", "-u", "/app/server.py"]
