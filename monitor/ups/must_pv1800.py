import json #enable if you need to dump to a json file
import time

from . import Sample, UPS


class MustPV1800(UPS):
    def __init__(self, device_path: str):
        super().__init__(device_path, 4, 19200)

    def sample(self) -> Sample:
        states = {
            0: "PowerOn",
            1: "SelfTest",
            2: "OffGrid",
            3: "GridTie",
            4: "ByPass",
            5: "Stop",
            6: "GridCharging"
        }
        # 15205: ["PV voltage", 0.1, "V"],
        # 15206: ["Battery voltage", 0.1, "V"],
        # 15207: ["Charger current", 0.1, "A"],
        # 15208: ["Charger power", 0.1, "W"],
        # 15209 : ["Radiator temperature", 1, "°C"],
        # 15210 : ["External temperature", 1, "°C"],
        # 15211: ["Battery Relay", 1, ""],
        # 15212: ["PV Relay", 1, ""],

        # 25205: ["Battery voltage", 0.1, "V"],
        # 25206: ["Inverter voltage", 0.1, "V"],
        # 25207: ["Grid voltage", 0.1, "V"],
        # 25208: ["BUS voltage", 0.1, "V"],
        # 25209: ["Control current", 0.1, "A"],
        # 25210: ["Inverter current", 0.1, "A"],
        # 25211: ["Grid current", 0.1, "A"],
        # 25212: ["Load current", 0.1, "A"],
        # 25213: ["Inverter power(P)", 1, "W"],
        # 25214: ["Grid power(P)", 1, "W"],
        # 25215: ["Load power(P)", 1, "W"],
        # 25216: ["Load percent", 1, "%"],
        # 25217: ["Inverter complex power(S)", 1, "VA"],
        # 25218: ["Grid complex power(S)", 1, "VA"],
        # 25219: ["Load complex power(S)", 1, "VA"],
        # 25221: ["Inverter reactive power(Q)", 1, "var"],
        # 25222: ["Grid reactive power(Q)", 1, "var"],
        # 25223: ["Load reactive power(Q)", 1, "var"],
        # 25225: ["Inverter frequency", 0.01, "Hz"],
        # 25226: ["Grid frequency", 0.01, "Hz"],
        # 25233: ["AC radiator temperature", 1, "°C"],
        # 25234: ["Transformer temperature", 1, "°C"],
        # 25235: ["DC radiator temperature", 1, "°C"],
        # 25237: ["Inverter relay state", 1, ""],
        # 25238: ["Grid relay state", 1, ""],
        # 25239: ["Load relay state", 1, ""],
        # 25240: ["N_Line relay state", 1, ""],
        # 25241: ["DC relay state", 1, ""],
        # 25242: ["Earth relay state", 1, ""],
        # 25245: ["Accumulated charger power high", 1, "kWh"],
        # 25246: ["Accumulated charger power low", 0.1, "kWh"],
        # 25247: ["Accumulated discharger power high", 1, "kWh"],
        # 25248: ["Accumulated discharger power low", 0.1, "kWh"],
        # 25249: ["Accumulated buy power high", 1, "kWh"],
        # 25250: ["Accumulated buy power low", 0.1, "kWh"],
        # 25251: ["Accumulated sell power high", 1, "kWh"],
        # 25252: ["Accumulated sell power low", 0.1, "kWh"],
        # 25253: ["Accumulated load power high", 1, "kWh"],
        # 25254: ["Accumulated load power low", 0.1, "kWh"],
        # 25255: ["Accumulated self_use power high", 1, "kWh"],
        # 25256: ["Accumulated self_use power low", 0.1, "kWh"],
        # 25257: ["Accumulated PV_sell power high", 1, "kWh"],
        # 25258: ["Accumulated PV_sell power low", 0.1, "kWh"],
        # 25259: ["Accumulated grid_charger power high", 1, "kWh"],
        # 25260: ["Accumulated grid_charger power low", 0.1, "kWh"],
        # 25271: ["Hardware version", 1, ""],
        # 25272: ["Software version", 1, ""],
        # 25273: ["Battery power", 1, "W"],
        # 25274: ["Battery current", 1, "A"],

        soc_15200 = self.scc.read_registers(15200, 75)
        #2 seconds seems to be the ideal wait time for this inverter. timeoute errors occasionaly came up with 1s wait
        time.sleep(3)
        soc_25200 = self.scc.read_registers(25200, 75)
        time.sleep(3)
        soc_10100 = self.scc.read_registers(10100, 40)


############################################################################################
#Uncomment this block when you need to dump the data to a json file for reviews etc
 # Create a dictionary with soc data
        # soc_15200_data = {
        #     "StateOfCharge": soc_15200  # Update with the correct soc_15200 array
        # }
        # soc_25200_data = {
        #     "StateOfCharge": soc_25200  # Update with the correct soc_25200 array
        # }
        # soc_10100_data = {
        #     "StateOfCharge": soc_10100  # Update with the correct soc_25200 array
        # }

        # # # Save soc_25200 data to a JSON file
        # file_path = "soc_25200_data.json"  # Choose a file path/name
        # with open(file_path, 'w') as json_file:
        #     json.dump(soc_25200_data, json_file)

        # # # Save soc_25200 data to a JSON file
        # file_path = "soc_15200_data.json"  # Choose a file path/name
        # with open(file_path, 'w') as json_file:
        #     json.dump(soc_15200_data, json_file)

        # # # Save soc_10100 data to a JSON file
        # file_path = "soc_10100_data.json"  # Choose a file path/name
        # with open(file_path, 'w') as json_file:
        #     json.dump(soc_10100_data, json_file)
############################################################################################

        pvVoltage = soc_15200 [5]/10
        radiatorTemp = soc_15200[9]
        pvChargeCurrent = soc_15200[7]/10
        pvChargePower = soc_15200[8]/1000
        pvBattVoltage = soc_15200[6]/10
        batVolts = soc_25200[5] / 10.0
        inputVolts = soc_25200[7] // 10
        batAmps = soc_25200[74]
        if batAmps > 32768:
            batAmps = batAmps - 65536
        batCharge = 0
        discharge = soc_25200[54]
        loadPercent = soc_25200[16]
        outputVA = soc_25200[19]
        outputW = soc_25200[15]
        tempInt = soc_25200[33]
        state = states[soc_25200[1]]
        gridState = soc_25200[38]
        gridPower = soc_25200[14]
        accdischargerpower = (soc_25200[47] + soc_25200[48]/10)
        accloadpower = soc_25200[54]/10
        accselfusepower = soc_25200[56]/10

        return Sample(
            batVolts,
            batAmps,
            batCharge,
            inputVolts,
            loadPercent,
            outputVA,
            outputW,
            tempInt,
            discharge,
            state,
            pvVoltage,
            pvChargePower,
            radiatorTemp,
            pvChargeCurrent,
            pvBattVoltage,
            gridState,
            gridPower,
            accdischargerpower,
            accloadpower,
            accselfusepower
            )
