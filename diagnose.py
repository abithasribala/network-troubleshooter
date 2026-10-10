"""Rule-based diagnosis engine.

Runs the checks in the order a technician would (device -> router -> internet
-> DNS -> web -> quality) and decides where the problem is.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import checks

INTERNET_HOST = "8.8.8.8"
DNS_NAME = "google.com"
WEB_URL = "https://www.google.com"

LATENCY_WARN, LATENCY_BAD = 100, 200   # ms
LOSS_WARN, LOSS_BAD = 20, 50           # percent

STEP_INFO = [
    ("adapter", "Network adapter", "Link and IP address"),
    ("gateway", "Router (default gateway)", "Local network"),
    ("internet", "Internet reachability", "Network layer (ICMP)"),
    ("dns", "DNS lookup", "Application layer (DNS)"),
    ("web", "Web access", "Application layer (HTTP)"),
    ("quality", "Connection quality", "Latency and packet loss"),
]


def _step(step_id, status, detail, short="", data=None):
    title, layer = next((t, l) for i, t, l in STEP_INFO if i == step_id)
    return {
        "id": step_id,
        "title": title,
        "layer": layer,
        "status": status,       # pass | warn | fail | skipped
        "detail": detail,
        "short": short,         # short text shown under the node
        "data": data or {},
    }


# ---------------------------------------------------------------- steps
def step_adapter(prefer_ip=None):
    state, interfaces = checks.check_adapter(prefer_ip)
    if state == "ok":
        first = interfaces[0]
        return _step("adapter", "pass",
                     f"{first['name']} is up with IP address {first['ip']}.",
                     first["ip"], {"interfaces": interfaces})
    if state == "apipa":
        ip = interfaces[0]["ip"]
        return _step("adapter", "fail",
                     f"The adapter only has {ip}. This self-assigned address means "
                     "the router did not give your device an IP address.",
                     ip, {"state": state})
    return _step("adapter", "fail",
                 "No active network adapter with an IP address was found.",
                 "Not connected", {"state": state})


def step_gateway(gateway_ip):
    if not gateway_ip:
        return _step("gateway", "warn", "Could not read the default gateway address.", "Unknown")
    result = checks.ping(gateway_ip, count=3, timeout_ms=1500)
    if result["received"] == 0:
        return _step("gateway", "fail",
                     f"The router at {gateway_ip} did not reply to ping.",
                     gateway_ip, result)
    return _step("gateway", "pass",
                 f"Router {gateway_ip} replied in {result['avg']} ms on average.",
                 f"{result['avg']} ms", result)


def step_internet():
    result = checks.ping(INTERNET_HOST, count=4, timeout_ms=2000)
    if result["received"] == 0:
        return _step("internet", "fail",
                     f"No reply from {INTERNET_HOST} (100% packet loss).",
                     "No reply", result)
    return _step("internet", "pass",
                 f"{INTERNET_HOST} replied. Average {result['avg']} ms, "
                 f"packet loss {result['loss']}%.",
                 f"{result['avg']} ms", result)


def step_dns():
    ok, value, ms = checks.dns_lookup(DNS_NAME)
    if ok:
        return _step("dns", "pass", f"{DNS_NAME} resolved to {value} in {ms} ms.", value,
                     {"ip": value, "ms": ms})
    return _step("dns", "fail", f"Could not resolve {DNS_NAME}: {value}", "Failed",
                 {"error": value})


def step_web():
    ok, value, ms = checks.http_check(WEB_URL)
    if ok:
        return _step("web", "pass", f"{WEB_URL} answered with HTTP {value} in {ms} ms.",
                     f"HTTP {value}", {"status": value, "ms": ms})
    return _step("web", "fail", f"Could not open {WEB_URL}: {value}", "Failed",
                 {"error": value})


def step_quality(internet):
    if internet["status"] == "fail":
        return _step("quality", "skipped", "Skipped because the internet is unreachable.", "")
    d = internet["data"]
    loss, avg = d["loss"], d["avg"]
    detail = (f"Average {avg} ms (min {d['min']}, max {d['max']}). "
              f"Packet loss {loss}%.")
    if loss >= LOSS_BAD or avg >= LATENCY_BAD:
        status = "fail"
    elif loss >= LOSS_WARN or avg >= LATENCY_WARN or loss > 0:
        status = "warn"
    else:
        status = "pass"
    return _step("quality", status, detail, f"{avg} ms, {loss}% loss", d)


def _skipped(step_id, reason):
    return _step(step_id, "skipped", reason, "")


# ---------------------------------------------------------------- verdict
def _fix(text, command=None):
    return {"text": text, "command": command}


def build_verdict(steps):
    s = {x["id"]: x for x in steps}

    def verdict(severity, title, cause, fixes, failed=None):
        return {"severity": severity, "title": title, "cause": cause,
                "fixes": fixes, "failed_step": failed}

    if s["adapter"]["status"] == "fail":
        if s["adapter"]["data"].get("state") == "apipa":
            return verdict(
                "fail", "Your device did not get an IP address",
                "DHCP failed, so Windows gave itself a 169.254.x.x address. "
                "The router is not handing out addresses.",
                [_fix("Restart your router and wait one minute."),
                 _fix("Release the old address.", "ipconfig /release"),
                 _fix("Ask the router for a new address.", "ipconfig /renew")],
                "adapter")
        return verdict(
            "fail", "Your device is not connected to a network",
            "No adapter is up with an IP address.",
            [_fix("Turn on Wi-Fi or plug in the network cable."),
             _fix("Turn off airplane mode."),
             _fix("Reconnect to your Wi-Fi network and try again.")],
            "adapter")

    if s["gateway"]["status"] == "fail" and s["internet"]["status"] == "fail":
        return verdict(
            "fail", "Problem between your device and the router",
            "Your device has an IP address, but the router does not answer. "
            "The Wi-Fi signal, the cable or the router itself may be at fault.",
            [_fix("Move closer to the router or check the cable."),
             _fix("Restart the router (unplug for 30 seconds)."),
             _fix("Renew the IP address.", "ipconfig /renew")],
            "gateway")

    if s["internet"]["status"] == "fail":
        return verdict(
            "fail", "Router works, but the internet is unreachable",
            "Your device reaches the router, but nothing gets past it. "
            "This usually means a problem with the internet provider or the modem.",
            [_fix("Check whether other devices on the same network have internet."),
             _fix("Restart the modem and router."),
             _fix("Contact your internet provider if it stays down.")],
            "internet")

    if s["dns"]["status"] == "fail":
        return verdict(
            "fail", "DNS is not working",
            "The internet is reachable by IP address, but names like google.com "
            "cannot be turned into addresses, so websites will not open.",
            [_fix("Clear the DNS cache.", "ipconfig /flushdns"),
             _fix("Set the DNS server to 8.8.8.8 in your adapter settings."),
             _fix("Restart the router.")],
            "dns")

    if s["web"]["status"] == "fail":
        return verdict(
            "fail", "Websites are blocked or not answering",
            "The network and DNS work, but the web request failed. "
            "A proxy, firewall, antivirus or captive login page may be in the way.",
            [_fix("Check whether a proxy is set.", "netsh winhttp show proxy"),
             _fix("Open a web page in your browser and look for a login page (college or public Wi-Fi)."),
             _fix("Turn off VPN or antivirus web protection briefly and test again.")],
            "web")

    q = s["quality"]
    if q["status"] in ("warn", "fail"):
        d = q["data"]
        if d["loss"] >= LOSS_WARN:
            sev = "fail" if d["loss"] >= LOSS_BAD else "warn"
            title = "High packet loss" if sev == "warn" else "Critical packet loss"
            cause = (f"{d['loss']}% of packets got no reply. Video calls and games "
                     "suffer first. Weak Wi-Fi or a congested link is the usual cause.")
            fixes = [_fix("Move closer to the router or use a cable."),
                     _fix("Stop large downloads and uploads."),
                     _fix("Trace the route to see where packets are lost.")]
        elif d["avg"] >= LATENCY_WARN:
            sev = "fail" if d["avg"] >= LATENCY_BAD else "warn"
            title = "Very high latency" if sev == "fail" else "High latency"
            cause = (f"Replies take {d['avg']} ms on average, so pages feel slow "
                     "even though everything is connected.")
            fixes = [_fix("Close apps that use the network heavily."),
                     _fix("Trace the route to see where the delay starts."),
                     _fix("Restart the router.")]
        else:
            sev, title = "warn", "Minor packet loss"
            cause = (f"{d['loss']}% of packets got no reply. This is small, "
                     "but worth watching if calls or games stutter.")
            fixes = [_fix("Run the diagnosis again in a minute to see if it repeats.")]
        return verdict(sev, title, cause, fixes, "quality")

    d = q["data"]
    return verdict(
        "ok", "Network is healthy",
        f"All checks passed. Latency is {d['avg']} ms with {d['loss']}% packet loss.",
        [], None)


# ---------------------------------------------------------------- main entry
def run_diagnosis():
    gateway_ip, local_ip = checks.get_default_route()
    adapter = step_adapter(local_ip)

    if adapter["status"] == "fail":
        steps = [adapter] + [
            _skipped(i, "Skipped because the device has no network connection.")
            for i, _, _ in STEP_INFO[1:]
        ]
    else:
        # These checks do not depend on each other, so run them together.
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_gw = pool.submit(step_gateway, gateway_ip)
            f_net = pool.submit(step_internet)
            f_dns = pool.submit(step_dns)
            f_web = pool.submit(step_web)
            gateway, internet = f_gw.result(), f_net.result()
            dns, web = f_dns.result(), f_web.result()

        # Some routers ignore ping. If the internet works, that is not a fault.
        if gateway["status"] == "fail" and internet["status"] == "pass":
            gateway["status"] = "warn"
            gateway["detail"] += " The internet works, so the router is probably set to ignore ping."

        # DNS and web checks only make sense if the internet is reachable.
        if internet["status"] == "fail":
            dns = _skipped("dns", "Skipped because the internet is unreachable.")
            web = _skipped("web", "Skipped because the internet is unreachable.")

        steps = [adapter, gateway, internet, dns, web, step_quality(internet)]

    verdict = build_verdict(steps)
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "steps": steps,
        "verdict": verdict,
    }
if __name__ == "__main__":
    result = run_diagnosis()
    print("===== Network Diagnosis =====")
    print("Time:", result["timestamp"])
    for s in result["steps"]:
        print(f"[{s['status'].upper():7}] {s['title']}: {s['detail']}")
    v = result["verdict"]
    print("\nDiagnosis:", v["title"])
    print("Cause:", v["cause"])
    for i, fix in enumerate(v["fixes"], 1):
        line = f"  {i}. {fix['text']}"
        if fix["command"]:
            line += f"   ->  {fix['command']}"
        print(line)