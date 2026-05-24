# AXIOM Autonomous Coding Agent — sandbox image.
#
# This image is the per-task sandbox the AutonomousAgent spawns. It is
# NOT the orchestrator — the orchestrator runs on the host and calls
# `docker exec` into containers built from this image. Network is
# disabled at run time (`--network none`); a pre-vetted /wheels
# directory replaces live PyPI access.
#
# Build:
#   docker build -t orivael/axiom-autonomous:local \
#     -f deploy/autonomous/Dockerfile .
#
# The host orchestrator passes the workdir as a bind-mount at /work and
# the repo root read-only at /repo. The container itself is read-only
# except for /work + /tmp.

FROM python:3.11-slim AS runtime

# Non-root user — every action inside the container runs as `axiom`.
RUN groupadd -r axiom && useradd -r -g axiom -d /work -s /sbin/nologin axiom

# System packages that the agent's tool inventory depends on. Keep
# this list minimal so the attack surface stays small.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        git \
 && rm -rf /var/lib/apt/lists/*

# Python deps the runtime needs. `pytest` + `pytest-json-report` cover
# the run_tests tool. Anything else the agent's generated code wants
# to import must already be installed here (no live PyPI).
RUN pip install --no-cache-dir \
        pytest==8.* \
        pytest-json-report==1.* \
        requests==2.* \
        pyyaml==6.*

# Tool runner — the tiny script `docker exec`d for every tool call.
# (See deploy/autonomous/tool_runner.py for the source.)
COPY deploy/autonomous/tool_runner.py /usr/local/bin/axiom_tool_runner
RUN chmod 0555 /usr/local/bin/axiom_tool_runner

# Pre-vetted wheelhouse. Anything the agent's pip install wants must
# be staged here at build time. Empty by default — operators populate
# it for their workload.
RUN mkdir -p /wheels
COPY deploy/autonomous/wheels/ /wheels/

WORKDIR /work

# /work is the only writable area at run time. The orchestrator passes
# `--read-only --tmpfs /tmp:size=256m --tmpfs /var/tmp:size=64m` so
# the rest of the filesystem stays immutable.
RUN chown -R axiom:axiom /work
USER axiom

# `sleep infinity` keeps the container alive so the orchestrator can
# docker-exec tool calls into it. Teardown is `docker rm -f` from the
# orchestrator, not a graceful in-container shutdown.
CMD ["sleep", "infinity"]
