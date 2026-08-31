"""Shared inverter plumbing: the Sample record and the serial/Modbus transport."""

import glob
import logging
import os
import time
from dataclasses import dataclass, field

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
    # Raw values for registers named in DEBUG_REGISTERS, keyed by address.
    # For identifying undocumented registers by watching them over a real
    # charge/discharge cycle. Empty unless that variable is set.
    extra: dict = field(default_factory=dict)


class UPS(object):
    def __init__(self, device_path: str, device_id: int, baud_rate: int,
                 timeout: float = 0.5):
        self.device_path = device_path
        self.device_id = device_id
        self.baud_rate = baud_rate
        self.timeout = timeout

        self.scc = None
        self.connect()

    # /dev/serial/by-path and by-id name a specific physical device: by-path
    # by USB socket, by-id by chipset. If such a path disappears, that device
    # is gone -- another adapter is a *different* device, not the same one
    # renumbered.
    STABLE_PREFIXES = ("/dev/serial/by-path/", "/dev/serial/by-id/")

    def resolve_device(self) -> str:
        """Find the adapter, tolerating renumbering but never substituting.

        A bare /dev/ttyUSBn is an unstable name: the adapter is powered from
        the inverter, so an inverter outage de-enumerates it and the kernel may
        hand it back as a different node. Following that is correct.

        A by-path or by-id name is different -- it identifies the device, so if
        it is missing the device is missing. Substituting the only other
        adapter present is how this code came to poll a battery BMS dongle with
        the inverter's Modbus settings on 2026-08-31, after the inverter's
        adapter dropped off the bus and a second one was plugged in.
        """
        if os.path.exists(self.device_path):
            return self.device_path

        if self.device_path.startswith(self.STABLE_PREFIXES):
            log.error(
                "%s is gone. That path names one specific device, so any other "
                "adapter is a different device -- not substituting. Reconnect "
                "it, or point USB_DEVICE at the right path.", self.device_path)
            return self.device_path

        candidates = sorted(glob.glob("/dev/ttyUSB*"))
        if len(candidates) == 1:
            log.warning("%s is gone; following the only ttyUSB node %s",
                        self.device_path, candidates[0])
            return candidates[0]
        if len(candidates) > 1:
            log.error("%s is gone and %d ttyUSB nodes exist (%s). Refusing to "
                      "guess -- set USB_DEVICE to a /dev/serial/by-path entry.",
                      self.device_path, len(candidates), ", ".join(candidates))
        return self.device_path

    def connect(self) -> None:
        """Open the serial port and bind a Modbus instrument to it."""
        self.scc = minimalmodbus.Instrument(self.resolve_device(), self.device_id)
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
