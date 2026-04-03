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

# Ralph's own instructions baked into /opt/ralph (for third-party repo mode)
COPY CLAUDE.md /opt/ralph/CLAUDE.md
COPY pyproject.toml uv.lock /opt/ralph/
COPY docs/ /opt/ralph/docs/
COPY multi_agent/ /opt/ralph/multi_agent/

# Ralph's own venv (separate from workspace venv at /tmp/venv)
RUN UV_PROJECT_ENVIRONMENT=/opt/ralph/.venv uv sync --frozen --no-install-project --directory /opt/ralph

# Wrapper: ralph-run uses ralph's own venv with correct PYTHONPATH
RUN printf '#!/bin/sh\nPYTHONPATH=/opt/ralph exec /opt/ralph/.venv/bin/python "$@"\n' \
    > /usr/local/bin/ralph-run && chmod +x /usr/local/bin/ralph-run

RUN chown -R agent:agent /opt/ralph

# Personal-scope skill so Claude Code auto-discovers it regardless of CWD
COPY skills /opt/ralph/.claude/skills

USER agent
ENV COLORTERM=truecolor
