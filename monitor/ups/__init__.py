import minimalmodbus
from dataclasses import dataclass


@dataclass
class Sample(object):
    # battery voltage
    bat_volts: int
    # battery amperage used
    bat_amps: int
    # battery state of charge
    soc: int
    # input ac voltage
    ac: int
    # systa load in percents
    load_percent: int
    # load voltage
    output_va: int
    # load power
    output_w: float
    # inverter temperature
    temp: int
    # accumulated used power, 100W per
    discharge: int
    # inverter state
    state: str
    #pv Voltage levels
    pvVoltage: float
    #pv power levels
    pvChargePower: float
    #pv inveter temp (comparing this figure with temp above)
    radiatorTemp: float
    #pv charging current
    pvChargeCurrent: float
    #pv charging batt level
    pvBattVoltage: float
    #Grid State on or off
    gridState: int
    #Grid State on or off
    gridPower: float
    # 25248: ["Accumulated discharger power low", 0.1, "kWh"],
    accdischargerpower: float
    # 25254: ["Accumulated load power low", 0.1, "kWh"],
    accloadpower: float
    # 25256: ["Accumulated self_use power low", 0.1, "kWh"],
    accselfusepower: float


class UPS(object):
    def __init__(self, device_path: str, device_id: int, baud_rate: int):
        self.device_path = device_path
        self.device_id = device_id
        self.baud_rate = baud_rate

        self.scc = minimalmodbus.Instrument(device_path, device_id)
        self.scc.serial.baudrate = baud_rate
        self.scc.serial.timeout = 0.5

    def sample(self) -> Sample:
        pass