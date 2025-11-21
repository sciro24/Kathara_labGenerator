# Kathara Lab Generator

This repository contains two main tools for automatically generating Kathara network labs: a command-line script (`labGenerator.py`) and a modern graphical interface (`labGenerator_GUI.py`).

## 1. LabGenerator (CLI)

`labGenerator.py` is the core of the project. It is an interactive Python script that guides the user step-by-step through the creation of an entire network topology.

### How it works
The script asks the user to define network devices one by one. It supports:
- **FRR Routers**: Automatic configuration of daemons (zebra, bgpd, ospfd, ripd), interfaces, loopbacks, and routing protocols (BGP, OSPF, RIP, Static).
- **Client Hosts**: Simple PCs with IP and default gateway configuration.
- **Web Servers**: Apache servers with customizable index page.
- **DNS Servers**: BIND9 servers configurable as Root, Master, or Caching/Forwarding.

Once the devices are defined, the script automatically generates the folder structure, `.startup` files, `lab.conf`, and FRR daemon configurations. It also includes post-creation menus to refine the configuration (e.g., BGP policies, automatic neighbors).


| CLI Demo 1 | CLI Demo 2 |
| :---: | :---: |
| <img src="images/labGenerator_cli.png" width="400"> | <img src="images/labGenerator_cli2.png" width="400"> |



---

## 2. LabGenerator GUI

`labGenerator_GUI.py` is a modern graphical interface based on PySide6 that makes creating labs even more intuitive and visual.

### Features
- **Topology Visualization**: An interactive graph shows connections between devices (Routers, Hosts, LANs) in real-time.
- **Guided Configuration**: Dedicated dialog windows for each device type allow entering parameters (IP, protocols, routes) without remembering the syntax.
- **Complete Management**: Allows saving, loading, and modifying existing labs.
- **Full Integration**: Uses the `labGenerator.py` engine to ensure the same quality and correctness of the generated files.


| GUI Demo 1 | GUI Demo 2 |
| :---: | :---: |
| <img src="images/labGenerator_gui.png" width="400"> | <img src="images/labGenerator_gui2.png" width="400"> |



## Prerequisites and Installation

1. Ensure you have Python 3.8+ installed.
2. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Usage

For the GUI:
```bash
python3 labGenerator_GUI.py
```

For the CLI:
```bash
python3 labGenerator.py
```
