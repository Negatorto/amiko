"""
AMIKO - L7 Host Firewall & Fail2Ban Intrusion Prevention (NIS2 Art. 21(2)(e)(j))

Two coordinated enforcement levels:
  1. Application & Socket layer (zero root): a WSGI middleware evaluates every HTTP request
     and WebSocket handshake, before Flask/Socket.IO routing, against the Fail2Ban ban list
     and the ordered INPUT rules. Outbound SSH/SFTP/FTP connections towards managed servers
     are evaluated against the OUTPUT rules (egress control).
  2. Kernel sync (optional): when AMIKO holds CAP_NET_ADMIN, banned IPs are mirrored into
     nftables (iptables fallback). Without privileges it degrades silently to level 1.

L7 semantics of the iptables-like rule fields:
  - protocol: AMIKO traffic is TCP; UDP/ICMP rules never match at L7 (kernel-level only).
  - port:     INPUT = AMIKO listening port, OUTPUT = target server port.
  - interface: resolved from the TCP peer through the host routing table (`ip -j`).
  - state:    ESTABLISHED = request carrying a valid authenticated session, NEW otherwise.

State is persisted encrypted (Fernet, same key as the settings vault) in
data/firewall.json.enc and hot-reloaded when changed externally, e.g. by the break-glass
CLI utils/reset_firewall.py.
"""

import os
import re
import copy
import json
import time
import uuid
import shutil
import socket
import ipaddress
import threading
import subprocess
from functools import lru_cache

from cryptography.fernet import Fernet

from core.audit import log_audit_event
from core.client_ip import parse_ip, is_trusted_proxy, client_ip_from_environ
from core.config import settings
from utils.settings_manager import DATA_DIR, KEY_FILE

FIREWALL_FILE = os.path.join(DATA_DIR, "firewall.json.enc")

CHAINS = ("INPUT", "OUTPUT")
TARGETS = ("ACCEPT", "DROP", "REJECT")
POLICIES = ("ACCEPT", "DROP")
RULE_PROTOCOLS = ("ALL", "TCP", "UDP", "ICMP")
SERVICE_PROTOCOLS = ("TCP", "UDP", "ICMP")
STATES = ("ANY", "NEW", "ESTABLISHED")
JAIL_NAMES = ("amiko-login", "amiko-2fa", "amiko-sec", "recidive")
ANY_NET = "0.0.0.0/0"

_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")

BLOCKED_AUDIT_INTERVAL = 60      # seconds between FIREWALL_REQUEST_BLOCKED records per IP
SWEEP_INTERVAL = 30              # seconds between expired-ban sweeps
MAX_TRACKED_FAILURES = 50000     # memory cap for failure counters (IPv6 address spraying)


def amiko_listen_port() -> int:
    try:
        return int(os.environ.get("AMIKO_PORT", "5423"))
    except ValueError:
        return 5423


def default_config() -> dict:
    return {
        "version": 1,
        "firewall": {
            "enabled": False,
            "input_policy": "ACCEPT",
            "output_policy": "ACCEPT",
            "rules": []
        },
        "services": [
            {"id": "svc-amiko-web", "name": "AMIKO-WEB", "protocol": "TCP",
             "ports": str(amiko_listen_port()), "description": "AMIKO web console"},
            {"id": "svc-ssh", "name": "SSH", "protocol": "TCP", "ports": "22", "description": "Secure Shell / SFTP"},
            {"id": "svc-http", "name": "HTTP", "protocol": "TCP", "ports": "80", "description": "Web (plain)"},
            {"id": "svc-https", "name": "HTTPS", "protocol": "TCP", "ports": "443", "description": "Web (TLS)"},
            {"id": "svc-ping", "name": "PING", "protocol": "ICMP", "ports": "8", "description": "ICMP echo request"}
        ],
        "fail2ban": {
            "enabled": True,
            "ignore_ips": [],
            "jails": {
                "amiko-login": {"enabled": True, "max_retry": 5, "find_time": 10, "ban_time": 30,
                                "description": "Password brute-force on the login page"},
                "amiko-2fa": {"enabled": True, "max_retry": 5, "find_time": 10, "ban_time": 60,
                              "description": "TOTP / recovery code guessing"},
                "amiko-sec": {"enabled": True, "max_retry": 10, "find_time": 10, "ban_time": 60,
                              "description": "Probing of reserved endpoints, CSRF/CORS violations"},
                "recidive": {"enabled": True, "max_retry": 3, "find_time": 1440, "ban_time": 10080,
                             "description": "Repeat offenders banned multiple times by other jails"}
            }
        },
        "bans": {},
        "ban_history": {},
        "kernel_sync": {"enabled": False}
    }


# ── Matching helpers ─────────────────────────────────────────────────────────

def _is_any_net(spec) -> bool:
    return str(spec or "").strip().lower() in ("", "any", "*", "0.0.0.0/0", "::/0")


@lru_cache(maxsize=512)
def _networks(spec: str):
    return tuple(ipaddress.ip_network(p.strip(), strict=False) for p in spec.split(",") if p.strip())


def _ip_in_spec(ip, spec) -> bool:
    if _is_any_net(spec):
        return True
    if ip is None:
        return False
    return any(ip.version == n.version and ip in n for n in _networks(spec))


@lru_cache(maxsize=512)
def _port_ranges(spec: str):
    ranges = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            ranges.append((int(lo), int(hi)))
        else:
            ranges.append((int(part), int(part)))
    return tuple(ranges)


def _port_in_spec(port, spec) -> bool:
    if not str(spec or "").strip():
        return True
    if port is None:
        return False
    return any(lo <= int(port) <= hi for lo, hi in _port_ranges(spec))


def normalize_net_spec(value, field="address") -> str:
    """Validate a comma-separated list of IPs/CIDRs and return its normalized form."""
    if _is_any_net(value):
        return ANY_NET
    parts = []
    for raw in str(value).split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            parts.append(str(ipaddress.ip_network(raw, strict=False)))
        except ValueError:
            raise ValueError(f"Invalid {field}: '{raw}' is not a valid IP address or CIDR subnet.")
    if not parts:
        return ANY_NET
    return ",".join(parts)


def normalize_port_spec(value, protocol="TCP") -> str:
    """Validate '22', '8000-8100', '22,80,443' (TCP/UDP) or an ICMP type '8' (empty = any)."""
    spec = str(value or "").replace(" ", "")
    if not spec:
        return ""
    upper = 255 if protocol == "ICMP" else 65535
    lower = 0 if protocol == "ICMP" else 1
    try:
        for lo, hi in _port_ranges(spec):
            if not (lower <= lo <= upper and lower <= hi <= upper and lo <= hi):
                raise ValueError
    except ValueError:
        what = "ICMP type" if protocol == "ICMP" else "port/range"
        raise ValueError(f"Invalid {what} '{value}' (allowed {lower}-{upper}, e.g. 22, 8000-8100, 22,443).")
    return spec


# ── Host network introspection (interfaces & routing) ────────────────────────

class HostNetInfo:
    """Cached view of local interfaces/addresses, used to map peers to interfaces at L7."""

    TTL = 60

    def __init__(self):
        self._lock = threading.Lock()
        self._expires = 0
        self._ifaces = {}          # name -> [IPv4Interface | IPv6Interface]
        self._default_iface = None

    @staticmethod
    def _run_json(cmd):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=True).stdout
            return json.loads(out or "[]")
        except Exception:
            return []

    def _refresh(self):
        ifaces = {}
        for entry in self._run_json(["ip", "-j", "addr", "show"]):
            name = entry.get("ifname")
            if not name:
                continue
            addrs = []
            for a in entry.get("addr_info", []):
                try:
                    addrs.append(ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}"))
                except (KeyError, ValueError):
                    continue
            ifaces[name] = addrs
        if not ifaces:
            try:
                ifaces = {name: [] for _, name in socket.if_nameindex()}
            except OSError:
                ifaces = {}
        routes = self._run_json(["ip", "-j", "route", "show", "default"])
        self._ifaces = ifaces
        self._default_iface = routes[0].get("dev") if routes else None
        self._expires = time.time() + self.TTL

    def _snapshot(self):
        with self._lock:
            if time.time() >= self._expires:
                self._refresh()
            return self._ifaces, self._default_iface

    def interface_names(self):
        return sorted(self._snapshot()[0].keys())

    def local_addresses(self, iface=None):
        ifaces, _ = self._snapshot()
        selected = [ifaces.get(iface, [])] if iface else ifaces.values()
        return [a.ip for addrs in selected for a in addrs]

    def resolve_interface(self, ip):
        """Interface through which traffic from/to `ip` flows (on-link subnet, else default route)."""
        if ip is None:
            return None
        if ip.is_loopback:
            return "lo"
        ifaces, default_iface = self._snapshot()
        best, best_prefix = None, -1
        for name, addrs in ifaces.items():
            for a in addrs:
                if a.ip == ip:
                    return "lo"
                if a.version == ip.version and ip in a.network and a.network.prefixlen > best_prefix:
                    best, best_prefix = name, a.network.prefixlen
        return best or default_iface


host_net = HostNetInfo()


# ── Kernel sync (nftables / iptables) ────────────────────────────────────────

def _has_net_admin() -> bool:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return True
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    return bool(int(line.split()[1], 16) & (1 << 12))  # CAP_NET_ADMIN
    except OSError:
        pass
    return False


class KernelSync:
    """Mirrors Fail2Ban bans into the Linux kernel packet filter (best effort, never raises)."""

    NFT_TABLE = "amiko"
    IPT_CHAIN = "AMIKO-BANS"

    def __init__(self):
        self.backend = None
        self.error = ""
        self._detected = False

    def detect(self):
        if self._detected:
            return self.backend
        self._detected = True
        if not _has_net_admin():
            self.error = "AMIKO is not running with CAP_NET_ADMIN (root). Level 1 (L7) protection only."
        elif shutil.which("nft"):
            self.backend = "nft"
        elif shutil.which("iptables"):
            self.backend = "iptables"
        else:
            self.error = "Neither nft nor iptables was found on this host."
        return self.backend

    def status(self) -> dict:
        backend = self.detect()
        return {"available": backend is not None, "backend": backend, "error": self.error}

    @staticmethod
    def _run(cmd, stdin=None) -> bool:
        try:
            res = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=10)
            return res.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _nft_element(ip, seconds):
        return f"{ip} timeout {int(seconds)}s" if seconds else ip

    def resync(self, bans: dict):
        """Atomically rebuild the kernel ban set from the current ban table."""
        backend = self.detect()
        now = time.time()
        entries = []
        for ip, ban in bans.items():
            ip_obj = parse_ip(ip)
            if ip_obj is None:
                continue
            remaining = None
            if ban.get("expires_at"):
                remaining = int(ban["expires_at"] - now)
                if remaining <= 0:
                    continue
            entries.append((ip_obj, remaining))

        if backend == "nft":
            v4 = [self._nft_element(ip, s) for ip, s in entries if ip.version == 4]
            v6 = [self._nft_element(ip, s) for ip, s in entries if ip.version == 6]
            script = [
                f"add table inet {self.NFT_TABLE}",
                f"delete table inet {self.NFT_TABLE}",
                f"table inet {self.NFT_TABLE} {{",
                "  set banned4 { type ipv4_addr; flags timeout; }",
                "  set banned6 { type ipv6_addr; flags timeout; }",
                "  chain input { type filter hook input priority -10; policy accept;",
                "    ip saddr @banned4 drop",
                "    ip6 saddr @banned6 drop",
                "  }",
                "}"
            ]
            if v4:
                script.append(f"add element inet {self.NFT_TABLE} banned4 {{ {', '.join(v4)} }}")
            if v6:
                script.append(f"add element inet {self.NFT_TABLE} banned6 {{ {', '.join(v6)} }}")
            self._run(["nft", "-f", "-"], stdin="\n".join(script) + "\n")
        elif backend == "iptables":
            for binary, version in (("iptables", 4), ("ip6tables", 6)):
                if not shutil.which(binary):
                    continue
                self._run([binary, "-w", "-N", self.IPT_CHAIN])
                self._run([binary, "-w", "-F", self.IPT_CHAIN])
                if not self._run([binary, "-w", "-C", "INPUT", "-j", self.IPT_CHAIN]):
                    self._run([binary, "-w", "-I", "INPUT", "1", "-j", self.IPT_CHAIN])
                for ip, _ in entries:
                    if ip.version == version:
                        self._run([binary, "-w", "-A", self.IPT_CHAIN, "-s", str(ip), "-j", "DROP"])

    def add(self, ip, seconds=None):
        ip_obj = parse_ip(ip)
        backend = self.detect()
        if ip_obj is None or backend is None:
            return
        if backend == "nft":
            set_name = "banned4" if ip_obj.version == 4 else "banned6"
            self._run(["nft", "add", "element", "inet", self.NFT_TABLE, set_name,
                       "{", self._nft_element(ip_obj, seconds), "}"])
        else:
            binary = "iptables" if ip_obj.version == 4 else "ip6tables"
            rule = [self.IPT_CHAIN, "-s", str(ip_obj), "-j", "DROP"]
            if not self._run([binary, "-w", "-C", *rule]):
                self._run([binary, "-w", "-I", *rule])

    def remove(self, ip):
        ip_obj = parse_ip(ip)
        backend = self.detect()
        if ip_obj is None or backend is None:
            return
        if backend == "nft":
            set_name = "banned4" if ip_obj.version == 4 else "banned6"
            self._run(["nft", "delete", "element", "inet", self.NFT_TABLE, set_name, "{", str(ip_obj), "}"])
        else:
            binary = "iptables" if ip_obj.version == 4 else "ip6tables"
            while self._run([binary, "-w", "-D", self.IPT_CHAIN, "-s", str(ip_obj), "-j", "DROP"]):
                pass

    def teardown(self):
        backend = self.detect()
        if backend == "nft":
            self._run(["nft", "delete", "table", "inet", self.NFT_TABLE])
        elif backend == "iptables":
            for binary in ("iptables", "ip6tables"):
                if shutil.which(binary):
                    self._run([binary, "-w", "-D", "INPUT", "-j", self.IPT_CHAIN])
                    self._run([binary, "-w", "-F", self.IPT_CHAIN])
                    self._run([binary, "-w", "-X", self.IPT_CHAIN])


# ── Firewall & Fail2Ban manager ──────────────────────────────────────────────

class FirewallManager:
    """Thread-safe, persistent firewall rule engine and Fail2Ban jail manager."""

    def __init__(self, path=FIREWALL_FILE):
        self._path = path
        self._lock = threading.RLock()
        self._fernet = settings.get_fernet()
        self._file_sig = None
        self._last_stat = 0.0
        self._config = self._load()
        self._failures = {}        # (jail, ip) -> [timestamps]
        self._blocked_audit = {}   # ip -> [last_logged_ts, suppressed_count]
        self._kernel_dirty = True
        self.kernel = KernelSync()
        self._sweeper_started = False
        settings.register_unseal_listener(self._on_vault_unsealed)
        settings.register_rotate_listener(self._on_key_rotated)

    def _on_vault_unsealed(self, fernet):
        """Called when the AMIKO vault is unsealed in RAM; loads encrypted firewall config and merges early bans."""
        with self._lock:
            early_bans = dict(self._config.get("bans") or {})
            early_history = dict(self._config.get("ban_history") or {})
            self._fernet = fernet
            self._config = self._load()
            merged = False
            for ip, b_info in early_bans.items():
                if ip not in self._config["bans"]:
                    self._config["bans"][ip] = b_info
                    merged = True
            for ip, h_list in early_history.items():
                if ip not in self._config["ban_history"]:
                    self._config["ban_history"][ip] = h_list
                    merged = True
            if merged:
                self._save()
            self._kernel_dirty = True

    def _on_key_rotated(self, old_fernet, new_fernet):
        """Called when the AMIKO vault DEK is rotated; re-encrypts firewall.json.enc with the new key."""
        with self._lock:
            self._fernet = new_fernet
            if os.path.exists(self._path):
                self._save()

    # --- Persistence ---

    def _stat_sig(self):
        try:
            st = os.stat(self._path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _load(self) -> dict:
        config = default_config()
        if self._fernet is None:
            self._file_sig = None
            return config
        self._file_sig = self._stat_sig()
        if self._file_sig is None:
            return config
        try:
            with open(self._path, "rb") as f:
                stored = json.loads(self._fernet.decrypt(f.read()).decode("utf-8"))
        except Exception as e:
            corrupt = f"{self._path}.corrupt-{int(time.time())}"
            print(f"[AMIKO Firewall] Cannot read {self._path} ({e}); moved to {corrupt}, using defaults.")
            try:
                os.replace(self._path, corrupt)
            except OSError:
                pass
            self._file_sig = None
            return config

        for key in ("version", "services", "bans", "ban_history"):
            if key in stored:
                config[key] = stored[key]
        for key in ("firewall", "kernel_sync"):
            config[key].update(stored.get(key) or {})
        f2b = stored.get("fail2ban") or {}
        config["fail2ban"]["enabled"] = f2b.get("enabled", True)
        config["fail2ban"]["ignore_ips"] = f2b.get("ignore_ips", [])
        for name, params in (f2b.get("jails") or {}).items():
            if name in config["fail2ban"]["jails"]:
                config["fail2ban"]["jails"][name].update(params)
        return config

    def _save(self):
        if self._fernet is None:
            self._fernet = settings.get_fernet()
        if self._fernet is None:
            return
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        token = self._fernet.encrypt(json.dumps(self._config).encode("utf-8"))
        tmp = f"{self._path}.tmp"
        with open(tmp, "wb") as f:
            f.write(token)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)
        self._file_sig = self._stat_sig()

    def _maybe_reload(self, force=False):
        """Pick up changes written by another process (break-glass CLI). Caller holds the lock."""
        if self._fernet is None:
            self._fernet = settings.get_fernet()
            if self._fernet is None:
                return
        now = time.time()
        if not force and now - self._last_stat < 1.0:
            return
        self._last_stat = now
        sig = self._stat_sig()
        if sig != self._file_sig:
            self._config = self._load()
            self._kernel_dirty = True

    # --- Read API ---

    def get_state(self) -> dict:
        with self._lock:
            self._maybe_reload(force=True)
            state = copy.deepcopy({k: v for k, v in self._config.items() if k not in ("bans", "ban_history")})
            state["bans"] = self._ban_list_locked()
        state["kernel_sync"].update(self.kernel.status())
        state["interfaces"] = host_net.interface_names()
        state["listen_port"] = amiko_listen_port()
        return state

    def list_bans(self) -> list:
        with self._lock:
            self._maybe_reload(force=True)
            return self._ban_list_locked()

    def _ban_list_locked(self) -> list:
        now = time.time()
        bans = []
        for ip, ban in self._config["bans"].items():
            if ban.get("expires_at") and ban["expires_at"] <= now:
                continue  # expired: removed and audited by the sweeper
            item = dict(ban)
            item["ip"] = ip
            item["remaining_seconds"] = max(0, int(ban["expires_at"] - now)) if ban.get("expires_at") else None
            bans.append(item)
        bans.sort(key=lambda b: b.get("banned_at", 0), reverse=True)
        return bans

    # --- Rule evaluation ---

    def _evaluate(self, config, chain, src=None, peer=None, dst=None, port=None, protocol="TCP", state="NEW"):
        """Return (target, rule_id) for a packet/request description. First matching rule wins."""
        fw = config["firewall"]
        services = {s["name"].lower(): s for s in config["services"]}
        src_ip, dst_ip = parse_ip(src), parse_ip(dst)
        iface_cache = []

        def iface():
            if not iface_cache:
                ref = (parse_ip(peer) or src_ip) if chain == "INPUT" else dst_ip
                iface_cache.append(host_net.resolve_interface(ref))
            return iface_cache[0]

        def local_side_matches(spec):
            if _is_any_net(spec):
                return True
            return any(_ip_in_spec(a, spec) for a in host_net.local_addresses(iface()))

        for rule in fw["rules"]:
            if not rule.get("enabled", True) or rule["chain"] != chain:
                continue
            if rule["protocol"] not in ("ALL", protocol):
                continue
            svc = rule.get("service", "ANY")
            if svc != "ANY":
                service = services.get(svc.lower())
                if service:
                    if service["protocol"] != protocol or not _port_in_spec(port, service["ports"]):
                        continue
                elif protocol not in ("TCP", "UDP") or not _port_in_spec(port, svc):
                    continue
            if rule.get("state", "ANY") not in ("ANY", state):
                continue
            if rule.get("interface", "ANY") != "ANY" and rule["interface"] != iface():
                continue
            if chain == "INPUT":
                if not _ip_in_spec(src_ip, rule["source"]) or not local_side_matches(rule["destination"]):
                    continue
            else:
                if not local_side_matches(rule["source"]) or not _ip_in_spec(dst_ip, rule["destination"]):
                    continue
            return rule["target"], rule["id"]

        return fw["input_policy" if chain == "INPUT" else "output_policy"], None

    def _lockout_violation(self, config, operator_ctx):
        """Anti-lockout safeguard: loopback and the operator's own connection must stay reachable."""
        if not config["firewall"]["enabled"]:
            return None
        port = amiko_listen_port()
        probes = [("127.0.0.1", "127.0.0.1", "NEW"), ("::1", "::1", "NEW")]
        op = operator_ctx or {}
        if op.get("client_ip") and parse_ip(op["client_ip"]):
            for state in ("NEW", "ESTABLISHED"):
                probes.append((op["client_ip"], op.get("peer_ip") or op["client_ip"], state))
        for src, peer, state in probes:
            target, rule_id = self._evaluate(config, "INPUT", src=src, peer=peer, port=port, state=state)
            if parse_ip(src).is_loopback and rule_id is None:
                continue  # loopback is implicitly accepted at runtime; only explicit rules can isolate it
            if target != "ACCEPT":
                cause = f"rule {rule_id}" if rule_id else "the default INPUT policy"
                who = "the loopback interface" if parse_ip(src).is_loopback else f"your current connection ({src})"
                return (f"Anti-lockout safeguard: this change would {target} {who} "
                        f"for {state} traffic via {cause}. Add an ACCEPT rule above it first.")
        return None

    def _commit_firewall_change(self, mutate, operator_ctx):
        """Apply `mutate` to a copy of the config, enforce the anti-lockout check, then persist."""
        with self._lock:
            self._maybe_reload(force=True)
            candidate = copy.deepcopy(self._config)
            result = mutate(candidate)
            violation = self._lockout_violation(candidate, operator_ctx)
            if violation:
                raise PermissionError(violation)
            self._config = candidate
            self._save()
            return result

    def check_inbound(self, environ, session_state=None):
        """
        Evaluate an inbound HTTP/WebSocket request. Returns None when allowed, otherwise
        {'target': 'DROP'|'REJECT', 'reason': 'fail2ban'|'rule', 'rule_id', 'client_ip'}.
        `session_state` is a callable returning 'ESTABLISHED' or 'NEW' (evaluated lazily).
        """
        client = client_ip_from_environ(environ)
        ip = parse_ip(client)
        if ip is None or ip.is_loopback:
            return None
        with self._lock:
            self._maybe_reload()
            ban = self._active_ban_locked(str(ip))
            if ban:
                return {"target": "REJECT", "reason": "fail2ban", "rule_id": None,
                        "client_ip": str(ip), "jail": ban.get("jail")}
            config = self._config
            if not config["firewall"]["enabled"]:
                return None
            needs_state = any(r.get("state", "ANY") != "ANY" for r in config["firewall"]["rules"])
            state = session_state() if (needs_state and session_state) else "NEW"
            try:
                port = int(environ.get("SERVER_PORT") or amiko_listen_port())
            except ValueError:
                port = amiko_listen_port()
            target, rule_id = self._evaluate(config, "INPUT", src=str(ip), peer=environ.get("REMOTE_ADDR"),
                                             port=port, state=state)
        if target == "ACCEPT":
            return None
        return {"target": target, "reason": "rule", "rule_id": rule_id, "client_ip": str(ip)}

    def check_outbound(self, hostname, port):
        """Egress control for SSH/SFTP/FTP connections. Raises PermissionError when blocked."""
        with self._lock:
            self._maybe_reload()
            if not self._config["firewall"]["enabled"]:
                return
            config = copy.deepcopy(self._config)
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)}
        except socket.gaierror:
            return  # resolution failure surfaces in the connection attempt itself
        for addr in addresses:
            target, rule_id = self._evaluate(config, "OUTPUT", dst=addr, port=int(port), state="NEW")
            if target != "ACCEPT":
                log_audit_event(
                    event_type="FIREWALL_EGRESS_BLOCKED",
                    status="DENIED",
                    severity="WARNING",
                    target={"hostname": hostname, "port": int(port)},
                    details={"resolved_ip": addr, "rule_id": rule_id, "action": target}
                )
                raise PermissionError(
                    f"Outbound connection to {hostname} ({addr}:{port}) blocked by AMIKO firewall "
                    f"({'rule ' + rule_id if rule_id else 'OUTPUT default policy'})."
                )

    def audit_blocked_request(self, verdict, environ):
        """Audit blocked requests, rate-limited per IP so a flood cannot bloat the audit trail."""
        ip = verdict["client_ip"]
        now = time.time()
        with self._lock:
            entry = self._blocked_audit.get(ip)
            if entry and now - entry[0] < BLOCKED_AUDIT_INTERVAL:
                entry[1] += 1
                return
            suppressed = entry[1] if entry else 0
            self._blocked_audit[ip] = [now, 0]
        log_audit_event(
            event_type="FIREWALL_REQUEST_BLOCKED",
            status="DENIED",
            severity="WARNING",
            actor={"username": "anonymous", "role": "none", "source_ip": ip,
                   "user_agent": environ.get("HTTP_USER_AGENT", "")[:160]},
            target={"resource": environ.get("PATH_INFO", "")},
            details={"reason": verdict["reason"], "action": verdict["target"], "rule_id": verdict.get("rule_id"),
                     "jail": verdict.get("jail"), "method": environ.get("REQUEST_METHOD", ""),
                     "suppressed_since_last_record": suppressed}
        )

    # --- Firewall settings / rules / services mutations ---

    def update_settings(self, data, operator_ctx):
        def mutate(cfg):
            fw = cfg["firewall"]
            if "enabled" in data:
                fw["enabled"] = bool(data["enabled"])
            for key in ("input_policy", "output_policy"):
                if key in data:
                    val = str(data[key]).upper()
                    if val not in POLICIES:
                        raise ValueError(f"{key} must be one of {', '.join(POLICIES)}.")
                    fw[key] = val
            return {k: v for k, v in fw.items() if k != "rules"}
        return self._commit_firewall_change(mutate, operator_ctx)

    @staticmethod
    def _normalize_rule(data, services, base=None) -> dict:
        rule = dict(base or {
            "enabled": True, "chain": "INPUT", "interface": "ANY", "source": ANY_NET,
            "destination": ANY_NET, "protocol": "ALL", "service": "ANY", "state": "ANY",
            "target": "ACCEPT", "comment": ""
        })
        for key in rule:
            if key in data and key != "id":
                rule[key] = data[key]

        rule["enabled"] = bool(rule["enabled"])
        for key, allowed in (("chain", CHAINS), ("protocol", RULE_PROTOCOLS), ("state", STATES), ("target", TARGETS)):
            rule[key] = str(rule[key]).upper()
            if rule[key] not in allowed:
                raise ValueError(f"Invalid {key} '{rule[key]}'. Allowed: {', '.join(allowed)}.")

        iface = str(rule["interface"]).strip() or "ANY"
        if iface.upper() == "ANY":
            iface = "ANY"
        elif not _IFACE_RE.match(iface):
            raise ValueError(f"Invalid interface name '{iface}'.")
        rule["interface"] = iface

        rule["source"] = normalize_net_spec(rule["source"], "source address")
        rule["destination"] = normalize_net_spec(rule["destination"], "destination address")

        svc = str(rule["service"]).strip() or "ANY"
        names = {s["name"].lower(): s["name"] for s in services}
        if svc.upper() == "ANY":
            svc = "ANY"
        elif svc.lower() in names:
            svc = names[svc.lower()]
        else:
            if rule["protocol"] not in ("TCP", "UDP"):
                raise ValueError(f"Unknown service '{svc}'. Literal ports require protocol TCP or UDP.")
            svc = normalize_port_spec(svc, rule["protocol"])
        rule["service"] = svc

        rule["comment"] = str(rule.get("comment") or "")[:120]
        return rule

    def add_rule(self, data, operator_ctx):
        def mutate(cfg):
            rule = self._normalize_rule(data, cfg["services"])
            rule["id"] = "r-" + uuid.uuid4().hex[:8]
            rules = cfg["firewall"]["rules"]
            pos = data.get("position")
            if isinstance(pos, int) and 0 <= pos <= len(rules):
                rules.insert(pos, rule)
            else:
                rules.append(rule)
            return rule
        return self._commit_firewall_change(mutate, operator_ctx)

    def update_rule(self, rule_id, data, operator_ctx):
        def mutate(cfg):
            rules = cfg["firewall"]["rules"]
            for i, existing in enumerate(rules):
                if existing["id"] == rule_id:
                    updated = self._normalize_rule(data, cfg["services"], base=existing)
                    updated["id"] = rule_id
                    rules[i] = updated
                    return updated
            raise KeyError("Rule not found")
        return self._commit_firewall_change(mutate, operator_ctx)

    def delete_rule(self, rule_id, operator_ctx):
        def mutate(cfg):
            rules = cfg["firewall"]["rules"]
            match = next((r for r in rules if r["id"] == rule_id), None)
            if not match:
                raise KeyError("Rule not found")
            rules.remove(match)
            return match
        return self._commit_firewall_change(mutate, operator_ctx)

    def move_rule(self, rule_id, direction, operator_ctx):
        def mutate(cfg):
            rules = cfg["firewall"]["rules"]
            idx = next((i for i, r in enumerate(rules) if r["id"] == rule_id), None)
            if idx is None:
                raise KeyError("Rule not found")
            new_idx = idx - 1 if direction == "up" else idx + 1
            if 0 <= new_idx < len(rules):
                rules[idx], rules[new_idx] = rules[new_idx], rules[idx]
            return {"id": rule_id, "position": max(0, min(new_idx, len(rules) - 1))}
        return self._commit_firewall_change(mutate, operator_ctx)

    @staticmethod
    def _normalize_service(data, services, base=None) -> dict:
        svc = dict(base or {"name": "", "protocol": "TCP", "ports": "", "description": ""})
        for key in svc:
            if key in data and key != "id":
                svc[key] = data[key]
        svc["name"] = str(svc["name"]).strip()
        if not _SERVICE_NAME_RE.match(svc["name"]) or svc["name"].upper() == "ANY":
            raise ValueError("Service name must be 1-32 chars (letters, digits, '_', '-', '.') and not 'ANY'.")
        for other in services:
            if other["name"].lower() == svc["name"].lower() and other.get("id") != svc.get("id"):
                raise ValueError(f"A service named '{svc['name']}' already exists.")
        svc["protocol"] = str(svc["protocol"]).upper()
        if svc["protocol"] not in SERVICE_PROTOCOLS:
            raise ValueError(f"Service protocol must be one of {', '.join(SERVICE_PROTOCOLS)}.")
        svc["ports"] = normalize_port_spec(svc["ports"], svc["protocol"])
        if not svc["ports"] and svc["protocol"] != "ICMP":
            raise ValueError("TCP/UDP services require a port or port range.")
        svc["description"] = str(svc.get("description") or "")[:120]
        return svc

    def add_service(self, data, operator_ctx):
        def mutate(cfg):
            svc = self._normalize_service(data, cfg["services"])
            svc["id"] = "svc-" + uuid.uuid4().hex[:8]
            cfg["services"].append(svc)
            return svc
        return self._commit_firewall_change(mutate, operator_ctx)

    def update_service(self, service_id, data, operator_ctx):
        def mutate(cfg):
            for i, existing in enumerate(cfg["services"]):
                if existing["id"] == service_id:
                    updated = self._normalize_service(data, cfg["services"], base=existing)
                    updated["id"] = service_id
                    if updated["name"] != existing["name"]:
                        for rule in cfg["firewall"]["rules"]:
                            if rule.get("service") == existing["name"]:
                                rule["service"] = updated["name"]
                    cfg["services"][i] = updated
                    return updated
            raise KeyError("Service not found")
        return self._commit_firewall_change(mutate, operator_ctx)

    def delete_service(self, service_id, operator_ctx):
        def mutate(cfg):
            svc = next((s for s in cfg["services"] if s["id"] == service_id), None)
            if not svc:
                raise KeyError("Service not found")
            used = [r["id"] for r in cfg["firewall"]["rules"] if r.get("service") == svc["name"]]
            if used:
                raise ValueError(f"Service '{svc['name']}' is referenced by rules: {', '.join(used)}.")
            cfg["services"].remove(svc)
            return svc
        return self._commit_firewall_change(mutate, operator_ctx)

    # --- Fail2Ban configuration ---

    def update_fail2ban(self, data, operator_ctx=None):
        with self._lock:
            self._maybe_reload(force=True)
            f2b = self._config["fail2ban"]
            if "enabled" in data:
                f2b["enabled"] = bool(data["enabled"])
            if "ignore_ips" in data:
                raw = data["ignore_ips"]
                items = raw if isinstance(raw, list) else str(raw).replace("\n", ",").split(",")
                cleaned = []
                for item in items:
                    item = str(item).strip()
                    if item:
                        cleaned.append(normalize_net_spec(item, "whitelist entry"))
                f2b["ignore_ips"] = sorted(set(cleaned), key=cleaned.index)
            self._save()
            return {"enabled": f2b["enabled"], "ignore_ips": list(f2b["ignore_ips"])}

    def update_jail(self, name, data):
        with self._lock:
            self._maybe_reload(force=True)
            jail = self._config["fail2ban"]["jails"].get(name)
            if jail is None:
                raise KeyError("Jail not found")
            if "enabled" in data:
                jail["enabled"] = bool(data["enabled"])
            limits = {"max_retry": (1, 1000), "find_time": (1, 525600), "ban_time": (1, 5256000)}
            for key, (lo, hi) in limits.items():
                if key not in data:
                    continue
                try:
                    val = int(data[key])
                except (TypeError, ValueError):
                    raise ValueError(f"{key} must be an integer.")
                if key == "ban_time" and val == -1:
                    jail[key] = -1  # permanent
                    continue
                if not lo <= val <= hi:
                    raise ValueError(f"{key} must be between {lo} and {hi}" + (" (or -1 = permanent)." if key == "ban_time" else "."))
                jail[key] = val
            self._save()
            return dict(jail)

    # --- Fail2Ban runtime ---

    def is_exempt(self, ip) -> bool:
        ip_obj = parse_ip(ip)
        if ip_obj is None or ip_obj.is_loopback or is_trusted_proxy(ip_obj):
            return True
        return any(_ip_in_spec(ip_obj, spec) for spec in self._config["fail2ban"]["ignore_ips"])

    def _active_ban_locked(self, ip):
        ban = self._config["bans"].get(ip)
        if ban and ban.get("expires_at") and ban["expires_at"] <= time.time():
            return None  # expired: removed (and audited) by the sweeper
        return ban

    def _purge_expired_locked(self) -> list:
        now = time.time()
        expired = [ip for ip, b in self._config["bans"].items() if b.get("expires_at") and b["expires_at"] <= now]
        for ip in expired:
            self._config["bans"].pop(ip, None)
        if expired:
            self._save()
        return expired

    def _ban_locked(self, ip, jail, minutes, reason, failures=0, actor="fail2ban"):
        """Create a ban and apply recidive escalation. Returns (ban, escalated)."""
        now = time.time()
        ban = {
            "jail": jail,
            "reason": reason,
            "failures": failures,
            "banned_at": now,
            "expires_at": None if minutes == -1 else now + int(minutes) * 60,
            "actor": actor
        }
        escalated = False
        recidive = self._config["fail2ban"]["jails"]["recidive"]
        if jail != "recidive":
            history = [t for t in self._config["ban_history"].get(ip, []) if now - t < recidive["find_time"] * 60]
            history.append(now)
            self._config["ban_history"][ip] = history
            if recidive["enabled"] and len(history) >= recidive["max_retry"]:
                escalated = True
                ban.update({
                    "jail": "recidive",
                    "reason": f"Repeat offender: {len(history)} bans within {recidive['find_time']} min (last: {jail})",
                    "expires_at": None if recidive["ban_time"] == -1 else now + recidive["ban_time"] * 60
                })
        self._config["bans"][ip] = ban
        self._save()
        return ban, escalated

    def _after_ban(self, ip, ban, escalated):
        log_audit_event(
            event_type="FAIL2BAN_RECIDIVE_ESCALATION" if escalated else "FAIL2BAN_IP_BANNED",
            status="SUCCESS",
            severity="ALERT",
            actor={"username": ban["actor"], "role": "system" if ban["actor"] == "fail2ban" else "operator"},
            target={"resource": f"ip:{ip}"},
            details={"jail": ban["jail"], "reason": ban["reason"], "failures": ban["failures"],
                     "expires_at": ban["expires_at"]}
        )
        if self._config["kernel_sync"]["enabled"]:
            seconds = int(ban["expires_at"] - time.time()) if ban["expires_at"] else None
            self.kernel.add(ip, seconds)

    def record_failure(self, jail, ip, reason=""):
        """Register a failed/suspicious event for `ip` in `jail`. Bans when max_retry is reached."""
        ip_obj = parse_ip(ip)
        if ip_obj is None:
            return None
        ip = str(ip_obj)
        with self._lock:
            self._maybe_reload()
            f2b = self._config["fail2ban"]
            params = f2b["jails"].get(jail)
            if not f2b["enabled"] or not params or not params["enabled"] or jail == "recidive":
                return None
            if self.is_exempt(ip) or self._active_ban_locked(ip):
                return None
            now = time.time()
            key = (jail, ip)
            hits = [t for t in self._failures.get(key, []) if now - t < params["find_time"] * 60]
            hits.append(now)
            if len(hits) < params["max_retry"]:
                if key not in self._failures and len(self._failures) >= MAX_TRACKED_FAILURES:
                    self._prune_failures_locked(force=True)
                self._failures[key] = hits
                return None
            self._failures.pop(key, None)
            ban, escalated = self._ban_locked(
                ip, jail, params["ban_time"],
                reason or f"{len(hits)} failures within {params['find_time']} min",
                failures=len(hits)
            )
        self._after_ban(ip, ban, escalated)
        return ban

    def manual_ban(self, ip, minutes, reason, actor, operator_ctx=None):
        ip_obj = parse_ip(ip)
        if ip_obj is None:
            raise ValueError(f"'{ip}' is not a valid IP address.")
        ip = str(ip_obj)
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise ValueError("Duration must be an integer number of minutes (-1 = permanent).")
        if minutes != -1 and not 1 <= minutes <= 5256000:
            raise ValueError("Duration must be between 1 and 5256000 minutes, or -1 for permanent.")
        op = operator_ctx or {}
        if ip in (op.get("client_ip"), op.get("peer_ip")):
            raise ValueError("Anti-lockout safeguard: you cannot ban the IP of your current connection.")
        with self._lock:
            self._maybe_reload(force=True)
            if self.is_exempt(ip):
                raise ValueError(f"{ip} is loopback, a trusted proxy or in the global whitelist and cannot be banned.")
            ban, escalated = self._ban_locked(ip, "manual", minutes,
                                              (reason or "Manual ban").strip()[:200], actor=actor)
        self._after_ban(ip, ban, escalated)
        return dict(ban, ip=ip)

    def unban(self, ip, actor="admin", audit=True) -> bool:
        ip_obj = parse_ip(ip)
        key = str(ip_obj) if ip_obj else str(ip)
        with self._lock:
            self._maybe_reload(force=True)
            ban = self._config["bans"].pop(key, None)
            self._config["ban_history"].pop(key, None)
            for fkey in [k for k in self._failures if k[1] == key]:
                self._failures.pop(fkey, None)
            if ban is None:
                return False
            self._save()
            kernel_on = self._config["kernel_sync"]["enabled"]
        if kernel_on:
            self.kernel.remove(key)
        if audit:
            log_audit_event(
                event_type="FAIL2BAN_IP_UNBANNED",
                status="SUCCESS",
                severity="NOTICE",
                actor={"username": actor} if actor else None,
                target={"resource": f"ip:{key}"},
                details={"jail": ban.get("jail"), "reason": ban.get("reason")}
            )
        return True

    def whitelist_ip(self, ip, actor="admin"):
        ip_obj = parse_ip(ip)
        if ip_obj is None:
            raise ValueError(f"'{ip}' is not a valid IP address.")
        ip = str(ip_obj)
        with self._lock:
            self._maybe_reload(force=True)
            entries = self._config["fail2ban"]["ignore_ips"]
            entry = str(ipaddress.ip_network(ip))
            if entry not in entries:
                entries.append(entry)
                self._save()
        self.unban(ip, actor=actor)
        return entry

    def flush_bans(self) -> int:
        with self._lock:
            self._maybe_reload(force=True)
            count = len(self._config["bans"])
            self._config["bans"] = {}
            self._config["ban_history"] = {}
            self._failures.clear()
            self._save()
            kernel_on = self._config["kernel_sync"]["enabled"]
        if kernel_on:
            self.kernel.resync({})
        return count

    def set_kernel_sync(self, enabled: bool) -> dict:
        if enabled:
            status = self.kernel.status()
            if not status["available"]:
                raise ValueError(status["error"] or "Kernel sync is not available on this host.")
        with self._lock:
            self._maybe_reload(force=True)
            self._config["kernel_sync"]["enabled"] = bool(enabled)
            self._save()
            bans = copy.deepcopy(self._config["bans"])
        if enabled:
            self.kernel.resync(bans)
        else:
            self.kernel.teardown()
        self._kernel_dirty = False
        return dict(self._config["kernel_sync"], **self.kernel.status())

    # --- Background maintenance ---

    def _prune_failures_locked(self, force=False):
        now = time.time()
        jails = self._config["fail2ban"]["jails"]
        for key in list(self._failures):
            window = jails.get(key[0], {}).get("find_time", 10) * 60
            hits = [t for t in self._failures[key] if now - t < window]
            if hits and not force:
                self._failures[key] = hits
            elif hits and force:
                # Under memory pressure keep only counters that are close to a ban
                if len(hits) * 2 >= jails.get(key[0], {}).get("max_retry", 5):
                    self._failures[key] = hits
                else:
                    self._failures.pop(key, None)
            else:
                self._failures.pop(key, None)
        recidive_window = jails["recidive"]["find_time"] * 60
        history = self._config["ban_history"]
        for ip in list(history):
            history[ip] = [t for t in history[ip] if now - t < recidive_window]
            if not history[ip]:
                history.pop(ip)
        for ip in [ip for ip, e in self._blocked_audit.items() if now - e[0] > BLOCKED_AUDIT_INTERVAL]:
            self._blocked_audit.pop(ip, None)

    def sweep(self):
        with self._lock:
            self._maybe_reload(force=True)
            expired = [(ip, self._config["bans"][ip]) for ip in list(self._config["bans"])
                       if self._config["bans"][ip].get("expires_at")
                       and self._config["bans"][ip]["expires_at"] <= time.time()]
            self._purge_expired_locked()
            self._prune_failures_locked()
            kernel_on = self._config["kernel_sync"]["enabled"]
            resync = kernel_on and self._kernel_dirty
            bans = copy.deepcopy(self._config["bans"]) if resync else None
            self._kernel_dirty = False
        if resync:
            self.kernel.resync(bans)
        for ip, ban in expired:
            if kernel_on:
                self.kernel.remove(ip)
            log_audit_event(
                event_type="FAIL2BAN_BAN_EXPIRED",
                status="INFO",
                severity="INFO",
                actor={"username": "fail2ban", "role": "system", "source_ip": "localhost"},
                target={"resource": f"ip:{ip}"},
                details={"jail": ban.get("jail")}
            )

    def start_sweeper(self):
        if self._sweeper_started:
            return
        self._sweeper_started = True

        def loop():
            while True:
                try:
                    self.sweep()
                except Exception as e:
                    print(f"[AMIKO Firewall] Sweeper error: {e}")
                time.sleep(SWEEP_INTERVAL)

        threading.Thread(target=loop, name="amiko-firewall-sweeper", daemon=True).start()


# ── WSGI middleware (Level 1 enforcement) ────────────────────────────────────

class FirewallMiddleware:
    """Intercepts every request (HTTP, static, Socket.IO polling & WebSocket upgrade) pre-routing."""

    def __init__(self, wsgi_app, flask_app, manager):
        self.wsgi_app = wsgi_app
        self.flask_app = flask_app
        self.manager = manager

    def _session_state(self, environ):
        from werkzeug.http import parse_cookie
        cookie_name = self.flask_app.config.get("SESSION_COOKIE_NAME", "session")
        raw = parse_cookie(environ.get("HTTP_COOKIE", "")).get(cookie_name)
        if not raw:
            return "NEW"
        try:
            serializer = self.flask_app.session_interface.get_signing_serializer(self.flask_app)
            max_age = int(self.flask_app.permanent_session_lifetime.total_seconds())
            data = serializer.loads(raw, max_age=max_age) if serializer else {}
            return "ESTABLISHED" if data.get("user_id") else "NEW"
        except Exception:
            return "NEW"

    def __call__(self, environ, start_response):
        try:
            verdict = self.manager.check_inbound(environ, session_state=lambda: self._session_state(environ))
        except Exception as e:
            # Fail open on engine errors so a bug cannot take the management plane down
            print(f"[AMIKO Firewall] Evaluation error (request allowed): {e}")
            verdict = None
        if verdict is None:
            return self.wsgi_app(environ, start_response)

        self.manager.audit_blocked_request(verdict, environ)
        headers = [("Connection", "close"), ("Cache-Control", "no-store")]
        if verdict["target"] == "DROP":
            # L7 cannot silently discard a TCP stream: answer with an empty body and close it
            start_response("403 Forbidden", headers + [("Content-Length", "0")])
            return [b""]

        if verdict["reason"] == "fail2ban":
            message = "Access temporarily blocked by AMIKO intrusion prevention (Fail2Ban)."
        else:
            message = "Access denied by AMIKO firewall policy."
        path = environ.get("PATH_INFO", "")
        if path.startswith("/api/") or "application/json" in environ.get("HTTP_ACCEPT", ""):
            body = json.dumps({"error": message, "status": "error", "success": False}).encode("utf-8")
            ctype = "application/json"
        else:
            body = message.encode("utf-8")
            ctype = "text/plain; charset=utf-8"
        start_response("403 Forbidden", headers + [("Content-Type", ctype), ("Content-Length", str(len(body)))])
        return [body]


firewall = FirewallManager()


def report_failure(jail, reason=""):
    """Convenience hook for request handlers: count a failure for the current client IP."""
    from core.client_ip import get_client_ip
    try:
        return firewall.record_failure(jail, get_client_ip(), reason)
    except Exception as e:
        print(f"[AMIKO Firewall] Failed to record {jail} failure: {e}")
        return None


def operator_context() -> dict:
    """Connection info of the operator performing a change (used by the anti-lockout safeguard)."""
    from flask import request
    from core.client_ip import get_client_ip
    return {"client_ip": get_client_ip(), "peer_ip": str(parse_ip(request.remote_addr) or "")}


def init_firewall(app):
    """Install the Level 1 middleware (outermost, wraps Socket.IO too) and start the sweeper."""
    app.wsgi_app = FirewallMiddleware(app.wsgi_app, app, firewall)
    firewall.start_sweeper()
    return firewall
