# Optional runtime image. The coordinator is normally run on the host so it
# can use the Docker socket and the Herdr session. Build the binary on the
# host, then:
#   docker build -f deploy/coordinator.Dockerfile -t proteus-coordinator coordinator/target/release
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates docker.io \
    && rm -rf /var/lib/apt/lists/*
COPY proteus /usr/local/bin/proteus
ENTRYPOINT ["proteus"]
