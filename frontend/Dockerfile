FROM node:22-slim AS build

WORKDIR /app

ARG VITE_APP_VERSION
ENV VITE_APP_VERSION=$VITE_APP_VERSION

ARG VITE_FINTOC_PUBLIC_KEY
ENV VITE_FINTOC_PUBLIC_KEY=$VITE_FINTOC_PUBLIC_KEY

ARG VITE_FINTOC_WEBHOOK_URL
ENV VITE_FINTOC_WEBHOOK_URL=$VITE_FINTOC_WEBHOOK_URL

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Serve with nginx
FROM nginxinc/nginx-unprivileged:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 8080
