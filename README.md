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
| `PI_KNOWN_HOSTS` | `ssh-keyscan -H <PI_IP>` output for that host |

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

1. **Accumulated energy counters** (`accDischargerPower`, `accLoadPower`,
   `accSelfusePower`) combine a high/low register pair. The vendor map's scale
   factors are self-contradictory, so the pair is read as a 32-bit counter in
   0.1 kWh units. Compare against the lifetime totals on the inverter's LCD.
2. **`pvChargePower`** — settled on 2026-08-25. Register 15208 is **1 W per
   count**, not the 0.1 W the vendor map claims. Measured against
   `pvBattVoltage * pvChargeCurrent`: exactly 10.0x across every sample.
   `CHARGER_POWER_TO_KW` is 1000.0 and should stay there.

## Credits

Based entirely off the work done by [desertkun](https://github.com/desertkun) at
https://github.com/desertkun/home-inverter-grafana-monitor
