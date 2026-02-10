"""Docker image helpers for ralph agent containers."""

import os
import subprocess

from multi_agent.constants import RALPH_IMAGE


DOCKERFILE_PATH = os.environ.get('RALPH_DOCKERFILE', 'docker/Dockerfile')


def image_exists(image: str = RALPH_IMAGE) -> bool:
    result = subprocess.run(
        ['docker', 'image', 'inspect', image],
        capture_output=True,
    )
    return result.returncode == 0


def build_image(image: str = RALPH_IMAGE) -> None:
    print(f'==> Building {image}...')
    subprocess.run(
        ['docker', 'build', '-t', image, '-f', DOCKERFILE_PATH, '.'],
        check=True,
    )


def docker_sock_gid() -> str:
    """Return the GID of /var/run/docker.sock for --group-add."""
    return str(os.stat('/var/run/docker.sock').st_gid)
