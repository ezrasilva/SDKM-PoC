import subprocess
import time
import json
import os
import sys
import threading
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor

# --- CONFIGURAÇÕES GERAIS ---
# Ajuste o diretório de scripts se estiver rodando de outra pasta
SCRIPTS_DIR = os.path.abspath("./scripts") if os.path.exists("./scripts") else os.path.abspath("../scripts")
METRICS_FILE = "experiment_metrics.csv"
RESOURCE_FILE = "resource_metrics.csv"

# Configuração de Logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("QuantumBench")

class ExperimentController:
    def __init__(self, duration, profile, iterations, interval, is_baseline):
        self.duration = duration
        self.profile = profile
        self.iterations = iterations
        self.interval = interval
        self.is_baseline = is_baseline
        self.start_time = time.time()
        self.running = True
        
        # Mapeamento Lógico -> Real
        # Se for baseline, usa prefixo 'base_' (ex: base_alice)
        # Se for normal, usa nomes diretos (ex: alice)
        self.logical_nodes = ['alice', 'bob', 'carol', 'dave']
        self.container_map = {}
        
        prefix = "base_" if is_baseline else ""
        for node in self.logical_nodes:
            self.container_map[node] = f"{prefix}{node}"
            
        # Lista de containers reais para Docker commands
        self.real_containers = list(self.container_map.values())
        
        # Orquestrador só existe no modo Normal
        if not is_baseline:
            self.orchestrator_name = "orchestrator"
            self.all_containers = self.real_containers + [self.orchestrator_name]
        else:
            self.orchestrator_name = None
            self.all_containers = self.real_containers

        # Garante que diretórios existam
        # Se estivermos na pasta baseline, os scripts podem estar um nível acima
        if not os.path.exists(SCRIPTS_DIR):
             logger.warning(f"Diretório de scripts {SCRIPTS_DIR} não encontrado!")

    def get_cname(self, logical_name):
        """Retorna o nome real do container (ex: alice -> base_alice)"""
        return self.container_map.get(logical_name, logical_name)

    def run_cmd(self, cmd, detached=False, shell=False, ignore_errors=False):
        try:
            if detached:
                return subprocess.Popen(cmd, shell=shell, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                return subprocess.run(cmd, shell=shell, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            if not ignore_errors:
                logger.error(f"Erro ao executar '{cmd}': {e.stderr}")
            return None

    def docker_exec(self, logical_name, cmd, detached=False, ignore_errors=False):
        container = self.get_cname(logical_name)
        full_cmd = f"docker exec {'-d' if detached else ''} {container} {cmd}"
        return self.run_cmd(full_cmd, shell=True, detached=detached, ignore_errors=ignore_errors)

    def install_iperf(self, logical_name):
        """Instala iperf3 no container (mapa nome lógico -> real)"""
        container = self.get_cname(logical_name)
        
        # 1. Verifica se já existe
        check = self.run_cmd(f"docker exec {container} which iperf3", shell=True, ignore_errors=True)
        if check and check.returncode == 0:
            return 

        logger.info(f"🛠️  Instalando iperf3 em {container}...")
        
        # 2. Tenta APK (Alpine)
        res_apk = self.run_cmd(f"docker exec {container} apk add --no-cache iperf3", shell=True, ignore_errors=True)
        if res_apk and res_apk.returncode == 0:
            return

        # 3. Tenta APT (Debian/Ubuntu)
        self.run_cmd(f"docker exec {container} apt-get update", shell=True, ignore_errors=True)
        res_apt = self.run_cmd(f"docker exec {container} apt-get install -y iperf3", shell=True, ignore_errors=True)
        
        if not res_apt or res_apt.returncode != 0:
            logger.error(f"❌ Falha crítica: Não foi possível instalar iperf3 em {container}.")

    def setup_environment(self):
        logger.info(f"♻️  Reiniciando ambiente {'BASELINE' if self.is_baseline else 'QUANTUM'}...")
        
        # Assume que o docker-compose.yml está no diretório atual
        self.run_cmd("docker compose down", shell=True)
        time.sleep(5)
        self.run_cmd("docker compose up -d", shell=True)
        
        # Tempo de estabilização
        wait_time = 15 if self.is_baseline else 30
        logger.info(f"⏳ Aguardando {wait_time}s para estabilização...")
        time.sleep(wait_time)
        
        # Aplica perfil de rede (Simulação WAN)
        if self.profile != "perfect":
            logger.info(f"🌐 Aplicando perfil de rede: {self.profile}")
            # Passa a lista EXPLICITA de containers para o script de rede saber quem limitar
            containers_str = " ".join(self.real_containers)
            cmd = f"python3 {SCRIPTS_DIR}/simulate_network_conditions.py --profile {self.profile} --containers {containers_str}"
            self.run_cmd(cmd, shell=True)
        
        # Instalação de Dependências
        logger.info("🛠️  Verificando dependências...")
        with ThreadPoolExecutor(max_workers=4) as executor:
            for node in self.logical_nodes:
                executor.submit(self.install_iperf, node)

        # Limpa arquivos antigos
        for f in [METRICS_FILE, RESOURCE_FILE]:
            if os.path.exists(f): os.remove(f)
        
        with open(RESOURCE_FILE, 'w') as f:
            f.write("timestamp,container,cpu_perc,mem_mib\n")

    def monitor_resources_thread(self):
        logger.info("📈 Iniciando monitoramento de recursos...")
        while self.running:
            try:
                ts = time.time()
                # Monitora apenas os containers relevantes para o modo atual
                cmd = "docker stats " + " ".join(self.all_containers) + " --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}'"
                res = self.run_cmd(cmd, shell=True, ignore_errors=True)
                
                if res and res.stdout:
                    with open(RESOURCE_FILE, 'a') as f:
                        for line in res.stdout.strip().split('\n'):
                            if not line: continue
                            parts = line.split(',')
                            if len(parts) >= 3:
                                name = parts[0]
                                cpu = parts[1].replace('%', '')
                                mem = parts[2].split('/')[0].strip().replace('MiB', '').replace('GiB', '')
                                f.write(f"{ts},{name},{cpu},{mem}\n")
            except Exception:
                pass
            time.sleep(1)

    def run_throughput_test(self, iteration):
        duration = self.duration
        logger.info(f"🚀 [Iter {iteration}] Throughput TCP ({duration}s)...")
        
        # 1. Inicia Servidores (Bob e Dave)
        self.docker_exec("bob", "pkill iperf3", ignore_errors=True)
        self.docker_exec("dave", "pkill iperf3", ignore_errors=True)
        self.docker_exec("bob", "iperf3 -s -p 5201", detached=True)
        self.docker_exec("dave", "iperf3 -s -p 5202", detached=True)
        time.sleep(2)
        
        # IPs fixos da rede de transporte (definidos no docker-compose)
        # Alice -> Bob (192.168.100.11)
        # Carol -> Dave (192.168.100.13)
        
        def run_client(node_src, target_ip, port, pair_name):
            cmd = f"iperf3 -c {target_ip} -p {port} -t {duration} -J"
            res = self.docker_exec(node_src, cmd)
            if res and res.stdout:
                try:
                    data = json.loads(res.stdout)
                    data['experiment_meta'] = {
                        'pair': pair_name, 
                        'iteration': iteration, 
                        'type': 'throughput',
                        'mode': 'baseline' if self.is_baseline else 'quantum'
                    }
                    return data
                except json.JSONDecodeError:
                    return None
            return None

        results = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(run_client, "alice", "192.168.100.11", 5201, "alice-bob"),
                executor.submit(run_client, "carol", "192.168.100.13", 5202, "carol-dave")
            ]
            for f in futures:
                if f.result(): results.append(f.result())

        self._append_to_json(results, "throughput_combined.json")
        logger.info(f"✅ [Iter {iteration}] Throughput concluído.")

    def run_video_test(self, iteration):
        duration = self.duration
        logger.info(f"🎥 [Iter {iteration}] Vídeo UDP ({duration}s)...")
        bitrate = "5M"
        
        self.docker_exec("bob", "pkill iperf3", ignore_errors=True)
        self.docker_exec("dave", "pkill iperf3", ignore_errors=True)
        self.docker_exec("bob", "iperf3 -s -p 5201", detached=True)
        self.docker_exec("dave", "iperf3 -s -p 5202", detached=True)
        time.sleep(2)
        
        def run_udp(node_src, target_ip, port, pair_name):
            cmd = f"iperf3 -c {target_ip} -p {port} -u -b {bitrate} -t {duration} -J"
            res = self.docker_exec(node_src, cmd)
            if res and res.stdout:
                try:
                    data = json.loads(res.stdout)
                    data['experiment_meta'] = {
                        'pair': pair_name, 
                        'iteration': iteration, 
                        'type': 'video_jitter',
                        'mode': 'baseline' if self.is_baseline else 'quantum'
                    }
                    return data
                except json.JSONDecodeError:
                    return None
            return None

        results = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(run_udp, "alice", "192.168.100.11", 5201, "alice-bob"),
                executor.submit(run_udp, "carol", "192.168.100.13", 5202, "carol-dave")
            ]
            for f in futures:
                if f.result(): results.append(f.result())

        self._append_to_json(results, "video_combined.json")
        logger.info(f"✅ [Iter {iteration}] Vídeo concluído.")

    def run_file_transfer(self, iteration):
        logger.info(f"📂 [Iter {iteration}] Transferência de Arquivo...")
        size_mb = 500
        file_path = "/tmp/real_test_file.bin"
        
        # 1. Cria arquivos (Verifica se já existem)
        gen_cmd = f"dd if=/dev/urandom of={file_path} bs=1M count={size_mb} status=none"
        check = self.docker_exec("alice", f"ls {file_path}", ignore_errors=True)
        
        if not check or check.returncode != 0:
            with ThreadPoolExecutor(max_workers=2) as executor:
                executor.submit(self.docker_exec, "alice", gen_cmd)
                executor.submit(self.docker_exec, "carol", gen_cmd)
        
        # 2. Servidores
        self.docker_exec("bob", "pkill iperf3", ignore_errors=True)
        self.docker_exec("dave", "pkill iperf3", ignore_errors=True)
        self.docker_exec("bob", "iperf3 -s -p 5201", detached=True)
        self.docker_exec("dave", "iperf3 -s -p 5202", detached=True)
        time.sleep(2)

        # 3. Envia
        def run_file_send(node_src, target_ip, port, pair_name):
            cmd = f"iperf3 -c {target_ip} -p {port} -F {file_path} -J"
            res = self.docker_exec(node_src, cmd)
            if res and res.stdout:
                try:
                    data = json.loads(res.stdout)
                    data['experiment_meta'] = {
                        'pair': pair_name, 
                        'iteration': iteration, 
                        'type': 'file_transfer',
                        'mode': 'baseline' if self.is_baseline else 'quantum'
                    }
                    return data
                except json.JSONDecodeError:
                    return None
            return None

        results = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(run_file_send, "alice", "192.168.100.11", 5201, "alice-bob"),
                executor.submit(run_file_send, "carol", "192.168.100.13", 5202, "carol-dave")
            ]
            for f in futures:
                if f.result(): results.append(f.result())

        self._append_to_json(results, "file_transfer_combined.json")
        logger.info(f"✅ [Iter {iteration}] Arquivo concluído.")

    def _append_to_json(self, new_data, filename):
        # Salva no diretório atual (pode ser baseline_final ou raiz)
        path = filename
        data = []
        if os.path.exists(path):
            try:
                with open(path, 'r') as f: data = json.load(f)
            except: pass
        
        if isinstance(new_data, list): data.extend(new_data)
        else: data.append(new_data)
            
        with open(path, 'w') as f: json.dump(data, f, indent=2)

    def archive_metrics(self):
        # Cria pasta separada para baseline se necessário
        prefix = "baseline_" if self.is_baseline else "sdn_"
        dest_dir = f"metrics_{prefix}real/{self.profile}"
        
        os.makedirs(dest_dir, exist_ok=True)
        logger.info(f"💾 Arquivando métricas em {dest_dir}...")
        
        files = [
            METRICS_FILE, RESOURCE_FILE,
            "throughput_combined.json", "video_combined.json",
            "file_transfer_combined.json"
        ]
        for fname in files:
            if os.path.exists(fname):
                self.run_cmd(f"cp {fname} {dest_dir}/", shell=True)

    def cleanup(self):
        self.running = False
        logger.info("🧹 Limpando ambiente...")
        # Passa nomes reais para limpar as regras de rede corretamente
        containers_str = " ".join(self.real_containers)
        self.run_cmd(f"python3 {SCRIPTS_DIR}/simulate_network_conditions.py --clear --containers {containers_str}", shell=True)
        self.run_cmd("docker compose down", shell=True)

    def run(self):
        try:
            self.setup_environment()
            
            mon_thread = threading.Thread(target=self.monitor_resources_thread)
            mon_thread.start()
            
            for i in range(1, self.iterations + 1):
                logger.info(f"\n--- INICIANDO ITERAÇÃO {i}/{self.iterations} ---")
                self.run_throughput_test(i)
                time.sleep(5)
                self.run_video_test(i)
                time.sleep(5)
                self.run_file_transfer(i)
                logger.info(f"--- FIM ITERAÇÃO {i} ---\n")
                
                if i < self.iterations:
                    logger.info(f"💤 Intervalo ({self.interval}s)...")
                    time.sleep(self.interval)
            
            self.archive_metrics()
            
        except KeyboardInterrupt:
            logger.warning("⚠️ Interrompido pelo usuário!")
        finally:
            self.cleanup()
            if 'mon_thread' in locals():
                mon_thread.join(timeout=5)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orquestrador SDN/Baseline")
    parser.add_argument("--profile", default="wan-fiber", help="Perfil de Rede")
    parser.add_argument("--iterations", type=int, default=2, help="Ciclos")
    parser.add_argument("--duration", type=int, default=60, help="Duração (s)")
    parser.add_argument("--interval", type=int, default=10, help="Intervalo (s)")
    parser.add_argument("--baseline", action="store_true", help="Rodar modo Baseline (sem orquestrador)")
    
    args = parser.parse_args()
    
    bench = ExperimentController(
        args.duration, 
        args.profile, 
        args.iterations, 
        args.interval,
        args.baseline
    )
    bench.run()