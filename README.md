# solar-inverter-monitor

Polls a MUST PV18-series solar inverter (currently a **PV18-3224 VPM II**;
previously a PV18-3024 VPM) over Modbus RTU, and its MUST LP16 battery's BMS
over RS485, and writes samples to InfluxDB.

It runs as two pods on the home cluster's `mbarukville-02` node, one per device,
with both USB adapters plugged into that node. The manifests live in
`MbarukInc/homelab-infra` (`solarmonitoring/solar-inverter.yaml`,
`solar-bms.yaml`); this repo builds the image they run. Until 2026-09-24 each
device had its own Raspberry Pi, for a reason that turned out to be one wire --
see [Both devices on one host](#both-devices-on-one-host-no-ground-wire-on-the-battery-link).

## Configuration

The running readers are configured in `MbarukInc/homelab-infra`, not here:

- **Settings** are `env` entries on each Deployment, in
  `solarmonitoring/solar-inverter.yaml` and `solarmonitoring/solar-bms.yaml`.
  Every variable the code reads is listed under
  [Environment variables read by the container](#environment-variables-read-by-the-container).
- **The InfluxDB credentials** are the `solar-influx` Secret in the
  `solarmonitoring` namespace, keys `DB_USERNAME` and `DB_PASSWORD`. Rotating the
  password means replacing that Secret and restarting both Deployments.
- **The image pull** uses the `ghcr-pull` Secret, a `read:packages` token.

Both Secrets are made by hand and documented in homelab-infra's README, under
Secrets.

The repository secrets and variables that used to render `.env` on the Pis --
`DB_HOST`, `USB_DEVICE`, `BMS_BATTERY_DEVICE`, `RASPBERRY_PI_IP`, `BMS_PI_IP`,
`PI_SSH_KEY` and the rest -- drive nothing since the Pi deploy workflows were
removed. They are left in place, not deleted, and can go whenever convenient.

### Manual setup

To run it outside the cluster -- for local testing, or on a spare machine with
an adapter plugged in -- use docker-compose:

```bash
cp .env.example .env
# edit .env: InfluxDB host, credentials, database
docker compose up -d --build
docker compose logs -f monitor
```

Compose interpolates `.env` values, so a literal `$` in a hand-written password
must be doubled (`pa$word` → `pa$$word`).

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
| `HOST_TAG` | `INVERTER_MODEL` | InfluxDB `host` tag. Override on a second writer |
| `BMS_ONLY` | unset | `true` on a host with a BMS and no inverter; points are tagged `BmsOnly` |
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

Two steps, and only the first is automatic:

1. **The image.** `Publish_Image`
   ([`.github/workflows/publish_image.yml`](.github/workflows/publish_image.yml))
   builds it on a GitHub-hosted runner and pushes it to
   `ghcr.io/mbarukinc/solar-inverter-monitor`, on every push to `main` that
   touches `monitor/`, and on demand. Its run summary prints the exact
   `image: tag@sha256:digest` line.
2. **The rollout.** Paste that line into both manifests in homelab-infra and
   apply them. It is by hand on purpose, the same digest pin every other image
   there gets.

The build runs on a GitHub-hosted runner, not the `mbarukville` runners:

- those are ARC pods with no Docker daemon, so they cannot build images;
- a hosted runner also never needs a way into the home LAN.

The package inherits this repo's visibility, so it is private, and the cluster
pulls it with the `ghcr-pull` Secret.

There used to be two more workflows, `Deploy_Latest_code` and `Build_Container`,
which rsynced this repo to each Pi, wrote `.env` there and ran
`docker compose up -d --build`. They were removed on 2026-09-24 along with the
last Pi target. Git history has them if a Pi ever comes back.

## After changing inverter, firmware or cabling

The PV18 models share one Modbus register map — the model number changes the VA
rating and battery voltage the readings land in, not the register addresses — so
the driver is expected to carry over between PV18 units. Expected is not
verified, though, and a swap is exactly when the link parameters and the map can
move. Confirm with the bundled probe before trusting the data.

Run these from a homelab-infra checkout. The image and device path are the ones in
`solarmonitoring/solar-inverter.yaml`:

```bash
./scripts/kubectl-remote.sh -n solarmonitoring scale deploy/solar-inverter --replicas=0
./scripts/kubectl-remote.sh -n solarmonitoring run probe --rm -i --restart=Never \
  --image='<image from solar-inverter.yaml>' --overrides='{"spec":{
    "nodeSelector":{"kubernetes.io/hostname":"mbarukville-02"},
    "tolerations":[{"key":"dedicated","operator":"Equal","value":"edge","effect":"NoSchedule"}],
    "imagePullSecrets":[{"name":"ghcr-pull"}],
    "containers":[{"name":"probe","image":"<image from solar-inverter.yaml>",
      "securityContext":{"privileged":true},
      "volumeMounts":[{"name":"dev","mountPath":"/dev"}],
      "command":["python3","probe.py","--nominal-va","3200",
                 "--device","<USB_DEVICE from solar-inverter.yaml>"]}],
    "volumes":[{"name":"dev","hostPath":{"path":"/dev"}}]}}'
./scripts/kubectl-remote.sh -n solarmonitoring scale deploy/solar-inverter --replicas=1
```

With docker-compose on a host outside the cluster, it is
`docker compose run --rm monitor python3 probe.py --nominal-va 3200` with the
daemon stopped.

Stop the daemon first, either way. Neither pyserial nor minimalmodbus opens the port
exclusively, so the probe and a running monitor would both hold
`/dev/ttyUSB0` and corrupt each other's replies.

It reads only, never writes. It reports every decoded value against a plausible
range and then cross-checks the things that would otherwise fail silently:
whether real power exceeds apparent power (which would mean registers have
moved), whether the charger-power scale still matches `pvBattVoltage *
pvChargeCurrent`, whether any state code is missing from `STATES`, and what VA
rating the load percentage implies — a PV18-3224 should come out near 3200.

If nothing answers, sweep the common link parameters by running the probe the
same way with `--scan` in place of `--nominal-va 3200`.

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

It checks that every variable the code reads is declared in
`docker-compose.yml`, so that setting it in `.env` actually reaches the
container. Compose is the one layer left where a setting can silently go
nowhere, and that has happened twice:

- `USB_DEVICE` was hardcoded past its own variable;
- `DEBUG_REGISTERS` was never plumbed through at all.

Until 2026-09-24 it also checked the Pi deploy action and workflows. On the
cluster there is no such layer: an `env` entry on the Deployment reaches the
process directly.

## Both devices on one host: no ground wire on the battery link

The inverter and the battery are read from **one host**, and that works only
because the battery's RS485 cable carries **A and B, and no ground**.

With the battery link's GND wired to the adapter (RJ45 pin 3, which this README
used to tell you to connect), plugging both adapters into one machine leaves the
battery working and the inverter mute:

- its port returns a continuously low line, ~110 bytes/s of `0x00`, where a
  healthy idle line yields nothing;
- it answers no Modbus at any baud;
- removing either adapter restores the other.

That was reproduced on a Pi Model B+, on a Pi 4 (every port, autosuspend off, a
fresh `ch341` bind) and on a mini PC. For three weeks it meant a separate
Raspberry Pi per device.

The cause is a ground loop. The adapters are not isolated, so the adapter's
RS485 GND terminal *is* the host's USB ground. Wiring it to the battery tied the
pack's negative to the host, and through the host to the inverter, whose
USB-serial chip sits inside a mains-connected box. Established on 2026-09-24:

- **Not power:** the mini PC, with ample USB current, failed the same way. (The
  Model B+ really was under-voltage, `throttled=0x50005`, but that was not the
  cause.)
- **Not software:** with the battery adapter deauthorized in sysfs -- no driver,
  no device node, still cabled -- the inverter stayed mute.
- **The wire:** with only the GND wire removed, both adapters read cleanly side
  by side on one host.
- **Harmless to remove:** the battery link lost nothing without it, 200 of 200
  reads in a burst test. RS485 is differential, and the two sides sit close
  enough in voltage for the transceivers without a shared reference.

If the battery link ever turns flaky, the fix is an isolated USB-RS485 adapter,
not reconnecting the ground.

**Keep the two readers separate processes anyway**, as the two Deployments are.
Each opens its own device at its own baud and slave id, and one failing never
takes the other down:

- **The battery reader sets `BMS_ONLY=true`**, which makes `monitor.py` skip the
  inverter entirely rather than trying and failing. Its `USB_DEVICE` also names
  a path that deliberately does not exist, as a fallback: left at the default,
  the inverter reader would pick up the battery's adapter and drive it at the
  wrong baud rate.
- **Each needs its own `host` tag.** Both write to the same `logs` measurement,
  so a shared tag makes the two writers inseparable: `GROUP BY state` returns
  the battery reader's rows interleaved with the inverter's, and no query can
  filter to one of them. The battery reader sets `HOST_TAG=must-lp16-bms`; the
  inverter reader falls back to the model name, so its series are the ones it
  always had.
- **The `state` tag on a battery-only point is `BmsOnly`**, not `NoComms`. Those
  are different events -- one reader has no inverter by design, the other has
  one that stopped answering -- and a query has to be able to tell them apart.

## Battery BMS (state of charge)

The inverter exposes **no** state of charge — the MUST PV18 Modbus protocol does
not define one. The battery does, over its own RS485 port, so the monitor reads
the BMS directly as a second device.

MUST LP16-24200, PACE BMS, **Modbus RTU, 9600 baud, slave 1**. RJ45 pinout from
the LP1600 manual: **pin 1 = RS485-B, pin 2 = RS485-A, pin 3 = GND** (pins 7/8
carry A/B as well, 6 is a second ground). On a T-568B patch lead that is
**orange-white to B and orange to A -- and nothing else.** Leave green-white
(pin 3) and green (pin 6) unconnected: wiring the ground mutes the inverter
whenever both adapters share a host. See
[Both devices on one host](#both-devices-on-one-host-no-ground-wire-on-the-battery-link).

Set `BATTERY_DEVICE` to the adapter's `/dev/serial/by-path` entry to enable it
(`solar-bms.yaml` does).
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

Run it the way the probe is run under
[After changing inverter, firmware or cabling](#after-changing-inverter-firmware-or-cabling),
with `"command":["python3","scan_registers.py","--watch","5"]` and the inverter
Deployment scaled to 0 meanwhile.

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
