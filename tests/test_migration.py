"""Tests validating the migration from the parent project to standalone repository.

Ensures no parent-project references remain and all infrastructure files exist.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar


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
        assert 'run_ralph/' in content

    def test_dockerfile_exists(self):
        dockerfile = PROJECT_ROOT / 'docker' / 'Dockerfile'
        assert dockerfile.exists()
        content = dockerfile.read_text()
        assert 'python:3.13' in content
        # Should NOT reference the parent project's registry
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

    def test_git_email_is_generic(self):
        from multi_agent.constants import GIT_EMAIL

        assert GIT_EMAIL.endswith('@dynamic-ralph.dev')

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


# ---------------------------------------------------------------------------
# Prompts decoupling
# ---------------------------------------------------------------------------


class TestPromptsDecoupled:
    """Verify prompts don't contain parent project references."""

    def test_no_parent_references_in_base_instructions(self):
        from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS

        # Should not reference specific parent project tech stack
        assert 'FastAPI' not in BASE_AGENT_INSTRUCTIONS
        assert 'SQLAlchemy' not in BASE_AGENT_INSTRUCTIONS
        assert 'MySQL' not in BASE_AGENT_INSTRUCTIONS
        assert 'TimescaleDB' not in BASE_AGENT_INSTRUCTIONS
        assert 'Kafka' not in BASE_AGENT_INSTRUCTIONS
        # Should not reference parent test runner or scripts
        assert 'run_agent_tests.sh' not in BASE_AGENT_INSTRUCTIONS
        assert 'bin/' not in BASE_AGENT_INSTRUCTIONS
        # Should not reference parent's layer architecture
        assert 'api/ → core/ → common/' not in BASE_AGENT_INSTRUCTIONS
        assert 'queries.py, actions.py' not in BASE_AGENT_INSTRUCTIONS

    def test_prepare_system_prompt_removed(self):
        import multi_agent.prompts

        assert not hasattr(multi_agent.prompts, 'PREPARE_SYSTEM_PROMPT')

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
# Agent prompt regression — no stale references
# ---------------------------------------------------------------------------


class TestNoStalePromptReferences:
    """Verify agent-facing prompts contain no references to non-existent scripts or paths."""

    FORBIDDEN_STRINGS: ClassVar[list[str]] = [
        'run_agent_tests.sh',
        'skills/',
        'tasks/prd-',
        'resources/geodb',
    ]

    def test_step_instructions_no_forbidden_strings(self):
        from multi_agent.workflow.prompts import STEP_INSTRUCTIONS

        violations = []
        for step_type, text in STEP_INSTRUCTIONS.items():
            for forbidden in self.FORBIDDEN_STRINGS:
                if forbidden in text:
                    violations.append(f'{step_type}: contains "{forbidden}"')
        assert violations == [], 'Stale references found in STEP_INSTRUCTIONS:\n' + '\n'.join(violations)

    def test_base_instructions_no_forbidden_strings(self):
        from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS

        violations = []
        for forbidden in self.FORBIDDEN_STRINGS:
            if forbidden in BASE_AGENT_INSTRUCTIONS:
                violations.append(f'BASE_AGENT_INSTRUCTIONS: contains "{forbidden}"')
        assert violations == [], 'Stale references found in BASE_AGENT_INSTRUCTIONS:\n' + '\n'.join(violations)


# ---------------------------------------------------------------------------
# Comprehensive parent-project reference sweep
# ---------------------------------------------------------------------------

# The forbidden substring we scan for (assembled to avoid self-match).
_FORBIDDEN = 'oct' + 'o'


class TestNoParentProjectReferences:
    """Sweep the entire repository to ensure no parent-project references remain."""

    _IGNORE_DIRS: ClassVar[set[str]] = {'.git', '.venv', '__pycache__', 'node_modules', '.mypy_cache', '.ruff_cache'}

    def _source_files(self):
        """Yield all .py files under PROJECT_ROOT, skipping ignored dirs and this file."""
        self_path = Path(__file__).resolve()
        for p in PROJECT_ROOT.rglob('*.py'):
            if p.resolve() == self_path:
                continue
            if not any(part in self._IGNORE_DIRS for part in p.parts):
                yield p

    def _json_files(self):
        """Yield all .json files under PROJECT_ROOT, skipping ignored dirs."""
        for p in PROJECT_ROOT.rglob('*.json'):
            if not any(part in self._IGNORE_DIRS for part in p.parts):
                yield p

    def test_no_forbidden_in_source_files(self):
        violations = []
        for path in self._source_files():
            content = path.read_text()
            for i, line in enumerate(content.splitlines(), 1):
                if _FORBIDDEN in line.lower():
                    violations.append(f'{path.relative_to(PROJECT_ROOT)}:{i}: {line.strip()}')
        assert violations == [], 'Forbidden string found in source files:\n' + '\n'.join(violations)

    def test_no_forbidden_in_json_files(self):
        violations = []
        for path in self._json_files():
            content = path.read_text()
            for i, line in enumerate(content.splitlines(), 1):
                if _FORBIDDEN in line.lower():
                    violations.append(f'{path.relative_to(PROJECT_ROOT)}:{i}: {line.strip()}')
        assert violations == [], 'Forbidden string found in JSON files:\n' + '\n'.join(violations)

    def test_no_forbidden_in_git_author_emails(self):
        result = subprocess.run(
            ['git', 'log', '--all', '--format=%ae'],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        emails = result.stdout.strip().splitlines()
        bad = [e for e in emails if _FORBIDDEN in e.lower()]
        assert bad == [], f'Forbidden string found in git author emails: {bad}'
