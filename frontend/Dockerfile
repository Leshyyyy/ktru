# Сборка: статические файлы SPA. VITE_API_MODE=http — запросы идут в настоящий бэкенд через nginx.
FROM node:22.22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# Раздача: nginx отдаёт SPA и проксирует /api/ в сервис api из docker-compose.
FROM nginx:1.28-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
