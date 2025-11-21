#!/usr/bin/env python3
"""
Lab Generator GUI — Interfaccia grafica completa per labGenerator.py
Supporta tutte le funzionalità: Router (BGP/OSPF/RIP/Static), Host, WWW, DNS,
BGP Policies, Post-creation tools, Visualizzazione topologia interattiva
"""
import sys
import os
import json
import tempfile
from typing import Dict, List, Optional

try:
    import labGenerator as lg
except Exception:
    lg = None
    print("⚠️ labGenerator.py non trovato")

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
import networkx as nx
from pyvis.network import Network
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QAbstractAnimation

# --- CUSTOM WIDGETS ---
class HoverButton(QtWidgets.QPushButton):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        
        # Setup shadow effect (fallback for missing ScaleEffect)
        self.effect = QtWidgets.QGraphicsDropShadowEffect(self)
        self.effect.setBlurRadius(0)
        self.effect.setColor(QtGui.QColor(0, 0, 0, 100))
        self.effect.setOffset(0, 0)
        self.setGraphicsEffect(self.effect)
        
        # Animation
        self.anim = QPropertyAnimation(self.effect, b"blurRadius")
        self.anim.setDuration(200)
        self.anim.setEasingCurve(QEasingCurve.OutQuad)
        
    def enterEvent(self, event):
        self.anim.stop()
        self.anim.setStartValue(self.effect.blurRadius())
        self.anim.setEndValue(15.0)
        self.anim.start()
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        self.anim.stop()
        self.anim.setStartValue(self.effect.blurRadius())
        self.anim.setEndValue(0.0)
        self.anim.start()
        super().leaveEvent(event)


# --- TEMA CHIARO (GitHub-like) ---
LIGHT_BG = "#ffffff"
PANEL_BG = "#f6f8fa"
ACCENT = "#0969da"
ACCENT_HOVER = "#0860ca"
SUCCESS = "#1a7f37"
TEXT_PRIMARY = "#24292f"
TEXT_SECONDARY = "#57606a"
BORDER = "#d0d7de"
ERROR = "#cf222e"

STYLESHEET = f"""
QMainWindow {{background-color: {LIGHT_BG};}}
QWidget {{color: {TEXT_PRIMARY}; font-family: "Segoe UI", Helvetica, Arial, sans-serif; font-size: 13px;}}

/* Bottoni */
QPushButton {{
    background-color: {ACCENT}; 
    color: white; 
    border: 1px solid rgba(27,31,36,0.15); 
    border-radius: 6px; 
    padding: 6px 16px; 
    font-weight: 600;
}}
QPushButton:hover {{background-color: {ACCENT_HOVER};}}
QPushButton:pressed {{
    background-color: #064b9a;
    padding-top: 7px;
    padding-bottom: 5px;
}}
QPushButton:disabled {{background-color: {PANEL_BG}; color: {TEXT_SECONDARY}; border-color: {BORDER};}}

/* Input Fields */
QLineEdit, QTextEdit, QSpinBox, QComboBox {{
    background-color: {LIGHT_BG}; 
    border: 1px solid {BORDER}; 
    border-radius: 6px; 
    padding: 5px 12px; 
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 2px solid {ACCENT};
    padding: 4px 11px; /* Compensate for border width */
}}

/* Liste e Tabelle */
QListWidget, QTableWidget {{
    background-color: {LIGHT_BG};
    border: 1px solid {BORDER};
    border-radius: 6px;
    alternate-background-color: {PANEL_BG};
}}
QListWidget::item:selected, QTableWidget::item:selected {{
    background-color: {ACCENT};
    color: white;
}}
QHeaderView::section {{
    background-color: {PANEL_BG};
    padding: 4px;
    border: 1px solid {BORDER};
    font-weight: 600;
}}

/* GroupBox */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 20px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 10px;
    left: 10px;
}}

/* Tabs */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    background-color: {LIGHT_BG};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {PANEL_BG};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 16px;
    margin-right: 2px;
    color: {TEXT_SECONDARY};
}}
QTabBar::tab:selected {{
    background-color: {LIGHT_BG};
    color: {TEXT_PRIMARY};
    border-bottom: 1px solid {LIGHT_BG};
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
}}

/* Scrollbars */
QScrollBar:vertical {{
    border: none;
    background: {PANEL_BG};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    min-height: 20px;
    border-radius: 5px;
}}
"""

# --- WIDGET TOPOLOGIA INTERATTIVA ---
class TopologyView(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.temp_file = None
        self.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
        self.page().setBackgroundColor(QtGui.QColor(LIGHT_BG))
        
        # Setup WebChannel for communication
        self.channel = QWebChannel()
        self.page().setWebChannel(self.channel)
        self.channel.registerObject("backend", self)

        # Pagina di default
        self.setHtml(f"""
        <html><body style="background-color:{LIGHT_BG}; color:{TEXT_SECONDARY}; 
        display:flex; justify-content:center; align-items:center; height:100%; font-family:sans-serif;">
        <h2>Nessun dispositivo. Aggiungi router o host per iniziare.</h2>
        </body></html>
        """)

    @QtCore.Slot(str)
    def node_clicked(self, node_id):
        # Trova la finestra principale e chiama on_node_click
        mw = self.window()
        if hasattr(mw, 'on_node_click'):
            mw.on_node_click(node_id)

    def set_graph(self, G):
        if not G.nodes:
            self.setHtml(f"""
            <html><body style="background-color:{LIGHT_BG}; color:{TEXT_SECONDARY}; 
            display:flex; justify-content:center; align-items:center; height:100%; font-family:sans-serif;">
            <h2>Topologia Vuota</h2>
            </body></html>
            """)
            return

        # Configura PyVis
        # Usa cdn_resources='in_line' se possibile, altrimenti 'remote'
        try:
            net = Network(height="100%", width="100%", bgcolor=LIGHT_BG, font_color=TEXT_PRIMARY, cdn_resources='in_line')
        except TypeError:
            # Fallback per versioni vecchie
            net = Network(height="100%", width="100%", bgcolor=LIGHT_BG, font_color=TEXT_PRIMARY)

        # Opzioni fisiche
        net.barnes_hut(gravity=-2000, central_gravity=0.3, spring_length=150, spring_strength=0.05, damping=0.09)
        
        # Helper per icone custom
        import base64
        def get_icon_data(name):
            # Cerca in icons/Name.ico
            icon_path = os.path.join(os.getcwd(), 'icons', f'{name}.ico')
            if os.path.exists(icon_path):
                try:
                    with open(icon_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode('utf-8')
                        return f"data:image/x-icon;base64,{b64}"
                except: pass
            return None

        # Aggiungi nodi con stile
        for node, data in G.nodes(data=True):
            dtype = data.get('device_type', 'lan')
            
            # Default fallback
            shape = 'dot'
            icon = None
            image = None
            color = {'background': '#97c2fc', 'border': '#2b7ce9'}
            size = 25
            title = node

            if dtype == 'router':
                custom_img = get_icon_data('Router')
                if custom_img:
                    shape = 'image'
                    image = custom_img
                    size = 30
                else:
                    shape = 'icon'
                    icon = {'face': "'FontAwesome'", 'code': '\uf0e8', 'size': 50, 'color': '#0969da'}
                title = f"Router: {node}\nASN: {data.get('asn', '-')}"
                
            elif dtype == 'host':
                custom_img = get_icon_data('Host')
                if custom_img:
                    shape = 'image'
                    image = custom_img
                    size = 25
                else:
                    shape = 'icon'
                    icon = {'face': "'FontAwesome'", 'code': '\uf109', 'size': 40, 'color': '#1a7f37'}
                title = f"Host: {node}"
                
            elif dtype == 'www':
                custom_img = get_icon_data('WWW')
                if custom_img:
                    shape = 'image'
                    image = custom_img
                    size = 25
                else:
                    shape = 'icon'
                    icon = {'face': "'FontAwesome'", 'code': '\uf233', 'size': 40, 'color': '#bf3989'}
                title = f"WWW: {node}"
                
            elif dtype == 'dns':
                custom_img = get_icon_data('DNS')
                if custom_img:
                    shape = 'image'
                    image = custom_img
                    size = 25
                else:
                    shape = 'icon'
                    icon = {'face': "'FontAwesome'", 'code': '\uf233', 'size': 40, 'color': '#8250df'}
                title = f"DNS: {node}"
                
            else: # LAN
                # Box shape with label inside
                shape = 'box'
                color = {'background': '#ff9900', 'border': '#cc7a00'}
                size = None # size ignored for box?
                title = f"LAN: {data.get('label', node)}"
                # Font bianco per contrasto su arancione
                net.add_node(node, label=data.get('label', node), title=title, 
                             shape=shape, color=color, font={'size': 14, 'color': 'white', 'face': 'sans-serif', 'bold': True})
                continue # Skip default add_node

            # Aggiungi nodo (se non LAN)
            if shape == 'image':
                net.add_node(node, label=node, title=title, shape=shape, image=image, size=size)
            elif shape == 'icon':
                net.add_node(node, label=node, title=title, shape=shape, icon=icon)
            else:
                net.add_node(node, label=node, title=title, shape=shape, color=color, size=size)
        
        # Aggiungi archi con etichette (IP + Interfaccia)
        for edge in G.edges(data=True):
            # edge è (u, v, data)
            ip = edge[2].get('label', '')
            iface = edge[2].get('iface', '')
            
            # Costruisci label completa: "eth0\n10.0.0.1"
            full_label = ""
            if iface: full_label += f"{iface}\n"
            if ip: full_label += ip
            
            net.add_edge(edge[0], edge[1], color='#d0d7de', width=2, label=full_label, font={'align': 'middle', 'size': 10})

        # Salva su file temporaneo
        if self.temp_file:
            try: os.unlink(self.temp_file)
            except: pass
        
        fd, self.temp_file = tempfile.mkstemp(suffix='.html')
        os.close(fd)
        
        # Opzioni interazione
        net.set_options("""
        var options = {
          "interaction": {
            "hover": true,
            "navigationButtons": true,
            "keyboard": true,
            "zoomView": true
          },
          "physics": {
            "stabilization": false
          }
        }
        """)
        
        try:
            net.save_graph(self.temp_file)
            # Leggi il contenuto e usa setHtml per evitare problemi di caricamento file locale
            with open(self.temp_file, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # Inject FontAwesome CSS
            fa_css = '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">'
            html_content = html_content.replace('<head>', f'<head>{fa_css}')
            
            # Inject JS for click handling
            js_inject = """
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <script>
            new QWebChannel(qt.webChannelTransport, function (channel) {
                window.backend = channel.objects.backend;
            });
            
            network.on("click", function (params) {
                if (params.nodes.length > 0) {
                    var nodeId = params.nodes[0];
                    if (window.backend) {
                        window.backend.node_clicked(nodeId);
                    }
                }
            });
            </script>
            </body>
            """
            html_content = html_content.replace('</body>', js_inject)

            self.setHtml(html_content, baseUrl=QtCore.QUrl.fromLocalFile(os.path.dirname(self.temp_file) + os.sep))
        except Exception as e:
            self.setHtml(f"<html><body><h2>Errore visualizzazione: {str(e)}</h2></body></html>")

# --- DIALOGHI DI CONFIGURAZIONE ---

class RouterDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        self.setWindowTitle('Configurazione Guidata Router')
        self.resize(800, 600)
        self.data = data or {}
        self.setup_ui()
        if data: self.load_data(data)

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        
        # Header
        header = QtWidgets.QLabel("<h2>Configura Router</h2>")
        header.setStyleSheet(f"color: {ACCENT};")
        layout.addWidget(header)
        
        self.tabs = QtWidgets.QTabWidget()
        
        # TAB 1: Generale
        t1 = QtWidgets.QWidget()
        l1 = QtWidgets.QFormLayout(t1)
        l1.setSpacing(15)
        
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("es. R1")
        self.name.setToolTip("Nome univoco del router nella rete")
        
        self.asn = QtWidgets.QLineEdit()
        self.asn.setPlaceholderText("es. 100")
        self.asn.setToolTip("Autonomous System Number (richiesto se BGP è attivo)")
        
        self.proto_group = QtWidgets.QGroupBox("Protocolli di Routing")
        pg_layout = QtWidgets.QHBoxLayout()
        self.p_bgp = QtWidgets.QCheckBox("BGP")
        self.p_ospf = QtWidgets.QCheckBox("OSPF")
        self.p_rip = QtWidgets.QCheckBox("RIP")
        self.p_static = QtWidgets.QCheckBox("Statico")
        for w in [self.p_bgp, self.p_ospf, self.p_rip, self.p_static]:
            w.setCursor(Qt.PointingHandCursor)
            pg_layout.addWidget(w)
        self.proto_group.setLayout(pg_layout)
        
        l1.addRow("<b>Nome Router:</b>", self.name)
        l1.addRow("<b>ASN (BGP):</b>", self.asn)
        l1.addRow(self.proto_group)
        
        self.tabs.addTab(t1, "1. Generale")
        
        # TAB 2: Interfacce
        t2 = QtWidgets.QWidget()
        l2 = QtWidgets.QVBoxLayout(t2)
        l2.addWidget(QtWidgets.QLabel("Definisci le interfacce di rete e le connessioni:"))
        
        self.iface_table = QtWidgets.QTableWidget(0, 3)
        self.iface_table.setHorizontalHeaderLabels(["Nome Interfaccia", "IP/CIDR", "Dominio di Collisione (LAN)"])
        self.iface_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.iface_table.setAlternatingRowColors(True)
        
        btn_add_if = HoverButton("+ Aggiungi Interfaccia")
        btn_add_if.clicked.connect(self.add_iface)
        
        l2.addWidget(self.iface_table)
        l2.addWidget(btn_add_if)
        
        self.tabs.addTab(t2, "2. Interfacce")
        
        # TAB 3: Loopback
        t3 = QtWidgets.QWidget()
        l3 = QtWidgets.QVBoxLayout(t3)
        l3.addWidget(QtWidgets.QLabel("Indirizzi di Loopback (utili per iBGP e Router ID):"))
        
        self.loop_list = QtWidgets.QListWidget()
        btn_add_loop = HoverButton("+ Aggiungi Loopback")
        btn_add_loop.clicked.connect(self.add_loop)
        
        l3.addWidget(self.loop_list)
        l3.addWidget(btn_add_loop)
        
        self.tabs.addTab(t3, "3. Loopback")
        
        # TAB 4: OSPF Avanzato
        t4 = QtWidgets.QWidget()
        l4 = QtWidgets.QFormLayout(t4)
        
        self.ospf_area = QtWidgets.QLineEdit("0.0.0.0")
        self.ospf_area.setPlaceholderText("0.0.0.0")
        self.ospf_stub = QtWidgets.QCheckBox("Configura come Stub Area")
        
        l4.addRow("Area OSPF Principale:", self.ospf_area)
        l4.addRow("", self.ospf_stub)
        
        self.tabs.addTab(t4, "4. OSPF")
        
        # TAB 5: Route Statiche
        t5 = QtWidgets.QWidget()
        l5 = QtWidgets.QVBoxLayout(t5)
        
        self.static_table = QtWidgets.QTableWidget(0, 3)
        self.static_table.setHorizontalHeaderLabels(["Network (es. 10.0.0.0/24)", "Via (Gateway)", "Device (es. eth0)"])
        self.static_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        
        btn_add_static = HoverButton("+ Aggiungi Route")
        btn_add_static.clicked.connect(self.add_static)
        
        l5.addWidget(self.static_table)
        l5.addWidget(btn_add_static)
        
        self.tabs.addTab(t5, "5. Statiche")
        
        layout.addWidget(self.tabs)
        
        # Buttons
        bbox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.validate_and_accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def add_iface(self):
        r = self.iface_table.rowCount()
        self.iface_table.insertRow(r)
        self.iface_table.setItem(r, 0, QtWidgets.QTableWidgetItem(f"eth{r}"))
        self.iface_table.setItem(r, 1, QtWidgets.QTableWidgetItem(""))
        self.iface_table.setItem(r, 2, QtWidgets.QTableWidgetItem(""))

    def add_loop(self):
        ip, ok = QtWidgets.QInputDialog.getText(self, "Nuovo Loopback", "Inserisci IP/CIDR (es. 1.1.1.1/32):")
        if ok and ip:
            self.loop_list.addItem(ip)

    def add_static(self):
        r = self.static_table.rowCount()
        self.static_table.insertRow(r)

    def validate_and_accept(self):
        if not self.name.text().strip():
            QtWidgets.QMessageBox.warning(self, "Errore", "Il nome del router è obbligatorio.")
            return
        if self.p_bgp.isChecked() and not self.asn.text().strip():
            QtWidgets.QMessageBox.warning(self, "Errore", "L'ASN è obbligatorio se BGP è attivo.")
            return
        self.accept()

    def load_data(self, d):
        self.name.setText(d.get('name', ''))
        self.asn.setText(str(d.get('asn', '')))
        p = d.get('protocols', [])
        self.p_bgp.setChecked('bgp' in p)
        self.p_ospf.setChecked('ospf' in p)
        self.p_rip.setChecked('rip' in p)
        self.p_static.setChecked('statico' in p)
        
        for i in d.get('interfaces', []):
            r = self.iface_table.rowCount()
            self.iface_table.insertRow(r)
            self.iface_table.setItem(r, 0, QtWidgets.QTableWidgetItem(i.get('name', '')))
            self.iface_table.setItem(r, 1, QtWidgets.QTableWidgetItem(i.get('ip', '')))
            self.iface_table.setItem(r, 2, QtWidgets.QTableWidgetItem(i.get('lan', '')))
            
        for lb in d.get('loopbacks', []):
            self.loop_list.addItem(lb)
            
        self.ospf_area.setText(d.get('ospf_area', '0.0.0.0'))
        self.ospf_stub.setChecked(d.get('ospf_area_stub', False))
        
        for rt in d.get('static_routes', []):
            r = self.static_table.rowCount()
            self.static_table.insertRow(r)
            if isinstance(rt, dict):
                self.static_table.setItem(r, 0, QtWidgets.QTableWidgetItem(rt.get('network', '')))
                self.static_table.setItem(r, 1, QtWidgets.QTableWidgetItem(rt.get('via', '')))
                self.static_table.setItem(r, 2, QtWidgets.QTableWidgetItem(rt.get('dev', '')))

    def get_data(self):
        p = []
        if self.p_bgp.isChecked(): p.append('bgp')
        if self.p_ospf.isChecked(): p.append('ospf')
        if self.p_rip.isChecked(): p.append('rip')
        if self.p_static.isChecked(): p.append('statico')
        
        ifaces = []
        for r in range(self.iface_table.rowCount()):
            n = self.iface_table.item(r, 0).text() if self.iface_table.item(r, 0) else f"eth{r}"
            ip = self.iface_table.item(r, 1).text() if self.iface_table.item(r, 1) else ""
            lan = self.iface_table.item(r, 2).text() if self.iface_table.item(r, 2) else ""
            if ip: ifaces.append({'name': n, 'ip': ip, 'lan': lan})
            
        loops = [self.loop_list.item(i).text() for i in range(self.loop_list.count())]
        
        statics = []
        for r in range(self.static_table.rowCount()):
            net = self.static_table.item(r, 0).text() if self.static_table.item(r, 0) else ""
            via = self.static_table.item(r, 1).text() if self.static_table.item(r, 1) else ""
            dev = self.static_table.item(r, 2).text() if self.static_table.item(r, 2) else ""
            if net: statics.append({'network': net, 'via': via, 'dev': dev})
            
        return {
            'name': self.name.text().strip(),
            'asn': self.asn.text().strip(),
            'protocols': p,
            'interfaces': ifaces,
            'loopbacks': loops,
            'ospf_area': self.ospf_area.text().strip(),
            'ospf_area_stub': self.ospf_stub.isChecked(),
            'static_routes': statics
        }

class HostDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        self.setWindowTitle('Configurazione Host')
        self.resize(600, 400)
        self.data = data or {}
        self.setup_ui()
        if data: self.load_data(data)

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        
        l1 = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("es. PC1")
        l1.addRow("<b>Nome Host:</b>", self.name)
        layout.addLayout(l1)
        
        layout.addWidget(QtWidgets.QLabel("<b>Interfacce di Rete:</b>"))
        self.iface_table = QtWidgets.QTableWidget(0, 4)
        self.iface_table.setHorizontalHeaderLabels(["Nome", "IP/CIDR", "Gateway Default", "LAN"])
        self.iface_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        
        btn = HoverButton("+ Aggiungi Interfaccia")
        btn.clicked.connect(self.add_iface)
        
        layout.addWidget(self.iface_table)
        layout.addWidget(btn)
        
        bbox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def add_iface(self):
        r = self.iface_table.rowCount()
        self.iface_table.insertRow(r)
        self.iface_table.setItem(r, 0, QtWidgets.QTableWidgetItem(f"eth{r}"))
        self.iface_table.setItem(r, 1, QtWidgets.QTableWidgetItem(""))
        self.iface_table.setItem(r, 2, QtWidgets.QTableWidgetItem(""))
        self.iface_table.setItem(r, 3, QtWidgets.QTableWidgetItem(""))

    def load_data(self, d):
        self.name.setText(d.get('name', ''))
        for i in d.get('interfaces', []):
            r = self.iface_table.rowCount()
            self.iface_table.insertRow(r)
            self.iface_table.setItem(r, 0, QtWidgets.QTableWidgetItem(i.get('name', '')))
            self.iface_table.setItem(r, 1, QtWidgets.QTableWidgetItem(i.get('ip', '')))
            self.iface_table.setItem(r, 2, QtWidgets.QTableWidgetItem(i.get('gateway', '')))
            self.iface_table.setItem(r, 3, QtWidgets.QTableWidgetItem(i.get('lan', '')))

    def get_data(self):
        ifaces = []
        for r in range(self.iface_table.rowCount()):
            n = self.iface_table.item(r, 0).text() if self.iface_table.item(r, 0) else f"eth{r}"
            ip = self.iface_table.item(r, 1).text() if self.iface_table.item(r, 1) else ""
            gw = self.iface_table.item(r, 2).text() if self.iface_table.item(r, 2) else ""
            lan = self.iface_table.item(r, 3).text() if self.iface_table.item(r, 3) else ""
            if ip: ifaces.append({'name': n, 'ip': ip, 'gateway': gw, 'lan': lan})
        return {'name': self.name.text().strip(), 'interfaces': ifaces}

class WWWDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        self.setWindowTitle('Configurazione Server WWW')
        self.resize(600, 500)
        self.data = data or {}
        self.setup_ui()
        if data: self.load_data(data)

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        
        f = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.ip = QtWidgets.QLineEdit()
        self.gw = QtWidgets.QLineEdit()
        self.lan = QtWidgets.QLineEdit()
        
        f.addRow("<b>Nome Server:</b>", self.name)
        f.addRow("IP/CIDR:", self.ip)
        f.addRow("Gateway:", self.gw)
        f.addRow("LAN:", self.lan)
        layout.addLayout(f)
        
        layout.addWidget(QtWidgets.QLabel("<b>Contenuto index.html:</b>"))
        self.html = QtWidgets.QTextEdit()
        self.html.setPlaceholderText("<html>...</html>")
        self.html.setPlainText("<html>\n<head><title>Welcome</title></head>\n<body>\n<h1>It works!</h1>\n</body>\n</html>")
        layout.addWidget(self.html)
        
        bbox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def load_data(self, d):
        self.name.setText(d.get('name', ''))
        self.ip.setText(d.get('ip', ''))
        self.gw.setText(d.get('gateway', ''))
        self.lan.setText(d.get('lan', ''))
        if d.get('html'): self.html.setPlainText(d['html'])

    def get_data(self):
        return {
            'name': self.name.text().strip(),
            'ip': self.ip.text().strip(),
            'gateway': self.gw.text().strip(),
            'lan': self.lan.text().strip(),
            'html': self.html.toPlainText()
        }

class DNSDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        self.setWindowTitle('Configurazione Server DNS')
        self.resize(650, 500)
        self.data = data or {}
        self.setup_ui()
        if data: self.load_data(data)

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        
        # Base
        t1 = QtWidgets.QWidget()
        f1 = QtWidgets.QFormLayout(t1)
        self.name = QtWidgets.QLineEdit()
        self.ip = QtWidgets.QLineEdit()
        self.gw = QtWidgets.QLineEdit()
        self.lan = QtWidgets.QLineEdit()
        f1.addRow("<b>Nome Server:</b>", self.name)
        f1.addRow("IP/CIDR:", self.ip)
        f1.addRow("Gateway:", self.gw)
        f1.addRow("LAN:", self.lan)
        self.tabs.addTab(t1, "1. Rete")
        
        # Root
        t2 = QtWidgets.QWidget()
        f2 = QtWidgets.QFormLayout(t2)
        self.root_type = QtWidgets.QComboBox()
        self.root_type.addItems(['hint', 'master'])
        self.root_ip = QtWidgets.QLineEdit()
        self.root_ip.setPlaceholderText("IP del Root Server (se hint)")
        f2.addRow("Tipo Root:", self.root_type)
        f2.addRow("Root Server IP:", self.root_ip)
        self.tabs.addTab(t2, "2. Root Config")
        
        # Options
        t3 = QtWidgets.QWidget()
        f3 = QtWidgets.QFormLayout(t3)
        self.fwd = QtWidgets.QLineEdit()
        self.fwd.setPlaceholderText("es. 8.8.8.8 8.8.4.4")
        self.rec = QtWidgets.QLineEdit()
        self.rec.setPlaceholderText("es. any")
        self.dnssec = QtWidgets.QCheckBox("Disabilita DNSSEC Validation")
        f3.addRow("Forwarders:", self.fwd)
        f3.addRow("Allow Recursion:", self.rec)
        f3.addRow("", self.dnssec)
        self.tabs.addTab(t3, "3. Opzioni")
        
        layout.addWidget(self.tabs)
        
        bbox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def load_data(self, d):
        self.name.setText(d.get('name', ''))
        self.ip.setText(d.get('ip', ''))
        self.gw.setText(d.get('gateway', ''))
        self.lan.setText(d.get('lan', ''))
        self.root_type.setCurrentText(d.get('root_type', 'hint'))
        self.root_ip.setText(d.get('root_server_ip', ''))
        if d.get('forwarders'): self.fwd.setText(' '.join(d['forwarders']))
        self.rec.setText(d.get('allow_recursion', ''))
        self.dnssec.setChecked(d.get('dnssec_validation', False))

    def get_data(self):
        fwds = self.fwd.text().strip().split() if self.fwd.text().strip() else []
        return {
            'name': self.name.text().strip(),
            'ip': self.ip.text().strip(),
            'gateway': self.gw.text().strip(),
            'lan': self.lan.text().strip(),
            'root_type': self.root_type.currentText(),
            'root_server_ip': self.root_ip.text().strip(),
            'forwarders': fwds,
            'allow_recursion': self.rec.text().strip(),
            'dnssec_validation': self.dnssec.isChecked()
        }

class PostCreationDialog(QtWidgets.QDialog):
    def __init__(self, parent, base, routers):
        super().__init__(parent)
        self.setWindowTitle('Post-Creation Tools')
        self.resize(600, 450)
        self.base = base
        self.routers = routers
        self.setup_ui()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("<h2>Strumenti Post-Creazione</h2>"))
        layout.addWidget(QtWidgets.QLabel("Seleziona un'azione da eseguire sul lab generato:"))
        
        self.list = QtWidgets.QListWidget()
        items = [
            ("📝 Modifica manuale frr.conf", "Apre l'editor per modificare direttamente la configurazione FRR."),
            ("🔄 Auto-generate BGP Neighbors", "Crea automaticamente i neighbor BGP per router nella stessa LAN."),
            ("🔗 iBGP Loopback Neighbors", "Configura neighbor iBGP usando le interfacce di loopback."),
            ("🛡️ BGP Policies & Filters", "Menu avanzato per Prefix-List, Route-Map, Access-List."),
            ("💰 Assegna Costo OSPF", "Imposta manualmente il costo OSPF per specifiche interfacce."),
            ("⚡ Genera Ping One-Liner", "Crea uno script per testare la connettività tra tutti i nodi.")
        ]
        
        for title, desc in items:
            item = QtWidgets.QListWidgetItem(f"{title}\n   {desc}")
            item.setData(Qt.UserRole, title)
            self.list.addItem(item)
            
        self.list.setStyleSheet("QListWidget::item { padding: 10px; border-bottom: 1px solid #eee; }")
        layout.addWidget(self.list)
        
        btn = HoverButton("Esegui Azione")
        btn.clicked.connect(self.exec_action)
        layout.addWidget(btn)

    def exec_action(self):
        item = self.list.currentItem()
        if not item or not lg: return
        
        idx = self.list.row(item)
        try:
            if idx == 0: lg.modifica_router_menu(self.base, self.routers)
            elif idx == 1: 
                lg.auto_generate_bgp_neighbors(self.base, self.routers)
                QtWidgets.QMessageBox.information(self, 'Successo', 'BGP neighbors generati.')
            elif idx == 2: 
                lg.add_ibgp_loopback_neighbors(self.base, self.routers)
                QtWidgets.QMessageBox.information(self, 'Successo', 'iBGP loopback neighbors configurati.')
            elif idx == 3: lg.policies_menu(self.base, self.routers)
            elif idx == 4: lg.assegna_costo_interfaccia(self.base, self.routers)
            elif idx == 5:
                eps = lg.collect_lab_ips(self.base, self.routers)
                cmd = lg.generate_ping_oneliner(eps)
                d = QtWidgets.QDialog(self)
                d.setWindowTitle('Ping Test Command')
                d.resize(700, 200)
                l = QtWidgets.QVBoxLayout(d)
                t = QtWidgets.QTextEdit()
                t.setPlainText(cmd)
                t.setReadOnly(True)
                l.addWidget(t)
                d.exec()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Errore', str(e))

# --- MAIN WINDOW ---

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Lab Generator GUI')
        self.resize(1600, 900)
        self.lab = {'routers': {}, 'hosts': {}, 'www': {}, 'dns': {}}
        self.output_dir = ''
        self.setup_ui()

    def setup_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central)
        main_layout.setContentsMargins(0,0,0,0)
        main_layout.setSpacing(0)
        
        # --- LEFT PANEL: Controls ---
        left_panel = QtWidgets.QWidget()
        left_panel.setFixedWidth(300)
        left_panel.setStyleSheet(f"background-color: {PANEL_BG}; border-right: 1px solid {BORDER};")
        l_layout = QtWidgets.QVBoxLayout(left_panel)
        l_layout.setContentsMargins(15, 20, 15, 20)
        l_layout.setSpacing(10)
        
        l_layout.addWidget(QtWidgets.QLabel("<h3>Dispositivi</h3>"))
        
        self.dev_list = QtWidgets.QListWidget()
        self.dev_list.itemSelectionChanged.connect(self.on_selection)
        l_layout.addWidget(self.dev_list)
        
        # Action Buttons
        btns = [
            ("Router", self.new_router, "#0969da"),
            ("Host", self.new_host, "#1a7f37"),
            ("WWW", self.new_www, "#bf3989"),
            ("DNS", self.new_dns, "#8250df")
        ]
        
        grid = QtWidgets.QGridLayout()
        for i, (label, slot, col) in enumerate(btns):
            b = HoverButton(f"+ {label}")
            b.setStyleSheet(f"background-color: {col}; color: white; border: none; padding: 8px; font-weight: bold;")
            b.clicked.connect(slot)
            grid.addWidget(b, i//2, i%2)
        l_layout.addLayout(grid)
        
        l_layout.addSpacing(20)
        l_layout.addWidget(QtWidgets.QLabel("<h3>Azioni Lab</h3>"))
        
        self.btn_gen = HoverButton("🚀 Genera Lab")
        self.btn_gen.setStyleSheet(f"background-color: {SUCCESS}; color: white; font-size: 14px; padding: 10px; font-weight: bold;")
        self.btn_gen.clicked.connect(self.gen_lab)
        l_layout.addWidget(self.btn_gen)
        
        self.btn_post = HoverButton("🛠️ Post-Creation Tools")
        self.btn_post.setEnabled(False)
        self.btn_post.setStyleSheet(f"background-color: #6e7781; color: white; padding: 8px;")
        self.btn_post.clicked.connect(self.post_menu)
        l_layout.addWidget(self.btn_post)
        
        # Merged Save/Load Buttons
        h_io = QtWidgets.QHBoxLayout()
        b_save = HoverButton("💾 Salva JSON")
        b_save.setStyleSheet(f"background-color: {ACCENT}; color: white; font-weight: bold; padding: 8px;")
        b_save.clicked.connect(self.save_lab_dialog)
        
        b_load = HoverButton("📂 Carica JSON")
        b_load.setStyleSheet(f"background-color: {ACCENT}; color: white; font-weight: bold; padding: 8px;")
        b_load.clicked.connect(self.load_lab_dialog)
        
        h_io.addWidget(b_save)
        h_io.addWidget(b_load)
        l_layout.addLayout(h_io)

        # Open Lab Folder Button
        b_open_folder = HoverButton("📂 Apri Cartella Lab")
        b_open_folder.setStyleSheet(f"background-color: #d29922; color: white; font-weight: bold; padding: 8px;")
        b_open_folder.clicked.connect(self.open_lab_folder)
        l_layout.addWidget(b_open_folder)
        
        # --- CENTER PANEL: Topology ---
        center_panel = QtWidgets.QWidget()
        c_layout = QtWidgets.QVBoxLayout(center_panel)
        c_layout.setContentsMargins(0,0,0,0)
        
        # Toolbar topologia (Smaller)
        topo_bar = QtWidgets.QWidget()
        topo_bar.setStyleSheet(f"background-color: {LIGHT_BG}; border-bottom: 1px solid {BORDER};")
        topo_bar.setFixedHeight(40) # Reduced height
        tb_layout = QtWidgets.QHBoxLayout(topo_bar)
        tb_layout.setContentsMargins(10, 0, 10, 0) # Reduced margins
        tb_layout.addWidget(QtWidgets.QLabel("<b>Topologia</b>"))
        tb_layout.addStretch()
        btn_refresh = HoverButton("Aggiorna")
        btn_refresh.setStyleSheet(f"background-color: {ACCENT}; color: white; padding: 4px 8px;")
        btn_refresh.clicked.connect(self.redraw)
        tb_layout.addWidget(btn_refresh)
        
        c_layout.addWidget(topo_bar)
        self.topo_view = TopologyView()
        c_layout.addWidget(self.topo_view)
        
        # --- RIGHT PANEL: Details ---
        right_panel = QtWidgets.QWidget()
        right_panel.setFixedWidth(320)
        right_panel.setStyleSheet(f"background-color: {LIGHT_BG}; border-left: 1px solid {BORDER};")
        r_layout = QtWidgets.QVBoxLayout(right_panel)
        r_layout.setContentsMargins(20, 20, 20, 20)
        
        r_layout.addWidget(QtWidgets.QLabel("<h3>Dettagli Selezione</h3>"))
        self.details_area = QtWidgets.QTextEdit()
        self.details_area.setReadOnly(True)
        self.details_area.setStyleSheet("border: none; background: transparent;")
        r_layout.addWidget(self.details_area)
        
        btn_box = QtWidgets.QHBoxLayout()
        self.btn_edit = HoverButton("Modifica")
        self.btn_edit.setStyleSheet(f"background-color: {ACCENT}; color: white;")
        self.btn_edit.clicked.connect(self.edit_dev)
        self.btn_rem = HoverButton("Rimuovi")
        self.btn_rem.setStyleSheet(f"background-color: {ERROR}; color: white;")
        self.btn_rem.clicked.connect(self.rem_dev)
        
        btn_box.addWidget(self.btn_edit)
        btn_box.addWidget(self.btn_rem)
        r_layout.addLayout(btn_box)
        
        # Add panels to main
        main_layout.addWidget(left_panel)
        main_layout.addWidget(center_panel, 1) # Center expands
        main_layout.addWidget(right_panel)
        
        self.setStyleSheet(STYLESHEET)
        self.redraw()

    # --- LOGIC ---
    
    def on_node_click(self, node_id):
        # Seleziona il nodo nella lista e mostra dettagli
        # node_id potrebbe essere "Router_R1" o "R1" o "LAN_..."
        # Cerchiamo nella lista
        
        # Ignora LAN nodes per la selezione (o gestiscili se vuoi)
        if node_id.startswith("LAN_"):
            return

        # Cerca item nella lista
        # Format list: [Type] Name
        
        for i in range(self.dev_list.count()):
            item = self.dev_list.item(i)
            # Check if item text ends with "] node_id" to be exact
            if item.text().endswith(f"] {node_id}"):
                self.dev_list.setCurrentItem(item)
                self.on_selection()
                break

    def new_router(self):
        d = RouterDialog(self)
        if d.exec() == QtWidgets.QDialog.Accepted:
            data = d.get_data()
            name = data['name']
            self.lab['routers'][name] = data
            self.redraw()

    def new_host(self):
        d = HostDialog(self)
        if d.exec() == QtWidgets.QDialog.Accepted:
            data = d.get_data()
            name = data['name']
            self.lab['hosts'][name] = data
            self.redraw()

    def new_www(self):
        d = WWWDialog(self)
        if d.exec() == QtWidgets.QDialog.Accepted:
            data = d.get_data()
            name = data['name']
            self.lab['www'][name] = data
            self.redraw()

    def new_dns(self):
        d = DNSDialog(self)
        if d.exec() == QtWidgets.QDialog.Accepted:
            data = d.get_data()
            name = data['name']
            self.lab['dns'][name] = data
            self.redraw()

    def on_selection(self):
        items = self.dev_list.selectedItems()
        if not items:
            self.details_area.setText("Seleziona un dispositivo per vedere i dettagli.")
            return
        
        txt = items[0].text()
        dtype = txt.split(']')[0][1:] # R, H, W, D
        name = txt.split('] ')[1]
        
        info = ""
        if dtype == 'R':
            d = self.lab['routers'][name]
            info = f"<h1>Router {name}</h1>"
            info += f"<p><b>ASN:</b> {d.get('asn','-')}</p>"
            info += f"<p><b>Protocolli:</b> {', '.join(d.get('protocols',[]))}</p>"
            info += "<h3>Interfacce:</h3><ul>"
            for i in d.get('interfaces', []):
                info += f"<li><b>{i['name']}</b>: {i['ip']} (LAN: {i['lan']})</li>"
            info += "</ul>"
        elif dtype == 'H':
            d = self.lab['hosts'][name]
            info = f"<h1>Host {name}</h1>"
            info += "<h3>Interfacce:</h3><ul>"
            for i in d.get('interfaces', []):
                info += f"<li><b>{i['name']}</b>: {i['ip']} -> GW: {i['gateway']}</li>"
            info += "</ul>"
        elif dtype == 'W':
            d = self.lab['www'][name]
            info = f"<h1>WWW {name}</h1>"
            info += f"<p><b>IP:</b> {d.get('ip')}</p>"
            info += f"<p><b>LAN:</b> {d.get('lan')}</p>"
        elif dtype == 'D':
            d = self.lab['dns'][name]
            info = f"<h1>DNS {name}</h1>"
            info += f"<p><b>IP:</b> {d.get('ip')}</p>"
            info += f"<p><b>Root Type:</b> {d.get('root_type')}</p>"
            
        self.details_area.setHtml(info)

    def edit_dev(self):
        items = self.dev_list.selectedItems()
        if not items: return
        txt = items[0].text()
        dtype = txt.split(']')[0][1:]
        name = txt.split('] ', 1)[1]
        
        if dtype == 'R':
            d = RouterDialog(self, self.lab['routers'][name])
            if d.exec() == QtWidgets.QDialog.Accepted:
                self.lab['routers'][name] = d.get_data()
        elif dtype == 'H':
            d = HostDialog(self, self.lab['hosts'][name])
            if d.exec() == QtWidgets.QDialog.Accepted:
                self.lab['hosts'][name] = d.get_data()
        elif dtype == 'W':
            d = WWWDialog(self, self.lab['www'][name])
            if d.exec() == QtWidgets.QDialog.Accepted:
                self.lab['www'][name] = d.get_data()
        elif dtype == 'D':
            d = DNSDialog(self, self.lab['dns'][name])
            if d.exec() == QtWidgets.QDialog.Accepted:
                self.lab['dns'][name] = d.get_data()
        self.redraw()

    def rem_dev(self):
        items = self.dev_list.selectedItems()
        if not items: return
        txt = items[0].text()
        name = txt.split('] ', 1)[1]
        
        if QtWidgets.QMessageBox.question(self, "Conferma", f"Rimuovere {name}?") == QtWidgets.QMessageBox.Yes:
            for k in self.lab:
                if name in self.lab[k]: del self.lab[k][name]
            self.redraw()

    def build_graph(self):
        G = nx.Graph()
        
        # Routers
        for name, data in self.lab['routers'].items():
            G.add_node(name, device_type='router', asn=data.get('asn'), label=name)
            for iface in data.get('interfaces', []):
                lan = iface.get('lan', '').strip()
                ip = iface.get('ip', '').strip()
                if lan:
                    lan_id = f"LAN_{lan}"
                    if not G.has_node(lan_id):
                        G.add_node(lan_id, device_type='lan', label=lan)
                    G.add_edge(name, lan_id, label=ip)
                    
        # Hosts
        for name, data in self.lab['hosts'].items():
            G.add_node(name, device_type='host', label=name)
            # Host interfaces
            for iface in data.get('interfaces', []):
                # Se l'host ha un campo LAN (che aggiungeremo), usalo.
                # Altrimenti, per ora non colleghiamo se non c'è info.
                lan = iface.get('lan', '').strip()
                ip = iface.get('ip', '').strip()
                if lan:
                    lan_id = f"LAN_{lan}"
                    if not G.has_node(lan_id):
                        G.add_node(lan_id, device_type='lan', label=lan)
                    G.add_edge(name, lan_id, label=ip)
            
        # WWW
        for name, data in self.lab['www'].items():
            G.add_node(name, device_type='www', label=name)
            lan = data.get('lan', '').strip()
            ip = data.get('ip', '').strip()
            if lan:
                lan_id = f"LAN_{lan}"
                if not G.has_node(lan_id):
                    G.add_node(lan_id, device_type='lan', label=lan)
                G.add_edge(name, lan_id, label=ip)
                
        # DNS
        for name, data in self.lab['dns'].items():
            G.add_node(name, device_type='dns', label=name)
            lan = data.get('lan', '').strip()
            ip = data.get('ip', '').strip()
            if lan:
                lan_id = f"LAN_{lan}"
                if not G.has_node(lan_id):
                    G.add_node(lan_id, device_type='lan', label=lan)
                G.add_edge(name, lan_id, label=ip)
                
        return G

    def redraw(self):
        # Update List
        self.dev_list.clear()
        for n in sorted(self.lab['routers']): self.dev_list.addItem(f"[R] {n}")
        for n in sorted(self.lab['hosts']): self.dev_list.addItem(f"[H] {n}")
        for n in sorted(self.lab['www']): self.dev_list.addItem(f"[W] {n}")
        for n in sorted(self.lab['dns']): self.dev_list.addItem(f"[D] {n}")
        
        # Update Graph
        G = self.build_graph()
        self.topo_view.set_graph(G)

    def gen_lab(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Seleziona Cartella Output")
        if not folder: return
        if not lg:
            QtWidgets.QMessageBox.critical(self, "Errore", "Modulo labGenerator non disponibile.")
            return
            
        self.output_dir = folder
        try:
            # Routers
            for n, d in self.lab['routers'].items():
                lg.crea_router_files(folder, n, d)
                
            # Hosts
            for n, d in self.lab['hosts'].items():
                if d.get('interfaces'):
                    # Usa la prima interfaccia per compatibilità base, o estendi lg
                    i0 = d['interfaces'][0]
                    lg.crea_host_file(folder, n, i0.get('ip',''), i0.get('gateway',''), i0.get('lan', ''))
                    
            # WWW
            for n, d in self.lab['www'].items():
                lg.crea_www_file(folder, n, d.get('ip',''), d.get('gateway',''), d.get('lan',''))
                if d.get('html'):
                    p = os.path.join(folder, n, 'var', 'www', 'html', 'index.html')
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, 'w') as f: f.write(d['html'])
                    
            # DNS
            for n, d in self.lab['dns'].items():
                lg.crea_dns_host(folder, n, d.get('ip',''), d.get('gateway',''), d.get('lan',''),
                                 forwarders=d.get('forwarders'), root_type=d.get('root_type'),
                                 root_server_ip=d.get('root_server_ip'), allow_recursion=d.get('allow_recursion'),
                                 dnssec_validation=d.get('dnssec_validation'))
                                 
            # lab.conf
            lines = []
            for n, d in self.lab['routers'].items():
                for i, iface in enumerate(d.get('interfaces', [])):
                    if iface.get('lan'): lines.append(f"{n}[{i}]={iface['lan']}")
            for n, d in self.lab['hosts'].items():
                for i, iface in enumerate(d.get('interfaces', [])):
                    if iface.get('lan'): lines.append(f"{n}[{i}]={iface['lan']}")
            for n, d in self.lab['www'].items():
                if d.get('lan'): lines.append(f"{n}[0]={d['lan']}")
            for n, d in self.lab['dns'].items():
                if d.get('lan'): lines.append(f"{n}[0]={d['lan']}")
                
            with open(os.path.join(folder, 'lab.conf'), 'w') as f:
                f.write('\n'.join(lines))
                
            QtWidgets.QMessageBox.information(self, "Successo", "Lab generato correttamente!")
            self.btn_post.setEnabled(True)
            
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Errore Generazione", str(e))

    def post_menu(self):
        if not self.output_dir: return
        d = PostCreationDialog(self, self.output_dir, self.lab['routers'])
        d.exec()

    def save_lab_dialog(self):
        # Chiedi formato
        formats = "JSON (*.json);;XML (*.xml)"
        f, filter = QtWidgets.QFileDialog.getSaveFileName(self, "Salva Lab", "", formats)
        if not f: return
        
        if filter == "JSON (*.json)":
            with open(f, 'w') as fp: json.dump(self.lab, fp, indent=2)
            QtWidgets.QMessageBox.information(self, "Successo", f"Salvato JSON in {f}")
        else:
            # XML
            # Combina hosts e dns per l'export
            combined_hosts = []
            for h in self.lab['hosts'].values():
                combined_hosts.append(h)
            for d in self.lab['dns'].values():
                d_copy = d.copy()
                d_copy['dns'] = True
                combined_hosts.append(d_copy)
            combined_www = list(self.lab['www'].values())
            
            try:
                dname = os.path.dirname(f)
                fname = os.path.splitext(os.path.basename(f))[0]
                lg.export_lab_to_xml(fname, dname, self.lab['routers'], combined_hosts, combined_www)
                QtWidgets.QMessageBox.information(self, "Successo", f"Salvato XML in {f}")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Errore Export XML", str(e))

    def load_lab_dialog(self):
        formats = "JSON (*.json);;XML (*.xml)"
        f, filter = QtWidgets.QFileDialog.getOpenFileName(self, "Carica Lab", "", formats)
        if not f: return
        
        if filter == "JSON (*.json)":
            try:
                with open(f, 'r') as fp: self.lab = json.load(fp)
                self.redraw()
                QtWidgets.QMessageBox.information(self, "Successo", "Lab caricato da JSON")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Errore", str(e))
        else:
            # XML
            try:
                _, routers, hosts_all, wwws, _ = lg.load_lab_from_xml(f)
                self.lab = {'routers': routers, 'hosts': {}, 'www': {}, 'dns': {}}
                for h in hosts_all:
                    if h.get('dns'):
                        self.lab['dns'][h['name']] = h
                    else:
                        if 'interfaces' not in h:
                            h['interfaces'] = [{
                                'name': 'eth0',
                                'ip': h.get('ip',''),
                                'gateway': h.get('gateway',''),
                                'lan': h.get('lan','')
                            }]
                        self.lab['hosts'][h['name']] = h
                for w in wwws:
                    self.lab['www'][w['name']] = w
                self.redraw()
                QtWidgets.QMessageBox.information(self, "Successo", "Lab caricato da XML")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Errore Import XML", str(e))

    def local_parse_startup_files(self, folder, nodes):
        import re
        for name in nodes:
            startup_file = os.path.join(folder, f"{name}.startup")
            if os.path.exists(startup_file):
                with open(startup_file, 'r') as f:
                    content = f.read()
                    
                # Regex for "ip addr add <IP> ... dev eth<N>"
                # Matches: ip addr add 192.168.1.1/24 dev eth0
                matches = re.findall(r'ip\s+addr\s+add\s+([0-9\./]+).*?dev\s+eth(\d+)', content)
                
                # Also check for "ifconfig eth<N> <IP>"
                ifconfig_matches = re.findall(r'ifconfig\s+eth(\d+)\s+([0-9\./]+)', content)
                # Swap to (ip, idx) format and add to matches
                matches.extend([(m[1], m[0]) for m in ifconfig_matches])
                
                # Check for default gateway
                # ip route add default via <IP>
                gw_match = re.search(r'ip\s+route\s+add\s+default\s+via\s+([0-9\.]+)', content)
                if not gw_match:
                    # route add default gw <IP>
                    gw_match = re.search(r'route\s+add\s+default\s+gw\s+([0-9\.]+)', content)
                
                if matches:
                    if 'ips' not in nodes[name]:
                        nodes[name]['ips'] = {}
                    for ip, idx in matches:
                        nodes[name]['ips'][int(idx)] = ip
                        
                if gw_match:
                    nodes[name]['gateway'] = gw_match.group(1)
        return nodes

    def open_lab_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Seleziona Cartella Lab Esistente")
        if not folder: return
        
        # 1. Cerca file di export XML o JSON standard
        # Cerchiamo file .xml che potrebbero essere export
        # O cerchiamo lab.conf
        
        lab_conf = os.path.join(folder, 'lab.conf')
        if not os.path.exists(lab_conf):
            QtWidgets.QMessageBox.warning(self, "Attenzione", "Nessun file lab.conf trovato nella cartella.")
            return
            
        # Prova a vedere se c'è un XML con lo stesso nome della cartella
        dirname = os.path.basename(folder)
        xml_path = os.path.join(folder, f"{dirname}.xml")
        
        if os.path.exists(xml_path):
            # Carica da XML (migliore)
            try:
                _, routers, hosts_all, wwws, _ = lg.load_lab_from_xml(xml_path)
                self.lab = {'routers': routers, 'hosts': {}, 'www': {}, 'dns': {}}
                for h in hosts_all:
                    if h.get('dns'):
                        self.lab['dns'][h['name']] = h
                    else:
                        if 'interfaces' not in h:
                            h['interfaces'] = [{
                                'name': 'eth0',
                                'ip': h.get('ip',''),
                                'gateway': h.get('gateway',''),
                                'lan': h.get('lan','')
                            }]
                        self.lab['hosts'][h['name']] = h
                for w in wwws:
                    self.lab['www'][w['name']] = w
                self.redraw()
                QtWidgets.QMessageBox.information(self, "Successo", f"Lab caricato da XML: {xml_path}")
                return
            except Exception as e:
                print(f"Errore caricamento XML: {e}")
            # Fallback: parse lab.conf
        try:
            nodes, lab_conf_text = lg.parse_lab_conf_for_nodes(folder)
            if not nodes:
                QtWidgets.QMessageBox.warning(self, "Errore", "Nessun file lab.conf trovato o file vuoto.")
                return

            # Advanced Import: Parse startup files for IPs
            # nodes = lg.parse_startup_files(folder, nodes)
            nodes = self.local_parse_startup_files(folder, nodes)

            # Ricostruisci struttura lab
            self.lab_name = os.path.basename(folder)
            self.lab['routers'] = {}
            self.lab['hosts'] = {}
            self.lab['www'] = {}
            self.lab['dns'] = {}
            
            # Euristiche per tipo dispositivo
            for name, data in nodes.items():
                image = data.get('image', '').lower()
                interfaces = []
                # data['interfaces'] è {idx: lan}
                # data['ips'] è {idx: ip} (opzionale)
                ips = data.get('ips', {})
                
                sorted_idxs = sorted(data['interfaces'].keys())
                for idx in sorted_idxs:
                    lan = data['interfaces'][idx]
                    ip = ips.get(idx, '')
                    interfaces.append({'name': f'eth{idx}', 'lan': lan, 'ip': ip})

                # Heuristics based on Image AND Name
                is_router = False
                is_www = False
                is_dns = False
                
                # Check Image first
                if 'router' in image or 'frr' in image or 'quagga' in image:
                    is_router = True
                elif 'www' in image or 'apache' in image or 'nginx' in image:
                    is_www = True
                elif 'bind' in image or 'dns' in image:
                    is_dns = True
                
                # Check Name if Image didn't match specific types (or image is empty)
                if not (is_router or is_www or is_dns):
                    lower_name = name.lower()
                    if lower_name.startswith('r') or 'router' in lower_name:
                        is_router = True
                    elif lower_name.startswith('w') or 'www' in lower_name or 'server' in lower_name:
                        is_www = True
                    elif 'dns' in lower_name or 'ns' in lower_name:
                        is_dns = True
                
                if is_router:
                    self.lab['routers'][name] = {
                        'protocols': [],
                        'asn': '',
                        'interfaces': interfaces
                    }
                elif is_www:
                    # WWW usually 1 interface
                    ip = interfaces[0]['ip'] if interfaces else ''
                    lan = interfaces[0]['lan'] if interfaces else ''
                    self.lab['www'][name] = {
                        'image': data.get('image', ''),
                        'ip': ip,
                        'lan': lan,
                        'gateway': ''
                    }
                elif is_dns:
                     # DNS
                    ip = interfaces[0]['ip'] if interfaces else ''
                    lan = interfaces[0]['lan'] if interfaces else ''
                    self.lab['dns'][name] = {
                        'image': data.get('image', ''),
                        'ip': ip,
                        'lan': lan,
                        'gateway': ''
                    }
                else:
                    # Default Host
                    # Reconstruct interfaces list for HostDialog
                    host_ifaces = []
                    for iface in interfaces:
                        # iface is {'name': 'ethX', 'lan': '...', 'ip': '...'}
                        # Add gateway if it's the first interface (simplification)
                        gw = data.get('gateway', '') if iface['name'] == 'eth0' else ''
                        host_ifaces.append({
                            'name': iface['name'],
                            'ip': iface['ip'],
                            'lan': iface['lan'],
                            'gateway': gw
                        })
                        
                    self.lab['hosts'][name] = {
                        'name': name,
                        'image': data.get('image', ''),
                        'interfaces': host_ifaces
                    }
            
            self.output_dir = folder
            self.btn_post.setEnabled(True)
            
            QtWidgets.QMessageBox.information(self, "Importazione Parziale", 
                                              "Lab importato da lab.conf.\n"
                                              "Alcune configurazioni (protocolli, zone DNS, ecc.) potrebbero mancare.\n"
                                              "Gli IP e Gateway sono stati recuperati dai file .startup (se presenti).")
            
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Errore Import", str(e))

def main():
    # Fix per crash QWebEngineView su macOS
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
    
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion") # Base style
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
