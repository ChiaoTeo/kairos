FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /opt/kairos

COPY . /opt/kairos
RUN python -m pip install --no-cache-dir -e .

COPY examples/deploy/live-run/docker/entrypoint.sh /usr/local/bin/kairos-live-run
RUN chmod +x /usr/local/bin/kairos-live-run

VOLUME ["/opt/kairos/.kairos", "/var/lib/kairos"]
ENTRYPOINT ["/usr/local/bin/kairos-live-run"]
