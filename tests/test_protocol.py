import pytest

from cpx400dp import protocol as p

# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------


def test_set_voltage():
    assert p.set_voltage(1, 12.0) == "V1 12"
    assert p.set_voltage(2, 5.25) == "V2 5.25"


def test_set_voltage_with_verify():
    assert p.set_voltage(1, 12.0, verify=True) == "V1V 12"


def test_query_voltage():
    assert p.query_voltage(1) == "V1?"
    assert p.query_voltage(2) == "V2?"


def test_set_current():
    assert p.set_current(1, 2.0) == "I1 2"


def test_query_current():
    assert p.query_current(2) == "I2?"


def test_set_ovp_ocp():
    assert p.set_ovp(1, 66.0) == "OVP1 66"
    assert p.set_ocp(2, 22.0) == "OCP2 22"


def test_query_ovp_ocp():
    assert p.query_ovp(1) == "OVP1?"
    assert p.query_ocp(2) == "OCP2?"


def test_readback_queries():
    assert p.query_readback_voltage(1) == "V1O?"
    assert p.query_readback_current(2) == "I2O?"


def test_delta_builders():
    assert p.set_delta_v(1, 0.01) == "DELTAV1 0.01"
    assert p.set_delta_i(2, 0.5) == "DELTAI2 0.5"
    assert p.query_delta_v(1) == "DELTAV1?"
    assert p.query_delta_i(2) == "DELTAI2?"


def test_inc_dec_builders():
    assert p.inc_voltage(1) == "INCV1"
    assert p.inc_voltage(1, verify=True) == "INCV1V"
    assert p.dec_voltage(2) == "DECV2"
    assert p.dec_voltage(2, verify=True) == "DECV2V"
    assert p.inc_current(1) == "INCI1"
    assert p.dec_current(2) == "DECI2"


def test_output_builders():
    assert p.set_output(1, True) == "OP1 1"
    assert p.set_output(2, False) == "OP2 0"
    assert p.query_output(1) == "OP1?"


def test_save_recall():
    assert p.save_setup(1, 0) == "SAV1 0"
    assert p.recall_setup(2, 9) == "RCL2 9"
    with pytest.raises(ValueError):
        p.save_setup(1, 10)
    with pytest.raises(ValueError):
        p.recall_setup(1, -1)


def test_limit_status_builders():
    assert p.query_limit_status(1) == "LSR1?"
    assert p.set_limit_enable(2, 0x1F) == "LSE2 31"
    assert p.query_limit_enable(1) == "LSE1?"


def test_invalid_channel_raises():
    with pytest.raises(ValueError):
        p.set_voltage(3, 1.0)
    with pytest.raises(ValueError):
        p.query_current(0)


def test_global_builders():
    assert p.set_output_all(True) == "OPALL 1"
    assert p.set_output_all(False) == "OPALL 0"
    assert p.set_config(p.ConfigMode.INDEPENDENT) == "CONFIG 2"
    assert p.set_config(p.ConfigMode.VOLTAGE_TRACKING) == "CONFIG 0"
    assert p.query_config() == "CONFIG?"
    assert p.set_ratio(50) == "RATIO 50"
    assert p.query_ratio() == "RATIO?"
    with pytest.raises(ValueError):
        p.set_ratio(101)


def test_misc_global_builders():
    assert p.trip_reset() == "TRIPRST"
    assert p.go_local() == "LOCAL"
    assert p.interface_lock() == "IFLOCK"
    assert p.query_interface_lock() == "IFLOCK?"
    assert p.interface_unlock() == "IFUNLOCK"
    assert p.query_execution_error() == "EER?"
    assert p.query_query_error() == "QER?"
    assert p.query_address() == "ADDRESS?"


def test_common_commands():
    assert p.idn() == "*IDN?"
    assert p.reset() == "*RST"
    assert p.clear_status() == "*CLS"
    assert p.query_event_status() == "*ESR?"
    assert p.set_event_status_enable(128) == "*ESE 128"
    assert p.query_event_status_enable() == "*ESE?"
    assert p.query_status_byte() == "*STB?"
    assert p.set_service_request_enable(32) == "*SRE 32"
    assert p.query_service_request_enable() == "*SRE?"
    assert p.set_parallel_poll_enable(64) == "*PRE 64"
    assert p.query_parallel_poll_enable() == "*PRE?"
    assert p.query_ist() == "*IST?"
    assert p.operation_complete() == "*OPC"
    assert p.query_operation_complete() == "*OPC?"
    assert p.self_test() == "*TST?"
    assert p.trigger() == "*TRG"
    assert p.wait() == "*WAI"


def test_group():
    assert p.group("V1O?", "I1O?") == "V1O?;I1O?"
    assert p.group("OP1?") == "OP1?"
    with pytest.raises(ValueError):
        p.group()


# ---------------------------------------------------------------------------
# Response parsers — real reply formats from the manual
# ---------------------------------------------------------------------------


def test_parse_voltage_setpoint():
    assert p.parse_voltage_setpoint("V1 12.000\r\n") == 12.0
    assert p.parse_voltage_setpoint("V2 5.250") == 5.25


def test_parse_current_setpoint():
    assert p.parse_current_setpoint("I1 2.000\r\n") == 2.0


def test_parse_ovp_ocp():
    # Reply prefixes differ from the query name per the manual: OVP<n>?
    # replies as "VP<n>", OCP<n>? replies as "CP<n>".
    assert p.parse_ovp("VP1 66.0\r\n") == 66.0
    assert p.parse_ocp("CP1 22.00\r\n") == 22.0


def test_parse_delta():
    assert p.parse_delta_v("DELTAV1 0.010\r\n") == 0.01
    assert p.parse_delta_i("DELTAI2 0.500\r\n") == 0.5


def test_parse_readback_voltage():
    assert p.parse_readback_voltage("12.003V\r\n") == 12.003
    assert p.parse_readback_voltage("0.000V") == 0.0


def test_parse_readback_current():
    assert p.parse_readback_current("0.250A\r\n") == 0.25


def test_parse_bool01():
    assert p.parse_bool01("1\r\n") is True
    assert p.parse_bool01("0\r\n") is False
    with pytest.raises(p.ProtocolError):
        p.parse_bool01("garbage")


def test_parse_int():
    assert p.parse_int("4\r\n") == 4
    assert p.parse_int("0") == 0
    with pytest.raises(p.ProtocolError):
        p.parse_int("nope")


def test_parse_float():
    assert p.parse_float("100\r\n") == 100.0
    assert p.parse_float("33.3") == 33.3


def test_parse_idn():
    reply = "THURLBY THANDAR, CPX400DP, 12345, 2.01-3.04\r\n"
    idn = p.parse_idn(reply)
    assert idn.manufacturer == "THURLBY THANDAR"
    assert idn.model == "CPX400DP"
    assert idn.serial == "12345"
    assert idn.version == "2.01-3.04"


def test_parse_idn_malformed():
    with pytest.raises(p.ProtocolError):
        p.parse_idn("not enough fields")


def test_parse_limit_status_bits():
    ls = p.parse_limit_status("0\r\n")
    assert not ls.constant_voltage
    assert not ls.any_trip

    ls = p.parse_limit_status("1\r\n")  # CV
    assert ls.constant_voltage
    assert not ls.constant_current

    ls = p.parse_limit_status("2\r\n")  # CC
    assert ls.constant_current

    ls = p.parse_limit_status("4\r\n")  # OV trip
    assert ls.over_voltage_trip
    assert ls.any_trip

    ls = p.parse_limit_status("8\r\n")  # OC trip
    assert ls.over_current_trip
    assert ls.any_trip

    ls = p.parse_limit_status("16\r\n")  # unregulated
    assert ls.unregulated
    assert not ls.any_trip  # unregulated alone is not a "trip"

    ls = p.parse_limit_status("64\r\n")  # hard trip
    assert ls.hard_trip
    assert ls.any_trip

    ls = p.parse_limit_status("15\r\n")  # CV+CC+OV+OC combined
    assert ls.constant_voltage
    assert ls.constant_current
    assert ls.over_voltage_trip
    assert ls.over_current_trip


# ---------------------------------------------------------------------------
# Malformed input handling
# ---------------------------------------------------------------------------


def test_parse_readback_voltage_rejects_bad_suffix():
    with pytest.raises(p.ProtocolError):
        p.parse_readback_voltage("12.003A")


def test_parse_readback_voltage_rejects_garbage():
    with pytest.raises(p.ProtocolError):
        p.parse_readback_voltage("garbageV")


def test_parse_voltage_setpoint_rejects_wrong_prefix():
    with pytest.raises(p.ProtocolError):
        p.parse_voltage_setpoint("I1 12.000")


def test_parse_voltage_setpoint_rejects_missing_value():
    with pytest.raises(p.ProtocolError):
        p.parse_voltage_setpoint("V1")


# ---------------------------------------------------------------------------
# Group response splitting
# ---------------------------------------------------------------------------


def test_split_group_response_ok():
    blob = "12.003V\r\n0.250A\r\n5.001V\r\n0.100A\r\n"
    lines = p.split_group_response(blob, 4)
    assert lines == ["12.003V", "0.250A", "5.001V", "0.100A"]


def test_split_group_response_count_mismatch():
    blob = "12.003V\r\n0.250A\r\n"
    with pytest.raises(p.ProtocolError):
        p.split_group_response(blob, 4)


def test_split_group_response_ignores_trailing_empty():
    blob = "1\r\n0\r\n"
    lines = p.split_group_response(blob, 2)
    assert lines == ["1", "0"]
