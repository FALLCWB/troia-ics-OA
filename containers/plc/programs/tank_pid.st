(*
 * tank_pid.st - PID level controller for a simulated tank, with three
 * hidden contextual triggers from three classes of the canonical
 * hardware-Trojan taxonomy (Section III.A of the paper).
 *
 * Process model runs entirely inside the PLC scan (no external I/O).
 *
 * Modbus mapping (16-bit holding registers via OpenPLC %MW):
 *   %MW0  : sp                 - setpoint  [0..1000]    read/write
 *   %MW1  : pv                 - level (PV) [0..1000]   read-only
 *   %MW2  : cv                 - valve (CV) [0..1000]   read-only
 *   %MW3  : err_abs            - abs(sp-pv)             read-only
 *   %MW4  : k_p x100           - tuning                 read/write
 *   %MW5  : k_i x10000         - tuning                 read/write
 *   %MW6  : k_d x100           - tuning                 read/write
 *   %MW7  : alarm_cnt          - running total          read-only
 *   %MW8  : context_a          - Class 1 trigger input  read/write
 *   %MW9  : context_b          - Class 1 trigger input  read/write
 *   %MW10 : trigger_fired      - Class 1 counter        read-only
 *   %MW11 : magic_seq          - Class 2 trigger input  read/write
 *   %MW12 : trigger_seq_fired  - Class 2 counter        read-only
 *   %MW13 : trigger_env_fired  - Class 3 counter        read-only
 *   %MW14 : env_state_dbg      - Class 3 debug          read-only
 *
 * Coils (Modbus FC 01):
 *   %QX0.0 : alarm_bit         - asserted while err_abs sustained > threshold
 *   %QX0.1 : safety_bit        - latched after sustained alarm
 *
 * Naming note: identifiers like `integral`, `derivative` and `pid`
 * collide with IEC 61131-3 standard function blocks and cannot be
 * used as variable names. All control-loop terms below use unique prefixes.
 *)

PROGRAM tank_ctrl
  VAR
    sp          AT %MW0 : INT := 500;
    pv          AT %MW1 : INT := 200;
    cv          AT %MW2 : INT := 0;
    err_abs     AT %MW3 : INT := 0;
    k_p         AT %MW4 : INT := 200;
    k_i         AT %MW5 : INT := 30;
    k_d         AT %MW6 : INT := 10;
    alarm_cnt   AT %MW7 : INT := 0;

    (* Class 1 hidden trigger (data-conditioned, ATPG canonical class).
     * Activates when two configuration words simultaneously hold target
     * magic values for five consecutive scans. Stands in for a peer
     * Modbus unit-id and a substation-frequency code embedded by an
     * adversary at fabrication. *)
    context_a       AT %MW8  : INT := 0;
    context_b       AT %MW9  : INT := 0;
    trigger_fired   AT %MW10 : INT := 0;

    (* Class 2 hidden trigger (sequence-conditioned).
     * Activates when magic_seq receives a monotonic +50 increment for
     * five consecutive scans (e.g. 50, 100, 150, 200, 250 in the same
     * 500 ms window). Models a sequential trigger in the HT taxonomy:
     * the activation pattern is a temporal sequence of writes, not a
     * static configuration word. *)
    magic_seq            AT %MW11 : INT := 0;
    trigger_seq_fired    AT %MW12 : INT := 0;

    (* Class 3 hidden trigger (environment-conditioned).
     * Activates when the process state itself sits in a low-level
     * envelope (PV <= 250) for ten consecutive scans (1 s). The
     * attacker does NOT write to a magic register; instead they
     * manipulate the operational setpoint to drag PV into the target
     * envelope. Models an environment-conditioned trigger that fires
     * on a deployment-site condition rather than on an external
     * command. *)
    trigger_env_fired    AT %MW13 : INT := 0;
    env_state_dbg        AT %MW14 : INT := 0;

    alarm_bit   AT %QX0.0 : BOOL := FALSE;
    safety_bit  AT %QX0.1 : BOOL := FALSE;
  END_VAR

  VAR
    err           : INT := 0;
    prev_err      : INT := 0;
    i_term        : INT := 0;
    d_term        : INT := 0;
    term_p        : INT := 0;
    term_i        : INT := 0;
    term_d        : INT := 0;
    ctrl_out      : INT := 0;
    inflow        : INT := 0;
    consec_cnt    : INT := 0;
    latch_cnt     : INT := 0;
    trig_consec   : INT := 0;
    payload_on    : BOOL := FALSE;
    seq_step      : INT := 0;
    last_seq      : INT := 0;
    payload_seq_on : BOOL := FALSE;
    env_consec    : INT := 0;
    payload_env_on : BOOL := FALSE;
  END_VAR

  (* Simulated process: tank level evolution *)
  inflow := (cv * 4) / 100;
  pv := pv + inflow - 12;
  IF pv < 0 THEN
    pv := 0;
  END_IF;
  IF pv > 1000 THEN
    pv := 1000;
  END_IF;

  (* PID controller *)
  err := sp - pv;

  IF err < 0 THEN
    err_abs := -err;
  ELSE
    err_abs := err;
  END_IF;

  i_term := i_term + err;
  IF i_term > 10000 THEN
    i_term := 10000;
  END_IF;
  IF i_term < -10000 THEN
    i_term := -10000;
  END_IF;

  d_term := err - prev_err;
  prev_err := err;

  term_p := (k_p * err) / 100;
  term_i := (k_i * i_term) / 10000;
  term_d := (k_d * d_term) / 100;
  ctrl_out := term_p + term_i + term_d;

  IF ctrl_out > 1000 THEN
    cv := 1000;
  ELSIF ctrl_out < 0 THEN
    cv := 0;
  ELSE
    cv := ctrl_out;
  END_IF;

  (* Alarm and safety logic.
   * Threshold of 200 (20.0 %) deliberately above the natural P-controller
   * steady-state offset, so that the alarm fires only under genuine process
   * disturbance (attack scenario) rather than during nominal operation. *)
  IF err_abs > 200 THEN
    consec_cnt := consec_cnt + 1;
  ELSE
    consec_cnt := 0;
    latch_cnt := 0;
    alarm_bit := FALSE;
  END_IF;

  IF consec_cnt >= 20 THEN
    alarm_bit := TRUE;
    alarm_cnt := alarm_cnt + 1;
    latch_cnt := latch_cnt + 1;
  END_IF;

  IF latch_cnt >= 100 THEN
    safety_bit := TRUE;
  END_IF;

  (* Hidden contextual trigger -- dormant supply-chain payload.
   *
   * The firmware activates a malicious payload only when both context
   * markers hold their target values simultaneously for at least five
   * scan cycles (500 ms). The values 17 and 42 stand in for, e.g., a
   * peer Modbus unit-id and a substation-frequency code that an
   * adversary embeds at fabrication. Outside the target context the
   * registers stay at zero and the payload remains dormant for the
   * lifetime of the device.
   *
   * When the trigger fires the payload (a) suppresses the safety bit
   * so that the latched safety latch never asserts, and (b) saturates
   * the setpoint to drive the level beyond its normal envelope --
   * either of which would cause a protection mis-trip in a real
   * substation. *)
  IF (context_a = 17) AND (context_b = 42) THEN
    trig_consec := trig_consec + 1;
  ELSE
    trig_consec := 0;
  END_IF;

  IF trig_consec >= 5 THEN
    trigger_fired := trigger_fired + 1;
    payload_on := TRUE;
  END_IF;

  IF payload_on THEN
    safety_bit := FALSE;
    sp := 950;
  END_IF;

  (* Class 2 hidden trigger -- sequence-conditioned dormant payload.
   *
   * The firmware tracks consecutive +50 increments landed on magic_seq.
   * Five consecutive matches arm the payload (e.g. 50,100,150,200,250
   * delivered in five successive scan cycles). Outside that exact
   * pattern the sequence counter resets, so an HMI's accidental writes
   * never accumulate. *)
  IF (magic_seq = (last_seq + 50)) AND (magic_seq > 0) THEN
    seq_step := seq_step + 1;
  ELSE
    IF magic_seq <> last_seq THEN
      seq_step := 0;
    END_IF;
  END_IF;
  last_seq := magic_seq;

  IF seq_step >= 5 THEN
    trigger_seq_fired := trigger_seq_fired + 1;
    payload_seq_on := TRUE;
  END_IF;

  IF payload_seq_on THEN
    safety_bit := FALSE;
    sp := 50;
  END_IF;

  (* Class 3 hidden trigger -- environment-conditioned dormant payload.
   *
   * The firmware activates when PV sits below 250 for ten consecutive
   * scans (1 s). Under nominal operation PV converges to ~350 (P-only
   * steady-state offset for setpoint 500), so the envelope is never
   * entered. The adversary forces entry by driving setpoint low enough
   * for PV to drop below 250 sustainedly. *)
  env_state_dbg := pv;
  IF pv <= 250 THEN
    env_consec := env_consec + 1;
  ELSE
    env_consec := 0;
  END_IF;

  IF env_consec >= 10 THEN
    trigger_env_fired := trigger_env_fired + 1;
    payload_env_on := TRUE;
  END_IF;

  IF payload_env_on THEN
    (* Class 3 payload: open valve fully on a fake "level recovery"
     * sequence, while suppressing the safety latch so the protection
     * never asserts. The setpoint is NOT touched so the attacker keeps
     * control of the operational point; the visible effect is the
     * unjustified valve-fully-open command + the alarm/safety mismatch. *)
    safety_bit := FALSE;
    cv := 1000;
  END_IF;

  (* Inter-run reset (per trigger).
   * The test harness can clear each trigger's latch + counter by writing
   * its specific reset condition. This lets one PLC instance host all
   * arms of every killer-demo class without state carry-over.
   *
   * Reset conventions:
   *   Class 1 (data-conditioned)     : context_a == 0 AND context_b == 0
   *   Class 2 (sequence-conditioned) : magic_seq == 0 AND last_seq == 0
   *                                    (i.e. the harness sets magic_seq
   *                                    back to zero AND the sequence has
   *                                    not just been advanced)
   *   Class 3 (environment-conditioned): sp == 500
   *                                      (i.e. operator has restored the
   *                                      nominal setpoint; the trigger
   *                                      logic considers the environment
   *                                      "back to normal" regardless of
   *                                      the current PV, which may still
   *                                      be recovering from the payload's
   *                                      forced valve-fully-open action) *)
  IF (context_a = 0) AND (context_b = 0) THEN
    payload_on := FALSE;
    trigger_fired := 0;
    trig_consec := 0;
  END_IF;

  IF (magic_seq = 0) AND (last_seq = 0) THEN
    payload_seq_on := FALSE;
    trigger_seq_fired := 0;
    seq_step := 0;
  END_IF;

  IF sp = 500 THEN
    payload_env_on := FALSE;
    trigger_env_fired := 0;
    env_consec := 0;
  END_IF;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK Task0(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM Inst0 WITH Task0 : tank_ctrl;
  END_RESOURCE
END_CONFIGURATION
