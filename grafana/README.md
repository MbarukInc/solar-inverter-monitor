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

## Notes

**Grid Power vs KPLC.** `gridPower` is real power (W) and signed — negative is
import, positive is export. The KPLC panel plots volts and amps, whose product
is apparent power (VA) and reads higher whenever power factor is below 1. The
two disagreeing is expected, not a bug.

**Two datasources.** Most panels use `fdwa5hfyf5o1sa`; `WH` and `Daily kWH` use
one named `influx`. Probably a leftover, and worth consolidating.

**Gauge ranges assume a 3000 VA / 24 V system.** Power Consumption maxes at
3000 W and the battery gauges span 20.5–29 V. Revisit if the inverter changes.
