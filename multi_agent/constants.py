"""Shared constants for ralph multi-agent scripts."""

from pathlib import Path


RALPH_IMAGE = 'ralph-agent:latest'
COMPOSE_FILE = 'compose.test.yml'
ENV_FILE = '.env-sample-fastapi'
SERVICE = 'octobrowser-server-fastapi'
INFRA_SERVICES = ['mysql', 'timescaledb', 'redis', 'kafka']
GEODB_FILE = Path('resources/geodb/dbip-full.mmdb')
