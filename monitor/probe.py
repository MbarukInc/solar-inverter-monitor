"""One-shot diagnostic for the inverter link and register map.

Run after swapping inverters, changing firmware or re-cabling, to confirm the
unit still answers on the expected Modbus parameters and that the register map
decodes to plausible values:

    docker compose run --rm monitor python3 probe.py
    docker compose run --rm monitor python3 probe.py --scan

Reads only; it never writes to the inverter.
"""

import argparse
import logging
import os
import sys

import minimalmodbus

from ups.must_pv1800 import (MODBUS_BAUD_RATE, MODBUS_SLAVE_ID, STATES,
                             CHARGER_POWER_TO_KW, MustPV1800)

USB_DEVICE = os.environ.get("USB_DEVICE", "/dev/ttyUSB0")

# Ordered by likelihood for the MUST PV18 family.
CANDIDATE_BAUDS = [19200, 9600, 38400, 4800]
CANDIDATE_IDS = [4, 1, 2, 3, 5]

# A register every PV18 answers: the work-state word.
PROBE_REGISTER = 25201

OK, WARN, FAIL = "ok  ", "??  ", "FAIL"


def link_responds(device, slave, baud, timeout=0.4):
    inst = None
    try:
        inst = minimalmodbus.Instrument(device, slave)
        inst.serial.baudrate = baud
        inst.serial.timeout = timeout
        inst.read_registers(PROBE_REGISTER, 1)
        return True
    except (OSError, ValueError):
        return False
    finally:
        try:
            if inst is not None and inst.serial is not None:
                inst.serial.close()
        except OSError:
            pass


def scan(device):
    print("Scanning for a responding inverter (this takes a few seconds)...")
    found = []
    for baud in CANDIDATE_BAUDS:
        for slave in CANDIDATE_IDS:
            if link_responds(device, slave, baud):
                print("  RESPONDS  slave id {0} @ {1} baud".format(slave, baud))
                found.append((slave, baud))
    if not found:
        print("  nothing responded on any tried combination.")
    return found


def rng(label, value, low, high, unit="", note=""):
    """Report a value and flag it if outside its plausible range."""
    status = OK if low <= value <= high else WARN
    line = "  [{0}] {1:<26} {2:>10}  {3}".format(status, label, round(value, 2), unit)
    if status == WARN:
        line += "   <- outside {0}-{1}{2}".format(low, high, unit)
    if note:
        line += "   {0}".format(note)
    print(line)
    return status == OK


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan", action="store_true",
                    help="sweep common slave ids and baud rates")
    ap.add_argument("--device", default=USB_DEVICE)
    ap.add_argument("--slave", type=int, default=MODBUS_SLAVE_ID)
    ap.add_argument("--baud", type=int, default=MODBUS_BAUD_RATE)
    ap.add_argument("--nominal-va", type=float, default=0.0,
                    help="rated VA, e.g. 3200 for a PV18-3224, to check load percent")
    ap.add_argument("--battery-nominal", type=float, default=24.0,
                    help="nominal battery voltage (12, 24 or 48)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    print("device {0}, slave id {1}, {2} baud\n".format(
        args.device, args.slave, args.baud))

    if args.scan:
        found = scan(args.device)
        if not found:
            return 1
        if (args.slave, args.baud) not in found:
            args.slave, args.baud = found[0]
            print("\nUsing slave id {0} @ {1} baud. Set MODBUS_SLAVE_ID and "
                  "MODBUS_BAUD_RATE to keep it.\n".format(args.slave, args.baud))
    elif not link_responds(args.device, args.slave, args.baud):
        print("No response at slave id {0} @ {1} baud.".format(args.slave, args.baud))
        print("Re-run with --scan to sweep other combinations.")
        return 1

    inverter = MustPV1800(args.device, device_id=args.slave, baud_rate=args.baud)
    s = inverter.sample()
    raw25 = inverter.read_registers(25200, 75)

    bat_lo, bat_hi = args.battery_nominal * 0.75, args.battery_nominal * 1.4
    print("Decoded values ('??' means outside the plausible range, which is "
          "normal\nfor a source that is currently inactive):\n")
    rng("battery voltage", s.bat_volts, bat_lo, bat_hi, "V")
    rng("battery current", s.bat_amps, -300, 300, "A", "(negative = charging)")
    rng("pv voltage", s.pvVoltage, 0, 500, "V")
    rng("pv charge current", s.pvChargeCurrent, 0, 200, "A")
    rng("pv charge power", s.pvChargePower, 0, 20, "kW")
    rng("grid voltage", s.gridvoltage, 0, 300, "V")
    rng("grid current", s.gridcurrent, 0, 100, "A")
    rng("grid power", s.gridPower, -20000, 20000, "W")
    rng("load real power", s.output_w, 0, 20000, "W")
    rng("load apparent power", s.output_va, 0, 20000, "VA")
    rng("load percent", s.load_percent, 0, 110, "%")
    rng("inverter temperature", s.temp, -20, 120, "C")
    rng("radiator temperature", s.radiatorTemp, -20, 120, "C")

    print("\nConsistency checks:\n")
    problems = 0

    state_code = raw25[1]
    if state_code in STATES:
        print("  [{0}] state code {1} -> {2}".format(OK, state_code, s.state))
    else:
        print("  [{0}] state code {1} is not in STATES; add it to "
              "must_pv1800.py".format(WARN, state_code))
        problems += 1

    if s.output_va > 0 and s.output_w > s.output_va * 1.02:
        print("  [{0}] real power {1}W exceeds apparent power {2}VA -- registers "
              "25215/25219 may have moved".format(FAIL, s.output_w, s.output_va))
        problems += 1
    elif s.output_va > 0:
        pf = s.output_w / s.output_va
        print("  [{0}] power factor {1:.2f} (W <= VA as expected)".format(OK, pf))

    # The charger-power scale is ambiguous in the vendor map: 15208 is
    # documented at 0.1 W per count, but some firmware reports 1 W per count.
    vi_kw = s.pvBattVoltage * s.pvChargeCurrent / 1000.0
    if vi_kw > 0.05:
        ratio = s.pvChargePower / vi_kw
        if 0.5 <= ratio <= 2.0:
            print("  [{0}] charger power {1:.3f}kW matches V*I {2:.3f}kW "
                  "-- CHARGER_POWER_TO_KW is right".format(OK, s.pvChargePower, vi_kw))
        else:
            suggested = CHARGER_POWER_TO_KW * ratio
            print("  [{0}] charger power {1:.3f}kW vs V*I {2:.3f}kW (ratio {3:.1f}) "
                  "-- set CHARGER_POWER_TO_KW to about {4:.0f}".format(
                      FAIL, s.pvChargePower, vi_kw, ratio, suggested))
            problems += 1
    else:
        print("  [{0}] no PV charging right now, cannot check the charger power "
              "scale -- re-run in daylight".format(WARN))

    # While every high word is zero the 32-bit and "high + low/10" readings
    # agree, so the counters cannot be told apart yet.
    highs = {"discharger": raw25[47], "load": raw25[53], "self-use": raw25[55]}
    hot = {k: v for k, v in highs.items() if v}
    if hot:
        print("  [{0}] accumulated high words are non-zero ({1}) -- the kWh "
              "totals now depend on accumulated_kwh()'s formula. Check them "
              "against the inverter's LCD.".format(WARN, hot))
        problems += 1
    else:
        print("  [{0}] accumulated high words all zero; kWh totals are "
              "unambiguous for now".format(OK))
    print("      discharged {0} kWh, load {1} kWh, self-use {2} kWh".format(
        s.accdischargerpower, s.accloadpower, s.accselfusepower))

    if s.load_percent >= 10 and s.output_va > 0:
        inferred = s.output_va / (s.load_percent / 100.0)
        msg = "  [{0}] load {1}% at {2}VA implies a rating near {3:.0f}VA".format(
            OK, s.load_percent, s.output_va, inferred)
        if args.nominal_va:
            off = abs(inferred - args.nominal_va) / args.nominal_va
            if off > 0.2:
                msg = ("  [{0}] load {1}% at {2}VA implies about {3:.0f}VA, but "
                       "--nominal-va says {4:.0f}".format(
                           WARN, s.load_percent, s.output_va, inferred, args.nominal_va))
                problems += 1
        print(msg)
    else:
        print("  [{0}] load too low to infer the VA rating".format(WARN))

    print("\n{0}".format("Looks consistent." if not problems else
                         "{0} thing(s) to look at above.".format(problems)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
