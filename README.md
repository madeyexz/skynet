# Skynet

**Air traffic control and a black box, for the sea.**

## ▶ Try it: [skynet--hackathon-yc.onamp.dev](https://skynet--hackathon-yc.onamp.dev/)

- **Live app:** https://skynet--hackathon-yc.onamp.dev/ (no sign-in)
- **90-second demo stage:** https://skynet--hackathon-yc.onamp.dev/demo.html?auto=1
- **Data API docs:** https://skynet--hackathon-yc.onamp.dev/docs.html (demo key `sk_live_skynet_demo`)

Ships self-report their position over AIS, and that signal is more and more often switched off, jammed or faked. Skynet compares what a ship **claims** (AIS) with what **space observes** (satellite radar) and **emits** (RF), and turns the mismatch into three things:

1. **Detect**: the map stacks the three layers and shows verified, dark and false-position ships.
2. **Alert**: when something needs a decision, an alert arrives with the evidence, and watch zones decide who gets it.
3. **Report**: the same record becomes a fingerprinted evidence record, exported as a PDF.

Try the use-case switcher in the top bar: a seeded Gulf of Finland cable case (illustrative, inspired by Estlink 2), plus real data from the western Baltic cable cuts of Nov 2024, Sentinel-1 radar vs AIS off Skagen, and war risk at Hormuz.

The code, how to run it, and the deploy steps are in [`skynet/`](skynet/README.md).
