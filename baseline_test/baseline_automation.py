import subprocess
import time
import os
import csv
import threading
import sys
import json
import argparse
from concurrent.futures import ThreadPoolExecutor

# --- CONFIGURAÇÕES ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "metrics_baseline_real")
RESOURCE_FILE = os.path.join(OUTPUT_DIR, "resource_metrics.csv")

# Definição dos Pares
PAIRS = [
    {"src": "baseline_alice", "dst": "baseline_bob",  "dst_ip": "192.168.20.3", "port": 5201, "name": "alice-bob", "child_sa": "net"},
    {"src": "baseline_carol", "dst": "baseline_dave", "dst_ip": "192.168.20.5", "port": 5202, "name": "carol-dave", "child_sa": "net"}
]
ALL_CONTAINERS = ["baseline_alice", "baseline_bob", "baseline_carol", "baseline_dave"]

# Simulação de Rede
NET_PROFILE = {
    "latency": "20ms",
    "jitter": "3ms",
    "loss": "0.05%"
}

def ensure_dir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)

def run_cmd(cmd, ignore_errors=False, shell=True):
    try:
        res = subprocess.run(cmd, shell=shell, check=True, capture_output=True, text=True)
        return res
    except subprocess.CalledProcessError as e:
        if not ignore_errors:
            print(f"❌ Erro no comando: {cmd}")
        return None

def find_charon_binary(container):
    """Procura onde o charon está escondido"""
    paths = ["/usr/libexec/ipsec/charon", "/usr/lib/ipsec/charon", "/usr/sbin/charon"]
    for p in paths:
        res = run_cmd(f"docker exec {container} ls {p}", ignore_errors=True)
        if res and res.returncode == 0:
            return p
    return "ipsec start" 

def setup_container(container):
    """Configura IPsec e Rede no container"""
    print(f"🛠️  Configurando {container}...")
    
    # 1. Instalação de pacotes
    install_cmd = (
        "export DEBIAN_FRONTEND=noninteractive; "
        "(apk add --no-cache iperf3 iproute2 || "
        "(apt-get update -qq && apt-get install -yqq iperf3 iproute2) || "
        "echo '⚠️ Aviso: Falha na instalação de pacotes')"
    )
    run_cmd(f"docker exec {container} /bin/sh -c \"{install_cmd}\"", ignore_errors=True)
    
    # 2. Inicia IPsec (Charon)
    charon_bin = find_charon_binary(container)
    start_cmd = f"{charon_bin} &" if "/" in charon_bin else charon_bin
    run_cmd(f"docker exec {container} /bin/sh -c \"{start_cmd}\"", ignore_errors=True)
    
    # 3. Aplica Latência (WAN)
    tc_cmd = (
        f"tc qdisc del dev eth0 root 2>/dev/null; "
        f"tc qdisc add dev eth0 root netem delay {NET_PROFILE['latency']} {NET_PROFILE['jitter']} distribution normal loss {NET_PROFILE['loss']}"
    )
    run_cmd(f"docker exec {container} /bin/sh -c \"{tc_cmd}\"", ignore_errors=True)

def load_swanctl_conf():
    """Carrega as configurações IPsec"""
    print("🔄 Carregando regras swanctl...")
    time.sleep(5)
    for c in ALL_CONTAINERS:
        run_cmd(f"docker exec {c} swanctl --load-all", ignore_errors=True)
    
    # Fallback para iniciar conexões
    for p in PAIRS:
        run_cmd(f"docker exec {p['src']} swanctl --initiate --child {p['child_sa']}", ignore_errors=True)

def monitor_resources(stop_event):
    """Coleta CPU/RAM"""
    print("📈 Monitoramento de recursos iniciado.")
    with open(RESOURCE_FILE, 'w') as f:
        f.write("timestamp,container,cpu_perc,mem_mib\n")
        
    while not stop_event.is_set():
        try:
            cmd = "docker stats " + " ".join(ALL_CONTAINERS) + " --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}'"
            res = run_cmd(cmd, ignore_errors=True)
            if res and res.stdout:
                ts = time.time()
                with open(RESOURCE_FILE, 'a') as f:
                    for line in res.stdout.strip().split('\n'):
                        parts = line.split(',')
                        if len(parts) >= 3:
                            name = parts[0]
                            clean_name = name.replace("baseline_", "")
                            cpu = parts[1].replace('%', '')
                            mem = parts[2].split('/')[0].strip().replace('MiB', '').replace('GiB', '')
                            f.write(f"{ts},{clean_name},{cpu},{mem}\n")
        except: pass
        time.sleep(1)

def save_json_result(data, filename):
    filepath = os.path.join(OUTPUT_DIR, filename)
    existing = []
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f: existing = json.load(f)
        except: pass
    
    if isinstance(data, list): existing.extend(data)
    else: existing.append(data)
    
    with open(filepath, 'w') as f:
        json.dump(existing, f, indent=2)

def run_experiment(iterations, duration, interval):
    ensure_dir(OUTPUT_DIR)
    
    print("\n=== 1. INICIANDO AMBIENTE DOCKER ===")
    run_cmd("docker compose down", ignore_errors=True)
    time.sleep(2)
    run_cmd("docker compose up -d")
    print("⏳ Aguardando containers (5s)...")
    time.sleep(5)
    
    print("=== 2. CONFIGURANDO SOFTWARE E REDE ===")
    with ThreadPoolExecutor(max_workers=4) as executor:
        executor.map(setup_container, ALL_CONTAINERS)
    
    load_swanctl_conf()
    
    print("⏳ Aguardando estabilização dos túneis (10s)...")
    time.sleep(10)
    
    stop_mon = threading.Event()
    mon_thread = threading.Thread(target=monitor_resources, args=(stop_mon,))
    mon_thread.start()
    
    try:
        # LOOP PRINCIPAL: Baseado puramente em 'iterations', sem limite de tempo total
        for i in range(1, iterations + 1):
            print(f"\n--- CICLO {i}/{iterations} ---")
            
            # Prepara Servidores
            for p in PAIRS:
                run_cmd(f"docker exec {p['dst']} pkill iperf3", ignore_errors=True)
                run_cmd(f"docker exec -d {p['dst']} iperf3 -s -p {p['port']}")
            time.sleep(2)
            
            # 1. TCP
            print(f"🚀 [TCP] Throughput ({duration}s)...")
            def run_tcp(pair):
                cmd = f"iperf3 -c {pair['dst_ip']} -p {pair['port']} -t {duration} -J"
                res = run_cmd(f"docker exec {pair['src']} {cmd}")
                if res and res.stdout:
                    try:
                        d = json.loads(res.stdout)
                        d['experiment_meta'] = {'pair': pair['name'], 'iteration': i, 'type': 'throughput', 'mode': 'baseline'}
                        return d
                    except: pass
                return None

            with ThreadPoolExecutor(max_workers=2) as ex:
                results = list(ex.map(run_tcp, PAIRS))
            save_json_result([r for r in results if r], "throughput_combined.json")

            # 2. UDP
            print(f"🎥 [UDP] Vídeo/Jitter ({duration}s)...")
            def run_udp(pair):
                cmd = f"iperf3 -c {pair['dst_ip']} -p {pair['port']} -u -b 5M -t {duration} -J"
                res = run_cmd(f"docker exec {pair['src']} {cmd}")
                if res and res.stdout:
                    try:
                        d = json.loads(res.stdout)
                        d['experiment_meta'] = {'pair': pair['name'], 'iteration': i, 'type': 'video_jitter', 'mode': 'baseline'}
                        return d
                    except: pass
                return None

            with ThreadPoolExecutor(max_workers=2) as ex:
                results = list(ex.map(run_udp, PAIRS))
            save_json_result([r for r in results if r], "video_combined.json")

            # 3. ARQUIVO
            print(f"📂 [FILE] Transferência 500MB...")
            gen_cmd = "dd if=/dev/urandom of=/tmp/test.bin bs=1M count=500 status=none"
            run_cmd(f"docker exec baseline_alice ls /tmp/test.bin || docker exec baseline_alice {gen_cmd}", ignore_errors=True)
            run_cmd(f"docker exec baseline_carol ls /tmp/test.bin || docker exec baseline_carol {gen_cmd}", ignore_errors=True)
            
            def run_file(pair):
                cmd = f"iperf3 -c {pair['dst_ip']} -p {pair['port']} -F /tmp/test.bin -J"
                res = run_cmd(f"docker exec {pair['src']} {cmd}")
                if res and res.stdout:
                    try:
                        d = json.loads(res.stdout)
                        d['experiment_meta'] = {'pair': pair['name'], 'iteration': i, 'type': 'file_transfer', 'mode': 'baseline'}
                        return d
                    except: pass
                return None

            with ThreadPoolExecutor(max_workers=2) as ex:
                results = list(ex.map(run_file, PAIRS))
            save_json_result([r for r in results if r], "file_transfer_combined.json")
            
            # Intervalo entre ciclos
            if i < iterations:
                print(f"💤 Intervalo ({interval}s)...")
                time.sleep(interval)
                
    except KeyboardInterrupt:
        print("\n⚠️ Interrompido!")
    finally:
        stop_mon.set()
        mon_thread.join()
        print(f"✅ Dados salvos em: {OUTPUT_DIR}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Padrões definidos para seu caso: 60 ciclos, 300s duração, 10s intervalo
    parser.add_argument("--iterations", type=int, default=60, help="Número de ciclos de teste")
    parser.add_argument("--duration", type=int, default=300, help="Duração de cada teste (TCP/UDP)")
    parser.add_argument("--interval", type=int, default=10, help="Intervalo entre ciclos")
    args = parser.parse_args()
    
    print(f"--- INICIANDO BASELINE ---")
    print(f"Ciclos: {args.iterations}")
    print(f"Duração por Teste: {args.duration}s")
    print(f"Intervalo: {args.interval}s")
    
    run_experiment(args.iterations, args.duration, args.interval)