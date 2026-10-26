FROM rust:1.87-bookworm AS build
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY web ./web
ARG CARGO_BUILD_JOBS=1
ARG CARGO_REGISTRY_MIRROR=
RUN --mount=type=cache,id=douyin-chatbot-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=douyin-chatbot-target,target=/build/target,sharing=locked \
    if [ -n "$CARGO_REGISTRY_MIRROR" ]; then \
      printf '[source.crates-io]\nreplace-with = "server-mirror"\n[source.server-mirror]\nregistry = "%s"\n' "$CARGO_REGISTRY_MIRROR" > /usr/local/cargo/config.toml; \
    fi && cargo build --release --locked && cp target/release/douyin-chatbot-service /build/business-service

FROM debian:bookworm-slim
ARG DEBIAN_MIRROR_HOST=
RUN if [ -n "$DEBIAN_MIRROR_HOST" ]; then \
      sed -i "s|deb.debian.org|$DEBIAN_MIRROR_HOST|g" /etc/apt/sources.list.d/debian.sources; \
    fi && apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home chatbot && mkdir /data && chown chatbot:chatbot /data
WORKDIR /app
COPY --from=build /build/business-service /usr/local/bin/business-service
USER chatbot
ENV BIND=0.0.0.0:8080 DATABASE_PATH=/data/business.sqlite ACCOUNTS_FILE=/app/config/accounts.json
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s CMD curl -fsS http://localhost:8080/health || exit 1
CMD ["business-service"]

