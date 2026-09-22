import csv
import time

from cpx400dp.history import History


def test_add_and_len():
    h = History(max_points=10)
    assert len(h) == 0
    h.add(1.0, 0.1, 2.0, 0.2)
    assert len(h) == 1


def test_ring_buffer_eviction():
    h = History(max_points=5)
    for i in range(10):
        h.add(float(i), 0.0, 0.0, 0.0, t=float(i))
    assert len(h) == 5
    samples = h.since(None)
    assert [s.v1 for s in samples] == [5.0, 6.0, 7.0, 8.0, 9.0]


def test_since_window():
    h = History(max_points=100)
    for i in range(20):
        h.add(float(i), 0.0, 0.0, 0.0, t=float(i))
    recent = h.since(5.0)
    # last sample t=19, cutoff=14 -> t in [14..19]
    assert [s.v1 for s in recent] == [14.0, 15.0, 16.0, 17.0, 18.0, 19.0]


def test_since_empty():
    h = History()
    assert h.since(10.0) == []
    assert h.since(None) == []


def test_clear():
    h = History()
    h.add(1.0, 0.1, 2.0, 0.2)
    h.clear()
    assert len(h) == 0


def test_decimate_below_limit_passthrough():
    h = History()
    for i in range(50):
        h.add(float(i), 0, 0, 0, t=float(i))
    samples = h.since(None)
    decimated = History.decimate(samples, max_points=100)
    assert decimated == samples


def test_decimate_above_limit():
    h = History(max_points=10000)
    for i in range(5000):
        h.add(float(i), 0, 0, 0, t=float(i))
    samples = h.since(None)
    decimated = History.decimate(samples, max_points=500)
    assert len(decimated) == 500
    assert decimated[0] == samples[0]
    assert decimated[-1] == samples[-1]
    # monotonically increasing time
    times = [s.t for s in decimated]
    assert times == sorted(times)


def test_export_csv(tmp_path):
    h = History()
    h.add(12.0, 1.5, 5.0, 0.5, t=time.monotonic())
    h.add(12.1, 1.6, 5.1, 0.6, t=time.monotonic())
    out = tmp_path / "history.csv"
    count = h.export_csv(out)
    assert count == 2
    with open(out, newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["timestamp_iso", "t_rel_s", "v1", "i1", "p1", "v2", "i2", "p2"]
    assert len(rows) == 3
    # row 1: v1=12.0, i1=1.5 -> p1=18.0
    assert rows[1][2] == "12.0"
    assert rows[1][3] == "1.5"
    assert float(rows[1][4]) == 18.0


def test_export_csv_empty(tmp_path):
    h = History()
    out = tmp_path / "empty.csv"
    count = h.export_csv(out)
    assert count == 0
    with open(out, newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 1  # header only
