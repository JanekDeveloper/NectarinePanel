ARG JAVA_IMAGE=eclipse-temurin:21-jdk
FROM ${JAVA_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV HOME=/build
WORKDIR /build
