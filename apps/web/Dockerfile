ARG APP_ENVIRONMENT=local
ARG APP_BASE_URL=http://localhost:3000
ARG ADMIN_COOKIE_SECURE=false
ARG WEB_IDENTITY_PROVIDER=deny

FROM node:24.19.0-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS dependencies

ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages/api-client/package.json ./packages/api-client/package.json
RUN npm ci

FROM dependencies AS builder

ARG APP_ENVIRONMENT
ARG APP_BASE_URL
ARG ADMIN_COOKIE_SECURE
ARG WEB_IDENTITY_PROVIDER
ENV APP_ENVIRONMENT=${APP_ENVIRONMENT} \
    APP_BASE_URL=${APP_BASE_URL} \
    ADMIN_COOKIE_SECURE=${ADMIN_COOKIE_SECURE} \
    WEB_IDENTITY_PROVIDER=${WEB_IDENTITY_PROVIDER}

COPY apps/web ./apps/web
COPY packages/api-client ./packages/api-client
RUN npm run build --prefix apps/web

FROM node:24.19.0-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS runner

ARG APP_ENVIRONMENT
ARG APP_BASE_URL
ARG ADMIN_COOKIE_SECURE
ARG WEB_IDENTITY_PROVIDER
ENV ADMIN_COOKIE_SECURE=${ADMIN_COOKIE_SECURE} \
    APP_BASE_URL=${APP_BASE_URL} \
    APP_ENVIRONMENT=${APP_ENVIRONMENT} \
    WEB_IDENTITY_PROVIDER=${WEB_IDENTITY_PROVIDER} \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production \
    PORT=3000

WORKDIR /app

COPY --from=builder --chown=node:node /app/apps/web/.next/standalone ./
COPY --from=builder --chown=node:node /app/apps/web/.next/static ./apps/web/.next/static

USER node
WORKDIR /app/apps/web

EXPOSE 3000

CMD ["node", "server.js"]
