# Pin both the Python release and the already verified release artifact. The
# resulting image digest must be recorded by the later Kali/Linux acceptance.
FROM python:3.14.3-slim
WORKDIR /opt/worldfuzz
ARG WORLDFUZZ_WHEEL=dist/worldfuzz-0.4.0rc6-py3-none-any.whl
COPY ${WORLDFUZZ_WHEEL} /tmp/
RUN set -eu; wheel="/tmp/${WORLDFUZZ_WHEEL##*/}"; \
    sha256sum "$wheel" > /opt/worldfuzz/installed-wheel.sha256; \
    pip install --no-cache-dir "$wheel"; \
    rm "$wheel"
RUN useradd --uid 10001 --create-home worldfuzz
USER 10001:10001
ENTRYPOINT ["python", "-m", "worldfuzz", "platform"]
