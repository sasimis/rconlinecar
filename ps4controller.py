import pygame, time, socketio, json, os

# ============================================================
#  CONFIG
# ============================================================
SERVER       = os.getenv('RC_SERVER', 'https://localhost:5002')
CALIB_FILE   = 'ps4_calibration.json'
MAX_GEAR     = 5
DZ           = 0.1      # trigger dead-zone (fraction)
DELAY        = 0.05     # send loop period (s)

# Calibration detection tuning
DETECT_THRESH = 0.55    # how far an axis must move from rest to count as "the one"
HOLD_SECS     = 0.35    # how long that deviation must be held
DETECT_TIMEOUT = 20.0   # seconds before we give up and use fallback defaults

# Fallback axis/button indices if calibration is skipped/times out
FALLBACK = {
    'steer': {'axis': 0, 'rest': 0.0, 'full': 1.0},
    'throttle': {'axis': 5, 'rest': -1.0, 'full': 1.0},
    'brake': {'axis': 4, 'rest': -1.0, 'full': 1.0},
    'gear_up': 9,    # L1 on many layouts
    'gear_down': 10, # R1 on many layouts
}

# ============================================================
#  JOYSTICK INIT
# ============================================================
pygame.init(); pygame.joystick.init()
if pygame.joystick.get_count() == 0:
    print("❌ No controller found"); exit()
js = pygame.joystick.Joystick(0); js.init()
N_AXES = js.get_numaxes()
N_BTNS = js.get_numbuttons()
print(f"→ Detected “{js.get_name()}” with {N_AXES} axes and {N_BTNS} buttons.")


def pump():
    pygame.event.pump()


def read_axes():
    pump()
    return [js.get_axis(i) for i in range(N_AXES)]


def sample_rest(samples=10, gap=0.03):
    """Average axis values while controller sits untouched -> baseline."""
    acc = [0.0] * N_AXES
    for _ in range(samples):
        a = read_axes()
        for i in range(N_AXES):
            acc[i] += a[i]
        time.sleep(gap)
    return [v / samples for v in acc]


# ============================================================
#  CALIBRATION ROUTINES
# ============================================================
def detect_axis(label, rest, exclude):
    """Wait for the user to move ONE axis hard and hold it.
    Returns (idx, rest_val, full_val) or None on timeout."""
    print(f"\n🎮 {label}")
    print("   (hold it fully for half a second...)")
    t0 = time.time()
    cand_idx = None
    cand_since = None
    while time.time() - t0 < DETECT_TIMEOUT:
        cur = read_axes()
        # biggest deviation from rest among allowed axes
        best_idx, best_mag = None, 0.0
        for i in range(N_AXES):
            if i in exclude:
                continue
            mag = abs(cur[i] - rest[i])
            if mag > best_mag:
                best_idx, best_mag = i, mag
        if best_idx is not None and best_mag >= DETECT_THRESH:
            if best_idx == cand_idx and cand_since is not None:
                if time.time() - cand_since >= HOLD_SECS:
                    full_val = read_axes()[best_idx]
                    print(f"   ✓ axis {best_idx}  (rest={rest[best_idx]:+.2f} "
                          f"full={full_val:+.2f})")
                    return best_idx, rest[best_idx], full_val
            else:
                cand_idx, cand_since = best_idx, time.time()
        else:
            cand_idx, cand_since = None, None
        time.sleep(0.02)
    print("   ⚠️  timed out — using fallback default")
    return None


def detect_button(label, exclude):
    """Wait for any single button press. Returns idx or None on timeout."""
    print(f"\n🎮 {label}")
    t0 = time.time()
    # wait for all buttons released first
    while time.time() - t0 < DETECT_TIMEOUT:
        pump()
        if not any(js.get_button(i) for i in range(N_BTNS)):
            break
        time.sleep(0.02)
    while time.time() - t0 < DETECT_TIMEOUT:
        pump()
        for i in range(N_BTNS):
            if i not in exclude and js.get_button(i):
                print(f"   ✓ button {i}")
                # wait for release so it isn't re-read
                while js.get_button(i):
                    pump(); time.sleep(0.02)
                return i
        time.sleep(0.02)
    print("   ⚠️  timed out — using fallback default")
    return None


def calibrate():
    print("\n================ CALIBRATION ================")
    print("Leave the controller untouched for a moment...")
    time.sleep(0.6)
    rest = sample_rest()

    used_axes = set()
    cfg = {}

    # Steering (left stick X)
    r = detect_axis("Push the LEFT STICK fully RIGHT", rest, used_axes)
    if r:
        cfg['steer'] = {'axis': r[0], 'rest': r[1], 'full': r[2]}
        used_axes.add(r[0])
    else:
        cfg['steer'] = FALLBACK['steer']

    # Throttle (R2)
    r = detect_axis("Squeeze the RIGHT TRIGGER (R2 / accelerator) fully", rest, used_axes)
    if r:
        cfg['throttle'] = {'axis': r[0], 'rest': r[1], 'full': r[2]}
        used_axes.add(r[0])
    else:
        cfg['throttle'] = FALLBACK['throttle']

    # Brake (L2)
    r = detect_axis("Squeeze the LEFT TRIGGER (L2 / brake) fully", rest, used_axes)
    if r:
        cfg['brake'] = {'axis': r[0], 'rest': r[1], 'full': r[2]}
        used_axes.add(r[0])
    else:
        cfg['brake'] = FALLBACK['brake']

    # Gear shift buttons
    used_btns = set()
    b = detect_button("Press the button for GEAR UP (shift up)", used_btns)
    cfg['gear_up'] = b if b is not None else FALLBACK['gear_up']
    if b is not None:
        used_btns.add(b)
    b = detect_button("Press the button for GEAR DOWN (shift down)", used_btns)
    cfg['gear_down'] = b if b is not None else FALLBACK['gear_down']

    print("=============================================")
    return cfg


def load_or_calibrate():
    if os.path.exists(CALIB_FILE):
        try:
            with open(CALIB_FILE) as f:
                cfg = json.load(f)
            # basic sanity: required keys + axis indices in range
            for k in ('steer', 'throttle', 'brake'):
                if cfg[k]['axis'] >= N_AXES:
                    raise ValueError(f"{k} axis out of range")
            print(f"✅ Loaded saved calibration from {CALIB_FILE} "
                  f"(delete it to recalibrate)")
            return cfg
        except Exception as e:
            print(f"⚠️  Could not use {CALIB_FILE} ({e}); recalibrating.")
    cfg = calibrate()
    try:
        with open(CALIB_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)
        print(f"💾 Saved calibration to {CALIB_FILE}")
    except Exception as e:
        print(f"⚠️  Could not save calibration: {e}")
    return cfg


# ============================================================
#  NORMALIZATION HELPERS
# ============================================================
def norm_unipolar(v, rest, full):
    """Map raw axis -> 0..1 using captured rest/full, robust to sign/offset."""
    span = full - rest
    if span == 0:
        return 0.0
    x = (v - rest) / span
    return max(0.0, min(1.0, x))


def norm_bipolar(v, rest, full):
    """Map raw axis -> -1..1 (rest is centre, full is +1 direction)."""
    span = full - rest
    if span == 0:
        return 0.0
    x = (v - rest) / span
    return max(-1.0, min(1.0, x))


# ============================================================
#  MAIN
# ============================================================
cfg = load_or_calibrate()

print("\n📋 Active mapping:")
print(f"   steering : axis {cfg['steer']['axis']}")
print(f"   throttle : axis {cfg['throttle']['axis']}  (R2)")
print(f"   brake    : axis {cfg['brake']['axis']}  (L2)")
print(f"   gear up  : button {cfg['gear_up']}")
print(f"   gear down: button {cfg['gear_down']}")

s_ax, s_rest, s_full = cfg['steer']['axis'], cfg['steer']['rest'], cfg['steer']['full']
t_ax, t_rest, t_full = cfg['throttle']['axis'], cfg['throttle']['rest'], cfg['throttle']['full']
b_ax, b_rest, b_full = cfg['brake']['axis'], cfg['brake']['rest'], cfg['brake']['full']
GEAR_UP, GEAR_DOWN = cfg['gear_up'], cfg['gear_down']

sio = socketio.Client(ssl_verify=False)
sio.connect(SERVER, transports=['websocket'])
print("\n🟢 Connected, SID:", sio.sid)
print("— Move sticks/triggers; live debug below —\n")

gear = 1
prev_up = prev_down = False

try:
    while True:
        pump()
        rs = norm_bipolar(js.get_axis(s_ax), s_rest, s_full)
        thr = norm_unipolar(js.get_axis(t_ax), t_rest, t_full)
        brk = norm_unipolar(js.get_axis(b_ax), b_rest, b_full)
        thr = thr if thr > DZ else 0.0
        brk = brk if brk > DZ else 0.0

        # gear shift on button rising-edge
        up = bool(js.get_button(GEAR_UP)) if GEAR_UP < N_BTNS else False
        down = bool(js.get_button(GEAR_DOWN)) if GEAR_DOWN < N_BTNS else False
        if up and not prev_up:
            gear = min(gear + 1, MAX_GEAR); print("Gear ↑", gear)
        if down and not prev_down:
            gear = max(gear - 1, 1); print("Gear ↓", gear)
        prev_up, prev_down = up, down

        if abs(rs) > 0.05 or thr > 0.05 or brk > 0.05:
            print(f"[DBG] steer={rs:+.2f}, throttle={thr:.2f}, "
                  f"brake={brk:.2f}, gear={gear}")

        sio.emit('controller_input', {
            'steering': rs,
            'throttle': thr,
            'brake':    brk,
            'gear':     gear
        })
        time.sleep(DELAY)

except KeyboardInterrupt:
    pass
finally:
    pygame.quit()
    sio.disconnect()
