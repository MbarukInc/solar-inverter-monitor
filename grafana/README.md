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

| Rule | Fires when |
| --- | --- |
| [`battery-cell-overvoltage.yaml`](alerts/battery-cell-overvoltage.yaml) | any cell reaches 3.60 V, below the BMS cut-off at 3.65 V |
| [`inverter-silent-while-battery-reports.yaml`](alerts/inverter-silent-while-battery-reports.yaml) | no inverter samples for ten minutes while the battery reader keeps writing |

**In Grafana Cloud, not in the cluster's Alertmanager.** That Alertmanager
routes everything to the chart's `null` receiver, so a rule there would fire and
reach nobody, and Prometheus cannot query InfluxDB anyway. Grafana Cloud already
reaches these InfluxDBs through the PDC agent, its contact points need no
credential on the cluster, and it evaluates from outside the house. See
homelab-infra `observability/README.md`, "Alerting".

To apply: *Alerting → Alert rules → New alert rule → Import from file*, or
`POST /api/v1/provisioning/alert-rules` with a service-account token. The rule
carries `uid: bms-cell-overvoltage`, so re-importing updates it in place rather
than creating a duplicate.

The data source uid `fdwa5hfyf5o1sa` is the same InfluxDB the dashboard's panels
use. It needs a contact point on the `Solar` folder to actually notify.

The second rule uses the battery reader as a control: both readers are separate
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
