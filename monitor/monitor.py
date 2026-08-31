"""Poll a MUST solar inverter over Modbus and write samples to InfluxDB.

Runs as a long-lived daemon. It used to be a one-shot script fired by cron
every minute, which meant a fresh interpreter and a fresh serial port per
sample, a hard 60s floor on resolution, and no way to stop two runs from
fighting over /dev/ttyUSB0 if one overran its minute.
"""

import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone

from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBClientError, InfluxDBServerError
from requests.exceptions import RequestException

from ups import UPS, must_bms, must_pv1800  # must_ep3000,  must_ph18_5248

SUPPORTED_INVERTERS = {
    "must-pv1800": must_pv1800.MustPV1800
}

USB_DEVICE = os.environ.get("USB_DEVICE", "/dev/ttyUSB0")

DB_HOST = os.environ.get("DB_HOST", "influxdb")
DB_PORT = int(os.environ.get("DB_PORT", "8086"))
DB_USERNAME = os.environ.get("DB_USERNAME", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "root")
DB_NAME = os.environ.get("DB_NAME", "ups")
INVERTER_MODEL = os.environ.get("INVERTER_MODEL", "must-pv1800")

# Path to the battery BMS adapter, e.g. a /dev/serial/by-path entry. Empty
# disables battery reading entirely, which is the default.
BATTERY_DEVICE = os.environ.get("BATTERY_DEVICE", "")

# Seconds between samples. The cron version was locked to 60.
SAMPLE_INTERVAL = float(os.environ.get("SAMPLE_INTERVAL", "30"))
# Reopen the serial port after this many consecutive failed samples.
RECONNECT_AFTER = int(os.environ.get("RECONNECT_AFTER", "3"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

log = logging.getLogger("monitor")

# Set by the signal handler; also used as an interruptible sleep so that
# `docker stop` does not have to wait out a full sample interval.
shutdown = threading.Event()


def implausible(sample) -> list:
    """Internal contradictions that mean a field decoded wrongly.

    Three sign bugs have reached production (gridPower, output_w, bat_amps),
    each producing values near 65535 that looked like real numbers and quietly
    poisoned energy totals. These checks cost nothing per sample and turn the
    next one into a log line instead of a corrupted dashboard.
    """
    problems = []
    if sample.output_va > 0 and sample.output_w > sample.output_va * 1.05:
        problems.append("output_w {0} exceeds output_va {1}".format(
            sample.output_w, sample.output_va))
    for name, value, low, high in (
            ("bat_volts", sample.bat_volts, 0, 80),
            ("output_w", sample.output_w, -20000, 20000),
            ("output_va", sample.output_va, 0, 20000),
            ("load_percent", sample.load_percent, 0, 200),
            ("gridvoltage", sample.gridvoltage, 0, 400)):
        if not low <= value <= high:
            problems.append("{0}={1} outside {2}..{3}".format(name, value, low, high))
    return problems


def battery_fields(battery) -> dict:
    """BMS readings, prefixed so they cannot collide with inverter fields."""
    if battery is None:
        return {}
    fields = {
        "bms_soc": int(battery.soc),
        "bms_soh": int(battery.soh),
        "bms_current": float(battery.current),
        "bms_voltage": float(battery.voltage),
        "bms_remaining_ah": float(battery.remaining_ah),
        "bms_full_ah": float(battery.full_ah),
        "bms_cycles": int(battery.cycles),
        "bms_temp_1": float(battery.temp_1),
        "bms_temp_2": float(battery.temp_2),
        "bms_cell_min": float(battery.cell_min),
        "bms_cell_max": float(battery.cell_max),
        # The single most useful derived number: cell spread widens long before
        # a pack fails outright.
        "bms_cell_delta": float(battery.cell_delta),
    }
    for n, volts in enumerate(battery.cells, start=1):
        fields["bms_cell_{0:02d}".format(n)] = float(volts)
    return fields


def build_point(sample, battery=None) -> dict:
    """One point per cycle, carrying whatever was read successfully.

    The inverter and the BMS are separate devices on separate adapters, and one
    can fail while the other works -- the inverter link was dead for a day while
    the battery answered fine. So `sample` may be None, in which case the point
    carries only BMS fields and the state tag records that the inverter was
    unreachable rather than inventing a status.
    """
    if sample is None:
        return {
            "measurement": "logs",
            "tags": {"host": INVERTER_MODEL, "state": "NoComms"},
            "time": datetime.now(timezone.utc).isoformat(),
            "fields": battery_fields(battery),
        }
    # Raw debug registers ride alongside as reg_<address>. Integers, so they
    # cannot collide in type with anything already written.
    extra_fields = {"reg_{0}".format(a): int(v) for a, v in sample.extra.items()}
    return {
        "measurement": "logs",
        "tags": {
            "host": INVERTER_MODEL,
            "state": sample.state
        },
        "time": datetime.now(timezone.utc).isoformat(),
        "fields": {
            "bat_volts": sample.bat_volts,
            "bat_amps": sample.bat_amps,
            "ac": sample.ac,
            "load_percent": sample.load_percent,
            "output_va": sample.output_va,
            "output_w": sample.output_w,
            "temp": sample.temp,
            "pv_Voltage": sample.pvVoltage,
            "radiatorTemp": sample.radiatorTemp,
            "pvChargeCurrent": sample.pvChargeCurrent,
            "pvBattVoltage": sample.pvBattVoltage,
            "pvChargePower": sample.pvChargePower,
            "inverterState": sample.state,
            "gridState": sample.gridState,
            "gridPower": sample.gridPower,
            "accDischargerPower": sample.accdischargerpower,
            "accLoadPower": sample.accloadpower,
            "accSelfusePower": sample.accselfusepower,
            "gridVoltage": sample.gridvoltage,
            "gridCurrent": sample.gridcurrent,
            **extra_fields,
            **battery_fields(battery),
        }
    }


def handle_signal(signum, _frame):
    log.info("received signal %d, shutting down", signum)
    shutdown.set()


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if INVERTER_MODEL not in SUPPORTED_INVERTERS:
        log.error("Unknown inverter model: %s (supported: %s)",
                  INVERTER_MODEL, ", ".join(sorted(SUPPORTED_INVERTERS)))
        return 1

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("polling %s on %s every %.0fs, writing to %s:%d/%s",
             INVERTER_MODEL, USB_DEVICE, SAMPLE_INTERVAL,
             DB_HOST, DB_PORT, DB_NAME)

    client = InfluxDBClient(DB_HOST, DB_PORT, DB_USERNAME, DB_PASSWORD,
                            DB_NAME, timeout=10, retries=3)

    try:
        inverter: UPS = SUPPORTED_INVERTERS[INVERTER_MODEL](USB_DEVICE)
    except Exception:
        log.exception("could not open %s", USB_DEVICE)
        return 1

    battery_reader = None
    if BATTERY_DEVICE:
        try:
            battery_reader = must_bms.MustBMS(BATTERY_DEVICE)
            log.info("also reading the battery BMS on %s", BATTERY_DEVICE)
        except Exception:
            log.exception("could not open the BMS on %s; continuing without it",
                          BATTERY_DEVICE)
    else:
        log.info("BATTERY_DEVICE unset; not reading the BMS")

    consecutive_failures = 0
    last_failure = None
    battery_failure = None

    while not shutdown.is_set():
        started = datetime.now(timezone.utc)

        # Read the BMS first and independently. The two devices fail
        # separately: the inverter link was down for a day while the battery
        # answered normally, and losing SOC because the inverter is mute would
        # be the wrong trade.
        battery = None
        if battery_reader is not None:
            try:
                battery = battery_reader.sample()
            except Exception as exc:
                signature = "{0}: {1}".format(type(exc).__name__, exc)
                if signature != battery_failure:
                    log.exception("BMS read failed")
                    battery_failure = signature
                else:
                    log.warning("BMS read failed (unchanged): %s", signature)
                try:
                    battery_reader.reconnect()
                except Exception:
                    pass
            else:
                if battery_failure is not None:
                    log.info("BMS recovered")
                    battery_failure = None

        try:
            sample = inverter.sample()
        except Exception as exc:
            consecutive_failures += 1
            # The adapter is powered from the inverter, so an inverter outage
            # takes the serial device with it -- for hours, at one failure per
            # SAMPLE_INTERVAL. A full traceback each time buried the log in
            # 989KB of identical stacks during one night's outage, so repeat
            # the traceback only when the failure changes.
            signature = "{0}: {1}".format(type(exc).__name__, exc)
            if signature != last_failure:
                log.exception("sample failed (%d consecutive)", consecutive_failures)
                last_failure = signature
            else:
                log.warning("sample failed (%d consecutive, unchanged): %s",
                            consecutive_failures, signature)
            if consecutive_failures >= RECONNECT_AFTER:
                try:
                    inverter.reconnect()
                    consecutive_failures = 0
                    last_failure = None
                except Exception as reconnect_exc:
                    log.warning("reconnect failed, retrying next cycle: %s",
                                reconnect_exc)
            if battery is not None:
                log.info("Battery: SOC %d%%, %.2fV, %.2fA, cell spread %.3fV",
                         battery.soc, battery.voltage, battery.current,
                         battery.cell_delta)
                try:
                    client.write_points([build_point(None, battery)])
                except (InfluxDBClientError, InfluxDBServerError, RequestException):
                    log.exception("write to InfluxDB failed, BMS sample dropped")
        else:
            if last_failure is not None:
                log.info("recovered after %d failed samples", consecutive_failures)
            consecutive_failures = 0
            last_failure = None
            for problem in implausible(sample):
                log.warning("implausible reading, check the decode: %s", problem)
            log.info("Measured: %s", sample)
            if battery is not None:
                log.info("Battery: SOC %d%%, %.2fV, %.2fA, cell spread %.3fV",
                         battery.soc, battery.voltage, battery.current,
                         battery.cell_delta)
            point = build_point(sample, battery)
            # A database outage must not cost us the next sample too, so the
            # write gets its own guard.
            try:
                client.write_points([point])
            except (InfluxDBClientError, InfluxDBServerError, RequestException):
                log.exception("write to InfluxDB failed, sample dropped")

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        shutdown.wait(max(0.0, SAMPLE_INTERVAL - elapsed))

    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
