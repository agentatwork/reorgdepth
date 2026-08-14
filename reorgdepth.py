#!/usr/bin/env python3
"""
reorgdepth — how many confirmations does your lnd node actually wait for
before it treats each of your channel closes as final?

Since lnd v20 that number is not a constant. It is derived from channel
capacity by CloseConfsForCapacity() in lnwallet/confscale_prod.go, it ranges
from 3 to 6, and there is currently no way for an operator to raise it on a
release binary. Most people running a node have never seen the number.

    lncli listchannels | reorgdepth.py
    lncli listchannels > ch.json && reorgdepth.py ch.json
    reorgdepth.py --capacity 5000000

No dependencies, no network, reads stdin or a file. It never talks to your
node itself — pipe it, so it needs no macaroon and can be run on a machine
that has never touched your keys.

The arithmetic is a transcription of lnd master as of 2026-08-14; the two
source files are quoted in full in ARITHMETIC.md so you can diff them against
your own build rather than trusting this one.
"""
import json
import sys

# --- lnwallet/confscale.go -------------------------------------------------
MIN_REQUIRED_CONFS = 1
MAX_REQUIRED_CONFS = 6
MAX_CHANNEL_SIZE = 16_777_215      # sat; matches MaxBtcFundingAmount

# --- lnwallet/confscale_prod.go --------------------------------------------
MIN_CLOSE_CONFS = 3

# The widely-cited "6 confirmations" is a convention, not a BOLT safety requirement.
# The only 6 in the whole spec is BOLT #7's gate on `channel_announcement` (a gossip
# rule). BOLT #5's actual finality number is 100 blocks — *irrevocably resolved* —
# and the MUST-monitor obligation runs until then. See ARITHMETIC.md.
CONVENTIONAL_CONFS = 6
BOLT5_IRREVOCABLE = 100


def scale_num_confs(chan_amt_sat, push_amt_msat=0):
    """Port of lnwallet.ScaleNumConfs. Integer division, deliberately."""
    if chan_amt_sat > MAX_CHANNEL_SIZE:
        return MAX_REQUIRED_CONFS          # wumbo always gets the max
    max_msat = MAX_CHANNEL_SIZE * 1000
    stake = chan_amt_sat * 1000 + push_amt_msat
    conf = MAX_REQUIRED_CONFS * stake // max_msat
    return max(MIN_REQUIRED_CONFS, min(MAX_REQUIRED_CONFS, conf))


def close_confs(capacity_sat):
    """Port of lnwallet.CloseConfsForCapacity."""
    return max(scale_num_confs(capacity_sat), MIN_CLOSE_CONFS)


def load_channels(src):
    """Accept `lncli listchannels` output, or anything shaped like it.

    Also accepts a bare list, and tolerates the pending/closed variants, so
    `pendingchannels` and `closedchannels` output doesn't just error out.
    """
    doc = json.load(src)
    if isinstance(doc, list):
        rows = doc
    else:
        for key in ("channels", "pending_open_channels", "channel"):
            if isinstance(doc.get(key), list):
                rows = doc[key]
                break
        else:
            raise SystemExit("no 'channels' array found — is this lncli listchannels output?")

    out = []
    for r in rows:
        # pendingchannels nests the real fields one level down
        c = r.get("channel", r)
        cap = c.get("capacity") or c.get("local_balance") or 0
        try:
            cap = int(cap)
        except (TypeError, ValueError):
            continue
        if cap <= 0:
            continue
        out.append({
            "capacity": cap,
            "peer": (c.get("remote_pubkey") or c.get("remote_node_pub") or "")[:16],
            "alias": c.get("peer_alias") or "",
            "active": c.get("active", None),
        })
    return out


def sats(n):
    return f"{n:,}"


def main():
    args = [a for a in sys.argv[1:]]

    if "-h" in args or "--help" in args:
        print(__doc__.strip())
        return 0

    if "--capacity" in args:
        i = args.index("--capacity")
        try:
            cap = int(args[i + 1].replace(",", "").replace("_", ""))
        except (IndexError, ValueError):
            raise SystemExit("--capacity needs a number in satoshis")
        n = close_confs(cap)
        print(f"{sats(cap)} sat  ->  {n} confirmation{'s' if n != 1 else ''} before the close is treated as final")
        if n < CONVENTIONAL_CONFS:
            print(f"          Below the conventional {CONVENTIONAL_CONFS}. You cannot raise this on a "
                  f"release build (see lnd#11072).")
        print(f"          For scale: BOLT #5 considers an output *irrevocably resolved* at "
              f"{BOLT5_IRREVOCABLE} blocks.")
        return 0

    paths = [a for a in args if not a.startswith("-")]
    if paths:
        with open(paths[0]) as fh:
            chans = load_channels(fh)
    elif not sys.stdin.isatty():
        chans = load_channels(sys.stdin)
    else:
        print(__doc__.strip())
        return 2

    if not chans:
        print("no channels with a capacity found.")
        return 0

    chans.sort(key=lambda c: (-c["capacity"]))

    w = max(len(sats(c["capacity"])) for c in chans)
    print(f"{'CAPACITY':>{w}}  CONFS  {'':2}PEER")
    print("-" * (w + 30))
    for c in chans:
        n = close_confs(c["capacity"])
        flag = "  <- floor" if n == MIN_CLOSE_CONFS else ("" if n >= CONVENTIONAL_CONFS else "  <- below 6")
        label = c["alias"] or c["peer"]
        print(f"{sats(c['capacity']):>{w}}  {n:>5}  {label:<20}{flag}")

    total = sum(c["capacity"] for c in chans)
    at_floor = [c for c in chans if close_confs(c["capacity"]) == MIN_CLOSE_CONFS]
    below = [c for c in chans if close_confs(c["capacity"]) < CONVENTIONAL_CONFS]
    val_below = sum(c["capacity"] for c in below)

    print()
    print(f"{len(chans)} channels, {sats(total)} sat total capacity")
    print(f"{len(at_floor)} at the {MIN_CLOSE_CONFS}-confirmation floor")
    print(f"{len(below)} below the conventional {CONVENTIONAL_CONFS} "
          f"({sats(val_below)} sat, {100 * val_below // total if total else 0}% of your capacity)")
    print(f"all {len(chans)} are below BOLT #5's {BOLT5_IRREVOCABLE}-block *irrevocably resolved*, "
          f"as is every implementation except CLN")

    if below:
        print()
        print("There is no supported way to raise this on a release build of lnd: the override")
        print("(p.cfg.ChannelCloseConfs) is fed from Dev config, which returns None unless the")
        print("binary was built with the `integration` build tag. Tracking: lnd#11072.")
        print("Note --coop-close-target-confs is NOT this knob; it only affects fee estimation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
