"""Claude Code agent backend.

Implements :class:`~multi_agent.backend.AgentBackend` for the Claude Code CLI
(``npx @anthropic-ai/claude-code``), including its ``stream-json`` output
format, Docker wrapping, and cost/token metric extraction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator

from multi_agent.backend import AgentEvent, AgentResult
from multi_agent.constants import GIT_EMAIL, RALPH_IMAGE
from multi_agent.docker import build_image, docker_sock_gid, image_exists


class ClaudeCodeBackend:
    """Backend for the Claude Code CLI (``npx @anthropic-ai/claude-code``)."""

    # ------------------------------------------------------------------
    # build_command
    # ------------------------------------------------------------------

    def build_command(
        self,
        prompt: str,
        *,
        system_prompt: str = '',
        max_turns: int | None = None,
    ) -> list[str]:
        cmd: list[str] = [
            'npx',
            '@anthropic-ai/claude-code',
            '--dangerously-skip-permissions',
            '--print',
            '--verbose',
            '--output-format',
            'stream-json',
        ]
        if system_prompt:
            cmd.extend(['--append-system-prompt', system_prompt])
        if max_turns is not None:
            cmd.extend(['--max-turns', str(max_turns)])
        cmd.append(prompt)
        return cmd

    # ------------------------------------------------------------------
    # build_docker_command
    # ------------------------------------------------------------------

    def build_docker_command(
        self,
        base_cmd: list[str],
        *,
        agent_id: int,
        workspace: str,
    ) -> list[str]:
        if not image_exists():
            build_image()

        compose_project = f'ralph_agent_{agent_id}'
        claude_config = Path.home() / '.claude'
        host_config_claude = Path.home() / '.config' / 'claude'

        return [
            'docker',
            'run',
            '--rm',
            '--group-add',
            docker_sock_gid(),
            '-e',
            f'AGENT_ID={agent_id}',
            '-e',
            f'COMPOSE_PROJECT_NAME={compose_project}',
            '-e',
            f'HOST_WORKSPACE={workspace}',
            '-e',
            'IS_SANDBOX=1',
            '-e',
            'UV_PROJECT_ENVIRONMENT=/tmp/venv',
            '-e',
            'GIT_AUTHOR_NAME=Claude Agent',
            '-e',
            f'GIT_AUTHOR_EMAIL={GIT_EMAIL}',
            '-e',
            'GIT_COMMITTER_NAME=Claude Agent',
            '-e',
            f'GIT_COMMITTER_EMAIL={GIT_EMAIL}',
            '-v',
            '/var/run/docker.sock:/var/run/docker.sock',
            '-v',
            f'{workspace}:/workspace',
            '-v',
            '/workspace/.venv',  # anonymous volume: hide host .venv
            '-v',
            f'{claude_config}:/home/agent/.claude',
            '-v',
            f'{host_config_claude}:/home/agent/.config/claude',
            '-w',
            '/workspace',
            RALPH_IMAGE,
            *base_cmd,
        ]

    # ------------------------------------------------------------------
    # parse_events
    # ------------------------------------------------------------------

    def parse_events(self, lines: Iterator[str]) -> Iterator[AgentEvent]:
        """Parse Claude Code ``stream-json`` lines into :class:`AgentEvent`."""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                # Non-JSON output (e.g. npm warnings) → raw event
                print(stripped, file=sys.stderr)
                yield AgentEvent(kind='raw', text=stripped)
                continue

            etype = event.get('type', '')

            if etype == 'system':
                model = event.get('model', '')
                yield AgentEvent(
                    kind='system',
                    text=f'session started (model={model})',
                    raw=event,
                )

            elif etype == 'assistant':
                message = event.get('message', {})
                for block in message.get('content', []):
                    btype = block.get('type')
                    if btype == 'text':
                        yield AgentEvent(
                            kind='assistant',
                            text=block.get('text', ''),
                            raw=event,
                        )
                    elif btype == 'tool_use':
                        name = block.get('name', '?')
                        tool_input = block.get('input', {})
                        detail = _tool_detail(name, tool_input)
                        yield AgentEvent(
                            kind='tool_use',
                            text=f'{name}: {detail}',
                            raw=event,
                        )

            elif etype == 'user':
                tool_result = event.get('tool_use_result')
                if tool_result is not None:
                    text = _tool_result_text(tool_result)
                    yield AgentEvent(
                        kind='tool_result',
                        text=text,
                        raw=event,
                    )

            elif etype == 'result':
                subtype = event.get('subtype', '')
                cost = event.get('total_cost_usd')
                turns = event.get('num_turns', '?')
                cost_str = f', cost=${cost:.4f}' if cost else ''
                yield AgentEvent(
                    kind='result',
                    text=f'{subtype} (turns={turns}{cost_str})',
                    raw=event,
                )

            else:
                # Unknown event type — pass through as raw
                yield AgentEvent(kind='raw', text=stripped, raw=event)

    # ------------------------------------------------------------------
    # extract_result
    # ------------------------------------------------------------------

    def extract_result(self, events: list[AgentEvent], exit_code: int) -> AgentResult:
        result = AgentResult(exit_code=exit_code)
        last_assistant_text = ''

        for ev in events:
            if ev.kind == 'result':
                raw = ev.raw
                result.num_turns = raw.get('num_turns', 0)
                result.cost_usd = raw.get('total_cost_usd', 0.0)
                result.input_tokens = raw.get('input_tokens', 0)
                result.output_tokens = raw.get('output_tokens', 0)
                result.completion_status = raw.get('subtype', 'unknown')
            elif ev.kind == 'assistant':
                last_assistant_text = ev.text

        result.final_response = last_assistant_text
        return result


# ---------------------------------------------------------------------------
# Helpers (private)
# ---------------------------------------------------------------------------


def _tool_detail(name: str, tool_input: dict) -> str:
    """Extract a short detail string for a tool_use event."""
    if name == 'Bash':
        return tool_input.get('command', '')
    if name in ('Read', 'Write', 'Edit'):
        return tool_input.get('file_path', '')
    if name in ('Glob', 'Grep'):
        return tool_input.get('pattern', '')
    if name == 'Task':
        return tool_input.get('description', '')
    return str(tool_input)


def _tool_result_text(tool_result: object) -> str:
    """Extract display text from a tool_use_result payload."""
    if isinstance(tool_result, str):
        return tool_result
    if isinstance(tool_result, dict):
        stdout = tool_result.get('stdout', '')
        stderr = tool_result.get('stderr', '')
        output = stdout or stderr
        if output:
            return output
        if tool_result.get('is_error'):
            return '(error)'
    return ''
