#!/usr/bin/env python3
"""
Script para calcular jitter estimado nos intervalos do iperf3
Baseado na variação dos intervalos entre pacotes
"""

import json
import math
from pathlib import Path


def estimate_jitter_from_intervals(intervals):
    """
    Estima o jitter baseado na variação dos intervalos de tempo entre medições
    """
    if len(intervals) < 2:
        return []
    
    # Extrair durações reais de cada intervalo
    durations = []
    for interval in intervals:
        if 'sum' in interval:
            seconds = interval['sum']['seconds']
            durations.append(seconds)
    
    # Calcular variação entre durações consecutivas (inter-packet delay variation)
    jitter_values = []
    jitter_accumulated = 0.0
    
    for i in range(len(durations)):
        if i == 0:
            # Primeiro intervalo: jitter = 0
            jitter_values.append(0.0)
        else:
            # Calcular diferença entre intervalos consecutivos
            delay_variation = abs(durations[i] - durations[i-1])
            
            # Aplicar fórmula RFC 3550 com suavização
            jitter_accumulated = jitter_accumulated + (delay_variation - jitter_accumulated) / 16.0
            jitter_values.append(jitter_accumulated * 1000)  # Converter para ms
    
    return jitter_values


def estimate_jitter_from_bitrate(intervals):
    """
    Estima o jitter baseado na variação da taxa de bits (método alternativo)
    """
    if len(intervals) < 2:
        return []
    
    # Extrair bits_per_second de cada intervalo
    bitrates = []
    for interval in intervals:
        if 'sum' in interval:
            bps = interval['sum'].get('bits_per_second', 0)
            bitrates.append(bps)
    
    # Calcular desvio padrão móvel como proxy para jitter
    window_size = 10
    jitter_values = []
    
    for i in range(len(bitrates)):
        if i < window_size:
            # Usar todos os valores disponíveis no início
            window = bitrates[:i+1]
        else:
            # Usar janela deslizante
            window = bitrates[i-window_size+1:i+1]
        
        if len(window) > 1:
            # Calcular desvio padrão manualmente
            mean = sum(window) / len(window)
            variance = sum((x - mean) ** 2 for x in window) / len(window)
            std_dev = math.sqrt(variance)
            
            # Normalizar para ms aproximado (heurística)
            jitter_ms = (std_dev / mean) * 10 if mean > 0 else 0
            jitter_values.append(jitter_ms)
        else:
            jitter_values.append(0.0)
    
    return jitter_values


def add_jitter_to_json(input_file, output_file, method='time'):
    """
    Processa o JSON e adiciona jitter estimado a cada intervalo
    
    Args:
        input_file: Arquivo JSON de entrada
        output_file: Arquivo JSON de saída com jitter
        method: 'time' (baseado em variação temporal) ou 'bitrate' (baseado em variação de taxa)
    """
    print(f"Lendo {input_file}...")
    with open(input_file, 'r') as f:
        data = json.load(f)
    
    print(f"Processando {len(data)} testes...")
    
    for test_idx, test in enumerate(data):
        if 'intervals' not in test:
            continue
        
        intervals = test['intervals']
        
        # Calcular jitter usando o método escolhido
        if method == 'time':
            jitter_values = estimate_jitter_from_intervals(intervals)
        elif method == 'bitrate':
            jitter_values = estimate_jitter_from_bitrate(intervals)
        else:
            raise ValueError(f"Método desconhecido: {method}")
        
        # Adicionar jitter a cada intervalo
        for i, interval in enumerate(intervals):
            if i < len(jitter_values):
                jitter = jitter_values[i]
                
                # Adicionar jitter ao stream individual
                if 'streams' in interval:
                    for stream in interval['streams']:
                        stream['jitter_ms'] = round(jitter, 6)
                
                # Adicionar jitter ao sum
                if 'sum' in interval:
                    interval['sum']['jitter_ms'] = round(jitter, 6)
        
        print(f"  Teste {test_idx + 1}/{len(data)}: {len(jitter_values)} intervalos processados")
    
    print(f"\nSalvando em {output_file}...")
    with open(output_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    print("✓ Concluído!")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Calcula jitter estimado para intervalos do iperf3'
    )
    parser.add_argument(
        'input_file',
        help='Arquivo JSON de entrada (ex: video_combined.json)'
    )
    parser.add_argument(
        '-o', '--output',
        help='Arquivo JSON de saída (padrão: <input>_with_jitter.json)'
    )
    parser.add_argument(
        '-m', '--method',
        choices=['time', 'bitrate'],
        default='time',
        help='Método de cálculo: time (variação temporal) ou bitrate (variação de taxa)'
    )
    
    args = parser.parse_args()
    
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Erro: Arquivo não encontrado: {input_path}")
        return 1
    
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.parent / f"{input_path.stem}_with_jitter{input_path.suffix}"
    
    add_jitter_to_json(input_path, output_path, method=args.method)
    return 0


if __name__ == '__main__':
    exit(main())
