# PREPARED ONLY. Build on approved Linux/Kali, never on this Windows host.
# Operator supplies a resolved python:3.14.3-slim digest after registry lookup.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
WORKDIR /opt/recipe
COPY worldfuzz-0.4.0rc6-py3-none-any.whl /tmp/
ARG WHEEL_SHA256
RUN test -n "$WHEEL_SHA256" && echo "$WHEEL_SHA256  /tmp/worldfuzz-0.4.0rc6-py3-none-any.whl" | sha256sum -c - \
 && pip install --no-cache-dir /tmp/worldfuzz-0.4.0rc6-py3-none-any.whl \
 && sha256sum /tmp/worldfuzz-0.4.0rc6-py3-none-any.whl > installed-wheel.sha256 \
 && pip freeze > installed-packages.txt \
 && rm /tmp/worldfuzz-0.4.0rc6-py3-none-any.whl \
 && useradd --uid 10001 --create-home worldfuzz
COPY competition.py /opt/recipe/
USER 10001:10001
# No credentials, public HTTP listener, external corpus or always-on endpoint.
CMD ["python", "/opt/recipe/competition.py", "--help"]
