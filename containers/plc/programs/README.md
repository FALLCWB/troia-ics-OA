# OpenPLC programs

## tank_pid.st

Simple PID level controller for a simulated tank. The process model runs entirely inside the PLC scan (no external I/O), making it self-contained and deterministic for experimentation.

### IEC 61131-3 keyword constraints

The `matiec` compiler used by OpenPLC v3 reserves identifiers `INTEGRAL`, `DERIVATIVE`, `PID`, `HYSTERESIS`, `LIMIT`, `TOTALIZER`, etc. as standard function-block names. Variables named after these will fail to compile. The variable names in `tank_pid.st` therefore use short, unambiguous identifiers (`i_term`, `d_term`, `term_p`, `term_i`, `term_d`, `ctrl_out`) instead of the natural full names. The semantics are unchanged.

### Holding registers (Modbus function code 03)

OpenPLC v3 maps `%MW0..%MWn` to Modbus holding registers starting at **address 1024**, and `%QW0..%QWn` to addresses starting at 0. The table below shows both the IEC location and the resulting Modbus address that clients (SCADA-LTS, `metrics/integrity.py`, the attacker) must use.

| Modbus HR | %MW addr | ST name     | Meaning                                  | Range / units                |
|-----------|----------|-------------|------------------------------------------|------------------------------|
| 1024      | %MW0     | `sp`        | setpoint (writable from HMI)             | 0..1000 (== 0.0..100.0 %)    |
| 1025      | %MW1     | `pv`        | process variable (level), read-only      | 0..1000                       |
| 1026      | %MW2     | `cv`        | control variable (valve), read-only      | 0..1000                       |
| 1027      | %MW3     | `err_abs`   | abs(sp - pv), read-only, unsigned        | 0..1000                       |
| 1028      | %MW4     | `k_p`       | proportional gain × 100, writable        | 0..10000                      |
| 1029      | %MW5     | `k_i`       | integral gain × 10000, writable          | 0..10000                      |
| 1030      | %MW6     | `k_d`       | derivative gain × 100, writable          | 0..10000                      |
| 1031      | %MW7     | `alarm_cnt` | total alarms raised since boot           | 0..65535                      |

### Coils (Modbus function code 01)

| Coil   | ST name       | Meaning                                                              |
|--------|---------------|----------------------------------------------------------------------|
| QX0.0  | `alarm_bit`   | TRUE when `err_abs > 200` for ≥ 20 scans (2 s at 100 ms scan)        |
| QX0.1  | `safety_bit`  | Latched TRUE after 100 scans of sustained alarm (~ 10 s sustained)   |

### Expected baseline behaviour

With default tuning (k_p = 200, k_i = 30, k_d = 10), outflow = 12 units/scan, and setpoint = 500: a steady-state P-controller offset of approximately 150 units (15.0 %) is observed (PV ≈ 350 with SP = 500). The integral term is intentionally clamped at ±10000 to give the system a deterministic and reproducible offset rather than slow asymptotic convergence; in this regime the testbed produces consistent baseline traces while still leaving sufficient headroom for an attack to push the deviation past the alarm threshold.

The `alarm_bit` threshold (`err_abs > 200`) is deliberately set above the natural P-controller offset, so that `alarm_bit` and the `alarm_cnt` counter remain idle under nominal operation and only trip when a trigger scenario drives the process away from its baseline steady state. This is what makes the alarm/safety channels diagnostically useful for the experimental matrix.

The `metrics/integrity.py` script polls these registers at 10 Hz and computes the RMS deviation of `pv` from `sp`. A run with deviation > 80 (8.0 %) is tagged `unsafe` *relative to setpoint*; for the calibrated baseline the deviation is ~150 so the absolute threshold needs to be interpreted with care — the change in deviation between baseline and attack scenarios is the meaningful signal.
