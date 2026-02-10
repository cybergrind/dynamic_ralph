"""Shared constants for ralph multi-agent scripts.

All values are configurable via environment variables for project-specific customization.
"""

import os
from pathlib import Path


RALPH_IMAGE = os.environ.get('RALPH_IMAGE', 'ralph-agent:latest')
COMPOSE_FILE = os.environ.get('RALPH_COMPOSE_FILE', 'compose.test.yml')
ENV_FILE = os.environ.get('RALPH_ENV_FILE', '.env')
SERVICE = os.environ.get('RALPH_SERVICE', 'app')
INFRA_SERVICES = os.environ.get('RALPH_INFRA_SERVICES', 'mysql,redis').split(',')
GEODB_FILE = Path(os.environ.get('RALPH_GEODB_FILE', 'resources/geodb/dbip-full.mmdb'))
GIT_EMAIL = os.environ.get('RALPH_GIT_EMAIL', 'claude-agent@dynamic-ralph.dev')
