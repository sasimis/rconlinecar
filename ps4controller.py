import pygame, time, socketio

# init joystick
pygame.init(); pygame.joystick.init()
if pygame.joystick.get_count()==0:
    print("No controller found"); exit()
js = pygame.joystick.Joystick(0); js.init()
print(f"→ Detected joystick “{js.get_name()}” with "
      f"{js.get_numaxes()} axes and {js.get_numbuttons()} buttons.")
# show the rest values once at start:
for i in range(js.get_numaxes()):
    print(f"  Axis {i:2d} initial value: {js.get_axis(i): .3f}")
time.sleep(1)
print("— OK, now move sticks/triggers and watch the live debug below —")

sio = socketio.Client()
sio.connect('http://localhost:5000', transports=['websocket'])
print("Connected, SID:", sio.sid)

gear = 1; MAX_GEAR=5
prev = set()
DZ=0.1; DELAY=0.05

try:
    while True:
        pygame.event.pump()
        rs = js.get_axis(0)
        rt = js.get_axis(5); lt = js.get_axis(4)
        thr = (rt+1)/2 if (rt+1)/2> DZ else 0
        brk = (lt+1)/2 if (lt+1)/2> DZ else 0

        # buttons
        buttons = {i:bool(js.get_button(i)) for i in range(js.get_numbuttons())}
        name_map = {0:'square',1:'x',2:'circle',3:'triangle',
                    4:'options',10:'R1',6:'L2',7:'R2',
                    8:'share',9:'L1'}
        pressed = {name_map.get(i,str(i)) for i,v in buttons.items() if v}

        # shift
        if 'L1' in pressed and 'L1' not in prev:
            gear = min(gear+1, MAX_GEAR); print("Gear ↑", gear)
        if 'R1' in pressed and 'R1' not in prev:
            gear = max(gear-1, 1);         print("Gear ↓", gear)
        prev = pressed

        # only print when something non-zero happens
        if abs(rs) > 0.05 or thr > 0.05 or brk > 0.05:
            print(f"[DBG] steer={rs:.3f}, throttle={thr:.3f}, "
                  f"brake={brk:.3f}, gear={gear}")

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
