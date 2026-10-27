# Образы закрепляются по sha256, чтобы сборка у проверяющих не зависела от того, что сегодня лежит под тегом.
# Узнать дайджест: docker buildx imagetools inspect <образ:тег> --format '{{json .Manifest.Digest}}'
# и дописать к FROM: node:22.22-alpine@sha256:… (то же для nginx).
ARG NODE_IMAGE=node:22.22-alpine
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.28-alpine

# Сборка: статические файлы SPA. Режим http (.env.production) — запросы идут в бэкенд через nginx.
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# Раздача: nginx без root (порт 8080), отдаёт SPA и проксирует /api/ в сервис api из docker-compose.
# Временные файлы и pid у этого образа в /tmp — в compose при read_only нужен tmpfs на /tmp.
FROM ${NGINX_IMAGE}
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
