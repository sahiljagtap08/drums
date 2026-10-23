"""Network policy for sessions, as an nftables table of our own (`inet drums`).

Why: with a veth per session and the host as router, whether session A can reach session B was
only a side effect of the host's `net.ipv4.ip_forward`. This makes it policy:

  forward:  established/related accepted; session -> session DROP; anything -> session DROP
            (new inbound connections to a session are never forwarded); session -> private,
            link-local and CGNAT ranges DROP (so a session cannot reach the cloud metadata service at
            169.254.169.254, other hosts on the VPC, or the host's LAN); everything else from a
            session (the internet) is allowed only with `nat=True`; without it every session-originated
            forward is dropped, so egress is policy too.
  input:    per-slot chain: a session may talk to the host only at its own gateway address; the host's
            other addresses, other sessions' gateway addresses and host services bound elsewhere are DROPped.
  nat:      with `nat=True`, masquerade session traffic leaving by a non-session interface.

Each Supervisor run owns ONE table named `drums_<run id>` (the default name `drums` is for direct use only);
the name is the ownership record, kept in the sweep ledger. `install()` creates it and `remove()` deletes it;
one `nft delete table` undoes all of it, and no other table is ever touched.
It does not change `ip_forward` (the Supervisor does that when egress is requested and restores it).

Requires the `nft` binary and CAP_NET_ADMIN in the host network namespace (kernel: nf_tables, NAT,
masquerade). IPv6: sessions get no IPv6 address and the table does not cover it.
Crash behaviour (observed): if the process is killed, nothing runs close(); the table, the session
processes and, with nat, `ip_forward=1` stay behind. envd_linux/sweep.py removes exactly what the run's
ledger entry says it owns.
Not covered: DNS (a session's resolv.conf is the host's, which may point at a loopback stub the session
cannot reach), bandwidth limits, and per-session egress allow-lists."""

import subprocess

PRIVATE = "{ 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10 }"


class FirewallError(RuntimeError):
    pass


def _nft(script):
    r = subprocess.run(["nft", "-f", "-"], input=script, capture_output=True, text=True)
    if r.returncode != 0:
        raise FirewallError(f"nft failed: {r.stderr.strip()[-300:]}")


def ruleset(nat=False, block_private=True, base="10.200", table="drums"):
    noegress = "" if nat else '        iifname "dvh*" drop\n'
    private = f"        iifname \"dvh*\" ip daddr {PRIVATE} drop\n" if block_private else ""
    natchain = (f"    chain post {{\n        type nat hook postrouting priority srcnat; policy accept;\n"
                f"        ip saddr {base}.0.0/16 oifname != \"dvh*\" masquerade\n    }}\n") if nat else ""
    return f"""add table inet {table}
delete table inet {table}
table inet {table} {{
    chain relay {{
        type filter hook forward priority -10; policy accept;
        ct state established,related accept
        iifname "dvh*" oifname "dvh*" drop
        oifname "dvh*" drop
{private}{noegress}    }}
    chain inp {{
        type filter hook input priority -10; policy accept;
    }}
{natchain}}}
"""


def install(nat=False, block_private=True, base="10.200", table="drums"):
    _nft(ruleset(nat, block_private, base, table))


def add_slot(slot, base="10.200", table="drums"):
    """Per-slot input policy, installed before the session's link is brought up."""
    gw = f"{base}.{slot}.1"
    _nft(f"""add table inet {table}
add chain inet {table} in_{slot}
flush chain inet {table} in_{slot}
add rule inet {table} in_{slot} ct state established,related accept
add rule inet {table} in_{slot} ip daddr {gw} accept
add rule inet {table} in_{slot} drop
""")
    exists = subprocess.run(["nft", "list", "chain", "inet", table, "inp"], capture_output=True, text=True).stdout
    if f'iifname "dvh{slot}"' not in exists:
        _nft(f'add rule inet {table} inp iifname "dvh{slot}" jump in_{slot}\n')


def remove(table="drums"):
    subprocess.run(["nft", "delete", "table", "inet", table], capture_output=True)
