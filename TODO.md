# TODO

Items found during roadmap work that are relevant but outside any roadmap's scope.

- **Flaky e2e test:** `tests/e2e/test_motion_pipeline.py::test_body_that_stops_reaches_zero_norm_velocity` failed in 2 of 7 full pytest runs on 2026-10-25 (GL Phase 1 gate), once while other test suites loaded the machine. It passes in isolation. Likely a timing assumption in the scripted NatNet source, the sender's rate gate or the viewer delivery wait; find the root cause rather than raising timeouts.
