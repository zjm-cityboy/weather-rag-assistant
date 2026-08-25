# 前端镜像：两阶段构建（node 打包 → nginx 托管静态文件并反代 /api 到后端）
FROM node:20-alpine AS build
WORKDIR /app

COPY package*.json ./
# 国内镜像源加速依赖安装
RUN npm ci --registry=https://registry.npmmirror.com

COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
