import json
import logging
import os
import time

from . import Sample, UPS

log = logging.getLogger(__name__)

# Set DUMP_REGISTERS=1 to write each raw register block to DUMP_DIR as JSON.
# Replaces the block of commented-out dump code this file used to carry.
DUMP_REGISTERS = os.environ.get("DUMP_REGISTERS", "").lower() in ("1", "true", "yes")
DUMP_DIR = os.environ.get("DUMP_DIR", "/tmp")

# The inverter needs a breather between block reads. The original comment said
# 2s was ideal and that 1s produced occasional timeouts; 3s is what has been
# running in production, so it stays the default. Tunable now that
# UPS.read_registers retries, but lower it carefully.
INTER_READ_DELAY = float(os.environ.get("INTER_READ_DELAY", "3"))

# 15208 "Charger power" is 1 W per count, NOT the 0.1 W the vendor register map
# below claims. Measured on hardware 2026-08-25: recorded 0.0477 kW against
# pvBattVoltage 25.1 V * pvChargeCurrent 18.9 A = 0.474 kW, a ratio of 10.0x
# across every sample.
#
# The original code divided by 1000 and was right. It was changed to 10000 to
# match the vendor documentation, which is simply wrong for this firmware --
# so trust this constant over the comment block in sample() below.
CHARGER_POWER_TO_KW = 1000.0

# Modbus link parameters. Overridable because they are the first things that
# can differ after an inverter swap or firmware change; run probe.py --scan if
# the defaults stop responding.
MODBUS_SLAVE_ID = int(os.environ.get("MODBUS_SLAVE_ID", "4"))
MODBUS_BAUD_RATE = int(os.environ.get("MODBUS_BAUD_RATE", "19200"))

STATES = {
    0: "PowerOn",
    1: "SelfTest",
    2: "OffGrid",
    3: "GridTie",
    4: "ByPass",
    5: "Stop",
    6: "GridCharging",
}


def accumulated_kwh(high: int, low: int) -> float:
    """Combine a high/low accumulated-energy register pair into kWh.

    The vendor map labels 'high' as 1 kWh per count and 'low' as 0.1 kWh per
    count, which cannot both be true -- 'low' alone spans 0..6553.5 kWh, so a
    1 kWh 'high' would be swamped by it. Treating the pair as the two halves of
    a 32-bit counter in 0.1 kWh units is the reading consistent with the
    high/low naming, and it is identical to the old behaviour while high == 0
    (which it will be until lifetime totals pass 6553.5 kWh). That keeps
    existing dashboards continuous today and correct after the rollover that
    would have broken them.

    VERIFY: compare against the lifetime totals on the inverter's own LCD. If
    they disagree, the other plausible reading is high * 1000 + low * 0.1.
    """
    return ((high << 16) | low) * 0.1


def signed16(value: int) -> int:
    """Interpret a raw 16-bit register as a signed two's-complement value."""
    return value - 65536 if value >= 32768 else value


class MustPV1800(UPS):
    """Driver for the MUST PV18 family (PV18-3024, PV18-3224, ...).

    The PV18 models share one Modbus register map; the model number affects the
    VA rating and battery voltage the numbers land in, not their addresses.
    Confirm with probe.py after changing units -- see README.
    """

    def __init__(self, device_path: str, device_id: int = MODBUS_SLAVE_ID,
                 baud_rate: int = MODBUS_BAUD_RATE):
        super().__init__(device_path, device_id, baud_rate)

    def _dump(self, name: str, registers: list) -> None:
        if not DUMP_REGISTERS:
            return
        path = os.path.join(DUMP_DIR, "{0}_data.json".format(name))
        try:
            with open(path, "w") as handle:
                json.dump({"StateOfCharge": registers}, handle)
        except OSError as exc:
            log.warning("could not write register dump %s: %s", path, exc)

    def sample(self) -> Sample:
        # 15205: ["PV voltage", 0.1, "V"],
        # 15206: ["Battery voltage", 0.1, "V"],
        # 15207: ["Charger current", 0.1, "A"],
        # 15208: ["Charger power", 0.1, "W"],   <- WRONG: measured as 1 W/count
        # 15209 : ["Radiator temperature", 1, "°C"],
        # 15210 : ["External temperature", 1, "°C"],
        # 15211: ["Battery Relay", 1, ""],
        # 15212: ["PV Relay", 1, ""],

        # 25205: ["Battery voltage", 0.1, "V"],
        # 25206: ["Inverter voltage", 0.1, "V"],
        # 25207: ["Grid voltage", 0.1, "V"],
        # 25208: ["BUS voltage", 0.1, "V"],
        # 25209: ["Control current", 0.1, "A"],
        # 25210: ["Inverter current", 0.1, "A"],
        # 25211: ["Grid current", 0.1, "A"],
        # 25212: ["Load current", 0.1, "A"],
        # 25213: ["Inverter power(P)", 1, "W"],
        # 25214: ["Grid power(P)", 1, "W"],
        # 25215: ["Load power(P)", 1, "W"],
        # 25216: ["Load percent", 1, "%"],
        # 25217: ["Inverter complex power(S)", 1, "VA"],
        # 25218: ["Grid complex power(S)", 1, "VA"],
        # 25219: ["Load complex power(S)", 1, "VA"],
        # 25221: ["Inverter reactive power(Q)", 1, "var"],
        # 25222: ["Grid reactive power(Q)", 1, "var"],
        # 25223: ["Load reactive power(Q)", 1, "var"],
        # 25225: ["Inverter frequency", 0.01, "Hz"],
        # 25226: ["Grid frequency", 0.01, "Hz"],
        # 25233: ["AC radiator temperature", 1, "°C"],
        # 25234: ["Transformer temperature", 1, "°C"],
        # 25235: ["DC radiator temperature", 1, "°C"],
        # 25237: ["Inverter relay state", 1, ""],
        # 25238: ["Grid relay state", 1, ""],
        # 25239: ["Load relay state", 1, ""],
        # 25240: ["N_Line relay state", 1, ""],
        # 25241: ["DC relay state", 1, ""],
        # 25242: ["Earth relay state", 1, ""],
        # 25245: ["Accumulated charger power high", 1, "kWh"],
        # 25246: ["Accumulated charger power low", 0.1, "kWh"],
        # 25247: ["Accumulated discharger power high", 1, "kWh"],
        # 25248: ["Accumulated discharger power low", 0.1, "kWh"],
        # 25249: ["Accumulated buy power high", 1, "kWh"],
        # 25250: ["Accumulated buy power low", 0.1, "kWh"],
        # 25251: ["Accumulated sell power high", 1, "kWh"],
        # 25252: ["Accumulated sell power low", 0.1, "kWh"],
        # 25253: ["Accumulated load power high", 1, "kWh"],
        # 25254: ["Accumulated load power low", 0.1, "kWh"],
        # 25255: ["Accumulated self_use power high", 1, "kWh"],
        # 25256: ["Accumulated self_use power low", 0.1, "kWh"],
        # 25257: ["Accumulated PV_sell power high", 1, "kWh"],
        # 25258: ["Accumulated PV_sell power low", 0.1, "kWh"],
        # 25259: ["Accumulated grid_charger power high", 1, "kWh"],
        # 25260: ["Accumulated grid_charger power low", 0.1, "kWh"],
        # 25271: ["Hardware version", 1, ""],
        # 25272: ["Software version", 1, ""],
        # 25273: ["Battery power", 1, "W"],
        # 25274: ["Battery current", 1, "A"],

        soc_15200 = self.read_registers(15200, 75)
        time.sleep(INTER_READ_DELAY)
        soc_25200 = self.read_registers(25200, 75)

        self._dump("soc_15200", soc_15200)
        self._dump("soc_25200", soc_25200)

        pvVoltage = soc_15200[5] / 10
        radiatorTemp = soc_15200[9]
        pvChargeCurrent = soc_15200[7] / 10
        pvChargePower = soc_15200[8] / CHARGER_POWER_TO_KW
        pvBattVoltage = soc_15200[6] / 10
        batVolts = soc_25200[5] / 10.0
        # Kept as whole volts so the existing InfluxDB field stays an integer;
        # gridvoltage below carries the same register at 0.1 V resolution.
        inputVolts = soc_25200[7] // 10
        batAmps = signed16(soc_25200[74])
        loadPercent = soc_25200[16]
        outputVA = soc_25200[19]
        outputW = soc_25200[15]
        tempInt = soc_25200[33]
        state_code = soc_25200[1]
        state = STATES.get(state_code, "Unknown({0})".format(state_code))
        if state_code not in STATES:
            log.warning("undocumented inverter state code %d", state_code)
        gridState = soc_25200[38]
        # 25214 is signed: the inverter reports grid flow direction by sign, so
        # a raw read wraps negatives into 32768..65535. Seen in production as
        # gridPower=64626 when the real value was -910 W (cross-checked against
        # gridVoltage 237.8 x gridCurrent 4.1 = 975 VA against a 914 W load).
        gridPower = signed16(soc_25200[14])
        accdischargerpower = accumulated_kwh(soc_25200[47], soc_25200[48])
        accloadpower = accumulated_kwh(soc_25200[53], soc_25200[54])
        accselfusepower = accumulated_kwh(soc_25200[55], soc_25200[56])
        gridvoltage = soc_25200[7] / 10
        gridcurrent = soc_25200[11] / 10

        # Keyword arguments deliberately: this record has 20 fields including
        # the near-identical pvVoltage/pvBattVoltage and gridPower/gridvoltage,
        # and a misplaced positional would write plausible-looking garbage.
        return Sample(
            bat_volts=batVolts,
            bat_amps=batAmps,
            ac=inputVolts,
            load_percent=loadPercent,
            output_va=outputVA,
            output_w=outputW,
            temp=tempInt,
            state=state,
            pvVoltage=pvVoltage,
            pvChargePower=pvChargePower,
            radiatorTemp=radiatorTemp,
            pvChargeCurrent=pvChargeCurrent,
            pvBattVoltage=pvBattVoltage,
            gridState=gridState,
            gridPower=gridPower,
            accdischargerpower=accdischargerpower,
            accloadpower=accloadpower,
            accselfusepower=accselfusepower,
            gridvoltage=gridvoltage,
            gridcurrent=gridcurrent,
        )
