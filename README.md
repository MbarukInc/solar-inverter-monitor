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

## Two things worth verifying against your unit

Both are documented inline where they are computed:

1. **Accumulated energy counters** (`accDischargerPower`, `accLoadPower`,
   `accSelfusePower`) combine a high/low register pair. The vendor map's scale
   factors are self-contradictory, so the pair is read as a 32-bit counter in
   0.1 kWh units. Compare against the lifetime totals on the inverter's LCD.
2. **`pvChargePower`** assumes register 15208 is 0.1 W per count. It should
   track `pvBattVoltage * pvChargeCurrent`; if it reads 10x low, your firmware
   uses 1 W per count — see `CHARGER_POWER_TO_KW` in `monitor/ups/must_pv1800.py`.

## Credits

Based entirely off the work done by [desertkun](https://github.com/desertkun) at
https://github.com/desertkun/home-inverter-grafana-monitor
