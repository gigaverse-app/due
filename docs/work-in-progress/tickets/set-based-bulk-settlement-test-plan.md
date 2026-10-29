# Set-based completion prototype

- [x] Three provider receipts produce one work UPDATE and one receipt-marker UPDATE.
- [x] Product-hook failure rolls back the group; healthy rows use ordinary settlement without another provider call.
- [x] Process death after combined work persistence converges through registered recovery.
- [ ] Token, revision and state replacement cannot publish buffered completion, in both modes.
- [ ] Custom model saves and signals keep the ordinary persistence path.
- [ ] Generated obligation proofs cover commit crashes, lost buffers, lost recovery and duplicate-effect regression controls, with inline and separate receipts.
- [ ] Native mail comparison measures SQL, commits, total transactions, CPU, latency and PostgreSQL work.

The opt-in requires independent hooks and accepts shared statement timestamps. No new claim, receipt or recovery lifecycle is introduced.
