# Where these numbers come from

`reorgdepth.py` is a transcription of two files from lnd `master`, read 2026-08-14.
It is deliberately a transcription and not a clever reimplementation, so you can
diff it against your own checkout. If your build disagrees with this tool, your
build is right and this tool is stale — please open an issue.

## lnwallet/confscale_prod.go

Note the build tag. This is the production path; there is a sibling
`confscale_integration.go` behind `//go:build integration` that simply returns `1`.

```go
//go:build !integration
// +build !integration

// CloseConfsForCapacity returns the number of confirmations to wait before
// signaling a channel close, scaled by channel capacity. This is used for both
// cooperative and force closes. We enforce a minimum of 3 confirmations to
// provide better reorg protection, even for small channels.
func CloseConfsForCapacity(capacity btcutil.Amount) uint32 {
	// For cooperative closes, we don't have a push amount to consider,
	// so we pass 0 for the pushAmt parameter.
	scaledConfs := uint32(ScaleNumConfs(capacity, 0))

	// Enforce a minimum of 3 confirmations for reorg safety.
	// This protects against shallow reorgs which are more common.
	const minCloseConfs = 3
	if scaledConfs < minCloseConfs {
		return minCloseConfs
	}

	return scaledConfs
}
```

## lnwallet/confscale.go

```go
const (
	minRequiredConfs = 1
	maxRequiredConfs = 6

	// maxChannelSize is the maximum expected channel size in satoshis.
	// This matches MaxBtcFundingAmount (0.16777215 BTC).
	maxChannelSize = 16777215
)

func ScaleNumConfs(chanAmt btcutil.Amount, pushAmt lnwire.MilliSatoshi) uint16 {
	// For wumbo channels, always require maximum confirmations.
	if chanAmt > maxChannelSize {
		return maxRequiredConfs
	}

	maxChannelSizeMsat := lnwire.NewMSatFromSatoshis(maxChannelSize)
	stake := lnwire.NewMSatFromSatoshis(chanAmt) + pushAmt

	conf := uint64(maxRequiredConfs) * uint64(stake) /
		uint64(maxChannelSizeMsat)

	if conf < minRequiredConfs {
		conf = minRequiredConfs
	}
	if conf > maxRequiredConfs {
		conf = maxRequiredConfs
	}

	return uint16(conf)
}
```

The division is integer division, which is where the thresholds come from:

| Channel capacity | Close confirmations |
|---|---|
| below 11,184,810 sat (0.1118 BTC) | 3 |
| 11,184,810 – 13,981,012 sat | 4 |
| 13,981,013 – 16,777,214 sat | 5 |
| 16,777,215 sat (0.16777215 BTC) and above, incl. wumbo | 6 |

## Why you can't raise it

`peer/brontide.go`:

```go
numConfs := p.cfg.ChannelCloseConfs.UnwrapOrFunc(func() uint32 {
    // No override, use normal capacity-based scaling.
    return lnwallet.CloseConfsForCapacity(chanCapacity)
})
```

`ChannelCloseConfs` is `s.cfg.Dev.ChannelCloseConfs()`. Under `//go:build !integration`
(`lncfg/dev.go`), `DevConfig` is an empty struct and:

```go
// ChannelCloseConfs returns the config value for channel close confirmations
// override, which is always None for production build.
func (d *DevConfig) ChannelCloseConfs() fn.Option[uint32] {
	return fn.None[uint32]()
}
```

The `--force-channel-close-confs` flag that would populate it exists only in
`lncfg/dev_integration.go`, behind `//go:build integration`.

Tracked upstream at [lnd#11072](https://github.com/lightningnetwork/lnd/issues/11072).

**Do not confuse this with `--coop-close-target-confs`.** That flag is a fee-estimation
target used as a lower bound during close fee negotiation. It has no effect on how long
lnd waits before considering a close final.

## Context

This all comes out of t-bast's [disclosure](https://delvingbitcoin.org/t/disclosure-lnd-doesnt-wait-for-enough-confirmations-when-closing-channels/2800)
of 2026-08-13 and the fix in [lnd#10331](https://github.com/lightningnetwork/lnd/pull/10331).
Before that fix the effective depth on the coop-close forget path was 1.

For comparison, from the same day's source reading:

| Implementation | Depth | Where |
|---|---|---|
| lnd (post-fix) | 3–6, scaled | `lnwallet/confscale.go`, `confscale_prod.go` |
| LDK | 6, flat | `ANTI_REORG_DELAY` in `lightning/src/chain/channelmonitor.rs` |
| Eclair | 8, configurable | `min-depth-blocks` in `reference.conf` |
| CLN | 100 | `onchaind/onchaind.c` — this is BOLT #5 "irrevocably resolved" |


## What BOLT actually says (correction, 2026-08-14)

I originally repeated, from the disclosure, that "the BOLT specification recommends 6
confirmations." **It does not.** I checked all of BOLT 1–11 afterwards and the claim
doesn't hold. Since this tool prints a comparison against 6, here is exactly what is
and isn't in the spec.

**The only "6 confirmations" in the entire BOLT spec is in BOLT #7**, and it gates
gossip, not safety:

```
- If the funding transaction has at least 6 confirmations:
  - SHOULD queue the `channel_announcement` message for its peers.
...
- If the funding transaction has less than 6 confirmations:
  - MUST NOT send `channel_announcement`.
```

That is a rule about when a channel may be announced to the network. It says nothing
about when your funds are safe from a reorg.

**BOLT #5's number is 100.** It defines *irrevocably resolved* as:

> Outputs that are *resolved* are considered *irrevocably resolved* once the remote's
> *resolving* transaction is included in a block at least 100 deep, on the most-work
> blockchain. 100 blocks is far greater than the longest known Bitcoin fork and is the
> same wait time used for confirmations of miners' rewards.

and the monitoring obligation is scoped to it:

> until all outputs are *irrevocably resolved*:
>   - MUST monitor the blockchain for transactions that spend any output that is NOT
>     *irrevocably resolved*.

**BOLT #2 doesn't fix a number either.** It leaves reorg depth as a parameter `R` in the
`cltv_expiry_delta` derivation, and only remarks that three-deep reorgs are unlikely "for
`R` of 2 or more". `minimum_depth` is left to the accepter's judgement ("SHOULD set
`minimum_depth` to a number of blocks it considers reasonable"), with a hard 100 required
only when the funding transaction is a coinbase.

So the honest summary is: **there is no BOLT-specified reorg-safety depth for channel
closes.** The only normative finality number is BOLT #5's 100, and every implementation
except CLN is far below it — lnd at 3–6, LDK at 6, Eclair at 8. The 6 that everyone
reaches for comes from Bitcoin's general six-confirmation convention and from BOLT #7's
announcement gate, which is an easy and very understandable conflation.

That does not make the implementations wrong. Monitoring every closed channel for 100
blocks is a real cost, and 6 is a defensible practical choice. It does mean that when
someone says an implementation is "below spec" at 3, the spec they mean is not written
down anywhere.

This tool therefore compares against 6 as a **convention**, and separately notes BOLT #5's
100, rather than calling 6 a requirement.
