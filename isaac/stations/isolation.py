"""Read-only Linux namespace checks; no firewall, interface or route changes."""
from pathlib import Path
import socket


def check_namespace(*, interfaces=None, ipv4_routes=None, ipv6_routes=None):
    names = sorted(name for _, name in socket.if_nameindex()) if interfaces is None else sorted(interfaces)
    if names != ['lo']:
        raise ValueError('Station namespace has a non-loopback interface')
    if ipv4_routes is None:
        ipv4_routes = Path('/proc/net/route').read_text()
    if ipv6_routes is None:
        ipv6_routes = Path('/proc/net/ipv6_route').read_text()
    # Linux retains unreachable default rows on lo (RTF_REJECT=0x200) even
    # inside network-none. They are not usable routes; record them separately.
    v4 = [line.split() for line in ipv4_routes.splitlines()[1:] if line.strip()]
    if v4:
        raise ValueError('An isolated station must have no IPv4 routes')
    v6 = [line.split() for line in ipv6_routes.splitlines() if line.strip()]
    rejected_defaults = 0
    for row in v6:
        if len(row) != 10 or row[-1] != 'lo':
            raise ValueError('Unexpected IPv6 route in isolated station')
        if row[0] == '0'*32 and row[1] == '00' and int(row[8],16) & 0x200 and row[4] == '0'*32:
            rejected_defaults += 1
        elif row[0] != '0'*31+'1' or row[1] != '80' or row[4] != '0'*32:
            raise ValueError('Usable non-loopback/default IPv6 route is forbidden')
    return dict(interfaces=names, ipv4_route_count=0, ipv6_loopback_routes=len(v6),
                ipv6_unreachable_default_entries=rejected_defaults,
                namespace_route_check_passed=True, physical_network_review_complete=False,
                packet_capture_complete=False)
