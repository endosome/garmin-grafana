"""Authenticate interactively without fetching health data or connecting to PostgreSQL."""
import argparse
import logging
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--token-dir', type=Path,
        default=Path.home() / '.local/share/garmin-grafana/live-validation/tokens',
        help='Private local token directory; default is outside the repository',
    )
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        parser.error('Run this command in your own interactive terminal; do not send credentials through chat or pipes')
    token_dir = args.token_dir.expanduser().resolve()
    previous_umask = os.umask(0o077)
    try:
        token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        token_dir.chmod(0o700)
        from garmin_grafana import garmin_fetch
        # Always prompt locally when cached tokens cannot authenticate. No passwords in env/history.
        garmin_fetch.TOKEN_DIR = str(token_dir)
        garmin_fetch.GARMINCONNECT_EMAIL = None
        garmin_fetch.GARMINCONNECT_PASSWORD = None
        logging.getLogger().setLevel(logging.INFO)
        garmin_fetch.garmin_login()
        if not any(path.is_file() for path in token_dir.rglob('*')):
            raise RuntimeError('Authentication returned without persisting tokens; check directory permissions')
    finally:
        os.umask(previous_umask)
    print(f'Login succeeded. Private token directory: {token_dir}')
    print('No measurements fetched and no database connection opened.')


if __name__ == '__main__':
    main()
