"""Tests validating the migration from the parent project to standalone repository.

Ensures no parent-project references remain and all infrastructure files exist.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# Root of the dynamic_ralph project
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Infrastructure files
# ---------------------------------------------------------------------------


class TestInfrastructureFiles:
    """Verify all required infrastructure files exist."""

    def test_gitignore_exists(self):
        gitignore = PROJECT_ROOT / '.gitignore'
        assert gitignore.exists()
        content = gitignore.read_text()
        assert '__pycache__/' in content
        assert '.venv/' in content
        assert 'workflow_state.json' in content

    def test_dockerfile_exists(self):
        dockerfile = PROJECT_ROOT / 'docker' / 'Dockerfile'
        assert dockerfile.exists()
        content = dockerfile.read_text()
        assert 'python:3.13' in content
        # Should NOT reference the parent project's registry
        assert 'octobrowser' not in content.lower()
        assert 'registry.gitlab.com' not in content

    def test_readme_exists(self):
        readme = PROJECT_ROOT / 'README.md'
        assert readme.exists()
        content = readme.read_text()
        assert 'Dynamic Ralph' in content

    def test_claude_md_exists(self):
        claude_md = PROJECT_ROOT / 'CLAUDE.md'
        assert claude_md.exists()
        content = claude_md.read_text()
        assert 'uv run' in content

    def test_pyproject_toml_correct(self):
        pyproject = PROJECT_ROOT / 'pyproject.toml'
        assert pyproject.exists()
        content = pyproject.read_text()
        assert "name = 'dynamic-ralph'" in content
        assert 'pydantic' in content
        assert 'pytest' in content
        # isort should reference multi_agent, not snapshot_manager
        assert 'snapshot_manager' not in content
        assert 'multi_agent' in content
        # Should not have dead alembic exclude
        assert 'alembic' not in content

    def test_pre_commit_config_exists(self):
        precommit = PROJECT_ROOT / '.pre-commit-config.yaml'
        assert precommit.exists()


# ---------------------------------------------------------------------------
# Constants decoupling
# ---------------------------------------------------------------------------


class TestConstantsDecoupled:
    """Verify constants are configurable and don't reference the parent project."""

    def test_no_octobrowser_in_constants(self):
        from multi_agent.constants import COMPOSE_FILE, ENV_FILE, RALPH_IMAGE, SERVICE

        assert 'octobrowser' not in RALPH_IMAGE.lower()
        assert 'octobrowser' not in SERVICE.lower()
        assert 'octobrowser' not in COMPOSE_FILE.lower()
        assert 'octobrowser' not in ENV_FILE.lower()

    def test_default_service_is_generic(self):
        from multi_agent.constants import SERVICE

        assert SERVICE == 'app'

    def test_default_env_file_is_generic(self):
        from multi_agent.constants import ENV_FILE

        assert ENV_FILE == '.env'

    def test_git_email_is_generic(self):
        from multi_agent.constants import GIT_EMAIL

        assert 'octobrowser' not in GIT_EMAIL
        assert '@' in GIT_EMAIL

    def test_ralph_image_configurable(self, monkeypatch):
        monkeypatch.setenv('RALPH_IMAGE', 'custom-image:v2')
        # Need to reimport to pick up new env var
        import importlib

        import multi_agent.constants

        importlib.reload(multi_agent.constants)
        try:
            assert multi_agent.constants.RALPH_IMAGE == 'custom-image:v2'
        finally:
            monkeypatch.delenv('RALPH_IMAGE')
            importlib.reload(multi_agent.constants)

    def test_compose_file_configurable(self, monkeypatch):
        monkeypatch.setenv('RALPH_COMPOSE_FILE', 'docker-compose.custom.yml')
        import importlib

        import multi_agent.constants

        importlib.reload(multi_agent.constants)
        try:
            assert multi_agent.constants.COMPOSE_FILE == 'docker-compose.custom.yml'
        finally:
            monkeypatch.delenv('RALPH_COMPOSE_FILE')
            importlib.reload(multi_agent.constants)

    def test_service_configurable(self, monkeypatch):
        monkeypatch.setenv('RALPH_SERVICE', 'my-backend')
        import importlib

        import multi_agent.constants

        importlib.reload(multi_agent.constants)
        try:
            assert multi_agent.constants.SERVICE == 'my-backend'
        finally:
            monkeypatch.delenv('RALPH_SERVICE')
            importlib.reload(multi_agent.constants)

    def test_infra_services_configurable(self, monkeypatch):
        monkeypatch.setenv('RALPH_INFRA_SERVICES', 'postgres,redis,rabbitmq')
        import importlib

        import multi_agent.constants

        importlib.reload(multi_agent.constants)
        try:
            assert multi_agent.constants.INFRA_SERVICES == ['postgres', 'redis', 'rabbitmq']
        finally:
            monkeypatch.delenv('RALPH_INFRA_SERVICES')
            importlib.reload(multi_agent.constants)

    def test_git_email_configurable(self, monkeypatch):
        monkeypatch.setenv('RALPH_GIT_EMAIL', 'bot@example.com')
        import importlib

        import multi_agent.constants

        importlib.reload(multi_agent.constants)
        try:
            assert multi_agent.constants.GIT_EMAIL == 'bot@example.com'
        finally:
            monkeypatch.delenv('RALPH_GIT_EMAIL')
            importlib.reload(multi_agent.constants)


# ---------------------------------------------------------------------------
# Prompts decoupling
# ---------------------------------------------------------------------------


class TestPromptsDecoupled:
    """Verify prompts don't contain parent project references."""

    def test_no_parent_project_stack_in_base_instructions(self):
        from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS

        # Should not reference specific parent project tech stack
        assert 'FastAPI' not in BASE_AGENT_INSTRUCTIONS
        assert 'SQLAlchemy' not in BASE_AGENT_INSTRUCTIONS
        assert 'MySQL' not in BASE_AGENT_INSTRUCTIONS
        assert 'TimescaleDB' not in BASE_AGENT_INSTRUCTIONS
        assert 'Kafka' not in BASE_AGENT_INSTRUCTIONS

    def test_no_parent_test_runner_in_base_instructions(self):
        from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS

        assert 'run_agent_tests.sh' not in BASE_AGENT_INSTRUCTIONS
        assert 'bin/' not in BASE_AGENT_INSTRUCTIONS

    def test_no_parent_architecture_in_base_instructions(self):
        from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS

        # Should not reference parent's layer architecture
        assert 'api/ → core/ → common/' not in BASE_AGENT_INSTRUCTIONS
        assert 'queries.py, actions.py' not in BASE_AGENT_INSTRUCTIONS

    def test_base_instructions_still_useful(self):
        from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS

        # Should still have generic guidance
        assert 'CLAUDE.md' in BASE_AGENT_INSTRUCTIONS
        assert 'Anti-loop' in BASE_AGENT_INSTRUCTIONS
        assert 'Scope Discipline' in BASE_AGENT_INSTRUCTIONS


# ---------------------------------------------------------------------------
# Docker decoupling
# ---------------------------------------------------------------------------


class TestDockerDecoupled:
    """Verify Docker configuration doesn't reference parent project."""

    def test_dockerfile_path_is_local(self):
        from multi_agent.docker import DOCKERFILE_PATH

        assert 'ralph/Dockerfile' not in DOCKERFILE_PATH or DOCKERFILE_PATH == 'docker/Dockerfile'
        assert DOCKERFILE_PATH == 'docker/Dockerfile'

    def test_dockerfile_path_configurable(self, monkeypatch):
        monkeypatch.setenv('RALPH_DOCKERFILE', 'custom/Dockerfile')
        import importlib

        import multi_agent.docker

        importlib.reload(multi_agent.docker)
        try:
            assert multi_agent.docker.DOCKERFILE_PATH == 'custom/Dockerfile'
        finally:
            monkeypatch.delenv('RALPH_DOCKERFILE')
            importlib.reload(multi_agent.docker)


# ---------------------------------------------------------------------------
# Git email decoupling
# ---------------------------------------------------------------------------


class TestGitEmailDecoupled:
    """Verify git email references don't use the parent project domain."""

    def test_run_dynamic_ralph_no_octobrowser_email(self):
        source = (PROJECT_ROOT / 'run_dynamic_ralph.py').read_text()
        assert 'octobrowser.net' not in source

    def test_executor_no_octobrowser_email(self):
        source = (PROJECT_ROOT / 'multi_agent' / 'workflow' / 'executor.py').read_text()
        assert 'octobrowser.net' not in source

    def test_constants_no_octobrowser(self):
        source = (PROJECT_ROOT / 'multi_agent' / 'constants.py').read_text()
        assert 'octobrowser' not in source.lower()
