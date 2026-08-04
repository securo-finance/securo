FROM node:22-slim AS build

WORKDIR /app

ARG VITE_APP_VERSION
ENV VITE_APP_VERSION=$VITE_APP_VERSION

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Serve with nginx
FROM nginxinc/nginx-unprivileged:alpine

# Dynamic Nginx DNS resolver fallback.
# This prevents Nginx from failing on boot in environments where NGINX_RESOLVER 
# is unset, while allowing PaaS to dynamically resolve upstream backends
# without hardcoding IPs or hitting 508 Loop Detected errors.
ENV NGINX_RESOLVER=127.0.0.11
ENV PROXY_HOST=\$host

COPY --from=build /app/dist /usr/share/nginx/html
COPY default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 8080
