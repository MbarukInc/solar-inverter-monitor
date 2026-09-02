# solar-inverter-monitor

Polls a MUST PV18-series solar inverter (currently a **PV18-3224 VPM II**;
previously a PV18-3024 VPM) over Modbus RTU and writes samples to InfluxDB. Runs as a Docker container on a
Raspberry Pi with the inverter's RS485/USB adapter on `/dev/ttyUSB0`.

## Configuration

Settings come from repository **secrets** (sensitive) and **variables**
(everything else). The deploy workflow renders them into `.env` on the Pi, so
that is the only place to change them — editing `.env` on the Pi directly will
be overwritten by the next deploy.

Repository secrets (Settings → Secrets and variables → Actions → Secrets):

| Secret | Notes |
| --- | --- |
| `DB_USERNAME` | InfluxDB username |
| `DB_PASSWORD` | InfluxDB password |
| `RASPBERRY_PI_IP` | already configured |
| `SOLAR_APP_PATH` | already configured |
| `PI_SSH_KEY` | Private deploy key for `pi@<RASPBERRY_PI_IP>` |
| `PI_KNOWN_HOSTS` | `ssh-keyscan -H <PI_IP>` output for **every** deploy host |
| `BMS_PI_IP` | Host running the battery reader. Unset skips that target entirely |
| `BMS_APP_PATH` | Absolute path to the checkout on the BMS host |

`PI_SSH_KEY` and `PI_KNOWN_HOSTS` live here rather than being mounted into the
runner because the runner is shared across the org — a key baked into the pod
would let any repo in `MbarukInc` reach the Pi. Capture the host key from a
machine you trust on the LAN; it is the trust anchor for every future deploy.

Repository variables (same page → Variables). Only `DB_HOST` is required; the
rest fall back to the defaults in `docker-compose.yml` when unset:

| Variable | Default |
| --- | --- |
| `DB_HOST` | *(required)* |
| `DB_PORT` | `8086` |
| `DB_NAME` | `ups` |
| `SAMPLE_INTERVAL` | `30` |
| `LOG_LEVEL` | `INFO` |
| `USB_DEVICE` | `/dev/ttyUSB0` |
| `MODBUS_SLAVE_ID` | `4` |
| `MODBUS_BAUD_RATE` | `19200` |
| `BMS_USB_DEVICE` | *(unset)* — see below |
| `BMS_BATTERY_DEVICE` | *(unset)* — `by-path` of the BMS adapter |
| `BMS_DB_HOST` | falls back to `DB_HOST` |

Rotating the InfluxDB password means updating the secret and re-running
`Build_Container` — no SSH to the Pi required. The workflow fails before it
touches the Pi if a required secret or variable is missing, and names it.

Note that this is a convenience and provenance win, not a security one: the Pi
still needs the values at container-create time, so they land on its disk as a
plaintext `.env` (mode 600) either way.

### Manual setup

For a fresh Pi or local testing, bring it up without the workflow:

```bash
cp .env.example .env
# edit .env: InfluxDB host, credentials, database
docker compose up -d --build
docker compose logs -f monitor
```

Compose interpolates `.env` values, so a literal `$` in a hand-written password
must be doubled (`pa$word` → `pa$$word`). The workflow handles that escaping
itself.

## Environment variables read by the container

| Variable | Default | Notes |
| --- | --- | --- |
| `DB_HOST` | *(required)* | InfluxDB host |
| `DB_PORT` | `8086` | |
| `DB_USERNAME` | *(required)* | |
| `DB_PASSWORD` | *(required)* | |
| `DB_NAME` | `ups` | |
| `USB_DEVICE` | `/dev/ttyUSB0` | Host path to the RS485 adapter |
| `INVERTER_MODEL` | `must-pv1800` | Must be a key of `SUPPORTED_INVERTERS` |
| `SAMPLE_INTERVAL` | `30` | Seconds between samples |
| `RECONNECT_AFTER` | `3` | Consecutive failures before reopening the port |
| `INTER_READ_DELAY` | `3` | Seconds between the two register block reads |
| `MODBUS_SLAVE_ID` | `4` | Only change if `probe.py --scan` finds another |
| `MODBUS_BAUD_RATE` | `19200` | Same |
| `BATTERY_DEVICE` | unset | by-path of the RS485 adapter on the battery BMS; empty disables it |
| `BMS_SLAVE_ID` | `1` | BMS Modbus slave id |
| `BMS_BAUD_RATE` | `9600` | BMS baud rate |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail |
| `DUMP_REGISTERS` | unset | Set to `1` to dump raw register blocks as JSON |
| `DUMP_DIR` | `/tmp` | Where those dumps land |

## How it runs

A single long-lived process holds the serial port open and samples on a fixed
cadence. It was previously a one-shot script invoked by cron every minute,
which meant a new interpreter and a newly opened serial port per sample, a hard
60-second floor on resolution, and no protection against two runs overlapping
on `/dev/ttyUSB0`.

Register reads retry before giving up, and after `RECONNECT_AFTER` consecutive
failed samples the serial port is closed and reopened — a USB re-enumeration
would otherwise leave the daemon wedged on a dead file descriptor forever.
An InfluxDB outage is logged and the sample dropped; it does not stop polling.

## Deployment

Jobs run on the shared org runner (`runs-on: mbarukville`), an ARC runner scale
set in the home MicroK8s cluster. That setup lives in its own repository, not
here — see the runner repo for the cluster-side manifests.

Pushing to `main` triggers `Deploy_Latest_code`; `Build_Container` does the same
thing on demand. Both are thin wrappers around
`.github/actions/deploy-to-pi`, which validates the configuration, rsyncs the
repo, writes `.env`, and rebuilds — kept in one place so the two workflows
cannot drift apart the way they previously did.

**The container no longer bind-mounts the source**, so a code change requires a
rebuild — an rsync alone is no longer enough.

## After changing inverter, firmware or cabling

The PV18 models share one Modbus register map — the model number changes the VA
rating and battery voltage the readings land in, not the register addresses — so
the driver is expected to carry over between PV18 units. Expected is not
verified, though, and a swap is exactly when the link parameters and the map can
move. Confirm with the bundled probe before trusting the data:

```bash
docker compose stop monitor
docker compose build monitor
docker compose run --rm monitor python3 probe.py --nominal-va 3200
docker compose start monitor
```

Stop the daemon first. Neither pyserial nor minimalmodbus opens the port
exclusively, so the probe and a running monitor would both hold
`/dev/ttyUSB0` and corrupt each other's replies.

It reads only, never writes. It reports every decoded value against a plausible
range and then cross-checks the things that would otherwise fail silently:
whether real power exceeds apparent power (which would mean registers have
moved), whether the charger-power scale still matches `pvBattVoltage *
pvChargeCurrent`, whether any state code is missing from `STATES`, and what VA
rating the load percentage implies — a PV18-3224 should come out near 3200.

If nothing answers, sweep the common link parameters:

```bash
docker compose run --rm monitor python3 probe.py --scan
```

Set `MODBUS_SLAVE_ID` and `MODBUS_BAUD_RATE` to whatever it finds.

Note that `INVERTER_MODEL` is a driver key, not a model number: the only valid
value is `must-pv1800`, which selects the PV18-family driver. Setting it to an
actual model string makes the container exit 1. It is also the `host` tag on
every InfluxDB point, so changing it starts a new series and splits your Grafana
history.

## Grafana

The dashboard lives in [`grafana/`](grafana/), tracked alongside the code that
produces the fields it queries. See that directory's README for the panel/field
map and for why exports are normalised before committing.

## Checking configuration is actually deliverable

```bash
python3 monitor/check_env_plumbing.py
```

A setting has to appear in four places to work: read by the code, declared in
`docker-compose.yml`, passed through `.github/actions/deploy-to-pi`, and mapped
from a repository variable in both workflows. Miss one and setting the variable
does nothing, silently — which has happened twice (`USB_DEVICE` was hardcoded
past its own variable, `DEBUG_REGISTERS` was absent from all three deploy
layers). This script cross-references them and exits non-zero on a gap.

## Two hosts, one device each

The inverter and the battery are read by **separate Pis**, and that is not a
convenience — the two adapters cannot share a host.

Plugging both into one machine leaves the battery working and the inverter
mute: its port returns a continuously low line (~110 bytes/s of `0x00`,
where a healthy idle RS485 line yields zero) and answers no Modbus at any baud.
Removing either adapter restores the other. Reproduced on both a Pi Model B+
and a Pi 4, on every USB port and both socket types, with autosuspend off and
a fresh `ch341` bind. The inverter is always the one that fails.

The likeliest cause is a ground loop rather than current draw: the inverter's
USB-serial chip is *inside* the mains-referenced inverter, while the battery
adapter is referenced to the pack's negative terminal, so one host bridges the
two grounds. A powered hub does not address that; a USB isolator on the battery
link would. Until then, one device per host.

Deploys are a matrix over two targets:

| Target | Reads | Configured by |
| --- | --- | --- |
| `inverter` | inverter over Modbus | `RASPBERRY_PI_IP`, `SOLAR_APP_PATH`, `USB_DEVICE` |
| `bms` | battery BMS only | `BMS_PI_IP`, `BMS_APP_PATH`, `BMS_BATTERY_DEVICE` |

`BMS_USB_DEVICE` must name a `by-path` that **does not exist** (for example
`/dev/serial/by-path/no-inverter-on-this-host`). Opening it then fails and
`monitor.py` falls back to reading the battery alone. Leaving it unset is worse
than useless: `docker-compose.yml` defaults it to `/dev/ttyUSB0`, which is the
BMS adapter, and the inverter reader would hammer it at the wrong baud rate.

`Build_Container` takes a **target** input (`all`, `inverter`, `bms`) so one
host can be rebuilt without touching the other. A target whose host secret is
unset is skipped with a notice rather than failing the run.

## Battery BMS (state of charge)

The inverter exposes **no** state of charge — the MUST PV18 Modbus protocol does
not define one. The battery does, over its own RS485 port, so the monitor reads
the BMS directly as a second device.

MUST LP16-24200, PACE BMS, **Modbus RTU, 9600 baud, slave 1**. RJ45 pinout from
the LP1600 manual: **pin 1 = RS485-B, pin 2 = RS485-A, pin 3 = GND** (pins 7/8
carry A/B as well, 6 is a second ground). On a T-568B patch lead that is
orange-white to B, orange to A, green-white to GND.

Set `BATTERY_DEVICE` to the adapter's `/dev/serial/by-path` entry to enable it.
Use by-path, not by-id: CH340 adapters carry no serial number, so two of them
are indistinguishable by id.

| Register | Meaning |
| --- | --- |
| 0 | pack current, ×0.01 A — **negative is discharging**, the opposite of the inverter's `bat_amps` |
| 1 | pack voltage, ×0.01 V |
| 2 | **SOC %** |
| 3 | SOH % |
| 4 | remaining capacity, ×0.01 Ah |
| 5, 6 | full / design capacity, ×0.01 Ah |
| 7 | cycle count |
| 15–22 | eight cell voltages, mV |
| 31, 32 | temperatures, ×0.1 °C |

Confirmed on hardware by sampling three times 25s apart: current, voltage,
remaining capacity and all eight cells moved; SOC, SOH, cycles, rated capacity
and the limits held. Two independent cross-checks: the eight cells sum to the
reported pack voltage, and SOC × design capacity matches remaining capacity.

Written as `bms_*` fields, plus `bms_cell_delta` — the spread between highest
and lowest cell, which widens long before a pack fails outright.

The BMS is read independently of the inverter. Either can fail without taking
the other down; when the inverter is unreachable the point carries only `bms_*`
fields and the `state` tag reads `NoComms`.

## Finding undocumented registers (battery SOC)

The register map in `monitor/ups/must_pv1800.py` is only what someone
transcribed from a vendor document. The inverter answers for more than that:
the driver reads 75 registers per block and decodes about 20. `soc` was dropped
as a field because the old code hardcoded it to `0`, not because the inverter
cannot report it.

If your battery's BMS is wired to the inverter over RS485/CAN **and** the
inverter is configured for a lithium battery type, state of charge is likely
sitting in an unlabelled register. To find it:

```bash
docker compose stop monitor && docker compose run --rm monitor python3 scan_registers.py --watch 5 && docker compose start monitor
```

It reads only, never writes. It lists every register holding a percentage-shaped
value and, with `--watch`, drops the ones that never move. Compare the survivors
against the SOC on the inverter's LCD at that moment; whichever matches is your
register, and can then be decoded in `must_pv1800.py` and re-added as a field.

If nothing matches, the inverter has no SOC to report — with no BMS link it only
estimates from voltage, which is what `bat_volts` already gives you.

### Result on this unit (2026-08-24)

**No SOC register found.** Scanned blocks 10100, 15200, 20100, 25200 and 25300
with `--watch 4`. Every register whose value moved is one the driver already
decodes:

```
25205 battery volts   25215 load W    25219 load VA    25233 radiator degC
25211 grid A          25216 load %    25222 grid var   25250 acc buy kWh
25212 load A          25218 grid VA   25223 load var   25254 acc load kWh
```

Everything in 10100 and 20100 was static across four samples — configuration,
not measurement. They appear to hold the charge setpoints: 28.2 V, 27.0 V,
25.0 V, with 21.4 V and 20.0 V looking like cutoffs.

Re-scanned 2026-08-25 with the pack charging at 26.8 V, which is the condition
that makes SOC identifiable — at 0% every candidate reads near zero and is
indistinguishable from the relay-state registers. Two undocumented registers
track charge state:

| register | flat pack, 20.3 V | charging, 26.8 V |
| --- | --- | --- |
| 15218 | 24 | 94 |
| 15204 | 0 | 90 |

Neither is in the vendor map, and the LCD shows only a four-bar icon with no
number, so they cannot be told apart from a snapshot. Set
`DEBUG_REGISTERS=15204,15218` to record both raw as `reg_15204` / `reg_15218`
and compare them across a full charge/discharge cycle: SOC should trace a smooth
curve against accumulated energy, while a stage or duty-cycle code will step.

Every *other* register that moves is already decoded, so if neither of these is
SOC, the inverter is not exposing it. Blocks outside the five scanned remain
unchecked.

## Two things worth verifying against your unit

Both are documented inline where they are computed:

1. ~~**Accumulated energy counters**~~ — settled. The official protocol
   spreadsheet (MUST "PH1800 PV1800 EP1800 PV3500 EP3500 RS485 Modbus RTU
   communication Protocol" v1.4.15, shipped in
   [xxx87/must-inverter-mon](https://github.com/xxx87/must-inverter-mon))
   gives the high register as **1000 kWh** per count and the low as 0.1 kWh, so
   the total is `high * 1000 + low * 0.1`. Identical while `high == 0`, which is
   why the earlier 32-bit reading looked correct.
2. **`pvChargePower`** — settled on 2026-08-25. Register 15208 is **1 W per
   count**, not the 0.1 W the vendor map claims. Measured against
   `pvBattVoltage * pvChargeCurrent`: exactly 10.0x across every sample.
   `CHARGER_POWER_TO_KW` is 1000.0 and should stay there.

## Credits

Based entirely off the work done by [desertkun](https://github.com/desertkun) at
https://github.com/desertkun/home-inverter-grafana-monitor
