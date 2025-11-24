#!/usr/bin/env python3
"""
lab_generator.py
- Sposta i comandi di debug BGP globali sotto 'log file /var/log/frr/frr.log'
  invece che dentro la sezione 'router bgp ...'
"""
import os
import shutil
import ipaddress
import subprocess
import argparse
import json
import sys
import re

# -------------------------
# Utility input / validazioni
# -------------------------
def input_non_vuoto(prompt):
    while True:
        v = input(prompt).strip()
        if v:
            return v

def input_lan(prompt):
    """Chiede il nome della LAN e valida che contenga solo lettere e numeri.
    Ritorna il valore in maiuscolo. Esempi validi: 'A', 'A1', 'LAN2'.
    Non permette punti o altri caratteri (es. indirizzi IP).
    """
    while True:
        s = input_non_vuoto(prompt).strip().upper()
        # accetta solo lettere e numeri, almeno 1 carattere
        if s.isalnum():
            return s
        print("❌ Nome LAN non valido. Usa solo lettere e numeri (es. A o A1). Evita punti o indirizzi IP.")

def input_int(prompt, min_val=0):
    while True:
        s = input(prompt).strip()
        try:
            n = int(s)
            if n >= min_val:
                return n
            print(f"❌ Inserisci un intero ≥ {min_val}")
        except ValueError:
            print("❌ Inserisci un numero intero valido")
 
def valida_ip_cidr(prompt):
    """Valida e ritorna un indirizzo con CIDR (es. 10.0.0.1/24)."""
    while True:
        s = input(prompt).strip()
        # richiediamo esplicitamente la presenza del prefisso '/'
        if '/' not in s:
            print("❌ Inserisci la maschera usando '/' alla fine (es. 192.168.1.1/24)")
            continue
        try:
            ipaddress.ip_interface(s)
            return s
        except Exception:
            print("❌ Formato non valido. Usa x.x.x.x/yy (es. 192.168.1.1/24)")

def valida_ip_senza_cidr(prompt):
    while True:
        s = input(prompt).strip()
        try:
            ipaddress.ip_address(s)
            return s
        except Exception:
            print("❌ IP non valido. Inserisci un IPv4 (es. 10.0.0.2)")

def valida_protocols(prompt):
    allowed = {"bgp", "ospf", "rip", "statico"}
    while True:
        s = input(prompt).strip().lower().replace(",", " ")
        # Non permettere input vuoto: l'utente deve indicare almeno un valore
        if not s:
            print("❌ Inserisci almeno un protocollo (bgp/ospf/rip/statico).")
            continue
        toks = [t for t in s.split() if t]
        if toks and all(t in allowed for t in toks):
            return list(dict.fromkeys(toks))
        print("❌ Usa solo: bgp ospf rip statico")


def print_menu(title, items, extra_options=None):
    """Stampa un menu ordinato con titolo, una riga vuota, opzioni numerate

    - `title`: stringa titolo (stampa su singola riga)
    - `items`: lista di stringhe che saranno numerate 1..n
    - `extra_options`: lista di tuple (chiave, etichetta) per opzioni non numeriche
    """
    # titolo e riga vuota
    print(f"\n{title}\n")
    # opzioni numerate
    for i, it in enumerate(items, start=1):
        print(f"  {i}) {it}")
    # opzioni extra (es. M) Inserisci manualmente)
    if extra_options:
        for k, lab in extra_options:
            print(f"  {k}) {lab}")
    # linea vuota prima del prompt
    print("")

# -------------------------
# Templates
# -------------------------
DAEMONS_TMPL = """zebra={zebra} 
ripd={ripd}
ospfd={ospfd}
bgpd={bgpd}

ospf6d=no
ripngd=no
isisd=no
pimd=no
ldpd=no
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
staticd=no
pbrd=no
bfdd=no
fabricd=no

######

vtysh_enable=yes
zebra_options=" -s 90000000 --daemon -A 127.0.0.1"
bgpd_options="   --daemon -A 127.0.0.1"
ospfd_options="  --daemon -A 127.0.0.1"
ospf6d_options=" --daemon -A ::1"
ripd_options="   --daemon -A 127.0.0.1"
ripngd_options=" --daemon -A ::1"
isisd_options="  --daemon -A 127.0.0.1"
pimd_options="  --daemon -A 127.0.0.1"
ldpd_options="  --daemon -A 127.0.0.1"
nhrpd_options="  --daemon -A 127.0.0.1"
eigrpd_options="  --daemon -A 127.0.0.1"
babeld_options="  --daemon -A 127.0.0.1"
sharpd_options="  --daemon -A 127.0.0.1"
staticd_options="  --daemon -A 127.0.0.1"
pbrd_options="  --daemon -A 127.0.0.1"
bfdd_options="  --daemon -A 127.0.0.1"
fabricd_options="  --daemon -A 127.0.0.1"
"""

VTYSH_TMPL = """service integrated-vtysh-config
hostname {hostname}
"""

FRR_HEADER = """password zebra
enable password zebra

log file /var/log/frr/frr.log
"""
STARTUP_ROUTER_TMPL = """{ip_config}

systemctl start frr
"""

STARTUP_HOST_TMPL = """{ip_config}
ip route add default via {gateway} dev eth0
"""

STARTUP_WWW_TMPL = """ip address add {ip} dev eth0
ip route add default via {gateway} dev eth0
systemctl start apache2 
"""

WWW_INDEX = """<html><head><title>www</title></head><body><h1>Server WWW</h1></body></html>"""

LAB_CONF_HEADER = ""

# -------------------------
# Helpers FRR: aggregazione reti
# -------------------------
def aggregate_to_supernet_for_router(interface_ips, agg_prefix=16):
    """
    Aggrega le reti collegate di un router.

    - Prima collassa gli indirizzi adiacenti (come prima).
    - Poi, per IPv4, se ci sono più reti che ricadono nello stesso supernet
      di lunghezza `agg_prefix` (es. /16), queste saranno aggregate in quel
      supernet. Se invece c'è una sola rete nel supernet, viene mantenuta
      la rete originale.

    Questo evita di permettere modifiche manuali ai singoli `frr.conf` per
    gestire l'aggregazione: viene calcolata automaticamente qui.

    Note/assunzioni:
    - L'aggregazione automatica opera per default su /16 per IPv4. Se vuoi
      un comportamento diverso puoi cambiare `agg_prefix`.
    - IPv6 viene semplicemente collassato ma non verrà ulteriormente
      supernettato dal comportamento di gruppo qui (per evitare scelte
      ambigue sui prefissi IPv6).
    """
    networks = []
    for ip_cidr in interface_ips:
        try:
            iface = ipaddress.ip_interface(ip_cidr)
            networks.append(iface.network)
        except ValueError:
            continue
    if not networks:
        return []

    # Collassa reti contigue come prima
    collapsed = list(ipaddress.collapse_addresses(networks))

    ipv4_nets = [n for n in collapsed if isinstance(n, ipaddress.IPv4Network)]
    ipv6_nets = [n for n in collapsed if isinstance(n, ipaddress.IPv6Network)]

    # Raggruppa IPv4 per supernet di lunghezza agg_prefix
    super_map = {}
    for n in ipv4_nets:
        if n.prefixlen <= agg_prefix:
            # la rete è già più ampia o uguale del target: la manteniamo così
            super_map.setdefault(n, []).append(n)
        else:
            s = n.supernet(new_prefix=agg_prefix)
            super_map.setdefault(s, []).append(n)

    result_nets = []
    for supern, members in super_map.items():
        if isinstance(supern, ipaddress.IPv4Network):
            # se ci sono più reti nel supernet, allora usiamo il supernet
            if len(members) > 1:
                result_nets.append(supern)
            else:
                # una sola rete -> mantieni la rete originale
                result_nets.append(members[0])
        else:
            # safety fallback
            for m in members:
                result_nets.append(m)

    # aggiungi IPv6 così come è stato collassato
    result_nets.extend(ipv6_nets)

    # Riricollassa per sicurezza e ritorna come stringhe
    final = list(ipaddress.collapse_addresses(result_nets))
    return [str(n) for n in final]


def collapse_interface_networks(interface_ips):
    """
    Collassa le reti derivate dalle interfacce evitando di forzare
    supernet di lunghezza fissa. Restituisce la lista di reti collassate
    (es. ['1.2.0.0/24','1.3.0.0/24'] -> ['1.2.0.0/24','1.3.0.0/24'] o
    se contigue vengono unite secondo ipaddress.collapse_addresses).

    Questa funzione è il punto centrale che useremo per passare le reti
    ai generatori di stanza (BGP/OSPF/RIP). I generatori possono poi
    scegliere di prendere un supernet più ampio se lo ritengono opportuno.
    """
    networks = []
    for ip_cidr in interface_ips:
        try:
            iface = ipaddress.ip_interface(ip_cidr)
            networks.append(iface.network)
        except Exception:
            continue
    if not networks:
        return []
    collapsed = list(ipaddress.collapse_addresses(networks))
    return [str(n) for n in collapsed]


def choose_allowed_byte_aligned_supernet(interface_ips):
    """
    Sceglie un singolo supernet byte-aligned (solo /8 o /16) che copra
    tutte le interfacce fornite in `interface_ips` (lista di stringhe IP/CIDR).

    Regole:
    - Preferisce /16 quando le interfacce condividono i primi due ottetti.
    - Usa /8 solo se condividono solo il primo ottetto.
    - Non restituisce /24: gli accorciamenti a /24 non sono consentiti.
    - Se non è possibile trovare un /16 o /8 sensato, ritorna None.
    """
    nets = []
    for ip_cidr in interface_ips:
        try:
            iface = ipaddress.ip_interface(ip_cidr)
            nets.append(iface.network)
        except Exception:
            continue
    if not nets:
        return None
    collapsed = list(ipaddress.collapse_addresses(nets))
    ipv4_nets = [n for n in collapsed if isinstance(n, ipaddress.IPv4Network)]
    if not ipv4_nets:
        return None
    min_addr = min(n.network_address for n in ipv4_nets)
    max_addr = max(n.broadcast_address for n in ipv4_nets)
    # Deterministic byte-by-byte logic to prefer /16 when sensible.
    # Extract IPv4 addresses as tuples of octets
    octets_list = []
    for ip_cidr in interface_ips:
        try:
            iface = ipaddress.ip_interface(ip_cidr)
            ip = iface.ip
            if isinstance(ip, ipaddress.IPv4Address):
                octs = tuple(int(x) for x in str(ip).split('.'))
                octets_list.append(octs)
        except Exception:
            continue
    if not octets_list:
        return None

    # check commonality
    same_first2 = all(o[:2] == octets_list[0][:2] for o in octets_list)
    same_first1 = all(o[0] == octets_list[0][0] for o in octets_list)

    if same_first2:
        # prefer /16 when possible
        a, b = octets_list[0][0], octets_list[0][1]
        return f"{a}.{b}.0.0/16"
    if same_first1:
        # /8
        a = octets_list[0][0]
        return f"{a}.0.0.0/8"

    return None


def group_by_first_octet(interface_ips):
    """Raggruppa gli indirizzi per primo ottetto (es. 100.x.x.x -> key 100).
    Restituisce dict: {first_octet_int: [ip_cidr_str, ...], ...}
    """
    groups = {}
    for ip_cidr in interface_ips:
        try:
            iface = ipaddress.ip_interface(ip_cidr)
            addr = iface.ip
            if isinstance(addr, ipaddress.IPv4Address):
                fo = int(str(addr).split('.')[0])
                groups.setdefault(fo, []).append(ip_cidr)
        except Exception:
            continue
    return groups

# -------------------------
# FRR stanza builders
# -------------------------
def mk_bgp_stanza(asn, redistribute=None, networks=None):
    """
    Costruisce la stanza BGP. Secondo le regole fornite dall'utente,
    BGP deve annunciare tutte le network effettivamente collegate al
    router senza accorciarle: manteniamo i prefissi originali.
    """
    lines = [
        f"router bgp {asn}",
        "    no bgp ebgp-requires-policy",
        "    no bgp network import-check",
    ]
    if networks:
        # scrivi le network così come sono (dedupando)
        seen = set()
        for net in networks:
            try:
                # normalizza la rete (es. 10.0.1.1/24 -> 10.0.1.0/24)
                n = ipaddress.ip_network(net, strict=False)
                s = str(n)
            except Exception:
                s = str(net)
            if s not in seen:
                lines.append(f"    network {s}")
                seen.add(s)
    # NOTE: non aggiungiamo più automaticamente comandi `redistribute`.
    # L'amministratore preferisce gestirli manualmente nel file `frr.conf`.
    # Questo evita policy non desiderate inserite automaticamente.

    return "\n".join(lines) + "\n\n"

def mk_ospf_stanza(networks, area=None, stub=False, redistribute=None):
    """
    Costruisce la stanza OSPF. Se viene passato `area`, le network saranno
    annotate con `area <area>`. Se `stub` è True, aggiunge la linea
    `area <area> stub` dopo le network.

    Per accorciamenti, usiamo solo prefissi /8, /16 o /24 quando possibile.
    """
    lines = ["router ospf"]
    if networks:
        try:
            nets = [ipaddress.ip_network(n, strict=False) for n in networks]
            ipv4_nets = [n for n in nets if isinstance(n, ipaddress.IPv4Network)]
            ipv6_nets = [n for n in nets if isinstance(n, ipaddress.IPv6Network)]
            if ipv4_nets:
                # collapse first
                collapsed = list(ipaddress.collapse_addresses(ipv4_nets))
                # use deterministic byte-based chooser (prefers /24, then /16, then /8)
                try:
                    cand = choose_allowed_byte_aligned_supernet([str(n) for n in ipv4_nets])
                except Exception:
                    cand = None
                if cand:
                    area_str = f" area {area}" if area else ""
                    lines.append(f"    network {cand}{area_str}")
                else:
                    # fallback: scrivi reti collassate con area
                    for n in collapsed:
                        area_str = f" area {area}" if area else ""
                        lines.append(f"    network {n}{area_str}")
            for n in ipv6_nets:
                area_str = f" area {area}" if area else ""
                lines.append(f"    network {n}{area_str}")
        except Exception:
            for net in networks:
                area_str = f" area {area}" if area else ""
                lines.append(f"    network {net}{area_str}")
    if stub and area:
        lines.append(f"area {area} stub")
    return "\n".join(lines) + "\n\n"

def mk_rip_stanza(networks, redistribute=None):
    lines = ["router rip"]
    for net in networks:
        lines.append(f"    network {net}")
    # Non aggiungiamo automaticamente `redistribute`.
    return "\n".join(lines) + "\n\n"


def format_ospf_multi_area(area_nets_map, stub_areas=None):
    """
    Costruisce un unico blocco 'router ospf' che contiene tutte le network
    annotate con la relativa area, e le dichiarazioni 'area <id> stub'
    dopo le network.

    - area_nets_map: dict area_id -> list of network strings
    - stub_areas: iterable of area_id da marcare come stub
    """
    if stub_areas is None:
        stub_areas = set()
    lines = ["router ospf"]
    # ordiniamo le aree per stabilità (area 0.0.0.0 prima se presente)
    def area_sort_key(a):
        return (0 if a == '0.0.0.0' else 1, str(a))
    for area in sorted(area_nets_map.keys(), key=area_sort_key):
        nets = area_nets_map.get(area) or []
        for n in nets:
            lines.append(f"    network {n} area {area}")
    # aggiungi dichiarazioni stub dopo le network
    for area in sorted(stub_areas):
        lines.append(f"    area {area} stub")
    return "\n".join(lines) + "\n\n"

# -------------------------
# Creazione file router
# -------------------------
def crea_router_files(base_path, rname, data):
    # Se il router è marcato come 'statico' (cioè l'utente ha scelto solo
    # il token 'statico' tra i protocolli), non creiamo la cartella del
    # router né i file FRR: generiamo solo lo startup con IP e rotte statiche.
    protos = data.get("protocols") or []
    only_static = ('statico' in protos) and all(p == 'statico' for p in protos)
    if only_static:
        ip_cfg_lines = [f"ip address add {iface['ip']} dev {iface.get('name','eth0')}" for iface in data.get('interfaces', [])]
        # loopbacks (se presenti) vanno aggiunte allo startup come interfacce lo
        for lb in (data.get('loopbacks') or []):
            ip_cfg_lines.append(f"ip address add {lb} dev lo")
        # static_routes può essere una lista di stringhe o dict.
        # Esempio dict: {"network":"30.0.0.0/24","via":"10.0.0.13","dev":"eth0"}
        static_routes = data.get('static_routes', []) or []
        route_lines = []
        # default dev se non specificato: prima interfaccia o 'eth0'
        default_dev = (data.get('interfaces') and data.get('interfaces')[0].get('name')) or 'eth0'
        for r in static_routes:
            if isinstance(r, str):
                # se l'utente ha fornito la stringa completa, rimuoviamo
                # eventuale maschera dal next-hop (es. 'via 10.0.0.13/30' -> 'via 10.0.0.13')
                s = r
                # sostituisce pattern 'via <ip>/<mask>' con 'via <ip>'
                s = re.sub(r"(via\s+)([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)/\d+", r"\1\2", s)
                route_lines.append(f"ip route add {s}")
            elif isinstance(r, dict):
                net = r.get('network') or r.get('net') or r.get('dest')
                via = r.get('via') or r.get('nexthop') or r.get('gw')
                dev = r.get('dev') or r.get('if') or default_dev
                # rimuovi la maschera dal next-hop se presente
                if isinstance(via, str) and '/' in via:
                    via = via.split('/')[0]
                if net and via:
                    route_lines.append(f"ip route add {net} via {via} dev {dev}")
                elif net and dev:
                    route_lines.append(f"ip route add {net} dev {dev}")
                elif isinstance(r.get('cmd'), str):
                    route_lines.append(r.get('cmd'))
        startup_lines = ip_cfg_lines + route_lines
        startup_path = os.path.join(base_path, f"{rname}.startup")
        with open(startup_path, "w") as f:
            f.write("\n".join(startup_lines) + "\n")
        try:
            os.chmod(startup_path, 0o755)
        except Exception:
            pass
        return

    etc_frr = os.path.join(base_path, rname, "etc", "frr")
    os.makedirs(etc_frr, exist_ok=True)

    # Forza sempre zebra=yes come richiesto
    zebra_flag = "yes"
    daemons = DAEMONS_TMPL.format(
        zebra=zebra_flag,
        ripd="yes" if "rip" in data["protocols"] else "no",
        ospfd="yes" if "ospf" in data["protocols"] else "no",
        bgpd="yes" if "bgp" in data["protocols"] else "no"
    )
    with open(os.path.join(etc_frr, "daemons"), "w") as f:
        f.write(daemons)

    hostname_line = f"{rname}-frr"
    with open(os.path.join(etc_frr, "vtysh.conf"), "w") as f:
        f.write(VTYSH_TMPL.format(hostname=hostname_line))

    parts = [FRR_HEADER]

    # Se il router usa BGP, aggiungi debug subito dopo l'intestazione
    if "bgp" in data["protocols"]:
        parts.append(
            "debug bgp keepalives\n"
            "debug bgp updates in\n"
            "debug bgp updates out\n"
        )

    phys_iface_ips = [iface["ip"] for iface in data["interfaces"]]
    # loopbacks separate: le aggiungiamo solo per OSPF/RIP e per lo startup,
    # ma non per BGP
    loopbacks = data.get('loopbacks') or []
    # combined (interfacce fisiche + loopbacks) per OSPF/RIP/aggregazioni
    combined_ips = phys_iface_ips + loopbacks
    # determiniamo le reti originali (una per interfaccia fisica) per BGP
    original_nets = []
    for ip_cidr in phys_iface_ips:
        try:
            n = ipaddress.ip_network(ip_cidr, strict=False)
            original_nets.append(str(n))
        except Exception:
            continue

    # collapsed networks (fallback per OSPF/RIP quando necessario)
    aggregated_nets = collapse_interface_networks(combined_ips)

    # Non creiamo più automaticamente direttive `redistribute`.
    # BGP: annuncia tutte le network collegate senza accorciarle (mantieni prefissi originali)
    if "bgp" in data["protocols"]:
        parts.append(mk_bgp_stanza(data.get("asn", ""), networks=original_nets))
    # OSPF: usa area se fornita nel dato del router (campo 'ospf_area'), supporta stub
    if "ospf" in data["protocols"]:
        area_main = data.get('ospf_area')
        stub_main = bool(data.get('ospf_area_stub'))
        # Raggruppiamo le interfacce per "nuvole" (primo ottetto)
        groups = group_by_first_octet(combined_ips)
        if not groups:
            # fallback: come prima
            chosen = choose_allowed_byte_aligned_supernet(combined_ips)
            nets_for_ospf = [chosen] if chosen else aggregated_nets
            parts.append(mk_ospf_stanza(nets_for_ospf, area=area_main, stub=stub_main))
        elif len(groups) == 1:
            # unica nuvola: comportamento normale
            chosen = choose_allowed_byte_aligned_supernet(combined_ips)
            nets_for_ospf = [chosen] if chosen else aggregated_nets
            parts.append(mk_ospf_stanza(nets_for_ospf, area=area_main, stub=stub_main))
        else:
            # multi-area: assegna l'area principale alla nuvola più grande
            main_key = max(groups.keys(), key=lambda k: len(groups[k]))
            # networks for main group
            main_ips = groups[main_key]
            chosen_main = choose_allowed_byte_aligned_supernet(main_ips + (loopbacks or []))
            nets_main = [chosen_main] if chosen_main else collapse_interface_networks(main_ips)
            # build mapping area -> nets and set of stub areas
            area_nets = {}
            stub_areas = set()
            # ensure main area has a valid id
            main_area_id = area_main if area_main else '0.0.0.0'
            area_nets.setdefault(main_area_id, []).extend(nets_main)
            if stub_main:
                stub_areas.add(main_area_id)
            # assicurati che le loopback (se presenti) siano allocate nell'area principale
            if loopbacks:
                for lb in loopbacks:
                    try:
                        lb_net = str(ipaddress.ip_network(lb, strict=False))
                        if lb_net not in area_nets.setdefault(main_area_id, []):
                            area_nets[main_area_id].append(lb_net)
                    except Exception:
                        # ignora loopback non valide
                        pass
            # gestisci le altre nuvole: area dedicata (stub) o richiesta interattiva
            extra_areas = data.get('ospf_extra_areas', {}) if isinstance(data.get('ospf_extra_areas', {}), dict) else {}
            for k in sorted(groups.keys()):
                if k == main_key:
                    continue
                # verifica se è stata fornita un'area specifica per questo primo ottetto
                if str(k) in extra_areas:
                    a_info = extra_areas[str(k)]
                    a_id = a_info.get('area') if isinstance(a_info, dict) else str(a_info)
                    a_stub = bool(a_info.get('stub')) if isinstance(a_info, dict) else True
                else:
                    # chiedi interattivamente quale area usare; se vuoto -> auto genera e marca stub
                    try:
                        ans = input(f"Router {rname} OSPF: area per interfacce con primo ottetto {k} (vuoto->auto stub 1.1.1.1): ").strip()
                    except Exception:
                        ans = ''
                    if ans:
                        a_id = ans
                        s = input("Marcare quest'area come stub? (s/N): ").strip().lower()
                        a_stub = s.startswith('s')
                    else:
                        # area auto-generata: assegna la stub predefinita '1.1.1.1'
                        a_id = '1.1.1.1'
                        a_stub = True
                group_ips = groups[k]
                # per ogni gruppo valutiamo anche loopbacks (se appartengono alla stessa "nuvola")
                chosen_g = choose_allowed_byte_aligned_supernet(group_ips + (loopbacks or []))
                nets_g = [chosen_g] if chosen_g else collapse_interface_networks(group_ips)
                area_nets.setdefault(a_id, []).extend(nets_g)
                if a_stub:
                    stub_areas.add(a_id)
            # finally append a single multi-area ospf block
            parts.append(format_ospf_multi_area(area_nets, stub_areas))
    # RIP: accorcia le reti alla network byte-aligned (/8,/16,/24) che copra tutte le LAN collegate
    # Per RIP/OSPF vogliamo considerare anche le loopback se presenti (possono
    # essere rilevanti per simulazioni), quindi usiamo `combined_ips`.
    if "rip" in data["protocols"]:
        rip_net = choose_allowed_byte_aligned_supernet(combined_ips)
        if rip_net:
            parts.append(mk_rip_stanza([rip_net]))
        else:
            parts.append(mk_rip_stanza(aggregated_nets))

    with open(os.path.join(etc_frr, "frr.conf"), "w") as f:
        f.write("\n".join(parts))

    ip_cfg_lines = [f"ip address add {iface['ip']} dev {iface['name']}" for iface in data["interfaces"]]
    # aggiungi le loopback allo startup
    for lb in loopbacks:
        ip_cfg_lines.append(f"ip address add {lb} dev lo")
    startup_path = os.path.join(base_path, f"{rname}.startup")
    with open(startup_path, "w") as f:
        f.write(STARTUP_ROUTER_TMPL.format(ip_config="\n".join(ip_cfg_lines)))
    try:
        os.chmod(startup_path, 0o755)
    except Exception:
        pass



# -------------------------
# Host e WWW
# -------------------------
def crea_host_file(base_path, hname, ip_cidr, gateway_cidr, lan):
    os.makedirs(base_path, exist_ok=True)
    # gateway_cidr può contenere /prefisso; rimuoviamo la maschera per la route
    gateway = gateway_cidr.split('/')[0] if '/' in gateway_cidr else gateway_cidr
    startup = STARTUP_HOST_TMPL.format(ip_config=f"ip address add {ip_cidr} dev eth0", gateway=gateway)
    path = os.path.join(base_path, f"{hname}.startup")
    with open(path, "w") as f:
        f.write(startup)
    try:
        os.chmod(path, 0o755)
    except Exception:
        pass

def crea_www_file(base_path, name, ip_cidr, gateway_cidr, lan):
    www_dir = os.path.join(base_path, name, "var", "www", "html")
    os.makedirs(www_dir, exist_ok=True)
    index_path = os.path.join(www_dir, "index.html")
    
    # Usa il nome del server nel titolo e nel corpo
    html_content = f"<html><head><title>{name}</title></head><body><h1>Server {name}</h1></body></html>"
    
    with open(index_path, "w") as f:
        f.write(html_content)
    startup_path = os.path.join(base_path, f"{name}.startup")
    # rimuovi la maschera dal gateway per la route (mantieni la maschera sull'IP dell'interfaccia)
    gateway = gateway_cidr.split('/')[0] if '/' in gateway_cidr else gateway_cidr
    with open(startup_path, "w") as f:
        f.write(STARTUP_WWW_TMPL.format(ip=ip_cidr, gateway=gateway))
    try:
        os.chmod(startup_path, 0o755)
    except Exception:
        pass


def crea_dns_host(base_path, name, ip_cidr, gateway_cidr, lan, forwarders=None, zones=None, root_type=None, root_server_ip=None, allow_recursion=None, dnssec_validation=False):
    """
    Crea la struttura di un Host DNS (BIND9) nella directory del lab.

    Struttura creata:
    <base_path>/<name>/etc/bind/
        db.root
        named.conf
        named.conf.options
        (facoltativo) db.<name>.<zone>

    Lo startup file conterrà `systemctl start named`.
    - forwarders: lista di indirizzi IP (stringhe) da inserire in named.conf.options
    - zones: dict zone_name -> dict of records (basic A records), e.g. {"example.local": {"host": "10.0.0.10"}}
    """
    bind_dir = os.path.join(base_path, name, 'etc', 'bind')
    os.makedirs(bind_dir, exist_ok=True)
    # --- named.conf.options ---
    # Two variants: minimal or with recursion + dnssec disabled
    opts_lines = [
        'options {',
        '    directory "/var/cache/bind";',
    ]
    # If forwarders are provided, include forwarders block
    if forwarders:
        # forwarders is expected to be a list of IPs
        fw_items = ' '.join(f"{x};" for x in forwarders)
        opts_lines.append('    forwarders { ' + fw_items + ' };')
    # allow_recursion can be 'any' or '0/0' or similar string to be placed verbatim
    if allow_recursion:
        opts_lines.append('    allow-recursion { ' + str(allow_recursion) + '; };')
    # dnssec_validation -> add dnssec-validation no;
    if dnssec_validation:
        opts_lines.append('    dnssec-validation no;')
    opts_lines.append('};')
    opts_content = "\n".join(opts_lines) + "\n"
    with open(os.path.join(bind_dir, 'named.conf.options'), 'w') as f:
        f.write(opts_content)

    # Helper per formattare record DNS in colonne allineate
    def format_dns_record(name, rtype, value, cls='IN', name_width=16):
        """
        Restituisce una stringa con colonne visivamente allineate:
        - `name` left-aligned in `name_width` caratteri
        - poi `cls` (es. IN), `rtype` (A/NS/CNAME) e `value`, separati da tab

        Esempio risultante:
        "nome            \tIN\tA\t10.0.0.1"
        """
        # Normalizza i nomi/valori per assicurare il punto finale sui FQDN
        # Non modificare il record speciale '@'
        def _is_ip(s):
            try:
                ipaddress.ip_address(s)
                return True
            except Exception:
                return False

        # se il name è un FQDN (contiene un punto ma non termina con '.') aggiungi '.'
        if name != '@' and isinstance(name, str) and '.' in name and not name.endswith('.'):
            name = name + '.'

        # per tipi che puntano ad un nome (NS, CNAME, PTR) aggiungi il punto anche al valore
        if isinstance(rtype, str) and rtype.upper() in ('NS', 'CNAME', 'PTR'):
            if isinstance(value, str) and '.' in value and not value.endswith('.') and not _is_ip(value):
                value = value + '.'

        # usa un campo nome con padding fisso, poi tab per separazione chiara
        name_field = f"{name:<{name_width}}"
        return f"{name_field}\t{cls}\t{rtype}\t{value}"

    # --- db.root ---
    # Decide se il server è root/master o hint: root_type == 'master' -> full SOA, 'hint' -> hint format
    host_ip = ip_cidr.split('/')[0] if ip_cidr else '127.0.0.1'
    # Determine which IP to write inside db.root:
    # - if this server is declared master -> use its own IP
    # - if this server is hint and a root_server_ip is provided -> use that IP
    # - otherwise fallback to the host IP
    if root_type == 'master':
        ip_root = host_ip
    elif root_type == 'hint' and root_server_ip:
        ip_root = root_server_ip
    else:
        ip_root = host_ip
    import datetime
    serial = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d') + '01'
    # Standard hint-style db.root for all DNS hosts: only the NS and the A for ROOT-SERVER
    db_root_lines = [
        format_dns_record('@', 'NS', 'ROOT-SERVER.'),
        format_dns_record('ROOT-SERVER.', 'A', ip_root),
        '',
    ]
    with open(os.path.join(bind_dir, 'db.root'), 'w') as f:
        f.write('\n'.join(db_root_lines))

    # --- named.conf ---
    # include options (absolute path) and declare zone for '.' as master or hint
    named_lines = [
        'include "/etc/bind/named.conf.options";',
        '',
    ]
    zone_type = 'hint' if root_type != 'master' else 'master'
    named_lines.append('zone "." {')
    named_lines.append(f'    type {zone_type};')
    named_lines.append('    file "/etc/bind/db.root";')
    named_lines.append('};')
    named_lines.append('')

    # add authoritative zones to named.conf if any
    if zones:
        for zname in zones.keys():
            if zname == '.':
                continue
            rev = '.'.join(zname.split('.')[::-1])
            fname = f"/etc/bind/db.{rev}"
            named_lines.append(f'zone "{zname}" {{')
            named_lines.append('    type master;')
            named_lines.append(f'    file "{fname}";')
            named_lines.append('};')
            named_lines.append('')

    with open(os.path.join(bind_dir, 'named.conf'), 'w') as f:
        f.write('\n'.join(named_lines))

    # create zone files if provided (other authoritative zones)
    if zones:
        for zname, records in zones.items():
            # skip root zone if present in zones (we already created db.root)
            if zname == '.':
                continue
            # create filename like db.it.roma3 for zone roma3.it
            rev = '.'.join(zname.split('.')[::-1])
            zone_file_path = os.path.join(bind_dir, f"db.{rev}")
            serial_zone = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d') + '01'
            # If this host is authoritative for the domain, create SOA as requested
            zone_lines = [
                '$TTL 60000',
                f"@ IN SOA dns.{zname}. root.dns.{zname}. (",
                f"    {serial_zone} ; serial",
                '    28800 ;  refresh',
                '    14400 ; retry',
                '    3600000 ; expire',
                '    0 ; negative cache ttl',
                ')',
                '',
                format_dns_record('@', 'NS', f'dns.{zname}.'),
                '',
            ]
            # add records if provided (A/NS/CNAME/delegation etc.)
            if isinstance(records, dict):
                for h, ipval in records.items():
                    # support simple A record (string) or complex record (dict)
                    if isinstance(ipval, str):
                        zone_lines.append(format_dns_record(h, 'A', ipval))
                        continue
                    if isinstance(ipval, dict):
                        rtype = str(ipval.get('type', 'A')).upper()
                        if rtype == 'A':
                            ipaddr = ipval.get('ip')
                            if ipaddr:
                                zone_lines.append(format_dns_record(h, 'A', ipaddr))
                        elif rtype == 'NS':
                            ns_host = ipval.get('ns') or ipval.get('host')
                            if ns_host:
                                zone_lines.append(format_dns_record(h, 'NS', ns_host))
                                glue_ip = ipval.get('glue') or ipval.get('ip') or ipval.get('glue_ip')
                                if glue_ip:
                                    zone_lines.append(format_dns_record(ns_host, 'A', glue_ip))
                        elif rtype == 'DELEGATION':
                            child_zone = ipval.get('zone') or h
                            ns_host = ipval.get('ns')
                            ns_ip = ipval.get('ns_ip') or ipval.get('glue_ip') or ipval.get('ip')
                            if child_zone and ns_host:
                                zone_lines.append(format_dns_record(child_zone, 'NS', ns_host))
                                if ns_ip:
                                    zone_lines.append(format_dns_record(ns_host, 'A', ns_ip))
                        elif rtype == 'CNAME':
                            target = ipval.get('target')
                            if target:
                                zone_lines.append(format_dns_record(h, 'CNAME', target))
                        else:
                            ipaddr = ipval.get('ip')
                            if ipaddr:
                                zone_lines.append(format_dns_record(h, 'A', ipaddr))
            with open(zone_file_path, 'w') as f:
                f.write('\n'.join(zone_lines) + '\n')

    # startup: configure IP and start named
    gateway = gateway_cidr.split('/')[0] if '/' in gateway_cidr else gateway_cidr
    startup = f"ip address add {ip_cidr} dev eth0\nip route add default via {gateway} dev eth0\nsystemctl start named\n"
    startup_path = os.path.join(base_path, f"{name}.startup")
    with open(startup_path, 'w') as f:
        f.write(startup)
    try:
        os.chmod(startup_path, 0o755)
    except Exception:
        pass

# -------------------------
# BGP relations: manual menu
# -------------------------
def aggiungi_relazioni_bgp_menu(base_path, routers):
    print("\n=== Aggiungi relazioni BGP (manuale) ===")
    if not routers:
        print("Nessun router disponibile.")
        return
    while True:
        print("\nRouter disponibili:")
        for rn, r in routers.items():
            print(f" - {rn} (ASN: {r.get('asn','-')})")
        src = input("Router sorgente (vuoto per uscire): ").strip()
        if not src:
            break
        if src not in routers:
            print("Router sorgente non valido.")
            continue
        dst = input("Router destinazione: ").strip()
        if dst not in routers:
            print("Router destinazione non valido.")
            continue
        if "bgp" not in routers[src]["protocols"] or "bgp" not in routers[dst]["protocols"]:
            print("Entrambi i router devono avere BGP abilitato.")
            continue
        rel = input("Tipo relazione (peer/provider/customer): ").strip().lower()
        if rel not in ("peer", "provider", "customer"):
            print("Tipo relazione non valido.")
            continue
        neigh_ip = valida_ip_senza_cidr("IP neighbor verso dst (es. 10.0.0.2): ")
        aggiungi_policy = input("Aggiungere prefix-list / route-map (s/N)? ").strip().lower().startswith("s")
        lp_map = {"peer": 100, "provider": 80, "customer": 120}
        local_pref = lp_map[rel]
        fpath = os.path.join(base_path, src, "etc", "frr", "frr.conf")
        # crea le righe neighbor da inserire dentro il blocco router bgp
        neighbor_lines = [f"neighbor {neigh_ip} remote-as {routers[dst]['asn']}",
                          f"neighbor {neigh_ip} description {rel}_{dst}"]
        insert_lines_into_protocol_block(fpath, proto='bgp', asn=None, lines=neighbor_lines)

        # se richiesto, aggiungi le policy (prefix-list / route-map) al fondo del file
        if aggiungi_policy:
            policy = []
            policy.append(f"neighbor {neigh_ip} prefix-list {rel}_{dst}_in in")
            policy.append(f"neighbor {neigh_ip} prefix-list {rel}_{dst}_out out")
            policy.append("")
            policy.append(f"ip prefix-list {rel}_{dst}_in permit any")
            policy.append(f"ip prefix-list {rel}_{dst}_out permit any")
            policy.append("")
            policy.append(f"route-map pref_{dst}_in permit 10")
            policy.append(f"    set local-preference {local_pref}")
            policy.append("")
            policy.append(f"neighbor {neigh_ip} route-map pref_{dst}_in in")
            with open(fpath, "a") as f:
                for line in policy:
                    f.write(line + "\n")
        print(f"Relazione BGP ({rel}) aggiunta su {src} verso {dst} (neighbor {neigh_ip}).")

# -------------------------
# Auto-generate BGP neighbors for routers sharing same LAN
# -------------------------
def auto_generate_bgp_neighbors(base_path, routers):
    """
    Per ogni coppia di router che condividono la stessa LAN (campo 'lan' su interfacce),
    aggiunge neighbor reciproci usando l'IP dell'interfaccia del peer collegata a quella LAN.
    """
    lan_map = {}
    for rname, rdata in routers.items():
        for iface in rdata["interfaces"]:
            lan = iface.get("lan")
            if not lan:
                continue
            lan_map.setdefault(lan, []).append((rname, iface["ip"], rdata.get("asn")))

    for lan, members in lan_map.items():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i+1, len(members)):
                r1, ip1, asn1 = members[i]
                r2, ip2, asn2 = members[j]
                if "bgp" not in routers[r1]["protocols"] or "bgp" not in routers[r2]["protocols"]:
                    continue
                add_neighbor_if_missing(base_path, r1, ip2, routers[r2]['asn'], desc=f"Router {r2}")
                add_neighbor_if_missing(base_path, r2, ip1, routers[r1]['asn'], desc=f"Router {r1}")

def add_neighbor_if_missing(base_path, src_router, neigh_ip, neigh_asn, desc=None):
    fpath = os.path.join(base_path, src_router, "etc", "frr", "frr.conf")
    if not os.path.exists(fpath):
        return
    with open(fpath, "r") as f:
        content = f.read()
    # strip CIDR if present
    neigh_ip_stripped = neigh_ip.split('/')[0] if '/' in neigh_ip else neigh_ip
    if f"neighbor {neigh_ip_stripped} remote-as" in content:
        return
    # Costruiamo le righe neighbor (senza newline finali)
    lines = [f"neighbor {neigh_ip_stripped} remote-as {neigh_asn}"]
    if desc:
        lines.append(f"neighbor {neigh_ip_stripped} description {desc}")
    # Inseriamo le righe dentro il blocco 'router bgp' se presente,
    # altrimenti appendiamo a fine file
    insert_lines_into_protocol_block(fpath, proto='bgp', asn=None, lines=lines)


def add_ibgp_loopback_neighbors(base_path, routers):
    """
    Cerca tutte le coppie di router che hanno BGP abilitato e lo stesso ASN
    e che dispongono di almeno una loopback ciascuno. Per ogni coppia crea
    nei rispettivi `frr.conf` le righe iBGP basate sulle loopback:

      neighbor <peer-loopback> remote-as <asn>
      neighbor <peer-loopback> update-source <local-loopback>

    Questa funzione evita di duplicare righe esistenti.
    """
    # helper per garantire la coppia su due router
    def _ensure_pair(r_local, r_peer):
        r_local_data = routers.get(r_local, {})
        r_peer_data = routers.get(r_peer, {})
        if not r_local_data or not r_peer_data:
            return
        asn_local = str(r_local_data.get('asn', '')).strip()
        asn_peer = str(r_peer_data.get('asn', '')).strip()
        if not asn_local or asn_local != asn_peer:
            return
        # prendiamo la prima loopback disponibile per ciascun router
        lbs_local = r_local_data.get('loopbacks') or []
        lbs_peer = r_peer_data.get('loopbacks') or []
        if not lbs_local or not lbs_peer:
            return
        lb_local = _strip_cidr(lbs_local[0])
        lb_peer = _strip_cidr(lbs_peer[0])
        if not lb_local or not lb_peer:
            return

        # files frr.conf
        f_local = os.path.join(base_path, r_local, 'etc', 'frr', 'frr.conf')
        f_peer = os.path.join(base_path, r_peer, 'etc', 'frr', 'frr.conf')

        # assicurati che i file esistano
        if not os.path.exists(f_local) or not os.path.exists(f_peer):
            return

        # leggi contenuti per verifica duplicati
        try:
            with open(f_local, 'r') as fh:
                cont_local = fh.read()
        except Exception:
            cont_local = ''
        try:
            with open(f_peer, 'r') as fh:
                cont_peer = fh.read()
        except Exception:
            cont_peer = ''

        # prepara righe da inserire in r_local per parlare con r_peer
        to_add_local = []
        if f"neighbor {lb_peer} remote-as" not in cont_local:
            to_add_local.append(f"neighbor {lb_peer} remote-as {asn_local}")
        if f"neighbor {lb_peer} update-source" not in cont_local:
            to_add_local.append(f"neighbor {lb_peer} update-source {lb_local}")

        # prepara righe da inserire in r_peer per parlare con r_local
        to_add_peer = []
        if f"neighbor {lb_local} remote-as" not in cont_peer:
            to_add_peer.append(f"neighbor {lb_local} remote-as {asn_local}")
        if f"neighbor {lb_local} update-source" not in cont_peer:
            to_add_peer.append(f"neighbor {lb_local} update-source {lb_peer}")

        # inserisci dentro il blocco router bgp <asn>
        if to_add_local:
            insert_lines_into_protocol_block(f_local, proto='bgp', asn=asn_local, lines=to_add_local)
            print(f"✅ Aggiunti iBGP neighbor su {r_local}: {', '.join(to_add_local)}")
        if to_add_peer:
            insert_lines_into_protocol_block(f_peer, proto='bgp', asn=asn_local, lines=to_add_peer)
            print(f"✅ Aggiunti iBGP neighbor su {r_peer}: {', '.join(to_add_peer)}")

    # cicla per tutte le coppie (n^2) — i dati sono piccoli, va bene
    names = [n for n in routers.keys()]
    for i in range(len(names)):
        for j in range(i+1, len(names)):
            n1 = names[i]
            n2 = names[j]
            # entrambi devono avere BGP abilitato
            if 'bgp' not in routers.get(n1, {}).get('protocols', []) or 'bgp' not in routers.get(n2, {}).get('protocols', []):
                continue
            # devono avere ASN uguale e non vuoto
            asn1 = str(routers.get(n1, {}).get('asn', '')).strip()
            asn2 = str(routers.get(n2, {}).get('asn', '')).strip()
            if not asn1 or asn1 != asn2:
                continue
            # devono avere almeno una loopback ciascuno
            lbs1 = routers.get(n1, {}).get('loopbacks') or []
            lbs2 = routers.get(n2, {}).get('loopbacks') or []
            if not lbs1 or not lbs2:
                continue
            # ensure pair both ways
            _ensure_pair(n1, n2)

# -------------------------
# Modifica frr.conf con editor
# -------------------------
def modifica_router_menu(base_path, routers):
    print("\n=== Modifica frr.conf router ===")
    keys = list(routers.keys())
    for i, k in enumerate(keys, 1):
        print(f"{i}. {k}")
    idx = input_int("Seleziona router (numero, 0 per annullare): ", 0)
    if idx == 0 or idx > len(keys):
        return
    sel = keys[idx - 1]
    fpath = os.path.join(base_path, sel, "etc", "frr", "frr.conf")
    if not os.path.exists(fpath):
        print("frr.conf non trovato per", sel)
        return
    editor = os.environ.get("EDITOR", "nano")
    try:
        subprocess.call([editor, fpath])
    except Exception as e:
        print("Errore aprendo editor:", e)


# -------------------------
# Menu post-creazione: implementazione richieste (in italiano)
# -------------------------
def append_frr_stanza(base_path, router, stanza):
    fpath = os.path.join(base_path, router, "etc", "frr", "frr.conf")
    if not os.path.exists(fpath):
        print(f"⚠️ frr.conf non trovato per {router}: {fpath}")
        return False
    with open(fpath, "a") as f:
        f.write("\n" + stanza + "\n")
    return True

def insert_lines_into_protocol_block(fpath, proto='bgp', asn=None, lines=None):
    """Inserisce `lines` (lista di stringhe, senza newline) dentro la sezione 'router <proto>' di fpath.
    - proto: 'bgp', 'ospf', 'rip', etc.
    - asn: opzionale, quando fornito cerca la stanza che contiene anche l'ASN (utile per 'router bgp <asn>').
    Se non trova una sezione valida, appende le righe alla fine del file.
    Le righe saranno indentate con 4 spazi quando inserite dentro il blocco.
    """
    if lines is None:
        return False
    try:
        with open(fpath, 'r') as f:
            content = f.readlines()
    except Exception as e:
        print(f"❌ Errore leggendo {fpath}: {e}")
        return False

    # trova la prima occorrenza di 'router <proto>' (case insensitive per 'router')
    idx = None
    target_start = f'router {proto}'.lower()
    for i, L in enumerate(content):
        s = L.strip().lower()
        if s.startswith(target_start):
            # se viene passato un asn, verifichiamo che la linea lo contenga (case insensitive check on line)
            if asn and str(asn) not in L:
                continue
            idx = i
            break

    if idx is None:
        # non trovato: append a fine file
        try:
            with open(fpath, 'a') as f:
                # Assicurati che ci sia una newline prima se il file non è vuoto e non finisce con newline
                if os.path.getsize(fpath) > 0:
                    # Leggi l'ultimo carattere per vedere se serve newline (costoso aprire in r+ o seek, 
                    # ma per sicurezza scriviamo sempre una newline iniziale se appendiamo)
                    f.write('\n')
                for l in lines:
                    f.write(l + '\n')
            return True
        except Exception as e:
            print(f"❌ Errore scrivendo (append) su {fpath}: {e}")
            return False

    # trova il punto di inserimento: il primo indice dopo il blocco indentato
    j = idx + 1
    while j < len(content):
        line = content[j]
        # considera appartenenti al blocco le linee vuote o indentate
        if line.startswith(' ') or line.startswith('\t') or line.strip() == '':
            j += 1
            continue
        # se incontriamo un commento di sezione (es. '!') trattiamolo come parte del blocco
        if line.lstrip().startswith('!'):
            j += 1
            continue
        break

    # prepara le righe indentate
    ind_lines = [('    ' + l) for l in lines]
    
    # Assicurati che la riga precedente abbia un newline
    if j > 0 and not content[j-1].endswith('\n'):
        content[j-1] += '\n'

    # inserisci prima dell'indice j
    new_content = content[:j] + [l + '\n' for l in ind_lines] + content[j:]
    
    try:
        with open(fpath, 'w') as f:
            f.writelines(new_content)
        return True
    except Exception as e:
        print(f"❌ Errore scrivendo su {fpath}: {e}")
        return False

def select_router(routers, prompt="Seleziona router:"):
    keys = list(routers.keys())
    if not keys:
        print("Nessun router disponibile.")
        return None
    # stampa menu ordinato
    items = [f"{k} (ASN: {routers[k].get('asn','-')})" for k in keys]
    print_menu(prompt, items)
    while True:
        sel = input("Numero router (o nome, vuoto per annullare): ").strip()
        if not sel:
            return None
        if sel.isdigit():
            idx = int(sel)
            if 1 <= idx <= len(keys):
                return keys[idx-1]
            print("Indice non valido.")
        else:
            if sel in routers:
                return sel
            print("Nome router non valido.")

def select_interface(router_data):
    if not router_data or not router_data.get('interfaces'):
        print('Nessuna interfaccia disponibile per questo router.')
        return None
    ifaces = router_data['interfaces']
    items = [f"{it.get('name')} - {it.get('ip')}" for it in ifaces]
    print_menu('Interfacce disponibili:', items)
    while True:
        sel = input('Seleziona interfaccia (numero o nome, vuoto per annullare): ').strip()
        if not sel:
            return None
        if sel.isdigit():
            idx = int(sel)
            if 1 <= idx <= len(ifaces):
                return ifaces[idx-1]['name']
            print('Indice non valido.')
        else:
            for it in ifaces:
                if it.get('name') == sel:
                    return sel
            print('Nome interfaccia non valido.')

def get_first_iface_ip(ifaces):
    # ritorna l'IP (senza /prefisso) della prima interfaccia fornita
    if not ifaces:
        return None
    ip_cidr = ifaces[0].get("ip")
    if not ip_cidr:
        return None
    return ip_cidr.split("/")[0]


def _strip_cidr(ip_cidr):
    if not ip_cidr:
        return None
    return str(ip_cidr).split('/')[0]


def collect_lab_ips(lab_path, routers=None):
    """Raccoglie gli endpoint del lab: tutte le IP associate a interfacce.

    Restituisce una lista di dict con chiavi: 'ip', 'name', 'iface'.
    - Se `routers` è fornito (dict), prende gli IP dalle interfacce dei router
      e imposta 'name' al nome del router e 'iface' al nome dell'interfaccia.
    - Scansiona i file `*.startup` nella directory del lab per trovare tutte le
      occorrenze di `ip address add <ip>` e aggiunge record con 'name' = nome
      del file (senza .startup) e 'iface' se presente nella riga.
    Mantiene l'ordine di scoperta e rimuove duplicati identici (stesso IP, name, iface).
    """
    endpoints = []
    seen = set()
    # dai router
    if routers:
        for rname, rdata in routers.items():
            for iface in rdata.get('interfaces', []):
                ip = _strip_cidr(iface.get('ip'))
                if ip:
                    key = (ip, rname, iface.get('name'))
                    if key not in seen:
                        seen.add(key)
                        endpoints.append({'ip': ip, 'name': rname, 'iface': iface.get('name')})

    # dai file .startup (tutte le occorrenze)
    try:
        for fname in os.listdir(lab_path):
            if not fname.endswith('.startup'):
                continue
            node_name = fname[:-8]
            fpath = os.path.join(lab_path, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    for L in f:
                        if 'ip address add' in L:
                            parts = L.strip().split()
                            # ip address add <ip> [dev <iface>]
                            if len(parts) >= 4:
                                ip = _strip_cidr(parts[3])
                                iface = None
                                if 'dev' in parts:
                                    try:
                                        dev_idx = parts.index('dev')
                                        if dev_idx + 1 < len(parts):
                                            iface = parts[dev_idx + 1]
                                    except Exception:
                                        iface = None
                                key = (ip, node_name, iface)
                                if ip and key not in seen:
                                    seen.add(key)
                                    endpoints.append({'ip': ip, 'name': node_name, 'iface': iface})
            except Exception:
                continue
    except Exception:
        pass

    return endpoints


def generate_ping_oneliner(endpoints):
    """Genera una one-liner bash che esegue `ping -c 1` su ogni endpoint.

    `endpoints` è una lista di dict con chiavi: 'ip', 'name', 'iface'. La one-liner
    esegue un singolo ping per IP e stampa per ogni record: "<ip> (<name> <iface>): <loss> packet loss"
    o "no reply" se l'output non è parsabile.
    """
    if not endpoints:
        return ''
    toks = []
    for e in endpoints:
        ip = e.get('ip')
        name = e.get('name') or ''
        iface = e.get('iface') or ''
        # escape any double quotes by removing them (names shouldn't contain quotes normally)
        token = f"{ip}|{name}|{iface}"
        toks.append(f'"{token}"')
    recs = ' '.join(toks)
    # single probe, print packet loss percentage extracted from ping summary
    cmd = (
        "for rec in " + recs + "; do IFS='|' read -r ip name iface <<< \"$rec\"; "
        "out=$(ping -c 1 \"$ip\" 2>&1); "
        "loss=$(echo \"$out\" | awk -F',' '/packet loss/ {print $3}' | awk '{print $1}'); "
        "if [ -z \"$loss\" ]; then echo -e \"[!] $ip ($name $iface): no reply\"; else "
        "loss_num=$(echo \"$loss\" | tr -d '%'); "
        "if [ \"$loss_num\" = \"0\" ]; then echo -e \"[OK] $ip ($name $iface): $loss packet loss\"; else echo -e \"[!] $ip ($name $iface): $loss packet loss\"; fi; fi; done"
    )
    return cmd

def find_routers_by_asn(routers, asn):
    return [name for name, r in routers.items() if str(r.get("asn","")) == str(asn)]

def assegna_costo_interfaccia(base_path, routers):
    print('\n--- Assegna costo OSPF su una interfaccia di un router ---')
    target = select_router(routers, prompt='Seleziona il router su cui impostare il costo OSPF:')
    if not target:
        print('Operazione annullata.')
        return
    # Validation: il router deve avere OSPF abilitato
    prot = routers.get(target, {}).get('protocols', [])
    if 'ospf' not in prot:
        print(f"⚠️ Il router {target} non ha OSPF abilitato (protocols: {prot}). Abilita OSPF prima di impostare il costo.")
        return
    iface = select_interface(routers[target])
    if not iface:
        print('Interfaccia non selezionata. Annullato.')
        return
    costo = input_int(f'Inserisci il costo OSPF desiderato per {iface} (intero ≥ 1): ', 1)
    stanza = f"interface {iface}\n    ospf cost {costo}\n"
    if append_frr_stanza(base_path, target, stanza):
        print(f"✅ Costo impostato su {target} {iface} = {costo} (append su frr.conf).")

# implementa_relazioni_as: rimosso — usare la creazione manuale di neighbor o riabilitare se necessario

# asboh_lookup_option: rimosso — l'operazione richiede consultazione esterna

# filter_as10_from_as60: rimosso — puoi riattivare la versione interattiva se ti serve

def preferenza_as50r1(base_path, routers):
    print('\n--- Imposta preferenza su un router per privilegiar annunci da un neighbor ---')
    src = select_router(routers, prompt='Seleziona il router sorgente che deve preferire gli annunci:')
    if not src:
        print('Operazione annullata.')
        return
    print('Seleziona il router preferito (neighbor) dalla lista, o premi invio per inserire IP/ASN manualmente:')
    neigh = select_router(routers, prompt='Seleziona il router preferito (neighbor):')
    if neigh:
        neigh_asn = routers[neigh].get('asn')
        neigh_ip = get_first_iface_ip(routers[neigh]['interfaces'])
    else:
        neigh_asn = input_non_vuoto('ASN del router preferito (es. 70): ')
        neigh_ip = input_non_vuoto('IP del neighbor (es. 10.0.0.2): ')
    if not neigh_ip or not neigh_asn:
        print('Informazioni incomplete; annullato.')
        return
    # strip CIDR if user or source provided it accidentally
    neigh_ip = neigh_ip.split('/')[0] if '/' in neigh_ip else neigh_ip
    # route-map (globale)
    policy_lines = [f"route-map PREF_FROM_{neigh_ip.replace('.', '_')} permit 10",
                    f"    match ip address prefix-list any",
                    f"    set local-preference 200",
                    ""]
    # neighbor lines (da inserire sotto router bgp)
    neighbor_lines = [f"neighbor {neigh_ip} remote-as {neigh_asn}",
                      f"neighbor {neigh_ip} route-map PREF_FROM_{neigh_ip.replace('.', '_')} in"]
    # append policy globalmente
    with open(os.path.join(base_path, src, 'etc', 'frr', 'frr.conf'), 'a') as f:
        for L in policy_lines:
            f.write(L + "\n")
    # inserisci neighbor nel blocco router bgp
    insert_lines_into_protocol_block(os.path.join(base_path, src, 'etc', 'frr', 'frr.conf'), proto='bgp', asn=None, lines=neighbor_lines)
    print(f"✅ Aggiunta preferenza su {src} per annunci provenienti da {neigh_ip} (ASN {neigh_asn}).")
    print(f"✅ Aggiunta preferenza su {src} per annunci provenienti da {neigh_ip} (ASN {neigh_asn}).")

def ensure_neighbor_exists(fpath, neigh_ip):
    """Se non esiste una riga 'neighbor <ip> remote-as' nel file fpath, chiede ASN e la crea."""
    try:
        with open(fpath, 'r') as f:
            content = f.read()
    except Exception:
        return False
    neigh_ip_stripped = neigh_ip.split('/')[0] if '/' in neigh_ip else neigh_ip
    if f"neighbor {neigh_ip_stripped} remote-as" in content:
        return True
    # chiedi ASN all'utente
    asn = input_non_vuoto(f"ASN del neighbor {neigh_ip_stripped} (necessario per creare il neighbor): ")
    # inserisci il neighbor dentro il blocco router bgp
    lines = [f"neighbor {neigh_ip_stripped} remote-as {asn}"]
    insert_lines_into_protocol_block(fpath, proto='bgp', asn=None, lines=lines)
    return True

def policies_menu(base_path, routers):
    """Sotto-menu per applicare policies BGP semplici ai router del lab."""
    while True:
        items = [
            'Aggiungi prefix-list (deny <rete> e permit any) e collega al neighbor (in/out)',
            'Aggiungi route-map prefIn (set local-preference) e collega al neighbor (in)',
            'Aggiungi route-map localMedOut (set metric) e collega al neighbor (out)',
            'Aggiungi access-list (deny rete, permit any) e collega al neighbor (in)',
            'Aggiungi configurazione Customer-Provider'
        ]
        print_menu('=== Policies BGP (scegli) ===', items, extra_options=[('0', 'Torna indietro')])
        c = input('Seleziona (numero): ').strip()
        if c == '0':
            break
        if c not in ('1','2','3','4','5'):
            print('Scelta non valida.')
            continue

        if c == '5':
            aggiungi_customer_provider_wizard(base_path, routers)
            continue

        src = select_router(routers, prompt='Seleziona il router su cui applicare la policy:')
        if not src:
            print('Annullato.')
            continue
        if 'bgp' not in routers.get(src, {}).get('protocols', []):
            print(f"⚠️ Il router {src} non ha BGP abilitato.")
            continue
        fpath = os.path.join(base_path, src, 'etc', 'frr', 'frr.conf')
        if not os.path.exists(fpath):
            print(f"frr.conf non trovato per {src}: {fpath}")
            continue

        # common: chiedi IP neighbor
        neigh_ip = valida_ip_senza_cidr('IP neighbor (es. 10.0.0.2): ')
        neigh_ip = neigh_ip.split('/')[0] if '/' in neigh_ip else neigh_ip

        # assicurati che esista il neighbor (remote-as). Se non esiste, chiediamo ASN e lo creiamo.
        ensure_neighbor_exists(fpath, neigh_ip)

        if c == '1':
            # prefix-list deny <rete> + permit any; collega con neighbor <ip> prefix-list NAME in/out
            direz = ''
            while direz not in ('in','out'):
                direz = input('Direzione (in/out): ').strip().lower()
            rete = input_non_vuoto('Rete da DENY (es. 100.200.0.0/16): ').strip()
            try:
                net = ipaddress.ip_network(rete, strict=False)
                rete_str = str(net)
            except Exception:
                print('Rete non valida.')
                continue
            pl_name = f"PL_{src}_{neigh_ip.replace('.','_')}_{direz}"
            # append prefix-list definizione e poi inserisci la linea neighbor ... prefix-list ...
            stanza = []
            stanza.append(f"ip prefix-list {pl_name} deny {rete_str}")
            stanza.append(f"ip prefix-list {pl_name} permit any")
            # append global definitions
            with open(fpath, 'a') as f:
                f.write('\n')
                for L in stanza:
                    f.write(L + '\n')
                f.write('\n')
            # insert neighbor reference into router bgp block
            insert_lines_into_protocol_block(fpath, proto='bgp', asn=None, lines=[f"neighbor {neigh_ip} prefix-list {pl_name} {direz}"])
            print(f"✅ Aggiunta prefix-list {pl_name} su {src} e collegata al neighbor {neigh_ip} ({direz}).")

        elif c == '2':
            # route-map prefIn permit 10 set local-preference 110 + neighbor ... route-map prefIn in
            lp = input_non_vuoto('Valore local-preference da impostare (es. 110): ').strip()
            try:
                lp_val = int(lp)
            except Exception:
                print('Valore non valido.')
                continue
            rm_name = f"PREF_IN_{neigh_ip.replace('.','_')}"
            # append route-map definition
            stanza = [f"route-map {rm_name} permit 10", f"    set local-preference {lp_val}", ""]
            with open(fpath, 'a') as f:
                f.write('\n')
                for L in stanza:
                    f.write(L + '\n')
            # insert neighbor route-map line
            insert_lines_into_protocol_block(fpath, proto='bgp', asn=None, lines=[f"neighbor {neigh_ip} route-map {rm_name} in"])
            print(f"✅ Aggiunta route-map {rm_name} (local-pref {lp_val}) su {src} per neighbor {neigh_ip} (in).")

        elif c == '3':
            # route-map localMedOut permit 10 set metric 20 + neighbor ... route-map localMedOut out
            metric = input_non_vuoto('Valore metric (MED) da impostare (es. 20): ').strip()
            try:
                m_val = int(metric)
            except Exception:
                print('Valore non valido.')
                continue
            rm_name = f"LOCALMED_OUT_{neigh_ip.replace('.','_')}"
            stanza = [f"route-map {rm_name} permit 10", f"    set metric {m_val}", ""]
            with open(fpath, 'a') as f:
                f.write('\n')
                for L in stanza:
                    f.write(L + '\n')
            insert_lines_into_protocol_block(fpath, proto='bgp', asn=None, lines=[f"neighbor {neigh_ip} route-map {rm_name} out"])
            print(f"✅ Aggiunta route-map {rm_name} (set metric {m_val}) su {src} per neighbor {neigh_ip} (out).")

        elif c == '4':
            # access-list 10 deny <rete> + permit any; route-map FILTER_IN permit 10 match ip address 10; neighbor ... route-map FILTER_IN in
            rete = input_non_vuoto('Rete da bloccare in ingresso (es. 10.0.1.0/24): ').strip()
            try:
                net = ipaddress.ip_network(rete, strict=False)
                rete_str = str(net)
            except Exception:
                print('Rete non valida.')
                continue
            
            # Trova un ID access-list libero (semplice euristica: inizia da 10 e incrementa se trova collisioni nel file, 
            # ma per semplicità qui usiamo 10 e se c'è già speriamo non confligga o l'utente gestisca. 
            # Meglio: leggiamo il file per vedere se "access-list 10" esiste già? 
            # L'utente ha chiesto specificamente "access-list 10", ma se lo facciamo più volte potrebbe servire diverso.
            # Per ora usiamo 10 come da esempio, o chiediamo? L'utente ha detto "come esempio usa questo schema", 
            # ma se lo faccio due volte sullo stesso router con reti diverse, non posso usare sempre 10.
            # Facciamo che cerchiamo un ID libero partendo da 10.
            acl_id = 10
            existing_content = ""
            if os.path.exists(fpath):
                with open(fpath, 'r') as f:
                    existing_content = f.read()
            
            while f"access-list {acl_id}" in existing_content:
                acl_id += 10
            
            rm_name = f"FILTER_IN_{neigh_ip.replace('.','_')}"
            
            stanza = []
            stanza.append(f"access-list {acl_id} deny {rete_str}")
            stanza.append(f"access-list {acl_id} permit any")
            stanza.append("")
            stanza.append(f"route-map {rm_name} permit 10")
            stanza.append(f" match ip address {acl_id}")
            stanza.append("")
            
            with open(fpath, 'a') as f:
                f.write('\n')
                for L in stanza:
                    f.write(L + '\n')
            
            insert_lines_into_protocol_block(fpath, proto='bgp', asn=None, lines=[f"neighbor {neigh_ip} route-map {rm_name} in"])
            print(f"✅ Aggiunta access-list {acl_id} (deny {rete_str}) e route-map {rm_name} su {src} per neighbor {neigh_ip} (in).")


def assegna_resolv_conf(base_path):
    """
    Interattivo per assegnare un file `resolv.conf` a un singolo dispositivo
    presente nella cartella `base_path`.

    Opzioni:
    1) imposta un unico nameserver (IP)
    2) inserisci il contenuto completo di resolv.conf (termina con una linea '.' da sola)
    3) copia il `resolv.conf` da un altro dispositivo che lo possiede
    """
    # elenca dispositivi: includi sia sottocartelle che eventuali file '<name>.startup'
    try:
        dir_items = [d for d in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, d)) and not d.startswith('.')]
        # aggiungi file .startup come possibili host/client (rimuovi estensione)
        startup_files = []
        for fn in os.listdir(base_path):
            if fn.endswith('.startup') and os.path.isfile(os.path.join(base_path, fn)):
                startup_files.append(os.path.splitext(fn)[0])
        # unisci e deduplica
        items = sorted(list(dict.fromkeys(dir_items + startup_files)))
    except Exception:
        print('Errore leggendo la cartella del laboratorio.')
        return

    if not items:
        print('Nessun dispositivo trovato nella cartella del lab.')
        return

    print_menu('Dispositivi trovati nel lab:', items)

    sel = input('Seleziona il dispositivo (numero o nome) o premi INVIO per annullare: ').strip()
    if not sel:
        print('Operazione annullata.')
        return

    target = None
    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(items):
            target = items[idx]
    else:
        if sel in items:
            target = sel

    if not target:
        print('Selezione non valida.')
        return

    # assicurati che la directory del dispositivo esista (per host creati come .startup la creiamo ora)
    device_dir = os.path.join(base_path, target)
    os.makedirs(device_dir, exist_ok=True)
    etc_dir = os.path.join(device_dir, 'etc')
    os.makedirs(etc_dir, exist_ok=True)
    dest_path = os.path.join(etc_dir, 'resolv.conf')

    # Per semplicità operativa: saltiamo il sotto-menu delle modalità e
    # scegliamo direttamente l'opzione che interessa di più: impostare
    # un unico nameserver. Tuttavia manteniamo la ricerca di eventuali
    # `resolv.conf` esistenti per suggerire candidati IP.
    available_sources = []
    for d in items:
        src = os.path.join(base_path, d, 'etc', 'resolv.conf')
        if os.path.isfile(src):
            available_sources.append((d, src))
    # Imposta automaticamente la modalità 1 (unico nameserver)
    choice = '1'

    try:
        if choice == '1':
            # Cerca un IP (IPv4) dentro i file del dispositivo o nello startup file
            import re
            candidate_ns = []
            ip_re = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
            for d in items:
                found_ip = None
                # se esiste una directory per il dispositivo, cerca dentro tutti i file
                dev_dir = os.path.join(base_path, d)
                if os.path.isdir(dev_dir):
                    try:
                        for root, _, files in os.walk(dev_dir):
                            for fn in files:
                                fpath = os.path.join(root, fn)
                                try:
                                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                                        for line in fh:
                                            m = ip_re.search(line)
                                            if m:
                                                cand = m.group(0)
                                                try:
                                                    ipaddress.ip_address(cand)
                                                    found_ip = cand
                                                    break
                                                except Exception:
                                                    continue
                                        if found_ip:
                                            break
                                except Exception:
                                    continue
                            if found_ip:
                                break
                    except Exception:
                        pass
                else:
                    # potrebbe esistere solo il file '<name>.startup' nel base_path
                    startup_path = os.path.join(base_path, f"{d}.startup")
                    if os.path.isfile(startup_path):
                        try:
                            with open(startup_path, 'r', encoding='utf-8', errors='ignore') as fh:
                                for line in fh:
                                    m = ip_re.search(line)
                                    if m:
                                        cand = m.group(0)
                                        try:
                                            ipaddress.ip_address(cand)
                                            found_ip = cand
                                            break
                                        except Exception:
                                            continue
                        except Exception:
                            pass
                if found_ip:
                    candidate_ns.append((d, found_ip))

            if candidate_ns:
                items = [f"{d} - {ip}" for (d, ip) in candidate_ns]
                print_menu('Dispositivi candidati come nameserver:', items, extra_options=[('M', 'Inserisci manualmente un IP')])
                selns = input('Seleziona il nameserver (numero) o M per manuale, INVIO per annullare: ').strip()
                if not selns:
                    print('Operazione annullata.')
                    return
                if selns.lower() == 'm':
                    manual = input_non_vuoto('Inserisci l\'IP del nameserver (es. 10.0.0.1): ').strip()
                    try:
                        ipaddress.ip_address(manual)
                        ns_ip = manual
                    except Exception:
                        print('IP non valido. Nessuna modifica effettuata.')
                        return
                else:
                    if not selns.isdigit():
                        print('Selezione non valida.')
                        return
                    idx = int(selns) - 1
                    if not (0 <= idx < len(candidate_ns)):
                        print('Selezione non valida.')
                        return
                    ns_ip = candidate_ns[idx][1]
            else:
                # nessun candidato trovato, fallback a inserimento manuale
                print('Nessun IP candidato trovato nelle cartelle dei dispositivi.')
                manual = input_non_vuoto('Inserisci l\'IP del nameserver (es. 10.0.0.1): ').strip()
                try:
                    ipaddress.ip_address(manual)
                    ns_ip = manual
                except Exception:
                    print('IP non valido. Nessuna modifica effettuata.')
                    return

            # Scrive il file con la forma esatta richiesta: "nameserver <IP>\n"
            try:
                with open(dest_path, 'w') as f:
                    f.write(f"nameserver {ns_ip}\n")
                print(f"✅ Wrote {dest_path} with nameserver {ns_ip}")
            except Exception as e:
                print('Errore scrivendo il file:', e)

        elif choice == '2':
            print("Inserisci il contenuto di resolv.conf. Termina con una linea contenente solo '.'")
            lines = []
            while True:
                try:
                    ln = input()
                except EOFError:
                    break
                if ln.strip() == '.':
                    break
                lines.append(ln)
            if not lines:
                print('Nessun contenuto inserito. Nessuna modifica effettuata.')
            else:
                with open(dest_path, 'w') as f:
                    f.write('\n'.join(lines).rstrip() + '\n')
                print(f"✅ Contenuto scritto in {dest_path}")

        elif choice == '3' and available_sources:
            items = [d for (d, p) in available_sources]
            print_menu('Dispositivi con resolv.conf disponibile:', items)
            src_sel = input('Seleziona il dispositivo sorgente (numero) o INVIO per annullare: ').strip()
            if not src_sel:
                print('Operazione annullata.')
                return
            if not src_sel.isdigit():
                print('Selezione non valida.')
                return
            sidx = int(src_sel) - 1
            if not (0 <= sidx < len(available_sources)):
                print('Selezione non valida.')
                return
            src_path = available_sources[sidx][1]
            try:
                shutil.copyfile(src_path, dest_path)
                print(f"✅ Copiato {src_path} -> {dest_path}")
            except Exception as e:
                print('Errore copiando il file:', e)
        else:
            print('Scelta non valida o nessuna sorgente disponibile.')
    except Exception as e:
        print('Errore durante l\'impostazione di resolv.conf:', e)


def aggiungi_loopback_menu(base_path, routers):
    """
    Aggiunge una loopback ad un dispositivo.
    - Per i router: aggiorna lo startup (`<name>.startup`) con `ip address add <ip/32> dev lo`
      e inserisce l'annuncio nei blocchi BGP/OSPF/RIP se il protocollo è abilitato.
    - Per altri dispositivi con file `.startup`: aggiunge solo la riga allo startup.
    """
    print('\n--- Aggiungi loopback ad un dispositivo ---')
    # Mostra direttamente la lista dei dispositivi disponibili: router + file .startup
    router_names = list(routers.keys()) if routers else []
    startup_devices = []
    try:
        for fn in os.listdir(base_path):
            if fn.endswith('.startup'):
                startup_devices.append(fn[:-8])
    except Exception:
        pass

    only_startup = [d for d in startup_devices if d not in router_names]
    items = []
    for r in router_names:
        asn = routers.get(r, {}).get('asn', '')
        items.append(f"{r} (router, ASN: {asn})")
    for d in only_startup:
        items.append(f"{d} (device)")

    if not items:
        print('Nessun dispositivo trovato nella cartella del lab.')
        return

    print_menu('Seleziona il dispositivo su cui aggiungere la loopback:', items)
    sel = input('Seleziona il dispositivo (numero o nome, vuoto per annullare): ').strip()
    if not sel:
        print('Operazione annullata.')
        return

    target = None
    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(items):
            if idx < len(router_names):
                target = router_names[idx]
            else:
                target = only_startup[idx - len(router_names)]
        else:
            print('Selezione non valida.')
            return
    else:
        # nome diretto
        if sel in router_names:
            target = sel
        elif sel in only_startup:
            target = sel
        else:
            print('Selezione non valida.')
            return

    # marca il tipo per il codice esistente
    typ = 'r' if target in router_names else 'd'
    if typ.startswith('r'):
        # seleziona router solo se non abbiamo già impostato target
        if not target or target not in routers:
            target = select_router(routers, prompt='Seleziona il router a cui aggiungere la loopback:')
            if not target:
                print('Operazione annullata.')
                return
        ip_only = valida_ip_senza_cidr('IP loopback (es. 1.2.3.4): ')
        ipcidr = ip_only + '/32'
        # memorizza nel metadata
        routers.setdefault(target, {}).setdefault('loopbacks', [])
        if ipcidr in routers[target]['loopbacks']:
            print('⚠️ Loopback già presente nei dati del router.')
        else:
            routers[target]['loopbacks'].append(ipcidr)
        # aggiorna startup
        startup = os.path.join(base_path, f"{target}.startup")
        try:
            # legge lo startup (se esiste) e inserisce la loopback prima di
            # qualsiasi riga 'systemctl start ...' per mantenere l'ordine
            lines = []
            if os.path.exists(startup):
                with open(startup, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            lb_line = f"ip address add {ipcidr} dev lo\n"
            # evita duplicati
            if any(l.strip() == lb_line.strip() for l in lines):
                pass
            else:
                insert_idx = None
                for i, l in enumerate(lines):
                    if re.match(r'^\s*systemctl start\b', l):
                        insert_idx = i
                        break
                if insert_idx is None:
                    # appendare alla fine
                    if lines and not lines[-1].endswith('\n'):
                        lines[-1] = lines[-1] + '\n'
                    lines.append(lb_line)
                else:
                    lines.insert(insert_idx, lb_line)
                with open(startup, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
        except Exception:
            print('⚠️ Impossibile aggiornare lo startup del router (file non trovato).')
        # aggiorna frr.conf: inserisci network nelle sezioni dei protocolli abilitati
        fpath = os.path.join(base_path, target, 'etc', 'frr', 'frr.conf')
        if os.path.exists(fpath):
            protos = routers.get(target, {}).get('protocols', [])
            # IMPORTANT: non annunciare mai la loopback in BGP
            # (anche se il router ha BGP abilitato non tocchiamo il blocco bgp)
            if 'ospf' in protos:
                area = routers.get(target, {}).get('ospf_area') or input('Area OSPF per annuncio della loopback (es. 0.0.0.0): ').strip() or '0.0.0.0'
                insert_lines_into_protocol_block(fpath, proto='ospf', asn=None, lines=[f"network {ipcidr} area {area}"])
            if 'rip' in protos:
                insert_lines_into_protocol_block(fpath, proto='rip', asn=None, lines=[f"network {ipcidr}"])
            # Prova ad aggiungere automaticamente iBGP neighbors via loopback
            try:
                add_ibgp_loopback_neighbors(base_path, routers)
            except Exception:
                # non blocchiamo l'operazione principale se fallisce
                pass
            print(f"\n\n✅ Loopback {ipcidr} aggiunta a router {target} (startup aggiornato).\n")
        else:
            print(f"⚠️ frr.conf non trovato per {target}; è stata aggiornata solo la startup (se esistente).")
        return

    # Dispositivi generici: cerca file .startup nella cartella del lab
    items = []
    try:
        for fn in os.listdir(base_path):
            if fn.endswith('.startup'):
                items.append(fn[:-8])
    except Exception:
        pass
    if not items:
        print('Nessun dispositivo con file .startup trovato nella cartella del lab.')
        return
    print_menu('Dispositivi con startup:', items)
    sel = input('Seleziona il dispositivo (numero o nome, vuoto per annullare): ').strip()
    if not sel:
        print('Annullato.')
        return
    target = None
    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(items):
            target = items[idx]
    else:
        if sel in items:
            target = sel
    if not target:
        print('Selezione non valida.')
        return
    ip_only = valida_ip_senza_cidr('\nIP loopback (es. 1.2.3.4): ')
    ipcidr = ip_only + '/32'
    startup = os.path.join(base_path, f"{target}.startup")
    try:
        lines = []
        if os.path.exists(startup):
            with open(startup, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        lb_line = f"ip address add {ipcidr} dev lo\n"
        if any(l.strip() == lb_line.strip() for l in lines):
            print(f'⚠️ Loopback {ipcidr} già presente nello startup di {target}.')
        else:
            insert_idx = None
            for i, l in enumerate(lines):
                if re.match(r'^\s*systemctl start\b', l):
                    insert_idx = i
                    break
            if insert_idx is None:
                if lines and not lines[-1].endswith('\n'):
                    lines[-1] = lines[-1] + '\n'
                lines.append(lb_line)
            else:
                lines.insert(insert_idx, lb_line)
            with open(startup, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            print(f"✅ Loopback {ipcidr} aggiunta a {target} (startup).")
    except Exception as e:
        print('Errore aggiornando lo startup:', e)




def aggiungi_customer_provider_wizard(base_path, routers):
    print("\n=== Configurazione Customer-Provider ===")
    
    # 1. Seleziona Router
    target = select_router(routers, prompt="Quale router stiamo configurando? ")
    if not target:
        return

    if "bgp" not in routers.get(target, {}).get("protocols", []):
        print(f"⚠️ Il router {target} non ha BGP abilitato.")
        return

    # 2. Seleziona Neighbor
    print(f"\nSeleziona il Neighbor (vicino) di {target}:")
    neigh_name = select_router(routers, prompt="Router vicino:")
    
    neigh_ip = None
    neigh_asn = None

    if not neigh_name:
        # Fallback manuale
        neigh_name = input("Nome del vicino (o invio per uscire): ").strip()
        if not neigh_name:
            return
        neigh_ip = valida_ip_senza_cidr("Inserisci IP del vicino: ")
        neigh_asn = input_non_vuoto("ASN del vicino: ")
    else:
        # Neighbor conosciuto: recuperiamo gli IP
        neigh_asn = routers[neigh_name].get('asn')
        ifaces = routers[neigh_name].get('interfaces', [])
        valid_ips = []
        for iface in ifaces:
            ip_cidr = iface.get('ip')
            if ip_cidr:
                ip = ip_cidr.split('/')[0]
                valid_ips.append(ip)
        
        if not valid_ips:
            print(f"⚠️ Nessun IP trovato per {neigh_name}.")
            neigh_ip = valida_ip_senza_cidr("Inserisci IP del vicino manualmente: ")
        elif len(valid_ips) == 1:
            neigh_ip = valid_ips[0]
            print(f"✅ IP rilevato per {neigh_name}: {neigh_ip}")
        else:
            print(f"\nIl router {neigh_name} ha più indirizzi IP. Seleziona quello verso {target}:")
            print_menu("IP Disponibili:", valid_ips)
            while True:
                sel = input("Seleziona numero (o premi invio per il primo): ").strip()
                if not sel:
                    neigh_ip = valid_ips[0]
                    break
                if sel.isdigit():
                    idx = int(sel) - 1
                    if 0 <= idx < len(valid_ips):
                        neigh_ip = valid_ips[idx]
                        break
                print("Selezione non valida.")
    
    if not neigh_ip:
        return

    # Assicurati che il neighbor esista nella conf BGP
    fpath = os.path.join(base_path, target, "etc", "frr", "frr.conf")
    if not os.path.exists(fpath):
        print(f"File {fpath} non trovato.")
        return

    # Check/Create neighbor (remote-as)
    with open(fpath, 'r') as f:
        content = f.read()
    
    if f"neighbor {neigh_ip} remote-as" not in content:
        print(f"Neighbor {neigh_ip} non presente. Lo aggiungo.")
        lines = [f"neighbor {neigh_ip} remote-as {neigh_asn}",
                 f"neighbor {neigh_ip} description {neigh_name}"]
        if not insert_lines_into_protocol_block(fpath, proto='bgp', asn=None, lines=lines):
            print("❌ Errore nell'inserimento del neighbor.")
            return
    
    # 3. Relazione Economica
    print("\nQual è la relazione economica?")
    print("1) PROVIDER (Il vicino è il mio provider)")
    print("2) CUSTOMER (Il vicino è il mio cliente)")
    print("3) PEER (Siamo alla pari)")
    rel_choice = input("Scelta (1-3): ").strip()
    
    if rel_choice == '1':
        rel_type = "provider"
    elif rel_choice == '2':
        rel_type = "customer"
    elif rel_choice == '3':
        rel_type = "peer"
    else:
        print("Scelta non valida.")
        return

    # 4. Reti coinvolte (Opzionale)
    # Usiamo nomi univoci per le prefix-list
    pl_in = f"{rel_type}_{neigh_name}_in"
    pl_out = f"{rel_type}_{neigh_name}_out"

    lines_to_append = []
    lines_to_append.append(f"!")
    lines_to_append.append(f"! Policy per relazione {rel_type.upper()} con {neigh_name}")
    lines_to_append.append(f"!")

    if rel_type == "provider":
        # Provider (Il vicino è il mio provider):
        # IN: permit any (accetto tutto dal provider)
        # OUT: permit solo le mie reti e quelle dei miei clienti
        lines_to_append.append(f"ip prefix-list {pl_in} permit any")
        
        print("Inserisci le TUE reti da annunciare al provider (separate da virgola):")
        my_nets = input("> ").strip()
        if my_nets:
            for net in my_nets.replace(',', ' ').split():
                lines_to_append.append(f"ip prefix-list {pl_out} permit {net}")
        
        print("Inserisci le reti dei tuoi CLIENTI da annunciare al provider (separate da virgola, o invio per nessuna):")
        cust_nets = input("> ").strip()
        if cust_nets:
            for net in cust_nets.replace(',', ' ').split():
                lines_to_append.append(f"ip prefix-list {pl_out} permit {net}")

    elif rel_type == "customer":
        # Customer (Il vicino è il mio cliente):
        # IN: permit any (accetto tutto dal cliente)
        # OUT: permit any (invio la full table al cliente)
        lines_to_append.append(f"ip prefix-list {pl_in} permit any")
        lines_to_append.append(f"ip prefix-list {pl_out} permit any")

    elif rel_type == "peer":
        # Peer (Siamo alla pari):
        # IN: permit any (accetto tutto dal peer)
        # OUT: permit solo le mie reti e quelle dei miei clienti (NON le reti del provider)
        lines_to_append.append(f"ip prefix-list {pl_in} permit any")
        
        print("Inserisci le TUE reti da annunciare al peer (separate da virgola):")
        print("⚠️  ATTENZIONE: NON inserire le reti del tuo provider!")
        my_nets = input("> ").strip()
        if my_nets:
            for net in my_nets.replace(',', ' ').split():
                lines_to_append.append(f"ip prefix-list {pl_out} permit {net}")
        
        print("Inserisci le reti dei tuoi CLIENTI da annunciare al peer (separate da virgola, o invio per nessuna):")
        print("⚠️  ATTENZIONE: NON inserire le reti del tuo provider!")
        cust_nets = input("> ").strip()
        if cust_nets:
            for net in cust_nets.replace(',', ' ').split():
                lines_to_append.append(f"ip prefix-list {pl_out} permit {net}")

    # Scrivi le prefix-list nel file (append global)
    try:
        with open(fpath, 'a') as f:
            f.write('\n')
            for l in lines_to_append:
                f.write(l + '\n')
    except Exception as e:
        print(f"❌ Errore scrivendo le prefix-list: {e}")
        return
    
    # Collega le prefix-list al neighbor
    neigh_lines = [
        f"neighbor {neigh_ip} prefix-list {pl_in} in",
        f"neighbor {neigh_ip} prefix-list {pl_out} out"
    ]
    if insert_lines_into_protocol_block(fpath, proto='bgp', asn=None, lines=neigh_lines):
        print(f"✅ Configurazione {rel_type.upper()} applicata su {target} verso {neigh_name}.")
    else:
        print(f"❌ Errore applicando la configurazione neighbor su {target}.")


def menu_post_creazione(base_path, routers):
    while True:
        items = [
            'Imposta costo OSPF su una interfaccia di un router',
            'Rigenera file XML del laboratorio (da file modificati)',
            'Genera comando ping per tutti gli indirizzi del lab (copia/incolla)',
            'Aggiungi Policies BGP a un router',
            'Assegna un file resolv.conf specifico a un dispositivo',
            "Aggiungi loopback a un dispositivo"
        ]
        print_menu('-------------- Menu post-creazione --------------', items, extra_options=[('0', 'Termina Programma')])
        # footer: mostrato in basso per identificazione dell'autore
        print('\n--------------------------------------------------')
        print('---- Programma realizzato da sciro24 (Github) ----')
        print('--------------------------------------------------\n')

        choice = input('Seleziona (numero): ').strip()
        if choice == '0':
            # torna al menu precedente
            break
        if choice == '1':
            assegna_costo_interfaccia(base_path, routers)
        elif choice == '2':
            # rigenera XML leggendo lab.conf / startup / etc per ricostruire lo stato corrente
            try:
                xmlpath = rebuild_lab_metadata_and_export(base_path)
                if xmlpath:
                    print(f"✅ XML rigenerato: {xmlpath}")
                else:
                    print("❌ Rigenerazione XML non riuscita.")
            except Exception as e:
                print('Errore durante la rigenerazione XML:', e)
        elif choice == '3':
            try:
                ips = collect_lab_ips(base_path, routers)
                if not ips:
                    print('Nessun IP trovato nel lab. Controlla i file .startup o le interfacce dei router.')
                else:
                    cmd = generate_ping_oneliner(ips)
                    print('\n=== Comando ping generato (copia/incolla sulle macchine del lab) ===\n\n')
                    print(cmd)
                    print('\n\n=== Fine comando ===\n')
            except Exception as e:
                print('Errore generando il comando ping:', e)
        elif choice == '4':
            policies_menu(base_path, routers)
        elif choice == '5':
            assegna_resolv_conf(base_path)
        elif choice == '6':
            aggiungi_loopback_menu(base_path, routers)
        else:
            print('Scelta non valida, riprova.')


def export_lab_to_xml(lab_name, lab_path, routers, hosts, wwws):
    """Esporta una rappresentazione XML del laboratorio in `lab_path/<lab_name>.xml`.
    Struttura semplice: <lab><routers>...<hosts>...<www>...<lab_conf>...</lab>
    """
    try:
        import xml.etree.ElementTree as ET
        from xml.dom import minidom

        root = ET.Element('lab', attrib={'name': lab_name})

        routers_el = ET.SubElement(root, 'routers')
        for rname, rdata in routers.items():
            r_el = ET.SubElement(routers_el, 'router', attrib={'name': rname})
            # protocols
            prot_el = ET.SubElement(r_el, 'protocols')
            for p in rdata.get('protocols', []):
                p_el = ET.SubElement(prot_el, 'protocol')
                p_el.text = str(p)
            # asn
            asn = rdata.get('asn', '')
            if asn:
                asn_el = ET.SubElement(r_el, 'asn')
                asn_el.text = str(asn)
            # OSPF info (optional)
            if rdata.get('ospf_area'):
                oa = ET.SubElement(r_el, 'ospf_area')
                oa.text = str(rdata.get('ospf_area'))
                if rdata.get('ospf_area_stub'):
                    oas = ET.SubElement(r_el, 'ospf_area_stub')
                    oas.text = '1'
            # ospf_extra_areas (dict) if present
            if isinstance(rdata.get('ospf_extra_areas'), dict):
                extra_el = ET.SubElement(r_el, 'ospf_extra_areas')
                for k, v in rdata.get('ospf_extra_areas', {}).items():
                    e = ET.SubElement(extra_el, 'area', attrib={'first_octet': str(k)})
                    if isinstance(v, dict):
                        if v.get('area'):
                            ET.SubElement(e, 'id').text = str(v.get('area'))
                        if v.get('stub'):
                            ET.SubElement(e, 'stub').text = '1'
                    else:
                        ET.SubElement(e, 'id').text = str(v)
            # interfaces
            if 'interfaces' in rdata:
                ifs_el = ET.SubElement(r_el, 'interfaces')
                for iface in rdata['interfaces']:
                    i_el = ET.SubElement(ifs_el, 'interface')
                    i_el.set('name', iface.get('name', ''))
                    i_el.set('lan', iface.get('lan', ''))
                    i_el.set('ip', iface.get('ip', ''))

        hosts_el = ET.SubElement(root, 'hosts')
        for h in hosts:
            h_attrib = {'name': h.get('name',''), 'ip': h.get('ip',''), 'gateway': h.get('gateway',''), 'lan': h.get('lan','')}
            if h.get('image'):
                h_attrib['image'] = h.get('image')
            h_el = ET.SubElement(hosts_el, 'host', attrib=h_attrib)
            # dns info
            if h.get('dns'):
                ET.SubElement(h_el, 'dns').text = '1'
                # export root_type if present
                if h.get('root_type'):
                    ET.SubElement(h_el, 'root_type').text = str(h.get('root_type'))
                if isinstance(h.get('forwarders'), list):
                    fw_el = ET.SubElement(h_el, 'forwarders')
                    for fwd in h.get('forwarders'):
                        ET.SubElement(fw_el, 'forwarder').text = str(fwd)
                if isinstance(h.get('zones'), dict):
                    zones_el = ET.SubElement(h_el, 'zones')
                    for zname, records in h.get('zones').items():
                        z_el = ET.SubElement(zones_el, 'zone', attrib={'name': zname})
                        if isinstance(records, dict):
                            for rname, rip in records.items():
                                rec = ET.SubElement(z_el, 'record', attrib={'name': rname})
                                # support both simple A-record (string) and complex record (dict)
                                if isinstance(rip, dict):
                                    # write child elements for complex record
                                    for k, v in rip.items():
                                        c = ET.SubElement(rec, k)
                                        c.text = str(v)
                                else:
                                    rec.text = str(rip)
                # export allow_recursion and dnssec_validation if present
                if h.get('allow_recursion'):
                    ET.SubElement(h_el, 'allow_recursion').text = str(h.get('allow_recursion'))
                if h.get('dnssec_validation'):
                    ET.SubElement(h_el, 'dnssec_validation').text = '1'

        www_el = ET.SubElement(root, 'www')
        for w in wwws:
            w_attrib = {'name': w.get('name',''), 'ip': w.get('ip',''), 'gateway': w.get('gateway',''), 'lan': w.get('lan','')}
            if w.get('image'):
                w_attrib['image'] = w.get('image')
            ET.SubElement(www_el, 'server', attrib=w_attrib)

        # include lab.conf content se presente
        try:
            with open(os.path.join(lab_path, 'lab.conf'), 'r') as f:
                labconf = f.read()
            lc_el = ET.SubElement(root, 'lab_conf')
            lc_el.text = labconf
        except Exception:
            pass

        rough = ET.tostring(root, 'utf-8')
        reparsed = minidom.parseString(rough)
        pretty = reparsed.toprettyxml(indent='  ', encoding='utf-8')

        out_path = os.path.join(lab_path, f"{lab_name}.xml")
        with open(out_path, 'wb') as f:
            f.write(pretty)
        print(f"\n\n✅ Esportato XML: {out_path}")
    except Exception as e:
        print('Errore esportazione XML:', e)
 
# Funzionalità di caricamento non-interattivo (XML/JSON) e ricreazione lab
def load_lab_from_xml(path):
    """Carica un lab da file XML generato dallo script.
    Ritorna: lab_name, routers(dict), hosts(list), wwws(list), lab_conf_text(str or None)
    """
    import xml.etree.ElementTree as ET
    tree = ET.parse(path)
    root = tree.getroot()
    lab_name = root.attrib.get('name') or os.path.splitext(os.path.basename(path))[0]

    routers = {}
    for r in root.findall('./routers/router'):
        rname = r.attrib.get('name')
        protocols = [p.text for p in r.findall('./protocols/protocol') if p.text]
        asn_el = r.find('asn')
        asn = asn_el.text if asn_el is not None else ''
        ospf_area = None
        ospf_stub = False
        oa_el = r.find('ospf_area')
        if oa_el is not None and oa_el.text:
            ospf_area = oa_el.text
        oas_el = r.find('ospf_area_stub')
        if oas_el is not None:
            ospf_stub = True
        # ospf_extra_areas
        extra_areas = {}
        for ea in r.findall('./ospf_extra_areas/area'):
            key = ea.attrib.get('first_octet')
            aid = ea.findtext('id')
            stub = ea.findtext('stub') is not None
            if key:
                if aid:
                    extra_areas[str(key)] = {'area': aid, 'stub': stub}
                else:
                    extra_areas[str(key)] = {'area': None, 'stub': stub}

        interfaces = []
        for it in r.findall('./interfaces/interface'):
            interfaces.append({
                'name': it.attrib.get('name',''),
                'lan': it.attrib.get('lan',''),
                'ip': it.attrib.get('ip','')
            })
        rd = {'protocols': protocols, 'asn': asn, 'interfaces': interfaces}
        if ospf_area:
            rd['ospf_area'] = ospf_area
            rd['ospf_area_stub'] = ospf_stub
        if extra_areas:
            rd['ospf_extra_areas'] = extra_areas
        routers[rname] = rd

    hosts = []
    for h in root.findall('./hosts/host'):
        name = h.attrib.get('name','')
        ip = h.attrib.get('ip','')
        gateway = h.attrib.get('gateway','')
        lan = h.attrib.get('lan','')
        image = h.attrib.get('image','')
        host_rec = {'name': name, 'ip': ip, 'gateway': gateway, 'lan': lan}
        if image:
            host_rec['image'] = image
        # dns
        if h.find('dns') is not None:
            host_rec['dns'] = True
            rt = h.find('root_type')
            if rt is not None and rt.text:
                host_rec['root_type'] = rt.text
            fwd_el = h.find('forwarders')
            if fwd_el is not None:
                host_rec['forwarders'] = [f.text for f in fwd_el.findall('forwarder') if f.text]
            zones_el = h.find('zones')
            if zones_el is not None:
                zones = {}
                for z in zones_el.findall('zone'):
                    zname = z.attrib.get('name')
                    records = {}
                    for rec in z.findall('record'):
                        rname = rec.attrib.get('name')
                        # record may be simple text or complex children
                        if len(list(rec)) == 0:
                            rip = rec.text
                            if rname and rip:
                                records[rname] = rip
                        else:
                            # complex record: parse children into dict
                            crec = {}
                            for c in rec:
                                crec[c.tag] = c.text
                            records[rname] = crec
                    zones[zname] = records
                host_rec['zones'] = zones
            # optional: allow_recursion and dnssec_validation flags
            ar_el = h.find('allow_recursion')
            if ar_el is not None and ar_el.text:
                host_rec['allow_recursion'] = ar_el.text.strip()
            dv_el = h.find('dnssec_validation')
            if dv_el is not None and (dv_el.text and dv_el.text.strip() in ('1','true','yes')):
                host_rec['dnssec_validation'] = True
        hosts.append(host_rec)

    wwws = []
    for w in root.findall('./www/server'):
        name = w.attrib.get('name','')
        ip = w.attrib.get('ip','')
        gateway = w.attrib.get('gateway','')
        lan = w.attrib.get('lan','')
        image = w.attrib.get('image','')
        wrec = {'name': name, 'ip': ip, 'gateway': gateway, 'lan': lan}
        if image:
            wrec['image'] = image
        wwws.append(wrec)

    lab_conf_text = None
    lc = root.find('lab_conf')
    if lc is not None and lc.text:
        lab_conf_text = lc.text

    return lab_name, routers, hosts, wwws, lab_conf_text


def load_lab_from_json(path):
    """Carica un lab da file JSON. Atteso schema simile all'XML prodotto.
    Ritorna: lab_name, routers(dict), hosts(list), wwws(list), lab_conf_text(str or None)
    """
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    lab_name = data.get('lab', {}).get('name') or data.get('name') or os.path.splitext(os.path.basename(path))[0]
    routers = {}
    for r in data.get('routers', []):
        rname = r.get('name')
        protocols = r.get('protocols', [])
        asn = r.get('asn', '')
        interfaces = r.get('interfaces', [])
        rd = {'protocols': protocols, 'asn': asn, 'interfaces': interfaces}
        # optional ospf fields
        if r.get('ospf_area'):
            rd['ospf_area'] = r.get('ospf_area')
            rd['ospf_area_stub'] = bool(r.get('ospf_area_stub'))
        if r.get('ospf_extra_areas'):
            rd['ospf_extra_areas'] = r.get('ospf_extra_areas')
        routers[rname] = rd
    hosts = data.get('hosts', []) or []
    wwws = data.get('www', []) or data.get('wwws', []) or []
    lab_conf_text = data.get('lab_conf') or None
    return lab_name, routers, hosts, wwws, lab_conf_text


def parse_startup_files(lab_path, nodes):
    """
    Legge i file <node>.startup per cercare configurazioni IP.
    Aggiorna il dizionario 'nodes' con gli IP trovati.
    Formato atteso: 'ip address add <IP> dev <IFACE>' o 'ifconfig <IFACE> <IP> ...'
    """
    import re
    # Regex per 'ip address add'
    re_ip_add = re.compile(r"ip\s+addr(?:ess)?\s+add\s+(?P<ip>[0-9\./]+)\s+dev\s+(?P<iface>\w+)")
    # Regex per 'ifconfig' - ora cattura anche /CIDR
    re_ifconfig = re.compile(r"ifconfig\s+(?P<iface>\w+)\s+(?P<ip>[0-9\./]+)")

    for node_name, node_data in nodes.items():
        startup_path = os.path.join(lab_path, f"{node_name}.startup")
        if not os.path.exists(startup_path):
            continue
        
        try:
            with open(startup_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Cerca IP
            # Prima cerca con 'ip addr'
            for m in re_ip_add.finditer(content):
                ip = m.group('ip')
                iface = m.group('iface')
                # Trova l'indice dell'interfaccia
                # Le interfacce in nodes sono {idx: lan}
                # Dobbiamo mappare iface (eth0 -> 0, eth1 -> 1)
                idx = -1
                if iface.startswith('eth'):
                    try:
                        idx = int(iface[3:])
                    except: pass
                
                if idx >= 0 and str(idx) in node_data['interfaces'] or idx in node_data['interfaces']:
                    # Normalizza key a int se possibile o stringa
                    key = idx if idx in node_data['interfaces'] else str(idx)
                    # Aggiungiamo info IP
                    if 'ips' not in node_data: node_data['ips'] = {}
                    node_data['ips'][key] = ip

            # Poi cerca con 'ifconfig' (se non trovato prima o aggiuntivo)
            for m in re_ifconfig.finditer(content):
                ip = m.group('ip')
                iface = m.group('iface')
                idx = -1
                if iface.startswith('eth'):
                    try:
                        idx = int(iface[3:])
                    except: pass
                
                if idx >= 0 and (str(idx) in node_data['interfaces'] or idx in node_data['interfaces']):
                    key = idx if idx in node_data['interfaces'] else str(idx)
                    if 'ips' not in node_data: node_data['ips'] = {}
                    # Sovrascrivi o aggiungi
                    node_data['ips'][key] = ip

        except Exception as e:
            print(f"Errore parsing startup {node_name}: {e}")
    
    return nodes

def recreate_lab_from_data(lab_name, base, routers, hosts, wwws, lab_conf_text=None):
    """Ricrea il lab sul filesystem usando le funzioni esistenti.
    Restituisce il percorso `lab_path` creato.
    """
    lab_path = os.path.join(base, lab_name)
    if os.path.exists(lab_path):
        # rimuovi esistente per ricreare
        if os.path.isdir(lab_path):
            shutil.rmtree(lab_path)
        else:
            os.remove(lab_path)
    os.makedirs(lab_path, exist_ok=True)

    # crea routers
    for rname, rdata in routers.items():
        crea_router_files(lab_path, rname, rdata)

    # crea hosts
    # prima pass: individua l'eventuale server DNS master per usare il suo IP come riferimento
    dns_root_ip = None
    for h in hosts:
        try:
            if isinstance(h, dict) and h.get('dns') and str(h.get('root_type','')).lower() == 'master':
                # estrai IP senza CIDR
                ip = h.get('ip') or ''
                dns_root_ip = ip.split('/')[0] if '/' in ip else ip
                break
        except Exception:
            continue

    for h in hosts:
        try:
            # supporta host DNS: se il dict contiene 'dns': True o 'type' == 'dns'
            if (isinstance(h, dict) and (h.get('dns') or str(h.get('type','')).lower() == 'dns')):
                # forwarders e zones sono opzionali
                fwd = h.get('forwarders') if isinstance(h.get('forwarders'), list) else None
                zones = h.get('zones') if isinstance(h.get('zones'), dict) else None
                rt = h.get('root_type') if isinstance(h.get('root_type'), str) else None
                # read optional flags from host dict (allow_recursion may be a string like 'any')
                allow_rec = h.get('allow_recursion') if isinstance(h.get('allow_recursion'), str) else None
                dnssec_val = bool(h.get('dnssec_validation'))
                if rt and str(rt).lower() != 'master' and dns_root_ip:
                    crea_dns_host(lab_path, h.get('name'), h.get('ip'), h.get('gateway'), h.get('lan'), forwarders=fwd, zones=zones, root_type=rt, root_server_ip=dns_root_ip, allow_recursion=allow_rec, dnssec_validation=dnssec_val)
                else:
                    crea_dns_host(lab_path, h.get('name'), h.get('ip'), h.get('gateway'), h.get('lan'), forwarders=fwd, zones=zones, root_type=rt, allow_recursion=allow_rec, dnssec_validation=dnssec_val)
            else:
                crea_host_file(lab_path, h.get('name'), h.get('ip'), h.get('gateway'), h.get('lan'))
        except Exception:
            pass

    # crea webservers
    for w in wwws:
        try:
            crea_www_file(lab_path, w.get('name'), w.get('ip'), w.get('gateway'), w.get('lan'))
        except Exception:
            pass

    # scrivi lab.conf se fornito (altrimenti lo lascio come prima)
    if lab_conf_text:
        try:
            with open(os.path.join(lab_path, 'lab.conf'), 'w', encoding='utf-8') as f:
                f.write(lab_conf_text)
        except Exception:
            pass

    # auto-generate BGP neighbors
    auto_generate_bgp_neighbors(lab_path, routers)

    # esporta XML (aggiorna o crea)
    try:
        export_lab_to_xml(lab_name, lab_path, routers, hosts, wwws)
    except Exception:
        pass

    return lab_path


def parse_lab_conf_for_nodes(lab_path):
    """Parse `lab.conf` per ricavare nodi, mapping interfacce->LAN e immagini.
    Ritorna: nodes dict with keys: name -> {'interfaces': {idx: lan}, 'image': image}
    """
    import re
    nodes = {}
    conf_path = os.path.join(lab_path, 'lab.conf')
    if not os.path.exists(conf_path):
        return nodes, None
    lab_conf_text = None
    try:
        with open(conf_path, 'r', encoding='utf-8') as f:
            lab_conf_text = f.read()
    except Exception:
        return nodes, None

    lines = [L.strip() for L in lab_conf_text.splitlines() if L.strip() and not L.strip().startswith('#')]
    # pattern: name[index]=value (handles spaces and quotes)
    # e.g. r1[0]="A" or r1[0]=A or r1 [ 0 ] = "A"
    p = re.compile(r"^\s*(?P<name>[^\[\s]+)\s*\[\s*(?P<idx>[^\]]+)\s*\]\s*=\s*(?:\"(?P<qval>.*)\"|(?P<val>[^#]*))")
    for L in lines:
        m = p.match(L)
        if not m:
            continue
        name = m.group('name')
        idx = m.group('idx')
        val = m.group('qval') if m.group('qval') is not None else m.group('val')
        val = val.strip()
        node = nodes.setdefault(name, {'interfaces': {}, 'image': ''})
        if idx == 'image':
            node['image'] = val.strip('"')
        else:
            # treat as interface index
            try:
                node['interfaces'][int(idx)] = val
            except Exception:
                node['interfaces'][idx] = val
    return nodes, lab_conf_text


def rebuild_lab_metadata_and_export(lab_path):
    """Ricostruisce routers/hosts/www dal filesystem (lab.conf, startup, etc) e rigenera l'XML.
    Restituisce il percorso del file XML creato o None in caso di errore.
    """
    lab_name = os.path.basename(os.path.normpath(lab_path))
    nodes, lab_conf_text = parse_lab_conf_for_nodes(lab_path)
    routers = {}
    hosts = []
    wwws = []

    for name, meta in nodes.items():
        image = meta.get('image','')
        # collect interfaces: build list of dicts {name, lan, ip}
        ifaces = []
        for idx, lan in sorted(meta.get('interfaces', {}).items(), key=lambda x: int(x[0]) if isinstance(x[0], int) or x[0].isdigit() else 0):
            eth = f"eth{idx}"
            ifaces.append({'name': eth, 'lan': lan, 'ip': ''})

        # read startup file for IPs and gateways
        startup_path = os.path.join(lab_path, f"{name}.startup")
        if os.path.exists(startup_path):
            try:
                with open(startup_path, 'r', encoding='utf-8') as f:
                    for L in f:
                        Ls = L.strip()
                        if not Ls:
                            continue
                        # ip address add <ip> dev <iface>
                        parts = Ls.split()
                        if len(parts) >= 5 and parts[0] == 'ip' and parts[1] == 'address' and parts[2] == 'add':
                            ip = parts[3]
                            dev = parts[5] if len(parts) > 5 and parts[4] == 'dev' else None
                            if dev:
                                for it in ifaces:
                                    if it['name'] == dev:
                                        it['ip'] = ip
                        # gateway extraction for hosts/www: 'ip route add default via <gw> dev eth0'
                        if len(parts) >= 7 and parts[0] == 'ip' and parts[1] == 'route' and parts[2] == 'add' and parts[3] == 'default' and parts[4] == 'via':
                            gw = parts[5]
                            # attach gateway to a host/www entry later
            except Exception:
                pass

        # if it's a FRR router image (heuristic)
        if 'frr' in image.lower() or 'kathara/frr' in image.lower():
            # detect protocols and ASN from etc/frr/frr.conf
            frr_conf = os.path.join(lab_path, name, 'etc', 'frr', 'frr.conf')
            protos = []
            asn = ''
            if os.path.exists(frr_conf):
                try:
                    with open(frr_conf, 'r', encoding='utf-8') as f:
                        txt = f.read()
                    if 'router bgp' in txt:
                        protos.append('bgp')
                        import re
                        m = re.search(r'router bgp\s+(\d+)', txt)
                        if m:
                            asn = m.group(1)
                    if 'router ospf' in txt:
                        protos.append('ospf')
                    if 'router rip' in txt:
                        protos.append('rip')
                except Exception:
                    pass
            routers[name] = {'protocols': protos, 'asn': asn, 'interfaces': ifaces}
        else:
            # decide host vs www by checking for www index file
            www_index = os.path.join(lab_path, name, 'var', 'www', 'html', 'index.html')
            # attempt to read IP/gateway from startup if present
            ip_val = ''
            gw_val = ''
            startup_path = os.path.join(lab_path, f"{name}.startup")
            if os.path.exists(startup_path):
                try:
                    with open(startup_path, 'r', encoding='utf-8') as f:
                        for L in f:
                            if 'ip address add' in L:
                                parts = L.strip().split()
                                if len(parts) >= 4:
                                    ip_val = parts[3]
                            if 'ip route add default via' in L:
                                parts = L.strip().split()
                                if len(parts) >= 6:
                                    gw_val = parts[5]
                except Exception:
                    pass
            if os.path.exists(www_index):
                wwws.append({'name': name, 'ip': ip_val, 'gateway': gw_val, 'lan': meta.get('interfaces', {}).get(0, '')})
            else:
                hosts.append({'name': name, 'ip': ip_val, 'gateway': gw_val, 'lan': meta.get('interfaces', {}).get(0, '')})

    try:
        export_lab_to_xml(lab_name, lab_path, routers, hosts, wwws)
        return os.path.join(lab_path, f"{lab_name}.xml")
    except Exception as e:
        print('Errore rigenerazione XML:', e)
        return None

# -------------------------
# Main
# -------------------------
def main():
    # supporto modalità non-interattiva: --from-xml / --from-json
    parser = argparse.ArgumentParser(description='Generatore Kathará')
    parser.add_argument('--from-xml', help='Percorso file XML da importare per creare il lab')
    parser.add_argument('--from-json', help='Percorso file JSON da importare per creare il lab')
    parser.add_argument('--regen-xml', help='Percorso della directory del lab per rigenerare il file XML')
    args, remaining = parser.parse_known_args()

    base = os.getcwd()

    # Se forniti via CLI, manteniamo il comportamento non-interattivo
    if args.regen_xml:
        out = rebuild_lab_metadata_and_export(args.regen_xml)
        if out:
            print(f"✅ XML rigenerato: {out}")
        else:
            print("❌ Rigenerazione XML fallita.")
        return
    if args.from_xml or args.from_json:
        if args.from_xml:
            lab_name, routers, hosts, wwws, lab_conf_text = load_lab_from_xml(args.from_xml)
        else:
            lab_name, routers, hosts, wwws, lab_conf_text = load_lab_from_json(args.from_json)
        lab_path = recreate_lab_from_data(lab_name, base, routers, hosts, wwws, lab_conf_text)
        print(f"✅ Lab '{lab_name}' creato (non-interattivo) in: {lab_path}")
        return

    # Modalità interattiva: chiedi all'utente se creare o importare
    print("\n\n----- Katharà Lab Generator 2025 (Github: sciro24) -----\n")
    print("Scegli una modalità:\n")
    print("  C - Crea nuovo laboratorio (interattivo)")
    print("  I - Importa da file (XML/JSON)")
    print("  R - Rigenera XML di un lab esistente")
    print("  G - Genera comando PING per un lab esistente (copia/incolla)")
    print("  A - Assegna un file resolv.conf specifico a un dispositivo")
    print("  L - Aggiungi loopback a dispositivo in un lab esistente")
    print("  P - Applica Policies BGP")
    print("  Q - Esci\n")
    print("--------------------------------------------------------\n")

    while True:
        mode = input_non_vuoto("Digita un'opzione (C/I/R/G/A/L/P/Q): ").strip().lower()
        if not mode:
            continue
        if mode.startswith('q'):
            print('Uscita.')
            return
        if mode.startswith('c'):
            # procedi con il flusso interattivo classico
            lab_name = input_non_vuoto("Nome del laboratorio: ")
            lab_path = os.path.join(base, lab_name)
            break
        if mode.startswith('i'):
            file_path = input_non_vuoto("Percorso del file XML/JSON da importare: ")
            if not os.path.exists(file_path):
                print(f"File non trovato: {file_path}")
                continue
            ext = os.path.splitext(file_path)[1].lower()
            try:
                if ext == '.xml':
                    lab_name, routers, hosts, wwws, lab_conf_text = load_lab_from_xml(file_path)
                elif ext == '.json':
                    lab_name, routers, hosts, wwws, lab_conf_text = load_lab_from_json(file_path)
                else:
                    # tenta XML prima, poi JSON
                    try:
                        lab_name, routers, hosts, wwws, lab_conf_text = load_lab_from_xml(file_path)
                    except Exception:
                        lab_name, routers, hosts, wwws, lab_conf_text = load_lab_from_json(file_path)
                lab_path = recreate_lab_from_data(lab_name, base, routers, hosts, wwws, lab_conf_text)
                print(f"✅ Lab '{lab_name}' creato da file in: {lab_path}")
                return
            except Exception as e:
                print('Errore importando il file:', e)
                continue
        if mode.startswith('r'):
            target = input_non_vuoto('Percorso della directory del lab da cui rigenerare l\'XML: ')
            if not os.path.isdir(target):
                print(f"Directory non trovata: {target}")
                continue
            out = rebuild_lab_metadata_and_export(target)
            if out:
                print(f"✅ XML rigenerato: {out}")
            else:
                print("❌ Rigenerazione XML fallita.")
            return
        if mode.startswith('g'):
            target = input_non_vuoto('Percorso della directory del lab per generare il comando ping: ')
            if not os.path.isdir(target):
                print(f"Directory non trovata: {target}")
                continue
            # proviamo a leggere routers.xml se presente (semplifica raccolta IP), ma la funzione
            # collect_lab_ips può operare anche senza routers
            routers_meta = None
            try:
                xmlpath = os.path.join(target, os.path.basename(os.path.normpath(target)) + '.xml')
                if os.path.exists(xmlpath):
                    try:
                        _, routers_meta, _, _, _ = load_lab_from_xml(xmlpath)
                    except Exception:
                        routers_meta = None
            except Exception:
                routers_meta = None
            ips = collect_lab_ips(target, routers_meta)
            if not ips:
                print('Nessun IP trovato nella directory del lab. Controlla che ci siano file .startup o i router con interfacce.')
                continue
            cmd = generate_ping_oneliner(ips)
            print('\n=== Comando ping generato (copia/incolla sulle macchine del lab) ===\n\n')
            print(cmd)
            print('\n\n=== Fine comando ===\n')
            # torna al menu principale
            continue
        if mode.startswith('a'):
            target = input_non_vuoto('Percorso della directory del lab (es. /path/to/lab): ').strip()
            if not os.path.isdir(target):
                print(f"Directory non trovata: {target}")
                continue
            try:
                assegna_resolv_conf(target)
            except Exception as e:
                print('Errore assegnando resolv.conf:', e)
            # torna al menu principale
            continue
        if mode.startswith('p'):
            target = input_non_vuoto('Percorso della directory del lab su cui applicare policies: ')
            if not os.path.isdir(target):
                print(f"Directory non trovata: {target}")
                continue
            # try to load existing XML for the lab, otherwise regenerate it
            xmlpath = os.path.join(target, os.path.basename(os.path.normpath(target)) + '.xml')
            try:
                if os.path.exists(xmlpath):
                    lab_name, routers_meta, _, _, _ = load_lab_from_xml(xmlpath)
                else:
                    out = rebuild_lab_metadata_and_export(target)
                    if not out:
                        print('Impossibile generare metadata del lab.')
                        continue
                    lab_name, routers_meta, _, _, _ = load_lab_from_xml(out)
                # apri il sotto-menu Policies
                policies_menu(target, routers_meta)
            except Exception as e:
                print('Errore caricando il lab o aprendo policies:', e)
            # torna al menu principale
            continue
        if mode.startswith('l'):
            target = input_non_vuoto('Percorso della directory del lab su cui aggiungere loopback: ').strip()
            if not os.path.isdir(target):
                print(f"Directory non trovata: {target}")
                continue
            # prova a caricare XML esistente, altrimenti rigenera metadata
            xmlpath = os.path.join(target, os.path.basename(os.path.normpath(target)) + '.xml')
            try:
                if os.path.exists(xmlpath):
                    lab_name, routers_meta, _, _, _ = load_lab_from_xml(xmlpath)
                else:
                    out = rebuild_lab_metadata_and_export(target)
                    if not out:
                        print('Impossibile generare metadata del lab.')
                        continue
                    lab_name, routers_meta, _, _, _ = load_lab_from_xml(out)
                aggiungi_loopback_menu(target, routers_meta)
            except Exception as e:
                print('Errore caricando il lab o aprendo il menu aggiungi loopback:', e)
            continue
        print('Scelta non valida, riprova.')

    if os.path.exists(lab_path):
        ans = input("⚠️ Esiste già. Sovrascrivere? (s/n): ").strip().lower()
        if ans != "s":
            print("Annullato.")
            return
        if os.path.isdir(lab_path):
            shutil.rmtree(lab_path)
        else:
            os.remove(lab_path)
        print("Precedente eliminato.")

    os.makedirs(lab_path, exist_ok=True)

    n_router = input_int("Numero di router: ", 0)
    n_host = input_int("Numero di host/PC: ", 0)
    n_www = input_int("Numero di server WWW: ", 0)
    n_dns = input_int("Numero di host DNS: ", 0)

    # stato OSPF: ricorda la prima area OSPF incontrata (se presente)
    first_ospf_area = None

    lab_conf_lines = [LAB_CONF_HEADER.strip()]
    routers = {}
    # traccia IP già assegnati durante la creazione interattiva per evitare duplicati
    used_ips = set()
    # Collezioni per esportazione XML
    hosts = []
    wwws = []

    # Routers
    for i in range(1, n_router + 1):
        default_name = f"r{i}"
        while True:
            rname_in = input(f"\nNome router (default {default_name}): ").strip()
            rname = rname_in if rname_in else default_name
            # basic validation: unique and no spaces
            if rname in routers:
                print(f"Nome '{rname}' già usato. Scegli un altro.")
                continue
            if ' ' in rname:
                print("Il nome del router non può contenere spazi.")
                continue
            break
        print(f"--- Configurazione router {rname} ---")
        protocols = valida_protocols(f"Protocolli attivi su {rname} (bgp/ospf/rip/statico, separati da virgola): ")
        asn = ""
        if "bgp" in protocols:
            asn = input_non_vuoto("Numero AS BGP: ")

        # OSPF: chiedi area per il primo router OSPF; per i successivi proponi la stessa area
        ospf_area = None
        ospf_stub = False
        if "ospf" in protocols:
            if first_ospf_area is None:
                ans = input_non_vuoto("Questo è il primo router OSPF. Appartiene alla backbone (area 0.0.0.0)? (s/N): ").strip().lower()
                if ans.startswith('s'):
                    ospf_area = '0.0.0.0'
                    first_ospf_area = ospf_area
                else:
                    a = input_non_vuoto("Inserisci l'area OSPF di questo router (es. 1.1.1.1): ")
                    ospf_area = a
                    ospf_stub = True
                    first_ospf_area = ospf_area
            else:
                ans = input(f"Area OSPF per {rname} (vuoto -> default {first_ospf_area}): ").strip()
                if ans:
                    ospf_area = ans
                    s = input_non_vuoto("Marcare quest'area come stub? (s/N): ").strip().lower()
                    ospf_stub = s.startswith('s')
                else:
                    ospf_area = first_ospf_area

        n_if = input_int("Numero interfacce: ", 1)
        interfaces = []
        for idx in range(n_if):
            eth = f"eth{idx}"
            lan = input_lan(f"  LAN associata a {eth} (es. A): ")
            # richiedi IP e verifica che non sia già stato assegnato
            while True:
                ip_cidr = valida_ip_cidr(f"  IP per {eth} (es. 10.0.{i}.{idx}/24): ")
                ip_only = ip_cidr.split('/')[0]
                if ip_only in used_ips:
                    print(f"❌ Errore: l'IP {ip_only} è già stato assegnato ad un'altra interfaccia. Scegli un altro IP.")
                    continue
                # non duplicato
                used_ips.add(ip_only)
                break
            interfaces.append({"name": eth, "lan": lan, "ip": ip_cidr})
            lab_conf_lines.append(f"{rname}[{idx}]={lan}")
        # fine ciclo interfacce: aggiungi la riga image solo se il router
        # usa realmente un protocollo di routing (bgp/ospf/rip). Se è
        # 'statico' o non ha protocolli, non mettiamo l'immagine FRR.
        if any(p in protocols for p in ("bgp", "ospf", "rip")):
            lab_conf_lines.append(f'{rname}[image]="kathara/frr"')
        lab_conf_lines.append("")  # blank line
        # salva i dati del router e genera i file (frr.conf, startup, ecc.)
        rdata = {"protocols": protocols, "asn": asn, "interfaces": interfaces}
        # Se l'utente ha selezionato 'statico' tra i protocolli, chiedi le rotte
        # statiche (altrimenti non chiedere nulla).
        if 'statico' in protocols:
            n_static = input_int("Numero di rotte statiche (0 se nessuna): ", 0)
            static_routes = []
            for si in range(n_static):
                print(f"Rotta statica #{si+1}:")
                net = input_non_vuoto("  Destinazione (es. 30.0.0.0/24): ")
                via = input_non_vuoto("  Via / next-hop (es. 10.0.0.13): ")
                # rimuovi eventuale maschera dal next-hop immediatamente
                via = via.split('/')[0] if '/' in via else via
                dev = input(f"  Interfaccia (vuoto -> {interfaces[0]['name']}): ").strip()
                r = {"network": net, "via": via}
                if dev:
                    r['dev'] = dev
                static_routes.append(r)
            rdata['static_routes'] = static_routes
        if "ospf" in protocols:
            rdata['ospf_area'] = ospf_area
            rdata['ospf_area_stub'] = ospf_stub
        # Loopbacks: non vengono richieste durante la creazione interattiva.
        # Le loopback possono essere aggiunte tramite il menu post-creazione
        # o tramite l'opzione 'L' nel menu principale.
        routers[rname] = rdata
        crea_router_files(lab_path, rname, routers[rname])

    

    # prepara l'insieme dei nomi già usati (router names)
    used_names = set(routers.keys())

    # Hosts
    for h in range(1, n_host + 1):
        default_hname = f"host{h}"
        while True:
            hname_in = input(f"\nNome host (default {default_hname}): ").strip()
            hname = hname_in if hname_in else default_hname
            if ' ' in hname:
                print("Il nome non può contenere spazi.")
                continue
            if hname in used_names:
                print(f"Nome '{hname}' già usato. Scegli un altro.")
                continue
            break
        used_names.add(hname)
        print(f"--- Configurazione host {hname} ---")
        
        n_if_host = input_int(f"Numero interfacce per {hname}: ", 1)
        host_interfaces = []
        
        for idx in range(n_if_host):
            eth = f"eth{idx}"
            print(f"  Configurazione {eth}:")
            # richiedi IP controllando duplicati
            while True:
                ip = valida_ip_cidr(f"    IP per {eth} (es. 192.168.10.{h}/24): ")
                ip_only = ip.split('/')[0]
                if ip_only in used_ips:
                    print(f"❌ Errore: l'IP {ip_only} è già stato assegnato. Scegli un altro IP.")
                    continue
                used_ips.add(ip_only)
                break
            lan = input_lan(f"    LAN associata a {eth} (es. A): ")
            host_interfaces.append({"name": eth, "ip": ip, "lan": lan})
            lab_conf_lines.append(f"{hname}[{idx}]={lan}")

        # Gateway (opzionale, o globale per l'host)
        # Se ha più interfacce, potrebbe avere più gateway o rotte specifiche.
        # Per semplicità manteniamo un gateway di default principale, o chiediamo rotte.
        # Qui chiediamo un gateway di default generico.
        gw = input(f"Gateway di default per {hname} (invio per nessuno): ").strip()
        gw_dev = ""
        if gw:
            # validazione base
            try:
                # accetta sia IP che IP/CIDR (prendiamo solo IP)
                gw = gw.split('/')[0]
                ipaddress.ip_address(gw)
                
                # Chiedi interfaccia
                gw_dev = input(f"Interfaccia per il gateway {gw} (default eth0): ").strip()
                if not gw_dev:
                    gw_dev = "eth0"
            except ValueError:
                print("⚠️ Gateway non valido, ignorato.")
                gw = ""

        # crea_host_file ora deve gestire multiple interfacce
        # Dobbiamo aggiornare crea_host_file o gestirlo qui inline?
        # crea_host_file è definita altrove, controlliamola.
        # Se crea_host_file accetta solo 1 IP/LAN, dobbiamo modificarla o non usarla.
        # Controlliamo crea_host_file.
        # Per ora assumiamo di doverla riscrivere o adattare.
        # Invece di chiamare crea_host_file(..., ip, gw, lan), scriviamo direttamente qui o chiamiamo una nuova funzione.
        
        # Scrittura file .startup
        startup_path = os.path.join(lab_path, f"{hname}.startup")
        with open(startup_path, 'w') as f:
            for iface in host_interfaces:
                f.write(f"ip address add {iface['ip']} dev {iface['name']}\n")
            if gw:
                f.write(f"ip route add default via {gw} dev {gw_dev}\n")

        hosts.append({"name": hname, "interfaces": host_interfaces, "gateway": gw})
        lab_conf_lines.append(f'{hname}[image]="kathara/base"')
        lab_conf_lines.append("")

    # WWW servers
    for w in range(1, n_www + 1):
        default_wname = f"www{w}"
        while True:
            wname_in = input(f"\nNome webserver (default {default_wname}): ").strip()
            wname = wname_in if wname_in else default_wname
            if ' ' in wname:
                print("Il nome non può contenere spazi.")
                continue
            if wname in used_names:
                print(f"Nome '{wname}' già usato. Scegli un altro.")
                continue
            break
        used_names.add(wname)
        print(f"--- Configurazione webserver {wname} ---")
        # richiedi IP controllando duplicati
        while True:
            ip = valida_ip_cidr(f"IP per {wname} (es. 10.10.{w}.1/24): ")
            ip_only = ip.split('/')[0]
            if ip_only in used_ips:
                print(f"❌ Errore: l'IP {ip_only} è già stato assegnato. Scegli un altro IP.")
                continue
            used_ips.add(ip_only)
            break
        gw = valida_ip_cidr(f"Gateway per {wname} (es. 10.10.{w}.254/24): ")
        lan = input_lan("LAN associata (es. Z): ")
        crea_www_file(lab_path, wname, ip, gw, lan)
        wwws.append({"name": wname, "ip": ip, "gateway": gw, "lan": lan})
        lab_conf_lines.append(f"{wname}[0]={lan}")
        lab_conf_lines.append(f'{wname}[image]="kathara/base"')
        lab_conf_lines.append("")

    # DNS hosts
    dns_root_ip = None
    for d in range(1, n_dns + 1):
        # Il primo host DNS deve essere per forza il server root
        if d == 1:
            dname = 'root'
            print(f"\nNome host DNS forzato per il primo DNS: '{dname}' (server root/master)")
            if ' ' in dname:
                # sanity check (non dovrebbe mai accadere)
                raise ValueError("Nome 'root' non valido")
            if dname in used_names:
                # se 'root' è già usato, chiediamo all'utente di inserire un altro nome
                print(f"Nome '{dname}' è già stato usato. Inserisci un nome alternativo per il primo DNS.")
                while True:
                    dname_in = input(f"\nNome host DNS (il primo deve essere root, ma '{dname}' non è disponibile): ").strip()
                    dname = dname_in if dname_in else dname
                    if ' ' in dname:
                        print("Il nome non può contenere spazi.")
                        continue
                    if dname in used_names:
                        print(f"Nome '{dname}' già usato. Scegli un altro.")
                        continue
                    break
        else:
            default_dname = f"dns{d}"
            while True:
                dname_in = input(f"\nNome host DNS (default {default_dname}): ").strip()
                dname = dname_in if dname_in else default_dname
                if ' ' in dname:
                    print("Il nome non può contenere spazi.")
                    continue
                if dname in used_names:
                    print(f"Nome '{dname}' già usato. Scegli un altro.")
                    continue
                break
        used_names.add(dname)
        print(f"--- Configurazione host DNS {dname} ---")
        # richiedi IP controllando duplicati
        while True:
            ip = valida_ip_cidr(f"IP per {dname} (es. 10.20.{d}.10/24): ")
            ip_only = ip.split('/')[0]
            if ip_only in used_ips:
                print(f"❌ Errore: l'IP {ip_only} è già stato assegnato. Scegli un altro IP.")
                continue
            used_ips.add(ip_only)
            break
        gw = valida_ip_cidr(f"Gateway per {dname} (es. 10.20.{d}.1/24): ")
        lan = input_lan("LAN associata (es. A): ")

        # Forwarders: opzionale (non richiesto qui, rimane None per default)
        forwarders = None

        # Zones (opzionali) - GUIDA: qui puoi creare zone authoritative su questo host.
        # Se l'host è 'root' non chiediamo di aggiungere zone authoritative (regola speciale per root)
        zones = None
        if dname != 'root':
            add_z = input("Aggiungere zone authoritative su questo host? (s/N): ").strip().lower()
            if add_z.startswith('s'):
                zones = {}
                nz = input_int("Numero di zone da creare su questo host: ", 0)
                for zi in range(1, nz+1):
                    zname = input_non_vuoto(f"  Nome zona {zi} (nome Host DNS in questione): ")
                    print(f"  Creazione zona '{zname}': inserisci i record desiderati.")
                    records = {}
                    nr = input_int(f"  Quanti record vuoi aggiungere nella zona '{zname}'? ", 0)
                    for r in range(1, nr+1):
                        print(f"    Record {r} (formati supportati: A, NS, CNAME, DELEGATION)")
                        rtype = input_non_vuoto("      Tipo record (A/NS/CNAME/DELEGATION): ").strip().upper()
                        if rtype == 'A':
                            h = input_non_vuoto("      Nome host (es. dns.it): ")
                            rip = valida_ip_cidr("      IP (es. 10.20.1.10/24): ")
                            records[h] = rip.split('/')[0]
                        elif rtype == 'NS':
                            h = input_non_vuoto("      Nome delegato (es. @ per la zona): ")
                            nsname = input_non_vuoto("      Nome del nameserver (es. dns.example.local.): ")
                            glue = input("      Glue IP per il nameserver (opzionale, invio per saltare): ").strip()
                            rec = {'type': 'NS', 'ns': nsname}
                            if glue:
                                rec['glue'] = glue
                            records[h] = rec
                        elif rtype == 'CNAME':
                            h = input_non_vuoto("      Nome alias (es. ftp): ")
                            target = input_non_vuoto("      Target canonical name (es. www): ")
                            records[h] = {'type': 'CNAME', 'target': target}
                        elif rtype == 'DELEGATION':
                            child = input_non_vuoto("      Nome sottodominio da delegare (es. sub): ")
                            nsname = input_non_vuoto("      Nameserver per la delega (es. ns.sub.example.local.): ")
                            ns_ip = input("      Glue IP per il nameserver (opzionale): ").strip()
                            rec = {'type': 'DELEGATION', 'zone': child, 'ns': nsname}
                            if ns_ip:
                                rec['ns_ip'] = ns_ip
                            # store delegation using the child label as key
                            records[child] = rec
                        else:
                            print("      Tipo record non riconosciuto, salto questo record.")
                    zones[zname] = records

        # chiedi se questo host deve essere root/master/hint
        # Se il nome host è 'root' forziamo il comportamento di root: sempre master
        if dname == 'root':
            root_type = 'master'
            # registra subito l'IP come IP del root master per gli hint successivi
            dns_root_ip = ip.split('/')[0]
            # per il server root non chiediamo allow-recursion né dnssec: usiamo valori di default
            allow_recursion = None
            dnssec_validation = False
        else:
            root_choice = input("Tipo server per '.' ? (m=master / h=hint / n=nessuno) [h]: ").strip().lower()
            if root_choice == 'm':
                root_type = 'master'
            elif root_choice == 'n':
                root_type = None
            else:
                root_type = 'hint'

            # se questo host è master, registrane l'IP per i successivi hint
            if root_type == 'master':
                dns_root_ip = ip.split('/')[0]

            # Opzioni named.conf.options: allow-recursion e dnssec-validation
            allow_recursion = None
            ar = input("Vuoi aggiungere la riga 'allow-recursion { any; } ?  (s/N): ").strip().lower()
            if ar.startswith('s'):
                allow_recursion = 'any'
            dnssec_validation = False
            dv = input("Vuoi disabilitare DNSSEC validation ?  (s/N): ").strip().lower()
            if dv.startswith('s'):
                dnssec_validation = True

        # crea files per DNS host; se è hint e abbiamo il root registrato, passalo
        try:
            if root_type != 'master' and dns_root_ip:
                crea_dns_host(lab_path, dname, ip, gw, lan, forwarders=forwarders, zones=zones, root_type=root_type, root_server_ip=dns_root_ip, allow_recursion=allow_recursion, dnssec_validation=dnssec_validation)
            else:
                crea_dns_host(lab_path, dname, ip, gw, lan, forwarders=forwarders, zones=zones, root_type=root_type, allow_recursion=allow_recursion, dnssec_validation=dnssec_validation)
        except Exception:
            pass
        hosts.append({"name": dname, "ip": ip, "gateway": gw, "lan": lan, "dns": True, "forwarders": forwarders, "zones": zones, 'root_type': root_type, 'allow_recursion': allow_recursion, 'dnssec_validation': dnssec_validation})
        lab_conf_lines.append(f"{dname}[0]={lan}")
        lab_conf_lines.append(f'{dname}[image]="kathara/base"')
        lab_conf_lines.append("")

    # write lab.conf
    # Se è presente almeno un host DNS, chiedi se uno di loro deve essere
    # DNS resolver per gli altri dispositivi (host/pc/www).
    try:
        has_dns = any(isinstance(h, dict) and h.get('dns') for h in hosts)
    except Exception:
        has_dns = False

    if has_dns:
        ask_resolver = input("\nVuoi che uno dei DNS creati sia resolver per gli altri dispositivi? (s/N): ").strip().lower()
        if ask_resolver.startswith('s'):
            # elenca i DNS disponibili con IP
            dns_list = [h for h in hosts if isinstance(h, dict) and h.get('dns')]
            items = [f"{dh.get('name')} - { (str(dh.get('ip') or '')).split('/')[0] }" for dh in dns_list]
            print_menu('Seleziona il DNS resolver dalla lista seguente:', items)
            # scegli per nome o numero
            resolver_choice = input("Inserisci il nome o il numero del DNS resolver scelto: ").strip()
            resolver_ip = None
            # check by number
            if resolver_choice.isdigit():
                idx = int(resolver_choice) - 1
                if 0 <= idx < len(dns_list):
                    resolver_ip = str(dns_list[idx].get('ip') or '').split('/')[0]
            else:
                for dh in dns_list:
                    if dh.get('name') == resolver_choice:
                        resolver_ip = str(dh.get('ip') or '').split('/')[0]
                        break

            if resolver_ip:
                # crea etc/resolv.conf in tutte le cartelle dei dispositivi host/pc/www/dns
                targets = []
                try:
                    targets.extend([h.get('name') for h in hosts if isinstance(h, dict) and h.get('name')])
                except Exception:
                    pass
                try:
                    targets.extend([w.get('name') for w in wwws if isinstance(w, dict) and w.get('name')])
                except Exception:
                    pass

                for dev in set(targets):
                    etc_dir = os.path.join(lab_path, dev, 'etc')
                    try:
                        os.makedirs(etc_dir, exist_ok=True)
                        resolver_path = os.path.join(etc_dir, 'resolv.conf')
                        with open(resolver_path, 'w') as rf:
                            rf.write(f"nameserver {resolver_ip}\n")
                    except Exception:
                        # ignoriamo errori di scrittura per non bloccare la creazione
                        pass
                print(f"\nResolver impostato: nameserver {resolver_ip} -> creati file resolv.conf nelle cartelle dei dispositivi.")
            else:
                print("Scelta del resolver non valida o IP non trovato: nessun resolver impostato.")

    with open(os.path.join(lab_path, "lab.conf"), "w") as f:
        f.write("\n".join(lab_conf_lines).strip() + "\n")

    # Auto-generate BGP neighbors for routers sharing LANs
    auto_generate_bgp_neighbors(lab_path, routers)

    # Esporta il laboratorio in formato XML per poterlo riusare come input
    try:
        export_lab_to_xml(lab_name, lab_path, routers, hosts, wwws)
    except Exception as e:
        print('Attenzione: esportazione XML fallita:', e)

    print(f"\n✅ Lab '{lab_name}' creato in: {lab_path}")
    # Menu in italiano per implementare richieste aggiuntive
    try:
        menu_post_creazione(lab_path, routers)
    except Exception as e:
        print('Errore durante il menu post-creazione:', e)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Graceful shutdown on Ctrl+C: clear terminal and exit without traceback
        try:
            print()  # ensure newline after ^C
        except Exception:
            pass
        try:
            if os.name == 'nt':
                os.system('cls')
            else:
                os.system('clear')
        except Exception:
            pass
        try:
            sys.exit(0)
        except Exception:
            # fallback
            os._exit(0)

