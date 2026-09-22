"""CLI tests against the real fake_cpx simulator over loopback TCP.

Each test invokes cpx400dp.cli.main() directly (in-process, no subprocess)
against a running simulator instance, checking both the exit code and
stdout. This exercises the CLI's argument parsing, protocol usage, and
transport handling exactly as a user's shell invocation would.
"""

from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from fake_cpx import FakeCpxServer  # noqa: E402

from cpx400dp.cli import main  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def sim():
    port = _free_port()
    server = FakeCpxServer("127.0.0.1", port, drop_after=0, slow=0.0, garbage=False, noisy=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    server.server_close()


def run(sim, *args):
    """Invoke the CLI in-process against the sim and return (exit_code, stdout)."""
    argv = ["--host", "127.0.0.1", "--port", str(sim), *args]
    exit_code = main(argv)
    return exit_code


def test_idn(sim, capsys):
    code = run(sim, "idn")
    out = capsys.readouterr().out
    assert code == 0
    assert "CPX400DP" in out


def test_voltage_set_and_get(sim, capsys):
    code = run(sim, "voltage", "1", "12.5")
    assert code == 0
    capsys.readouterr()

    code = run(sim, "voltage", "1")
    out = capsys.readouterr().out
    assert code == 0
    assert out.strip() == "12.500"


def test_current_set_and_get(sim, capsys):
    run(sim, "current", "1", "2.0")
    capsys.readouterr()
    code = run(sim, "current", "1")
    out = capsys.readouterr().out
    assert code == 0
    assert out.strip() == "2.000"


def test_ovp_ocp_set_and_get(sim, capsys):
    run(sim, "ovp", "1", "50.0")
    capsys.readouterr()
    code = run(sim, "ovp", "1")
    assert capsys.readouterr().out.strip() == "50.000"
    assert code == 0

    run(sim, "ocp", "1", "5.0")
    capsys.readouterr()
    code = run(sim, "ocp", "1")
    assert capsys.readouterr().out.strip() == "5.000"
    assert code == 0


def test_output_set_and_get(sim, capsys):
    code = run(sim, "output", "1", "on")
    assert code == 0
    capsys.readouterr()

    code = run(sim, "output", "1")
    out = capsys.readouterr().out
    assert code == 0
    assert out.strip() == "on"

    run(sim, "output", "1", "off")
    capsys.readouterr()
    code = run(sim, "output", "1")
    assert capsys.readouterr().out.strip() == "off"
    assert code == 0


def test_read(sim, capsys):
    run(sim, "voltage", "1", "12.0")
    run(sim, "current", "1", "5.0")  # generous limit so V isn't clamped
    run(sim, "output", "1", "on")
    capsys.readouterr()

    code = run(sim, "read", "1")
    out = capsys.readouterr().out
    assert code == 0
    assert "V=" in out and "I=" in out
    # sim's default load is 10 ohm: 12V/10ohm = 1.2A, under the 5A limit -> CV
    assert "V=12.000" in out
    assert "I=1.200" in out


def test_status_reports_cv(sim, capsys):
    run(sim, "voltage", "1", "12.0")
    run(sim, "current", "1", "5.0")
    run(sim, "output", "1", "on")
    run(sim, "read", "1")  # readback triggers the sim's LSR bit accumulation
    capsys.readouterr()

    code = run(sim, "status", "1")
    out = capsys.readouterr().out
    assert code == 0
    assert "CV" in out


def test_all_on_all_off(sim, capsys):
    code = run(sim, "all-on")
    assert code == 0
    capsys.readouterr()
    assert run(sim, "output", "1") == 0
    assert capsys.readouterr().out.strip() == "on"
    assert run(sim, "output", "2") == 0
    assert capsys.readouterr().out.strip() == "on"

    code = run(sim, "all-off")
    assert code == 0
    capsys.readouterr()
    assert run(sim, "output", "1") == 0
    assert capsys.readouterr().out.strip() == "off"


def test_trip_reset_local_reset_smoke(sim, capsys):
    assert run(sim, "trip-reset") == 0
    capsys.readouterr()
    assert run(sim, "local") == 0
    capsys.readouterr()
    assert run(sim, "reset") == 0


def test_raw_query(sim, capsys):
    code = run(sim, "raw", "*IDN?")
    out = capsys.readouterr().out
    assert code == 0
    assert "CPX400DP" in out


def test_raw_group_query(sim, capsys):
    run(sim, "voltage", "1", "12.0")
    capsys.readouterr()
    code = run(sim, "raw", "V1?;I1?")
    out = capsys.readouterr().out
    assert code == 0
    lines = out.strip().splitlines()
    assert len(lines) == 2


def test_raw_setter_no_output(sim, capsys):
    code = run(sim, "raw", "V1 3.3")
    out = capsys.readouterr().out
    assert code == 0
    assert out == ""


def test_connect_failure_returns_nonzero(capsys):
    free_port = _free_port()  # guaranteed nothing listening here
    code = main(["--host", "127.0.0.1", "--port", str(free_port), "--timeout", "0.5", "idn"])
    err = capsys.readouterr().err
    assert code == 1
    assert "error" in err.lower()


def test_invalid_channel_rejected(sim):
    with pytest.raises(SystemExit):
        main(["--host", "127.0.0.1", "--port", str(sim), "voltage", "3", "12.0"])


def test_missing_command_rejected():
    with pytest.raises(SystemExit):
        main(["--host", "127.0.0.1"])
