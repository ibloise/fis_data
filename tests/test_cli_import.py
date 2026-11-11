"""CLI startup must work in a fresh interpreter without database access."""

import subprocess
import sys


def test_cli_init_db_help_starts_without_circular_import():
    result = subprocess.run(
        [sys.executable, "-c",
         "from click.testing import CliRunner; from fis_data.cli import cli; "
         "result = CliRunner().invoke(cli, ['init-db', '--help']); "
         "print(result.output); raise SystemExit(result.exit_code)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--help" in result.stdout
