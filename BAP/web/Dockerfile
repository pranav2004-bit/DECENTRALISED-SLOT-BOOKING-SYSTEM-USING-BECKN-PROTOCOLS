# BAP Web — Next.js frontend. Multi-stage build, non-root runtime user, standalone output.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* vars are inlined into the client bundle at `next build` time, not read
# from the running container's environment — docker-compose's env_file has no effect
# here since it only applies at `docker run`, not `docker build`. Passed as build args
# from docker-compose.yml's build.args, same real values as .env for local dev.
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_WS_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_WS_BASE_URL=$NEXT_PUBLIC_WS_BASE_URL
RUN npm run build

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
# Next.js standalone mode binds to the container's specific network IP by default,
# not 0.0.0.0 — without this, the in-container healthcheck (which hits localhost)
# fails with ECONNREFUSED even though external requests via Docker's port mapping
# work fine. Found and fixed for real during Phase 1 Exit's full docker-compose test.
ENV HOSTNAME=0.0.0.0
RUN addgroup --system app && adduser --system --ingroup app app
COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER app

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3000/health', r => process.exit(r.statusCode===200?0:1)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]
