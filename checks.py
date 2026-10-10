"""Low-level network checks.

Each function does one small job (adapter, gateway, ping, DNS, HTTP, traceroute)
and returns plain Python data. The decision logic lives in diagnose.py.
Works on Windows (ping/tracert/ipconfig) and falls back to Linux/macOS commands.
"""
import platform
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request

import psutil

IS_WINDOWS = platform.system() == "Windows"


def _run(cmd, timeout=30):
    """Run a command and return its text output ('' if it fails or times out)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, errors="ignore"
        )
        return (result.stdout or "") + (result.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


# ---------------------------------------------------------------- adapter
def check_adapter(prefer_ip=None):
    """Look for an active adapter that has a usable IPv4 address.

    prefer_ip: the local IP used by the default route; that adapter is listed first.

    Returns (state, interfaces) where state is one of:
      "ok"          - at least one adapter has a normal IP address
      "apipa"       - only 169.254.x.x addresses (DHCP failed)
      "no_adapter"  - nothing is up with an IPv4 address
    """
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    found = []
    for name, st in stats.items():
        if not st.isup:
            continue
        for a in addrs.get(name, []):
            if a.family == socket.AF_INET and not a.address.startswith("127."):
                found.append({"name": name, "ip": a.address})

    if not found:
        return "no_adapter", []
    good = [i for i in found if not i["ip"].startswith("169.254.")]
    if not good:
        return "apipa", found
    good.sort(key=lambda i: i["ip"] != prefer_ip)   # the adapter in use comes first
    return "ok", good


# ---------------------------------------------------------------- gateway
def get_default_route():
    """Return (gateway_ip, local_ip) of the default route, or (None, None).

    local_ip is the address of the adapter that the default route uses.
    """
    if IS_WINDOWS:
        # `route print` lists the default route on one line, even when
        # `ipconfig` shows an IPv6 gateway first and the IPv4 one on the next line.
        out = _run(["route", "print", "-4", "0.0.0.0"], timeout=10)
        best = None
        pattern = r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)"
        for m in re.finditer(pattern, out, re.M):
            gateway, local, metric = m.group(1), m.group(2), int(m.group(3))
            if best is None or metric < best[2]:
                best = (gateway, local, metric)
        if best:
            return best[0], best[1]

        # Fallback: ipconfig. The IPv4 gateway may be on the line after the label.
        lines = _run(["ipconfig"], timeout=10).splitlines()
        for i, line in enumerate(lines):
            if "Default Gateway" not in line:
                continue
            block = line
            if i + 1 < len(lines) and ":" not in lines[i + 1]:
                block += "\n" + lines[i + 1]
            m = re.search(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])", block)
            if m:
                return m.group(1), None
        return None, None

    out = _run(["ip", "route"], timeout=10)
    match = re.search(r"default via (\d+\.\d+\.\d+\.\d+)(?: dev \S+)?(?: proto \S+)?(?: src (\d+\.\d+\.\d+\.\d+))?", out)
    if match:
        return match.group(1), match.group(2)
    try:  # fallback: read the Linux routing table directly
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts[1] == "00000000":
                    raw = bytes.fromhex(parts[2])[::-1]
                    return ".".join(str(b) for b in raw), None
    except OSError:
        pass
    return None, None


def get_default_gateway():
    """Return the default gateway (router) IPv4 address, or None."""
    return get_default_route()[0]


# ---------------------------------------------------------------- ping
def ping(host, count=4, timeout_ms=2000):
    """Ping a host and return reply statistics.

    Packet loss = (sent - received) / sent * 100
    """
    if IS_WINDOWS:
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(max(1, timeout_ms // 1000)), host]

    out = _run(cmd, timeout=count * (timeout_ms / 1000 + 1) + 5)
    times = [float(t) for t in re.findall(r"time[=<]\s*([\d.]+)\s*ms", out)]
    received = len(times)
    loss = round((count - received) / count * 100, 1)
    return {
        "host": host,
        "sent": count,
        "received": received,
        "loss": loss,
        "min": round(min(times), 1) if times else None,
        "avg": round(sum(times) / len(times), 1) if times else None,
        "max": round(max(times), 1) if times else None,
    }


# ---------------------------------------------------------------- DNS
def dns_lookup(name="google.com"):
    """Resolve a hostname. Returns (ok, ip_or_error, milliseconds)."""
    start = time.time()
    try:
        ip = socket.gethostbyname(name)
        return True, ip, round((time.time() - start) * 1000)
    except OSError as e:
        return False, str(e), round((time.time() - start) * 1000)


# ---------------------------------------------------------------- HTTP
def http_check(url="https://www.google.com", timeout=5):
    """Fetch a web page. Returns (ok, status_or_error, milliseconds)."""
    start = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NetTroubleshooter/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.status, round((time.time() - start) * 1000)
    except urllib.error.HTTPError as e:
        # The server answered, so the web path works even if the page is an error page.
        return True, e.code, round((time.time() - start) * 1000)
    except Exception as e:  # URLError, timeout, SSL error, proxy error ...
        return False, str(getattr(e, "reason", e)), round((time.time() - start) * 1000)


# ---------------------------------------------------------------- port
def check_port(host, port, timeout=3):
    """Return True if a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- traceroute
def traceroute(host="8.8.8.8", max_hops=15):
    """Run tracert/traceroute and return the hop lines as a list of strings."""
    if IS_WINDOWS:
        cmd = ["tracert", "-d", "-h", str(max_hops), "-w", "1000", host]
    else:
        cmd = ["traceroute", "-n", "-m", str(max_hops), "-w", "1", host]
    out = _run(cmd, timeout=max_hops * 4 + 15)
    return [line.rstrip() for line in out.splitlines() if line.strip()]
if __name__ == "__main__":
    print("===== Network Checks =====")
    state, interfaces = check_adapter()
    print("Adapter state:", state, interfaces)
    gateway, local_ip = get_default_route()
    print("Default gateway:", gateway, "| local IP:", local_ip)
    print("Ping 8.8.8.8:", ping("8.8.8.8"))
    print("DNS google.com:", dns_lookup("google.com"))
    print("HTTPS google.com:", http_check("https://www.google.com"))