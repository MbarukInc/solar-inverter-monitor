# The deploy job shells out to rsync and ssh, and the official ARC runner image
# has neither. Its Dockerfile installs only sudo, lsb-release, gpg-agent,
# software-properties-common, curl, jq, unzip, git and the Docker CLI -- so
# without this derived image the job fails on "rsync: command not found".
#
# Docker itself is not needed here: the image build happens on the Pi over ssh,
# not in this pod.
#
# Build and push to the MicroK8s built-in registry (microk8s enable registry):
#   docker build --platform linux/amd64 \
#     -f deploy/arc/runner.Dockerfile \
#     -t 192.168.13.40:32000/actions-runner-deploy:2.336.0 .
#   docker push 192.168.13.40:32000/actions-runner-deploy:2.336.0
#
# Bump RUNNER_VERSION in step with the controller; ARC warns when the runner
# image trails the controller by too much.
ARG RUNNER_VERSION=2.336.0
FROM ghcr.io/actions/actions-runner:${RUNNER_VERSION}

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends rsync openssh-client \
 && rm -rf /var/lib/apt/lists/*
USER runner
