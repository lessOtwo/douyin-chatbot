FROM rust:1.87-bookworm AS build
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY web ./web
ARG CARGO_BUILD_JOBS=1
RUN --mount=type=cache,id=douyin-chatbot-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=douyin-chatbot-target,target=/build/target,sharing=locked \
    cargo build --release --locked && cp target/release/douyin-chatbot-service /build/business-service

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home chatbot && mkdir /data && chown chatbot:chatbot /data
WORKDIR /app
COPY --from=build /build/business-service /usr/local/bin/business-service
USER chatbot
ENV BIND=0.0.0.0:8080 DATABASE_PATH=/data/business.sqlite ACCOUNTS_FILE=/app/config/accounts.json
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s CMD curl -fsS http://localhost:8080/health || exit 1
CMD ["business-service"]

