"""Hunt for undocumented registers -- battery SOC in particular.

The register map in must_pv1800.py covers only what someone transcribed from a
vendor document. This inverter answers for far more than that: the driver reads
75 registers per block and decodes about 20. If the battery BMS is wired to the
inverter over RS485/CAN and the inverter is configured for a lithium battery,
state of charge is almost certainly in there somewhere -- just unlabelled.

    docker compose stop monitor
    docker compose run --rm monitor python3 scan_registers.py
    docker compose start monitor

Stop the daemon first: neither pyserial nor minimalmodbus opens the port
exclusively, so two readers corrupt each other.

Reads only. It never writes to the inverter.

The output lists every register that plausibly holds a percentage. Compare the
candidates against the SOC your inverter's LCD (or the battery's own display)
shows at that moment -- the one that matches is your register. Then add it to
must_pv1800.py.

--watch samples repeatedly, which narrows it further: SOC drifts slowly, so a
candidate that never changes over several minutes is probably a config constant
rather than a live measurement.
"""

import argparse
import os
import sys
import time

import minimalmodbus

from ups.must_pv1800 import MODBUS_BAUD_RATE, MODBUS_SLAVE_ID

USB_DEVICE = os.environ.get("USB_DEVICE", "/dev/ttyUSB0")

# Blocks worth trying. 10100 is included because the original monitor read it
# every cycle and never used the result -- plausibly someone else hunting for
# exactly this. The rest bracket the two blocks the driver already decodes.
BLOCKS = [
    (10100, 40),
    (15200, 75),
    (20100, 40),
    (25200, 75),
    (25300, 40),
]

# What a percentage looks like in a register: 0-100 raw, or 0-1000 at 0.1%.
def soc_shaped(value):
    return 0 <= value <= 100 or 0 < value <= 1000


def read_block(inst, base, count):
    try:
        return inst.read_registers(base, count)
    except (OSError, ValueError) as exc:
        return exc


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default=USB_DEVICE)
    ap.add_argument("--slave", type=int, default=MODBUS_SLAVE_ID)
    ap.add_argument("--baud", type=int, default=MODBUS_BAUD_RATE)
    ap.add_argument("--all", action="store_true",
                    help="print every register, not just percentage-shaped ones")
    ap.add_argument("--watch", type=int, metavar="N", default=0,
                    help="take N samples 60s apart and report which candidates move")
    ap.add_argument("--delay", type=float, default=3.0,
                    help="seconds between block reads (default 3, as the driver uses)")
    args = ap.parse_args()

    inst = minimalmodbus.Instrument(args.device, args.slave)
    inst.serial.baudrate = args.baud
    inst.serial.timeout = 0.5

    print("device {0}, slave {1}, {2} baud\n".format(args.device, args.slave, args.baud))

    samples = []
    rounds = max(1, args.watch)
    for round_no in range(rounds):
        if round_no:
            print("\n--- sample {0}/{1} (60s later) ---".format(round_no + 1, rounds))
            time.sleep(60)
        reading = {}
        for base, count in BLOCKS:
            result = read_block(inst, base, count)
            if isinstance(result, Exception):
                if round_no == 0:
                    print("  {0}-{1}: no response ({2})".format(
                        base, base + count - 1, type(result).__name__))
                continue
            if round_no == 0:
                print("  {0}-{1}: {2} registers".format(base, base + count - 1, count))
            for offset, value in enumerate(result):
                reading[base + offset] = value
            time.sleep(args.delay)
        samples.append(reading)

    latest = samples[-1]
    if not latest:
        print("\nNothing responded. Check the device, slave id and baud rate.")
        return 1

    battery_v = latest.get(25205)
    if battery_v:
        print("\nbattery voltage (25205) reads {0} V, for reference".format(battery_v / 10))

    print("\nRegisters that could hold a percentage:\n")
    print("  {0:<9} {1:>7} {2:>10}   {3}".format("register", "raw", "as 0.1%", "note"))
    print("  " + "-" * 60)
    known = {25216: "documented: Load percent"}
    shown = 0
    for addr in sorted(latest):
        value = latest[addr]
        if not args.all and not soc_shaped(value):
            continue
        if not args.all and value == 0:
            continue
        note = known.get(addr, "")
        if args.watch > 1:
            seen = {s.get(addr) for s in samples if addr in s}
            note = (note + "  " if note else "") + (
                "changed: {0}".format(sorted(seen)) if len(seen) > 1 else "static")
        print("  {0:<9} {1:>7} {2:>10}   {3}".format(addr, value, value / 10, note))
        shown += 1

    print("\n{0} candidate(s).".format(shown))
    print("\nCompare these against the SOC on the inverter LCD or the battery's own")
    print("display right now. A register matching that number is your SOC. If none")
    print("does, the BMS is probably not wired to the inverter over RS485/CAN, or the")
    print("inverter is not configured for a lithium battery -- in which case it has no")
    print("SOC to report and is only estimating from voltage.")
    if args.watch <= 1:
        print("\nRe-run with --watch 5 to drop candidates that never move.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
