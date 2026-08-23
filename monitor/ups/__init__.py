"""Shared inverter plumbing: the Sample record and the serial/Modbus transport."""

import logging
import time
from dataclasses import dataclass

import minimalmodbus

log = logging.getLogger(__name__)

# minimalmodbus.ModbusException and serial.SerialException both derive from
# OSError, so this pair covers bus timeouts, CRC failures, short reads and USB
# re-enumeration without pinning us to library-specific exception names.
TRANSIENT_ERRORS = (OSError, ValueError)


@dataclass
class Sample(object):
    # battery voltage
    bat_volts: float
    # battery amperage (negative while charging)
    bat_amps: int
    # input ac voltage (whole volts; see gridvoltage for 0.1V resolution)
    ac: int
    # system load in percent
    load_percent: int
    # load apparent power
    output_va: int
    # load real power
    output_w: int
    # inverter temperature
    temp: int
    # inverter state
    state: str
    # pv Voltage levels
    pvVoltage: float
    # pv power levels
    pvChargePower: float
    # pv inverter temp (comparing this figure with temp above)
    radiatorTemp: int
    # pv charging current
    pvChargeCurrent: float
    # pv charging batt level
    pvBattVoltage: float
    # Grid relay state on or off
    gridState: int
    # Grid real power
    gridPower: int
    # 25247/25248: ["Accumulated discharger power", kWh]
    accdischargerpower: float
    # 25253/25254: ["Accumulated load power", kWh]
    accloadpower: float
    # 25255/25256: ["Accumulated self_use power", kWh]
    accselfusepower: float
    # 25207: ["Grid voltage", 0.1, "V"],
    gridvoltage: float
    # 25211: ["Grid current", 0.1, "A"],
    gridcurrent: float


class UPS(object):
    def __init__(self, device_path: str, device_id: int, baud_rate: int,
                 timeout: float = 0.5):
        self.device_path = device_path
        self.device_id = device_id
        self.baud_rate = baud_rate
        self.timeout = timeout

        self.scc = None
        self.connect()

    def connect(self) -> None:
        """Open the serial port and bind a Modbus instrument to it."""
        self.scc = minimalmodbus.Instrument(self.device_path, self.device_id)
        self.scc.serial.baudrate = self.baud_rate
        self.scc.serial.timeout = self.timeout

    def reconnect(self) -> None:
        """Tear the serial port down and reopen it.

        The daemon holds one port open for its whole life, so a USB
        re-enumeration (device unplugged, hub glitch, inverter power cycle)
        would otherwise wedge us on a stale file descriptor forever.
        """
        log.warning("reopening serial port %s", self.device_path)
        try:
            if self.scc is not None and self.scc.serial is not None:
                self.scc.serial.close()
        except TRANSIENT_ERRORS as exc:
            log.warning("error closing serial port (continuing): %s", exc)
        self.connect()

    def read_registers(self, address: int, count: int, attempts: int = 3,
                       backoff: float = 0.5) -> list:
        """read_registers with retries.

        RS485 timeouts and CRC errors are routine on a long run to an inverter;
        without this a single glitch costs a whole sample.
        """
        for attempt in range(1, attempts + 1):
            try:
                return self.scc.read_registers(address, count)
            except TRANSIENT_ERRORS as exc:
                if attempt == attempts:
                    raise
                log.warning(
                    "read of %d registers at %d failed (attempt %d/%d): %s",
                    count, address, attempt, attempts, exc)
                time.sleep(backoff * attempt)

    def sample(self) -> Sample:
        raise NotImplementedError
