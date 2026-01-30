#!/usr/bin/env python3
"""
Script para simular condições de rede realistas usando tc (traffic control)
Adiciona latência, jitter e perda de pacotes nos containers Docker
"""

import subprocess
import argparse
import sys
import json
from pathlib import Path


# Perfis de rede pré-definidos
NETWORK_PROFILES = {
    "perfect": {
        "name": "Rede Perfeita (Laboratório)",
        "delay": "0ms",
        "jitter": "0ms",
        "loss": "0%",
        "bandwidth": None
    },
    "wan-fiber": {
        "name": "WAN Fibra (Internet Típica)",
        "delay": "20ms",
        "jitter": "5ms",
        "loss": "0.05%",
        "bandwidth": "300mbit"
    },
    "wan-intercontinental": {
        "name": "WAN Intercontinental (EUA-Europa)",
        "delay": "120ms",
        "jitter": "10ms",
        "loss": "0.1%",
        "bandwidth": "100mbit"
    },
    "lan": {
        "name": "LAN Típica",
        "delay": "1ms",
        "jitter": "0.5ms",
        "loss": "0.01%",
        "bandwidth": "1000mbit"
    },
    "wifi-good": {
        "name": "WiFi Bom (5GHz, perto do roteador)",
        "delay": "5ms",
        "jitter": "2ms",
        "loss": "0.1%",
        "bandwidth": "300mbit"
    },
    "wifi-medium": {
        "name": "WiFi Médio (2.4GHz, distância média)",
        "delay": "15ms",
        "jitter": "5ms",
        "loss": "0.5%",
        "bandwidth": "100mbit"
    },
    "wifi-poor": {
        "name": "WiFi Ruim (interferência, longe do roteador)",
        "delay": "30ms",
        "jitter": "15ms",
        "loss": "2%",
        "bandwidth": "20mbit"
    },
    "4g-good": {
        "name": "4G Bom (sinal forte)",
        "delay": "40ms",
        "jitter": "10ms",
        "loss": "0.2%",
        "bandwidth": "50mbit"
    },
    "4g-medium": {
        "name": "4G Médio",
        "delay": "80ms",
        "jitter": "20ms",
        "loss": "0.5%",
        "bandwidth": "20mbit"
    },
    "4g-poor": {
        "name": "4G Ruim (sinal fraco)",
        "delay": "150ms",
        "jitter": "50ms",
        "loss": "2%",
        "bandwidth": "5mbit"
    },
    "satellite": {
        "name": "Internet via Satélite",
        "delay": "600ms",
        "jitter": "100ms",
        "loss": "1%",
        "bandwidth": "10mbit"
    },
    "3g": {
        "name": "3G Típico",
        "delay": "100ms",
        "jitter": "30ms",
        "loss": "1%",
        "bandwidth": "3mbit"
    },
    "congested": {
        "name": "Rede Congestionada",
        "delay": "50ms",
        "jitter": "30ms",
        "loss": "5%",
        "bandwidth": "10mbit"
    }
}


def run_command(cmd, check=True):
    """Executa comando e retorna resultado"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=check
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        return False, e.stdout, e.stderr


def get_containers():
    """Lista containers Docker em execução"""
    success, stdout, stderr = run_command(
        "docker ps --format '{{.Names}}' | grep -E '(alice|bob|carol|dave)'",
        check=False
    )
    
    if success and stdout.strip():
        containers = stdout.strip().split('\n')
        return [c for c in containers if c]
    return []


def get_container_interface(container, interface_pattern="eth0"):
    """Obtém a interface de rede do container"""
    cmd = f"docker exec {container} ip link show | grep -E '^[0-9]+: {interface_pattern}' | awk -F': ' '{{print $2}}'"
    success, stdout, stderr = run_command(cmd, check=False)
    
    if success and stdout.strip():
        return stdout.strip()
    return interface_pattern


def clear_tc_rules(container, interface="eth0"):
    """Remove todas as regras tc existentes"""
    print(f"  Limpando regras tc em {container}...")
    cmd = f"docker exec {container} tc qdisc del dev {interface} root 2>/dev/null || true"
    run_command(cmd, check=False)


def apply_network_profile(container, profile, interface="eth0"):
    """Aplica perfil de rede a um container"""
    config = NETWORK_PROFILES[profile]
    
    print(f"\n📡 Aplicando perfil '{config['name']}' em {container}...")
    print(f"   - Latência: {config['delay']} (±{config['jitter']} jitter)")
    print(f"   - Perda: {config['loss']}")
    if config['bandwidth']:
        print(f"   - Largura de banda: {config['bandwidth']}")
    
    # Limpar regras existentes
    clear_tc_rules(container, interface)
    
    # Construir comando tc
    if config['bandwidth']:
        # Com limitação de largura de banda
        cmd_parts = [
            f"docker exec {container} tc qdisc add dev {interface} root handle 1: tbf",
            f"rate {config['bandwidth']}",
            "burst 32kbit",
            "latency 400ms"
        ]
        cmd = " ".join(cmd_parts)
        success, stdout, stderr = run_command(cmd)
        
        if not success:
            print(f"  ❌ Erro ao aplicar limitação de banda: {stderr}")
            return False
        
        # Adicionar netem como filho
        cmd_parts = [
            f"docker exec {container} tc qdisc add dev {interface} parent 1:1 handle 10: netem",
            f"delay {config['delay']} {config['jitter']}",
            f"loss {config['loss']}"
        ]
    else:
        # Sem limitação de largura de banda
        cmd_parts = [
            f"docker exec {container} tc qdisc add dev {interface} root netem",
            f"delay {config['delay']} {config['jitter']}",
            f"loss {config['loss']}"
        ]
    
    cmd = " ".join(cmd_parts)
    success, stdout, stderr = run_command(cmd)
    
    if success:
        print(f"  ✓ Regras aplicadas com sucesso")
        return True
    else:
        print(f"  ❌ Erro ao aplicar regras: {stderr}")
        return False


def apply_custom_conditions(container, delay, jitter, loss, bandwidth, interface="eth0"):
    """Aplica condições de rede customizadas"""
    print(f"\n📡 Aplicando condições customizadas em {container}...")
    print(f"   - Latência: {delay} (±{jitter} jitter)")
    print(f"   - Perda: {loss}")
    if bandwidth:
        print(f"   - Largura de banda: {bandwidth}")
    
    clear_tc_rules(container, interface)
    
    if bandwidth:
        cmd_parts = [
            f"docker exec {container} tc qdisc add dev {interface} root handle 1: tbf",
            f"rate {bandwidth}",
            "burst 32kbit",
            "latency 400ms"
        ]
        cmd = " ".join(cmd_parts)
        success, stdout, stderr = run_command(cmd)
        
        if not success:
            print(f"  ❌ Erro ao aplicar limitação de banda: {stderr}")
            return False
        
        cmd_parts = [
            f"docker exec {container} tc qdisc add dev {interface} parent 1:1 handle 10: netem",
            f"delay {delay} {jitter}",
            f"loss {loss}"
        ]
    else:
        cmd_parts = [
            f"docker exec {container} tc qdisc add dev {interface} root netem",
            f"delay {delay} {jitter}",
            f"loss {loss}"
        ]
    
    cmd = " ".join(cmd_parts)
    success, stdout, stderr = run_command(cmd)
    
    if success:
        print(f"  ✓ Regras aplicadas com sucesso")
        return True
    else:
        print(f"  ❌ Erro ao aplicar regras: {stderr}")
        return False


def show_current_rules(container, interface="eth0"):
    """Mostra as regras tc atuais"""
    print(f"\n📋 Regras atuais em {container}:")
    cmd = f"docker exec {container} tc qdisc show dev {interface}"
    success, stdout, stderr = run_command(cmd)
    
    if success:
        if "qdisc noqueue" in stdout or "qdisc pfifo_fast" in stdout:
            print("  Sem regras tc aplicadas (rede normal)")
        else:
            print(f"  {stdout.strip()}")
    else:
        print(f"  ❌ Erro ao verificar regras: {stderr}")


def list_profiles():
    """Lista todos os perfis disponíveis"""
    print("\n📋 Perfis de Rede Disponíveis:\n")
    print(f"{'Perfil':<15} {'Nome':<45} {'Latência':<12} {'Jitter':<10} {'Perda':<8} {'Banda'}")
    print("-" * 105)
    
    for key, profile in NETWORK_PROFILES.items():
        bandwidth = profile['bandwidth'] or 'ilimitada'
        print(f"{key:<15} {profile['name']:<45} {profile['delay']:<12} {profile['jitter']:<10} {profile['loss']:<8} {bandwidth}")


def main():
    parser = argparse.ArgumentParser(
        description='Simula condições de rede realistas em containers Docker',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  # Listar perfis disponíveis
  %(prog)s --list-profiles
  
  # Aplicar perfil WiFi ruim em todos os containers
  %(prog)s --profile wifi-poor
  
  # Aplicar perfil 4G médio apenas no container bob
  %(prog)s --profile 4g-medium --containers bob
  
  # Condições customizadas
  %(prog)s --delay 50ms --jitter 10ms --loss 1%% --bandwidth 20mbit
  
  # Limpar todas as regras (voltar ao normal)
  %(prog)s --clear
  
  # Ver regras atuais
  %(prog)s --show
        """
    )
    
    parser.add_argument(
        '--profile', '-p',
        choices=list(NETWORK_PROFILES.keys()),
        help='Perfil de rede pré-definido'
    )
    parser.add_argument(
        '--containers', '-c',
        nargs='+',
        help='Containers específicos (padrão: todos alice, bob, carol, dave)'
    )
    parser.add_argument(
        '--interface', '-i',
        default='eth0',
        help='Interface de rede (padrão: eth0)'
    )
    parser.add_argument(
        '--delay',
        help='Latência customizada (ex: 50ms)'
    )
    parser.add_argument(
        '--jitter',
        help='Jitter customizado (ex: 10ms)'
    )
    parser.add_argument(
        '--loss',
        help='Perda de pacotes customizada (ex: 1%%)'
    )
    parser.add_argument(
        '--bandwidth', '-b',
        help='Limitação de banda customizada (ex: 20mbit)'
    )
    parser.add_argument(
        '--clear',
        action='store_true',
        help='Remover todas as regras tc (voltar ao normal)'
    )
    parser.add_argument(
        '--show',
        action='store_true',
        help='Mostrar regras atuais'
    )
    parser.add_argument(
        '--list-profiles',
        action='store_true',
        help='Listar perfis de rede disponíveis'
    )
    
    args = parser.parse_args()
    
    # Listar perfis
    if args.list_profiles:
        list_profiles()
        return 0
    
    # Obter containers
    if args.containers:
        containers = args.containers
    else:
        containers = get_containers()
        if not containers:
            print("❌ Nenhum container encontrado. Execute docker-compose up primeiro.")
            return 1
    
    print(f"🐳 Containers encontrados: {', '.join(containers)}")
    
    # Mostrar regras
    if args.show:
        for container in containers:
            show_current_rules(container, args.interface)
        return 0
    
    # Limpar regras
    if args.clear:
        print("\n🧹 Limpando todas as regras tc...")
        for container in containers:
            clear_tc_rules(container, args.interface)
            print(f"  ✓ {container} limpo")
        print("\n✓ Rede voltou ao normal")
        return 0
    
    # Aplicar perfil
    if args.profile:
        for container in containers:
            apply_network_profile(container, args.profile, args.interface)
        return 0
    
    # Aplicar condições customizadas
    if args.delay or args.jitter or args.loss or args.bandwidth:
        delay = args.delay or "0ms"
        jitter = args.jitter or "0ms"
        loss = args.loss or "0%"
        bandwidth = args.bandwidth
        
        for container in containers:
            apply_custom_conditions(
                container, delay, jitter, loss, bandwidth, args.interface
            )
        return 0
    
    # Se nenhuma ação foi especificada, mostrar ajuda
    parser.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
