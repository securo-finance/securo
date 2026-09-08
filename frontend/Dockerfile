FROM node:22-slim AS build

WORKDIR /app

ARG VITE_APP_VERSION
ENV VITE_APP_VERSION=$VITE_APP_VERSION

COPY package.json package-lock.json .npmrc ./
RUN npm ci

COPY . .
RUN npm run build

# Serve with nginx
FROM nginxinc/nginx-unprivileged:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY default.conf.template /etc/nginx/templates/default.conf.template

# Have the image derive the DNS resolver from /etc/resolv.conf, so the same
# image proxies to the backend on Docker, Podman, Kubernetes and Railway with
# no per-platform configuration. 19-resolver.envsh turns that into
# NGINX_RESOLVER (with a Docker fallback) for the config template; setting
# NGINX_RESOLVER explicitly still overrides both.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
COPY --chmod=755 docker-entrypoint.d/19-resolver.envsh /docker-entrypoint.d/

EXPOSE 8080
