"""Read the MUST LP16 battery BMS directly over RS485.

The inverter exposes no state of charge -- the MUST PV18 Modbus protocol does
not define one, confirmed by scanning all 31,000 registers from 5000-36000 and
by the vendor spec containing no mention of SOC. The battery knows, and says so
on its own panel, but never publishes it to the inverter's register map.

So read the BMS directly. The pack is a MUST LP16-24200 with a PACE BMS (the
manual's own filename says 沛城, which is Pace), on the RJ45 socket beside the
CAN port. Modbus RTU, 9600 baud, slave 1.

RJ45 pinout, from the LP1600 manual and matching the PACE map:
    pin 1 = RS485-B, pin 2 = RS485-A, pin 3 = GND  (also 7 = A, 8 = B, 6 = GND)

Register meanings were confirmed on hardware 2026-08-31 by sampling three times
25s apart and checking which moved: current, voltage, remaining capacity and
all eight cells changed; SOC, SOH, cycle count, rated capacity and the voltage
limits held steady. Remaining capacity fell while current read negative, which
fixes the sign convention below.
"""

import logging
import os
from dataclasses import dataclass, field

from . import TRANSIENT_ERRORS, UPS

log = logging.getLogger(__name__)

BMS_SLAVE_ID = int(os.environ.get("BMS_SLAVE_ID", "1"))
BMS_BAUD_RATE = int(os.environ.get("BMS_BAUD_RATE", "9600"))

CELL_FIRST, CELL_COUNT = 15, 8


@dataclass
class BatterySample(object):
    # Negative is DISCHARGING here -- the opposite of the inverter's bat_amps,
    # established by watching remaining capacity fall while this read negative.
    current: float          # A
    voltage: float          # V, pack
    soc: int                # %
    soh: int                # %
    remaining_ah: float
    full_ah: float
    cycles: int
    temp_1: float           # degC
    temp_2: float           # degC
    cells: list = field(default_factory=list)   # volts, one per cell

    @property
    def cell_min(self) -> float:
        return min(self.cells) if self.cells else 0.0

    @property
    def cell_max(self) -> float:
        return max(self.cells) if self.cells else 0.0

    @property
    def cell_delta(self) -> float:
        """Spread across the pack. The number that shows imbalance early."""
        return round(self.cell_max - self.cell_min, 4) if self.cells else 0.0


class MustBMS(UPS):
    """PACE BMS in a MUST LP16 pack. Reads only; never writes."""

    def __init__(self, device_path: str, device_id: int = BMS_SLAVE_ID,
                 baud_rate: int = BMS_BAUD_RATE):
        super().__init__(device_path, device_id, baud_rate)

    def sample(self) -> BatterySample:
        # Two 16-register reads rather than one 33: 16 is what the BMS was
        # verified to answer, and a longer read is not worth the risk for one
        # saved round trip.
        low = self.read_registers(0, 16)
        high = self.read_registers(16, 17)

        def signed(value):
            return value - 65536 if value >= 32768 else value

        cells = [low[CELL_FIRST] / 1000.0]
        cells += [high[i] / 1000.0 for i in range(CELL_COUNT - 1)]

        return BatterySample(
            current=signed(low[0]) / 100.0,
            voltage=low[1] / 100.0,
            soc=low[2],
            soh=low[3],
            remaining_ah=low[4] / 100.0,
            full_ah=low[5] / 100.0,
            cycles=low[7],
            temp_1=high[31 - 16] / 10.0,
            temp_2=high[32 - 16] / 10.0,
            cells=[round(c, 3) for c in cells],
        )
