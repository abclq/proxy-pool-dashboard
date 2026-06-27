import subprocess, os, sys, time, signal
processes = []
def cleanup():
    for p in processes:
        try: p.terminate()
        except: pass
signal.signal(signal.SIGTERM, lambda *a: cleanup())
signal.signal(signal.SIGINT, lambda *a: cleanup())
os.chdir('/app')
print('Starting validator...')
p3 = subprocess.Popen([sys.executable, '-u', 'validator.py'])
processes.append(p3)
print('Starting backend on :5051...')
p1 = subprocess.Popen([sys.executable, 'backend.py'])
processes.append(p1)
print('Starting frontend on :5050...')
p2 = subprocess.Popen([sys.executable, 'frontend.py'])
processes.append(p2)
print('Dashboard running: frontend :5050, backend :5051, validator bg')
while True:
    try:
        for p in processes[:]:
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
            print(f'Process {p.args} exited, restarting...')
            processes.remove(p)
            new = subprocess.Popen(p.args)
            processes.append(new)
    except KeyboardInterrupt:
        break
cleanup()
