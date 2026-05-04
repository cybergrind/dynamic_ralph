#!/usr/bin/env python3
"""Verify that Claude Code authorization works inside the ralph-agent Docker container.

Creates a temporary git repo, builds the Docker image if needed, and runs
incremental checks inside the container to pinpoint mount/permission issues.

Usage:
    uv run scripts/verify_docker_auth.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


CONTAINER_HOME = '/home/agent'
PASS = '\033[32mPASS\033[0m'
FAIL = '\033[31mFAIL\033[0m'
WARN = '\033[33mWARN\033[0m'
INFO = '\033[36mINFO\033[0m'

failed = False


def check(label: str, ok: bool, detail: str = '') -> bool:
    global failed
    status = PASS if ok else FAIL
    msg = f'  [{status}] {label}'
    if detail:
        msg += f'  — {detail}'
    print(msg)
    if not ok:
        failed = True
    return ok


def warn(label: str, detail: str = '') -> None:
    msg = f'  [{WARN}] {label}'
    if detail:
        msg += f'  — {detail}'
    print(msg)


def info(label: str, detail: str = '') -> None:
    msg = f'  [{INFO}] {label}'
    if detail:
        msg += f'  — {detail}'
    print(msg)


# ---------------------------------------------------------------------------
# Phase 1: Host-side checks
# ---------------------------------------------------------------------------

def check_host_prerequisites() -> tuple[Path, Path, Path]:
    """Check that credential files exist on the host."""
    print('\n--- Phase 1: Host-side credential files ---')

    home = Path.home()
    claude_dir = home / '.claude'
    config_claude = home / '.config' / 'claude'
    claude_json = home / '.claude.json'

    # Resolve via full_path if present (nested container scenario)
    full_path_file = claude_dir / 'full_path'
    if full_path_file.is_file():
        host_claude = Path(full_path_file.read_text().strip())
        host_home = host_claude.parent
        claude_dir = host_claude
        config_claude = host_home / '.config' / 'claude'
        claude_json = host_home / '.claude.json'
        info('full_path', f'resolved host home to {host_home}')

    check('~/.claude/ directory exists', claude_dir.is_dir(), str(claude_dir))

    creds = claude_dir / '.credentials.json'
    if check('credentials.json exists', creds.is_file(), str(creds)):
        try:
            data = json.loads(creds.read_text())
            has_token = bool(data.get('oauthRefreshToken') or data.get('claudeAiOauth'))
            check('credentials.json has OAuth token', has_token)
        except (json.JSONDecodeError, OSError) as e:
            check('credentials.json is valid JSON', False, str(e))

    if check('~/.claude.json exists', claude_json.is_file(), str(claude_json)):
        try:
            data = json.loads(claude_json.read_text())
            check(
                'claude.json has oauthAccount',
                bool(data.get('oauthAccount')),
                'needed for non-interactive mode',
            )
            check(
                'claude.json has hasCompletedOnboarding',
                data.get('hasCompletedOnboarding') is True,
                'without this, claude shows login screen',
            )
        except (json.JSONDecodeError, OSError) as e:
            check('claude.json is valid JSON', False, str(e))

    if not config_claude.is_dir():
        warn('~/.config/claude/ missing', f'{config_claude} — may be fine if unused')
    else:
        check('~/.config/claude/ exists', True, str(config_claude))

    # Check permissions — files must be readable by UID 1000
    for path, name in [(claude_dir, '~/.claude/'), (claude_json, '~/.claude.json')]:
        if path.exists():
            st = path.stat()
            readable_by_others = bool(st.st_mode & 0o004)
            owned_by_1000 = st.st_uid == 1000
            ok = readable_by_others or owned_by_1000
            check(
                f'{name} readable by container (uid=1000)',
                ok,
                f'uid={st.st_uid} mode={oct(st.st_mode)}',
            )

    return claude_dir, config_claude, claude_json


# ---------------------------------------------------------------------------
# Phase 2: Docker image
# ---------------------------------------------------------------------------

def check_docker_image() -> str:
    """Check Docker image availability, build if needed."""
    print('\n--- Phase 2: Docker image ---')

    image = os.environ.get('RALPH_IMAGE', 'ralph-agent:latest')

    result = subprocess.run(
        ['docker', 'image', 'inspect', image],
        capture_output=True,
    )
    if result.returncode != 0:
        info('image not found, building', image)
        # Import here to allow running even without multi_agent installed
        try:
            from multi_agent.docker import build_image
            build_image(image)
        except ImportError:
            print(f'  multi_agent not importable — build manually: docker build -t {image} -f docker/Dockerfile .')
            sys.exit(1)

    check('Docker image exists', True, image)

    # Check docker socket
    sock = Path('/var/run/docker.sock')
    check('Docker socket exists', sock.exists(), str(sock))
    if sock.exists():
        gid = os.stat(sock).st_gid
        info('Docker socket GID', str(gid))

    return image


# ---------------------------------------------------------------------------
# Phase 3: Container mount & permission checks
# ---------------------------------------------------------------------------

def docker_run_check(
    image: str,
    workspace: str,
    claude_dir: Path,
    config_claude: Path,
    claude_json: Path,
    cmd: list[str],
) -> subprocess.CompletedProcess:
    """Run a command inside the container with the same mounts as ralph-agent."""
    sock_gid = str(os.stat('/var/run/docker.sock').st_gid)

    docker_cmd = [
        'docker', 'run', '--rm',
        '--group-add', sock_gid,
        '-e', 'IS_SANDBOX=1',
        '-v', '/var/run/docker.sock:/var/run/docker.sock',
        '-v', f'{workspace}:/workspace',
        '-v', '/workspace/.venv',
        '-v', f'{claude_dir}:{CONTAINER_HOME}/.claude',
        '-v', f'{config_claude}:{CONTAINER_HOME}/.config/claude',
        '-v', f'{claude_json}:{CONTAINER_HOME}/.claude.json',
        '-w', '/workspace',
        image,
        *cmd,
    ]

    return subprocess.run(docker_cmd, capture_output=True, text=True, timeout=60)


def check_container_mounts(
    image: str,
    workspace: str,
    claude_dir: Path,
    config_claude: Path,
    claude_json: Path,
) -> None:
    """Run checks inside the container to verify mounts and permissions."""
    print('\n--- Phase 3: Container mount & permission checks ---')

    def run(cmd: list[str]) -> subprocess.CompletedProcess:
        return docker_run_check(image, workspace, claude_dir, config_claude, claude_json, cmd)

    # Check files are visible inside container
    r = run(['ls', '-la', f'{CONTAINER_HOME}/.claude/'])
    check(
        '.claude/ mounted and listable',
        r.returncode == 0,
        r.stderr.strip() if r.returncode != 0 else '',
    )
    if r.returncode == 0:
        info('contents', r.stdout.strip().replace('\n', '\n           '))

    r = run(['cat', f'{CONTAINER_HOME}/.claude.json'])
    check(
        '.claude.json mounted and readable',
        r.returncode == 0,
        r.stderr.strip() if r.returncode != 0 else '',
    )

    r = run(['test', '-f', f'{CONTAINER_HOME}/.claude/.credentials.json'])
    check('.credentials.json visible in container', r.returncode == 0)

    # Check writable (needed for token refresh)
    r = run(['sh', '-c', f'touch {CONTAINER_HOME}/.claude/.write_test && rm {CONTAINER_HOME}/.claude/.write_test'])
    check(
        '.claude/ is writable (needed for token refresh)',
        r.returncode == 0,
        r.stderr.strip() if r.returncode != 0 else '',
    )

    r = run(['sh', '-c', f'touch {CONTAINER_HOME}/.claude.json.tmp && rm {CONTAINER_HOME}/.claude.json.tmp'])
    check(
        '.claude.json parent dir is writable',
        r.returncode == 0,
        r.stderr.strip() if r.returncode != 0 else '',
    )

    # Check uid/gid inside container
    r = run(['id'])
    if r.returncode == 0:
        info('container user', r.stdout.strip())

    r = run(['stat', '-c', '%u:%g %a %n', f'{CONTAINER_HOME}/.claude'])
    if r.returncode == 0:
        info('.claude ownership in container', r.stdout.strip())

    r = run(['stat', '-c', '%u:%g %a %n', f'{CONTAINER_HOME}/.claude.json'])
    if r.returncode == 0:
        info('.claude.json ownership in container', r.stdout.strip())


# ---------------------------------------------------------------------------
# Phase 4: Claude Code smoke test
# ---------------------------------------------------------------------------

def check_claude_smoke_test(
    image: str,
    workspace: str,
    claude_dir: Path,
    config_claude: Path,
    claude_json: Path,
) -> None:
    """Run a minimal claude command to verify end-to-end auth."""
    print('\n--- Phase 4: Claude Code smoke test ---')

    r = docker_run_check(
        image, workspace, claude_dir, config_claude, claude_json,
        ['claude', '--version'],
    )
    check('claude --version', r.returncode == 0, r.stdout.strip() or r.stderr.strip())

    info('running', 'claude -p "say ok" (may take a few seconds)')
    r = docker_run_check(
        image, workspace, claude_dir, config_claude, claude_json,
        [
            'claude',
            '-p', 'Reply with exactly: ok',
            '--dangerously-skip-permissions',
            '--max-turns', '1',
        ],
    )
    if r.returncode == 0:
        check('claude -p "say ok"', True, repr(r.stdout.strip()[:200]))
    else:
        check('claude -p "say ok"', False)
        stderr = r.stderr.strip()
        stdout = r.stdout.strip()
        if stderr:
            print(f'    stderr: {stderr[:500]}')
        if stdout:
            print(f'    stdout: {stdout[:500]}')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print('=== Ralph Docker Auth Verification ===')

    claude_dir, config_claude, claude_json = check_host_prerequisites()
    image = check_docker_image()

    # Create a temp git repo to simulate "another repository" usage
    with tempfile.TemporaryDirectory(prefix='ralph-verify-') as tmpdir:
        subprocess.run(['git', 'init', tmpdir], capture_output=True)
        subprocess.run(
            ['git', 'commit', '--allow-empty', '-m', 'init'],
            capture_output=True,
            cwd=tmpdir,
        )
        info('temp workspace', tmpdir)

        check_container_mounts(image, tmpdir, claude_dir, config_claude, claude_json)
        check_claude_smoke_test(image, tmpdir, claude_dir, config_claude, claude_json)

    print()
    if failed:
        print(f'[{FAIL}] Some checks failed — see above for details.')
        sys.exit(1)
    else:
        print(f'[{PASS}] All checks passed.')


if __name__ == '__main__':
    main()
