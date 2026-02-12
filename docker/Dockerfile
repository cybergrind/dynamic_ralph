FROM python:3.13-slim

ARG CLAUDE_CODE_VERSION=latest

# Node.js 20 (for Claude Code CLI: npx @anthropic-ai/claude-code)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git jq make procps \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI + docker compose plugin + buildx
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
       https://download.docker.com/linux/debian $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /usr/local/bin/uv

# Pre-install project Python deps so `uv run` is instant at runtime
ENV UV_PROJECT_ENVIRONMENT=/tmp/venv
WORKDIR /workspace
COPY pyproject.toml uv.lock .pre-commit-config.yaml ./
RUN uv sync --frozen --no-install-project

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Allow --dangerously-skip-permissions in Docker
ENV IS_SANDBOX=1

# Create non-root user (UID=1000 matches typical host user)
RUN groupadd -g 1000 agent && useradd -u 1000 -g agent -m agent

# Ensure agent user can access pre-built venv and npm globals
RUN chown -R agent:agent /tmp/venv
ENV HOME=/home/agent

# Pre-create npm cache dir (npx needs it in interactive mode)
RUN mkdir -p /home/agent/.npm && chown agent:agent /home/agent/.npm

# Git safe directory for the non-root user
RUN su - agent -c "git config --global --add safe.directory /workspace"

USER agent
ENV COLORTERM=truecolor

# Pre-cache pre-commit hook environments so linting skips the install step
RUN uv run pre-commit install-hooks
