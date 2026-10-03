# UI image for docker/compose.yaml, profile `app` (plan §5). No PII. NOT BUILT YET.
# Needs the owner's approval of a Node base image (not on the approved install list); pin it by digest then.
# Installs exactly frontend/pnpm-lock.yaml; telemetry stays off through scripts/run-next.mjs.
FROM node:24-bookworm-slim

ENV NEXT_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 NODE_ENV=production
WORKDIR /app
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/.npmrc ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build
EXPOSE 3000
CMD ["node", "scripts/run-next.mjs", "start", "-H", "0.0.0.0", "-p", "3000"]
