# solar-inverter-monitor

Polls a MUST PV18-series solar inverter (tested against a **PV18-3024 VPM**) over
Modbus RTU and writes samples to InfluxDB. Runs as a Docker container on a
Raspberry Pi with the inverter's RS485/USB adapter on `/dev/ttyUSB0`.

## Setup

```bash
cp .env.example .env
# edit .env: InfluxDB host, credentials, database
docker compose up -d --build
docker compose logs -f monitor
```

Credentials live in `.env`, which is gitignored. They used to be hardcoded in
`docker-compose.yml` — if you are upgrading, create `.env` before deploying or
the stack will refuse to start with a message naming the missing variable.

## Configuration

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

Pushing to `main` triggers `Deploy_Latest_code`, which rsyncs the repo to the Pi
and rebuilds. **The container no longer bind-mounts the source**, so a code
change requires a rebuild — an rsync alone is no longer enough.

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
