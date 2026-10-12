# Widget CPU and connection recovery

- [ ] Profile unchanged Due and pinned original DBOS; separate profiler runs from latency/resource results and retain process/phase attribution.
- [ ] Reproduce a terminated worker SQL session through the real Celery path before changing its lifecycle.
- [ ] Preserve worker boundaries, atomic/fenced settlement, failure classification, lost-hint recovery and minimum business waits without adding durable rows.
- [ ] Bind recovery after connection loss to pytest-obligation with independent terminal/inventory outcomes and a negative control that disables the changed production behavior.
- [ ] Compare baseline Due, candidate Due and original DBOS under the same runner conditions in both orders; record CPU, connection recovery and SQL mutation counts.
- [ ] Require generated application proof cases and real-process fault journeys in CI.
