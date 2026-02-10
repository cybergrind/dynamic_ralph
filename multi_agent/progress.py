"""Progress logging for ralph loop."""

from datetime import datetime, UTC


def append_progress(message: str) -> None:
    with open('progress.txt', 'a') as f:
        timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
        f.write(f'[{timestamp}] {message}\n')
