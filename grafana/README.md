# Grafana dashboard

`dashboard.json` is the MbarukVille solar dashboard, tracked here rather than
only living in Grafana Cloud. It queries the fields written by `monitor/`, so a
decode change and the dashboard change that follows it can land in one PR — and
"what does the dashboard actually reference?" is answerable from the repo.

## Panels and the fields they need

Every panel reads measurement `logs` in database `ups`:

| Field | Used by |
| --- | --- |
| `bat_volts` | Battery Charge gauge, Battery Voltage Levels |
| `bat_amps`, `pvChargeCurrent` | Battery - Current |
| `load_percent` | Load %, Load Trend |
| `output_w`, `output_va` | Power Consumption, Output, Power Factor, WH, Daily kWH, Power Generated, Cost Savings |
| `pvChargePower`, `pv_Voltage` | Solar Metrics, Solar Power |
| `gridVoltage`, `gridCurrent` | KPLC |
| `gridPower` | Grid Power (KPLC) |
| `accDischargerPower` | Total Power Generated |
| `inverterState` | Inverter State |
| `temp` | Temperature |
| `bms_soc` | Battery SOC (gauge), Battery SOC & Capacity |
| `bms_remaining_ah` | Battery SOC & Capacity |
| `bms_cell_01`..`bms_cell_08`, `bms_cell_delta` | Cell Voltages |

Not currently shown, though collected: `ac`, `gridState`, `radiatorTemp`,
`pvBattVoltage`, `accLoadPower`, `accSelfusePower`, `bms_soh`, `bms_cycles`,
`bms_current`, `bms_voltage`, `bms_temp_1/2`, `bms_full_ah`.

The `bms_*` fields come from the battery BMS over its own RS485 link, not from
the inverter — the inverter has no SOC register at all. They are written even
when the inverter is unreachable, in which case the `state` tag reads
`NoComms` and no inverter fields are present, so panels on those fields will
show gaps while the SOC panels keep working.

## Updating

Export from Grafana, normalise, commit:

```bash
python3 grafana/normalise.py ~/Downloads/<export>.json grafana/dashboard.json
```

Normalising is not cosmetic. A raw export carries `resourceVersion`,
`generation` and `creationTimestamp`, which change on every save and would make
each diff unreadable, plus the Grafana Cloud stack id and the user ids of
whoever last touched it. This repo is public.

`uid` is deliberately kept, so re-importing updates the existing dashboard
rather than creating a duplicate.

## Importing

Dashboards → New → Import → upload `dashboard.json`. It matches on `uid` and
updates in place.

## Alerts

[`alerts/`](alerts/) holds Grafana Cloud alert rules in Grafana's provisioning
format, tracked for the same reason the dashboard is.

| Rule | Fires when | Notifies |
| --- | --- | --- |
| [`battery-cell-overvoltage.yaml`](alerts/battery-cell-overvoltage.yaml) | any cell reaches 3.60 V, below the BMS cut-off at 3.65 V | JM Email |
| [`inverter-silent-while-bms-reports.yaml`](alerts/inverter-silent-while-bms-reports.yaml) | no inverter samples for ten minutes while the battery reader keeps writing | JM Email |
| [`bms-silent-while-inverter-reports.yaml`](alerts/bms-silent-while-inverter-reports.yaml) | no battery samples for ten minutes while the inverter reader keeps writing | JM Email |
| [`kplc-voltage.yaml`](alerts/kplc-voltage.yaml) | the last `gridVoltage` reading in twelve hours is below 220 V | JM Email |
| [`inverter-data-polling-issues.yaml`](alerts/inverter-data-polling-issues.yaml) | the last `bat_volts` reading in six hours is below 20 V | `tr-node8-solar-remediation` webhook |

The last two were built from the dashboard long before the others and were live
but untracked until 2026-10-26, so both this table and this directory claimed
less than was actually running. They are exported as found.

`inverter-data-polling-issues.yaml` overlaps
`inverter-silent-while-bms-reports.yaml` and tests the same failure less
precisely: a battery-voltage floor cannot separate a dead inverter link from a
genuinely flat battery, nor the inverter stopping from the host stopping.
Whether it should now be retired is open.

**In Grafana Cloud, not in the cluster's Alertmanager.** That Alertmanager
routes everything to the chart's `null` receiver, so a rule there would fire and
reach nobody, and Prometheus cannot query InfluxDB anyway. Grafana Cloud already
reaches these InfluxDBs through the PDC agent, its contact points need no
credential on the cluster, and it evaluates from outside the house. See
homelab-infra `observability/README.md`, "Alerting".

To apply, run [`alerts/import.py`](alerts/import.py):

```bash
export GRAFANA_URL=https://<stack>.grafana.net
export GRAFANA_TOKEN=<service account token, Editor or Admin>
python3 grafana/alerts/import.py --dry-run   # then without it
```

**Not** *Alerting → Alert rules → Import*. That dialog reads **Prometheus**
rule files (`groups[].rules[].expr`) and creates data-source-managed rules;
these are Grafana-managed rules in Grafana's own provisioning format, and it
rejects them with *"missing or invalid groups array at index 0"*. The
provisioning API takes the rule object itself, not the `apiVersion`/`groups`
wrapper, which is what the script unwraps.

Every rule carries a fixed `uid`, so re-running updates in place rather than
duplicating. The script sends `X-Disable-Provenance` so the rules stay editable
in the UI -- without it Grafana marks them provisioned and read-only, which is
wrong for a threshold still being tuned.

A group's `interval:` is applied after that group's rules, in a second call:
the one-rule-at-a-time endpoint cannot carry it, so the API creates any new
group at its own default of 1m. Until 2026-10-26 the script did not make that
call at all and the field was silently ignored -- `solar-battery` asked for 5m
and evaluated every 1m from its first import onwards.

Each file names its own folder, so no argument is needed. `--folder` overrides
it when the rules should go somewhere else -- a scratch folder for testing, say
-- and the folder must already exist either way. The live folder is `Power`;
these files said `Solar`, which exists nowhere on this stack, so until
2026-10-26 every run needed `--folder Power` or it exited
*"FOLDER 'Solar' not found"*.

The data source uid `fdwa5hfyf5o1sa` is the same InfluxDB the dashboard's panels
use.

Each rule names its own receiver under `notification_settings`. Grafana routes
by notification policy, not by folder: the policy tree on this stack has no
routes, so a rule without an explicit receiver falls through to the default one
-- still Grafana's stock `<example@email.com>` -- and reaches nobody. The three
solar rules did exactly that from the day they were written until 2026-10-26. The key is snake_case while every key around it
is camelCase; that is Grafana's provisioning API, not a typo here.

`inverter-silent-while-bms-reports.yaml` uses the battery reader as a control: both readers are separate
processes on one host, so if the battery keeps reporting and the inverter does
not, the node, its USB bus, the driver, the network and InfluxDB are all
exonerated at once. If both go quiet it stays silent on purpose -- that is the
host or the database, and `NoData` covers it.

## Notes

**Grid Power vs KPLC.** `gridPower` is real power (W) and signed — negative is
import, positive is export. The KPLC panel plots volts and amps, whose product
is apparent power (VA) and reads higher whenever power factor is below 1. The
two disagreeing is expected, not a bug.

**Two datasources.** Most panels use `fdwa5hfyf5o1sa`; `WH` and `Daily kWH` use
one named `influx`. Probably a leftover, and worth consolidating.

**Gauge ranges assume a 3000 VA / 24 V system.** Power Consumption maxes at
3000 W and the battery gauges span 20.5–29 V. Revisit if the inverter changes.
