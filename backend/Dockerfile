# 后端镜像：Python slim + 依赖 + 应用代码（密钥经 env_file/挂载注入，不进镜像）
# 构建上下文为项目根（docker-compose.yml 已配置），故路径带 backend/ 前缀
FROM python:3.11-slim

WORKDIR /app

# 换清华源加速依赖安装（slim 镜像无编译链，psycopg2-binary/其余全为纯轮子）
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY backend/app ./app
# init_db 供容器内初始化表结构（compose 启动命令先建表再起服务）
COPY scripts/db/init_db.py ./init_db.py

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
