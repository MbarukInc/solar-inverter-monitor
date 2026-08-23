# Keeps the small Alpine base this project already used, but on a maintained
# Python. The previous base, alpine:3.13, has been end-of-life since Nov 2022.
FROM python:3.12-alpine

# Unbuffered so log lines reach `docker logs` immediately instead of sitting in
# a pipe buffer; no .pyc churn on the Pi's SD card.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /monitor

COPY requirements.txt ./

# Every runtime dep ships a pure-Python wheel except msgpack (pulled in by
# influxdb), which has a musllinux wheel for arm64 but none for 32-bit armv7.
# The toolchain covers that case and is deleted again in the same layer, so it
# never reaches the final image -- unlike before, where gcc, python3-dev and
# musl-dev shipped to the Pi permanently.
RUN apk add --no-cache --virtual .build-deps gcc musl-dev python3-dev \
 && pip install --no-cache-dir -r requirements.txt \
 && apk del .build-deps

# The image now contains the application. There was previously no COPY at all:
# the container worked only because docker-compose bind-mounted the source over
# an empty WORKDIR, so the built image was not runnable on its own and there
# was nothing to roll back to.
COPY monitor.py ./
COPY ups/ ./ups/

# Runs as root deliberately: /dev/ttyUSB0 is passed through with its host
# ownership (root:dialout), and a non-root user would need a matching GID
# inside the container. That is a silent-failure risk not worth taking for a
# device on a home LAN with no inbound network surface.
CMD ["python3", "monitor.py"]
