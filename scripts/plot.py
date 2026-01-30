import json
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os
import argparse

# Caminhos dinâmicos por perfil ou diretório de dados
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def resolve_paths():
    parser = argparse.ArgumentParser(description='Gerador de gráficos das métricas')
    parser.add_argument('-d', '--data-dir', default='.', help='Diretório contendo os arquivos de métricas e dados')
    parser.add_argument('-p', '--profile', help='Perfil em metrics/<perfil> (ex: lan, wifi-medium, congested)')
    args, _ = parser.parse_known_args()

    # Base padrão: diretório atual
    base_dir = os.path.abspath(args.data_dir)

    # Se perfil foi especificado, usar repo_root/metrics/<perfil>
    if args.profile:
        repo_root = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
        base_dir = os.path.join(repo_root, 'metrics', args.profile)

    # Montar caminhos dos arquivos
    metrics_csv = os.path.join(base_dir, 'experiment_metrics.csv')
    resource_csv = os.path.join(base_dir, 'resource_metrics.csv')

    # Preferir arquivo com jitter se existir
    video_with_jitter = os.path.join(base_dir, 'video_combined_with_jitter.json')
    video_plain = os.path.join(base_dir, 'video_combined.json')
    video_json = video_with_jitter if os.path.exists(video_with_jitter) else video_plain
    video_recovered = os.path.join(base_dir, 'video_metrics_recovered.json')

    throughput_json = os.path.join(base_dir, 'throughput_combined.json')

    return {
        'METRICS_CSV': metrics_csv,
        'RESOURCE_CSV': resource_csv,
        'THROUGHPUT_JSON': throughput_json,
        'VIDEO_JSON': video_json,
        'VIDEO_RECOVERED': video_recovered
    }

# --- CONFIGURAÇÃO DE ESTILO PARA ARTIGO (SBRC/IEEE) ---
plt.rcParams.update({
    'font.family': 'serif',          # Fonte serifada é padrão em artigos
    'font.size': 12,
    'axes.titlesize': 14,
    'axes.labelsize': 12,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10,
    'figure.titlesize': 16,
    'grid.alpha': 0.5,
    'lines.linewidth': 1.5
})

# ARQUIVOS (resolvidos dinamicamente)
_PATHS = resolve_paths()
METRICS_CSV = _PATHS['METRICS_CSV']
RESOURCE_CSV = _PATHS['RESOURCE_CSV']
THROUGHPUT_JSON = _PATHS['THROUGHPUT_JSON']
VIDEO_JSON = _PATHS['VIDEO_JSON']
# Fallback se o vídeo combinado falhar
VIDEO_RECOVERED = _PATHS['VIDEO_RECOVERED']

def load_json_data(filepath):
    """Carrega JSON e retorna a lista de iterações"""
    if not os.path.exists(filepath): return None
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except: return None

def load_metrics_csv(filepath):
    """
    Carrega CSV de métricas e converte timestamp absoluto para elapsed_sec.
    Suporta tanto CSVs antigos (com elapsed_sec) quanto novos (com timestamp).
    """
    if not os.path.exists(filepath):
        return None
    
    df = pd.read_csv(filepath)
    
    # Se tem 'timestamp' mas não 'elapsed_sec', converte
    if 'timestamp' in df.columns and 'elapsed_sec' not in df.columns:
        t_min = df['timestamp'].min()
        df['elapsed_sec'] = df['timestamp'] - t_min
    
    return df

def get_representative_session(data_list):
    """ABORDAGEM A: Pega a primeira sessão completa válida para representar o cenário."""
    if not isinstance(data_list, list): return None
    
    for item in data_list:
        # Tenta estrutura combinada
        if 'data' in item and 'error' not in item['data']:
            return item['data']
        # Tenta estrutura direta (fallback)
        elif 'intervals' in item and 'start' in item:
            return item
    return None

def get_rekey_times(df_metrics, start_time_ref=None):
    """
    Retorna tempos de rekey relativos ao início do teste (start_time_ref).
    start_time_ref: timestamp Unix absoluto do início do teste (iperf3)
    """
    if df_metrics is None or 'event_type' not in df_metrics.columns:
        return []
    
    # Precisa do timestamp absoluto para comparar com start_time_ref do iperf3
    if 'timestamp' in df_metrics.columns:
        rekeys_abs = df_metrics[df_metrics['event_type'] == 'TUNNEL_REKEY']['timestamp'].values
    elif 'elapsed_sec' in df_metrics.columns:
        # Se só tem elapsed_sec, usa diretamente (já é relativo)
        rekeys = df_metrics[df_metrics['event_type'] == 'TUNNEL_REKEY']['elapsed_sec'].values
        return list(rekeys)
    else:
        return []
    
    if start_time_ref:
        # Converte timestamps absolutos para tempo relativo ao início do teste
        rekeys_rel = [t - start_time_ref for t in rekeys_abs]
        # Retorna apenas rekeys que aconteceram DEPOIS do início do teste
        return [t for t in rekeys_rel if t >= 0]
    return list(rekeys_abs)

def get_best_zoom_event(df_metrics, data_start_abs, data_end_abs):
    """Encontra um rekey no meio do teste para o zoom."""
    if df_metrics is None: return None
    time_col = 'elapsed_sec' if 'elapsed_sec' in df_metrics.columns else 'timestamp'
    rekeys = df_metrics[df_metrics['event_type'] == 'TUNNEL_REKEY'][time_col].values
    # Margem de 60s das pontas
    candidates = [t for t in rekeys if data_start_abs + 60 < t < data_end_abs - 60]
    return candidates[0] if candidates else None

# --------------------------------------------------------------------------
# 1. GRÁFICO DE THROUGHPUT (LINHA DO TEMPO)
# --------------------------------------------------------------------------
def plot_throughput():
    print("Gerando Fig 1: Throughput...")
    df_sdn = load_metrics_csv(METRICS_CSV)
    data_list = load_json_data(THROUGHPUT_JSON)
    
    session = get_representative_session(data_list)
    if not session:
        print(f"ERRO: Dados de throughput não encontrados em {THROUGHPUT_JSON}")
        return

    # Extrair dados
    start_ts = session['start']['timestamp']['timesecs']
    timestamps = []
    mbps = []
    
    for i in session['intervals']:
        timestamps.append(i['sum']['start'])
        mbps.append(i['sum']['bits_per_second'] / 1e6)

    # Pegar rekeys relativos a ESSA sessão
    rekeys = get_rekey_times(df_sdn, start_ts)
    # Limita rekeys ao tempo do teste
    max_t = max(timestamps) if timestamps else 300
    rekeys = [r for r in rekeys if r <= max_t]

    # Plot
    plt.figure(figsize=(10, 5))
    plt.plot(timestamps, mbps, label='Vazão TCP', color='#1f77b4') # Azul Sólido
    
    # Média
    avg = np.mean(mbps)
    plt.axhline(y=avg, color='green', linestyle=':', linewidth=2, label=f'Média ({avg:.1f} Mbps)')

    # Rekeys - marcadores discretos no topo do gráfico
    if rekeys:
        max_mbps = max(mbps)
        # Plota triângulos pequenos no topo indicando rekeys
        plt.scatter(rekeys, [max_mbps * 1.05] * len(rekeys), 
                   marker='v', color='#d62728', s=30, zorder=5,
                   label=f'Rotação de Chave (n={len(rekeys)})')
        # Ajusta ylim para caber os marcadores
        plt.ylim(0, max_mbps * 1.12)

    plt.title('Estabilidade da Vazão TCP sob Rotação de Chaves')
    plt.xlabel('Tempo (s)')
    plt.ylabel('Vazão (Mbps)')
    plt.legend(loc='lower right')
    plt.grid(True)
    plt.tight_layout()
    plt.savefig('fig_throughput_macro.png', dpi=300)

# --------------------------------------------------------------------------
# 2. GRÁFICO DE VÍDEO (JITTER + PERDA)
# --------------------------------------------------------------------------
def plot_video():
    print("Gerando Fig 2: Vídeo (QoE)...")
    df_sdn = load_metrics_csv(METRICS_CSV)
    
    # Tenta carregar combinado, senão recuperado
    data_list = load_json_data(VIDEO_JSON)
    if not data_list: data_list = load_json_data(VIDEO_RECOVERED)
    
    session = get_representative_session(data_list)
    if not session:
        print("ERRO: Dados de vídeo não encontrados.")
        return

    start_ts = session['start']['timestamp']['timesecs']
    timestamps = []
    jitter = []
    packets_per_interval = []

    for i in session['intervals']:
        stream = i['sum']
        timestamps.append(stream['start'])
        jitter.append(stream.get('jitter_ms', 0))
        packets_per_interval.append(stream.get('packets', 0))

    # Extrai perda de pacotes do resumo final (end.streams[].udp)
    total_lost = 0
    total_packets = 0
    lost_percent = 0.0
    try:
        for s in session['end']['streams']:
            udp = s.get('udp', {})
            total_lost += udp.get('lost_packets', 0)
            total_packets += udp.get('packets', 0)
            if 'lost_percent' in udp:
                lost_percent = udp['lost_percent']
    except (KeyError, TypeError):
        pass

    # Estima perda por intervalo baseado na variação de pacotes
    # A ideia: intervalos com menos pacotes que a média provavelmente tiveram perda
    estimated_loss = []
    if packets_per_interval and total_lost > 0:
        avg_packets = np.mean(packets_per_interval)
        max_packets = max(packets_per_interval)
        # Usa o máximo como referência (intervalo sem perda)
        deficits = [max(0, max_packets - p) for p in packets_per_interval]
        total_deficit = sum(deficits)
        if total_deficit > 0:
            # Distribui a perda total proporcionalmente ao déficit de cada intervalo
            estimated_loss = [(d / total_deficit) * total_lost for d in deficits]
        else:
            # Se não há variação, distribui uniformemente
            estimated_loss = [total_lost / len(packets_per_interval)] * len(packets_per_interval)
    else:
        estimated_loss = [0] * len(timestamps)

    rekeys = get_rekey_times(df_sdn, start_ts)
    max_t = max(timestamps) if timestamps else 300
    rekeys = [r for r in rekeys if r <= max_t]

    # Subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    # Jitter
    ax1.plot(timestamps, jitter, color='#9467bd', label='Jitter (ms)')
    ax1.set_ylabel('Jitter (ms)')
    ax1.set_title('Métricas de Qualidade de Vídeo (UDP)')
    ax1.grid(True)
    
    # Annotação se Jitter for 0 (Sender side)
    if max(jitter) == 0:
        ax1.set_ylim(-0.1, 1.0)
        ax1.text(0.5, 0.5, 'Jitter ~ 0ms (Medido no Transmissor)', 
                 transform=ax1.transAxes, ha='center', va='center', 
                 bbox=dict(facecolor='white', alpha=0.9, edgecolor='gray'))

    # Rekeys - marcadores discretos no topo
    if rekeys:
        max_jitter = max(jitter) if max(jitter) > 0 else 0.5
        ax1.scatter(rekeys, [max_jitter * 1.08] * len(rekeys), 
                   marker='v', color='#d62728', s=25, zorder=5,
                   label=f'Rotação de Chave (n={len(rekeys)})')
        ax1.set_ylim(0, max_jitter * 1.15)
    ax1.legend(loc='upper right', fontsize=8)

    # Perda de Pacotes - Estimativa por intervalo (agrupada para legibilidade)
    ax2.set_ylabel('Pacotes Perdidos')
    ax2.set_xlabel('Tempo (s)')
    ax2.grid(True)
    
    # Agrupa em janelas de 10 segundos para melhor visualização
    if total_lost > 0 and estimated_loss:
        window_size = 10  # segundos
        grouped_times = []
        grouped_loss = []
        
        for start in range(0, int(max(timestamps)) + 1, window_size):
            end = start + window_size
            # Soma perda estimada nesta janela
            window_loss = sum(l for t, l in zip(timestamps, estimated_loss) if start <= t < end)
            if window_loss > 0:
                grouped_times.append(start + window_size/2)  # Centro da janela
                grouped_loss.append(window_loss)
        
        # Plota barras largas e legíveis
        ax2.bar(grouped_times, grouped_loss, color='#ff7f0e', width=window_size*0.8, 
                alpha=0.7, edgecolor='darkorange', linewidth=0.5)
        
        # Linha de média por janela
        avg_loss_window = total_lost / (max(timestamps) / window_size)
        ax2.axhline(y=avg_loss_window, color='red', linestyle='--', alpha=0.7, 
                    label=f'Média: {avg_loss_window:.1f} pkts/{window_size}s')
        ax2.legend(loc='upper right')
        
        # Anotação com total
        ax2.text(0.02, 0.95, f'Total: {total_lost} pkts ({lost_percent:.2f}%)', 
                 transform=ax2.transAxes, ha='left', va='top',
                 fontsize=10, fontweight='bold', color='darkorange',
                 bbox=dict(facecolor='white', alpha=0.8, edgecolor='orange'))
    
    # Se não há perda, mostra mensagem
    if total_lost == 0:
        ax2.set_ylim(0, 5)
        ax2.text(0.5, 0.5, 'Perda de Pacotes: 0%', 
                 transform=ax2.transAxes, ha='center', va='center', color='green',
                 fontsize=12, fontweight='bold',
                 bbox=dict(facecolor='white', alpha=0.9))

    plt.tight_layout()
    plt.savefig('fig_video_metrics.png', dpi=300)

# --------------------------------------------------------------------------
# 3. GRÁFICO DE RECURSOS (CPU)
# --------------------------------------------------------------------------
def plot_resources():
    print("Gerando Fig 3: Recursos...")
    if not os.path.exists(RESOURCE_CSV): return
    
    df = pd.read_csv(RESOURCE_CSV)
    df['cpu_perc'] = pd.to_numeric(df['cpu_perc'], errors='coerce')
    
    # Converte timestamp absoluto para elapsed_sec (tempo relativo desde o início)
    # Suporta tanto 'timestamp' quanto 'elapsed_sec' para compatibilidade
    if 'timestamp' in df.columns:
        t_min_res = df['timestamp'].min()
        df['elapsed_sec'] = df['timestamp'] - t_min_res
    elif 'elapsed_sec' not in df.columns:
        print("  [AVISO] CSV de recursos sem coluna 'timestamp' ou 'elapsed_sec'")
        return
    
    # Filtrar gaps anômalos causados por bug de timestamp (saltos > 1000s)
    df_sorted = df.sort_values('elapsed_sec').reset_index(drop=True)
    time_diffs = df_sorted['elapsed_sec'].diff()
    gap_indices = time_diffs[time_diffs > 1000].index
    if len(gap_indices) > 0:
        # Pega apenas dados antes do primeiro gap grande
        first_gap = gap_indices[0]
        df = df_sorted.iloc[:first_gap].copy()
        print(f"  [AVISO] Detectado gap de timestamp, usando apenas primeiros {first_gap} pontos")
    
    alice = df[df['container'] == 'alice'].sort_values('elapsed_sec')
    orch = df[df['container'] == 'orchestrator'].sort_values('elapsed_sec')

    # Eventos de cálculo (KeyGen) - sincroniza com o mesmo t_min dos recursos
    keygen_evs = []
    if os.path.exists(METRICS_CSV):
        df_m = load_metrics_csv(METRICS_CSV)
        if df_m is not None and 'event_type' in df_m.columns:
            # Usa elapsed_sec que é garantido existir após load_metrics_csv()
            time_col = 'elapsed_sec' if 'elapsed_sec' in df_m.columns else 'timestamp'
            evs = df_m[df_m['event_type'] == 'TOTAL_KEY_GEN'][time_col].values
            keygen_evs = list(evs)

    plt.figure(figsize=(10, 5))
    
    # Alice (Area)
    plt.fill_between(alice['elapsed_sec'], alice['cpu_perc'], color='#17becf', alpha=0.3, label='Agente VPN (Plano de Dados)')
    plt.plot(alice['elapsed_sec'], alice['cpu_perc'], color='#17becf', linewidth=1)
    
    # Orquestrador (Linha)
    plt.plot(orch['elapsed_sec'], orch['cpu_perc'], color='#d62728', linewidth=2, label='Orquestrador (Plano de Controle)')
    
    plt.title('Overhead Computacional: CPU')
    plt.xlabel('Tempo (s)')
    plt.ylabel('Uso de CPU (%)')
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig('fig_resources_cpu.png', dpi=300)

# --------------------------------------------------------------------------
# 4. GRÁFICO DE COMPOSIÇÃO DE TEMPO (BARRAS - ÚLTIMOS 30)
# --------------------------------------------------------------------------
def plot_keygen():
    print("Gerando Fig 4: KeyGen (Composição)...")
    if not os.path.exists(METRICS_CSV): return
    df = load_metrics_csv(METRICS_CSV)
    if df is None: return
    
    cycles = sorted(df['cycle'].unique())
    # LIMITE: 30
    if len(cycles) > 30: cycles = cycles[-30:]
    
    data = {'Labels': [], 'QKD': [], 'PQC': [], 'HKDF': []}
    
    for c in cycles:
        cdf = df[df['cycle'] == c]
        # Usar média por conexão (paralelização gera múltiplas conexões por ciclo)
        # Suporta tanto 'duration_ms' (antigo) quanto 'value_ms' (novo)
        value_col = 'value_ms' if 'value_ms' in cdf.columns else 'duration_ms'
        qkd = cdf[cdf['event_type'] == 'QKD_FETCH'][value_col].mean()
        pqc = cdf[cdf['event_type'] == 'PQC_GEN'][value_col].mean()
        hkdf = cdf[cdf['event_type'] == 'HKDF_MIX'][value_col].mean()
        
        # Filtrar valores anômalos:
        # - Valores negativos (bug de timestamp)
        # - Timeout >10s = 10000ms (outliers de rede)
        if qkd > 0 and qkd < 10000 and pqc > 0 and hkdf > 0:
            data['Labels'].append(str(c))
            data['QKD'].append(qkd)
            data['PQC'].append(pqc)
            data['HKDF'].append(hkdf)

    if not data['Labels']: return

    x = np.arange(len(data['Labels']))
    width = 0.6
    
    plt.figure(figsize=(12, 6))
    
    # Cores
    c_qkd = '#2ca02c'
    c_pqc = '#1f77b4'
    c_hkdf = '#ff7f0e'
    
    p1 = plt.bar(x, data['QKD'], width, label='QKD (Rede)', color=c_qkd, edgecolor='black', alpha=0.8)
    p2 = plt.bar(x, data['PQC'], width, bottom=data['QKD'], label='PQC (Local)', color=c_pqc, edgecolor='black', alpha=0.8)
    bot = [q+p for q,p in zip(data['QKD'], data['PQC'])]
    p3 = plt.bar(x, data['HKDF'], width, bottom=bot, label='HKDF', color=c_hkdf, edgecolor='black', alpha=0.8)
    
    plt.xlabel('Ciclo de Rotação (Últimos 30)')
    plt.ylabel('Tempo (ms)')
    plt.title('Tempo de Geração de Chave Híbrida')
    plt.xticks(x, data['Labels'], rotation=45)
    plt.legend(bbox_to_anchor=(1.01, 1), loc='upper left')
    plt.grid(axis='y')
    plt.tight_layout()
    plt.savefig('fig_keygen_composition.png', dpi=300)

# --------------------------------------------------------------------------
# 5. GRÁFICOS DE ZOOM (MICRO-ANÁLISE)
# --------------------------------------------------------------------------
def plot_zooms():
    print("Gerando Fig 5 e 6: Zooms...")
    # Carregar dados
    df_metrics = load_metrics_csv(METRICS_CSV)
    tp_data = load_json_data(THROUGHPUT_JSON)
    vid_data = load_json_data(VIDEO_JSON)
    
    session_tp = get_representative_session(tp_data)
    session_vid = get_representative_session(vid_data)
    
    if not session_tp: return

    # Preparar Throughput para Zoom
    tp_start = session_tp['start']['timestamp']['timesecs']
    t_tp_abs = []
    y_tp = []
    for i in session_tp['intervals']:
        t_tp_abs.append(tp_start + i['sum']['start'])
        y_tp.append(i['sum']['bits_per_second'] / 1e6)

    # Achar o melhor rekey dentro dessa sessão
    rekey_ts = get_best_zoom_event(df_metrics, t_tp_abs[0], t_tp_abs[-1])
    if not rekey_ts:
        print("Aviso: Nenhum rekey adequado para zoom.")
        return

    # Janela de Zoom
    WIN = 10 # segundos
    
    # 5.1 Zoom Throughput
    mask = (np.array(t_tp_abs) >= rekey_ts - WIN) & (np.array(t_tp_abs) <= rekey_ts + WIN)
    t_z = np.array(t_tp_abs)[mask] - rekey_ts
    y_z = np.array(y_tp)[mask]
    
    plt.figure(figsize=(8, 4))
    plt.plot(t_z, y_z, marker='o', markersize=4, color='#1f77b4', label='Vazão Instantânea')
    plt.axvline(x=0, color='red', linestyle='--', linewidth=2, label='Troca de Chave')
    plt.title('Zoom: Impacto na Vazão')
    plt.xlabel('Tempo relativo (s)')
    plt.ylabel('Mbps')
    plt.legend()
    plt.grid(True)
    if len(y_z) > 0: plt.ylim(bottom=min(y_z)*0.9, top=max(y_z)*1.1)
    plt.tight_layout()
    plt.savefig('fig_zoom_throughput.png', dpi=300)

    # 5.2 Zoom Video (se existir)
    if session_vid:
        vid_start = session_vid['start']['timestamp']['timesecs']
        t_vid_abs = []
        y_jit = []
        for i in session_vid['intervals']:
            t_vid_abs.append(vid_start + i['sum']['start'])
            y_jit.append(i['sum'].get('jitter_ms', 0))
            
        # Tenta achar rekey pra esse ou usa o mesmo se os tempos forem alinhados
        rekey_vid = get_best_zoom_event(df_metrics, t_vid_abs[0], t_vid_abs[-1])
        if rekey_vid:
            mask_v = (np.array(t_vid_abs) >= rekey_vid - WIN) & (np.array(t_vid_abs) <= rekey_vid + WIN)
            t_zv = np.array(t_vid_abs)[mask_v] - rekey_vid
            y_zv = np.array(y_jit)[mask_v]
            
            plt.figure(figsize=(8, 4))
            plt.plot(t_zv, y_zv, marker='s', markersize=4, color='#9467bd', label='Jitter')
            plt.axvline(x=0, color='red', linestyle='--', linewidth=2, label='Troca de Chave')
            plt.title('Zoom: Impacto no Jitter')
            plt.xlabel('Tempo relativo (s)')
            plt.ylabel('ms')
            plt.legend()
            plt.grid(True)
            plt.tight_layout()
            plt.savefig('fig_zoom_video.png', dpi=300)

# ==========================================================================
# MÉTRICAS ACADÊMICAS FOCADAS (Solicitadas pelo Orientador)
# ==========================================================================

def plot_key_availability():
    """
    MÉTRICA 1: Taxa de Disponibilidade de Chaves QKD (Key Availability Ratio)
    Compara Alice-Bob (1000 keys/s) vs Carol-Dave (500 keys/s)
    """
    print("Gerando Fig: Key Availability Ratio...")
    df = load_metrics_csv(METRICS_CSV)
    if df is None:
        print("ERRO: Não foi possível carregar metrics CSV")
        return
    
    # Filtrar eventos KEY_AVAILABILITY
    df_avail = df[df['event_type'] == 'KEY_AVAILABILITY'].copy()
    
    if df_avail.empty:
        print("AVISO: Nenhuma métrica KEY_AVAILABILITY encontrada")
        return
    
    connections = df_avail['connection'].unique()
    
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    
    # (a) Gráfico de Barras - Taxa de sucesso por conexão
    ax1 = axes[0]
    stats = {}
    for conn in connections:
        conn_data = df_avail[df_avail['connection'] == conn]
        total = len(conn_data)
        success = (conn_data['value_ms'] == 1).sum()
        stats[conn] = {'total': total, 'success': success, 'rate': (success/total)*100 if total > 0 else 0}
    
    colors = {'alice-bob': '#2ecc71', 'carol-dave': '#3498db'}
    bars = ax1.bar(stats.keys(), [s['rate'] for s in stats.values()], 
                   color=[colors.get(c, '#95a5a6') for c in stats.keys()],
                   edgecolor='black', linewidth=1.2)
    
    ax1.set_ylabel('Taxa de Disponibilidade (%)')
    ax1.set_xlabel('Conexão VPN')
    ax1.set_title('(a) Disponibilidade de Chaves QKD')
    ax1.set_ylim(0, 105)
    ax1.axhline(y=100, color='green', linestyle='--', alpha=0.5, label='Meta 100%')
    
    for bar, (conn, s) in zip(bars, stats.items()):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, 
                f'{s["rate"]:.1f}%', ha='center', fontsize=11, fontweight='bold')
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height()/2, 
                f'({s["success"]}/{s["total"]})', ha='center', fontsize=9, color='white')
    
    ax1.legend()
    ax1.grid(True, alpha=0.3, axis='y')
    
    # (b) Série temporal de disponibilidade
    ax2 = axes[1]
    for conn in connections:
        conn_data = df_avail[df_avail['connection'] == conn].copy()
        if 'elapsed_sec' in conn_data.columns and len(conn_data) > 1:
            conn_data = conn_data.sort_values('elapsed_sec')
            # Média móvel de 10 amostras
            conn_data['rolling'] = conn_data['value_ms'].rolling(window=10, min_periods=1).mean() * 100
            ax2.plot(conn_data['elapsed_sec'] / 60, conn_data['rolling'], 
                    label=conn, color=colors.get(conn, '#95a5a6'), linewidth=2)
    
    ax2.set_ylabel('Disponibilidade (%)')
    ax2.set_xlabel('Tempo (minutos)')
    ax2.set_title('(b) Evolução Temporal')
    ax2.set_ylim(0, 105)
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('fig_key_availability.png', dpi=300, bbox_inches='tight')
    print("   Salvo: fig_key_availability.png")


def plot_hybridization_overhead():
    """
    MÉTRICA 2: Latência de Hibridização (Hybridization Overhead)
    Decompõe: QKD_FETCH + PQC_GEN + HKDF_MIX
    """
    print("Gerando Fig: Hybridization Overhead...")
    df = load_metrics_csv(METRICS_CSV)
    if df is None:
        print("ERRO: Não foi possível carregar metrics CSV")
        return
    
    connections = df['connection'].unique()
    
    # Coletar dados por etapa
    data = {conn: {'QKD_FETCH': [], 'PQC_GEN': [], 'HKDF_MIX': []} for conn in connections}
    for conn in connections:
        for event in ['QKD_FETCH', 'PQC_GEN', 'HKDF_MIX']:
            vals = df[(df['connection'] == conn) & (df['event_type'] == event)]['value_ms'].values
            data[conn][event] = vals

    # --- Determinar duração do experimento (segundos) para detectar valores impossíveis ---
    # Primeiro tenta usar os dados de throughput (mais confiáveis para duração do teste)
    test_duration_s = None
    tp_data = load_json_data(THROUGHPUT_JSON)
    session_tp = get_representative_session(tp_data) if tp_data else None
    if session_tp and 'intervals' in session_tp and len(session_tp['intervals']) > 0:
        # assume 'start' em cada intervalo é relativo ao início do teste
        try:
            max_interval = max(i['sum']['start'] for i in session_tp['intervals'])
            test_duration_s = float(max_interval)
        except Exception:
            test_duration_s = None

    # Fallback: usar range de elapsed_sec no CSV
    if test_duration_s is None and 'elapsed_sec' in df.columns:
        try:
            test_duration_s = float(df['elapsed_sec'].max())
        except Exception:
            test_duration_s = None

    # Define limite máximo plausível em ms. Se não souber a duração, usa 10s (10000ms)
    if test_duration_s is not None and test_duration_s > 0:
        max_allowed_ms = test_duration_s * 1000.0 * 1.1  # margem 10%
    else:
        max_allowed_ms = 10000.0

    # Coletar anomalias (valores > max_allowed_ms) para inspeção
    anomalies = []
    for conn in connections:
        for event in ['QKD_FETCH', 'PQC_GEN', 'HKDF_MIX']:
            vals = data[conn][event]
            if len(vals) == 0:
                continue
            # numpy array or list
            import numpy as _np
            arr = _np.array(vals, dtype=float)
            bad_idx = _np.where(arr > max_allowed_ms)[0]
            for i in bad_idx:
                anomalies.append({'connection': conn, 'event': event, 'value_ms': float(arr[i])})

    # Salva CSV de anomalias para investigação (se houver)
    if anomalies:
        import csv
        out_csv = 'hybridization_anomalies.csv'
        with open(out_csv, 'w', newline='') as cf:
            writer = csv.DictWriter(cf, fieldnames=['connection', 'event', 'value_ms'])
            writer.writeheader()
            for r in anomalies:
                writer.writerow(r)
        print(f"   Aviso: {len(anomalies)} leituras anômalas detectadas. Salvo: {out_csv}")
    
    # Apenas o gráfico (a): barras empilhadas
    fig, ax1 = plt.subplots(figsize=(9, 6))
    x = np.arange(len(connections))
    width = 0.5
    
    # Calcula médias ignorando outliers impossíveis (> max_allowed_ms)
    qkd_means = []
    pqc_means = []
    hkdf_means = []
    for c in connections:
        # converter para numpy arrays para filtro
        q_arr = np.array(data[c]['QKD_FETCH'], dtype=float) if len(data[c]['QKD_FETCH']) > 0 else np.array([])
        p_arr = np.array(data[c]['PQC_GEN'], dtype=float) if len(data[c]['PQC_GEN']) > 0 else np.array([])
        h_arr = np.array(data[c]['HKDF_MIX'], dtype=float) if len(data[c]['HKDF_MIX']) > 0 else np.array([])

        # Filtrar zeros/negativos e valores absurdos
        q_f = q_arr[(q_arr > 0) & (q_arr <= max_allowed_ms)] if q_arr.size > 0 else np.array([])
        p_f = p_arr[(p_arr > 0) & (p_arr <= max_allowed_ms)] if p_arr.size > 0 else np.array([])
        h_f = h_arr[(h_arr > 0) & (h_arr <= max_allowed_ms)] if h_arr.size > 0 else np.array([])

        qkd_means.append(np.mean(q_f) if q_f.size > 0 else 0)
        pqc_means.append(np.mean(p_f) if p_f.size > 0 else 0)
        hkdf_means.append(np.mean(h_f) if h_f.size > 0 else 0)
    
    ax1.bar(x, qkd_means, width, label='QKD Fetch (API ETSI)', color='#3498db')
    ax1.bar(x, pqc_means, width, bottom=qkd_means, label='PQC Gen (ML-KEM-768)', color='#e74c3c')
    ax1.bar(x, hkdf_means, width, bottom=[q+p for q,p in zip(qkd_means, pqc_means)], 
            label='HKDF Mix', color='#2ecc71')
    
    totals = [q+p+h for q,p,h in zip(qkd_means, pqc_means, hkdf_means)]
    for i, total in enumerate(totals):
        ax1.text(i, total + 2, f'{total:.1f}ms', ha='center', fontsize=10, fontweight='bold')
    
    ax1.set_ylabel('Latência Média (ms)')
    ax1.set_xlabel('Conexão VPN')
    ax1.set_title('Decomposição do Overhead de Hibridização')
    ax1.set_xticks(x)
    ax1.set_xticklabels(connections)
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3, axis='y')
    
    plt.tight_layout()
    plt.savefig('fig_hybridization_overhead.png', dpi=300, bbox_inches='tight')
    print("   Salvo: fig_hybridization_overhead.png")


def plot_e2e_rekey_latency():
    """
    MÉTRICA 3: Latência Fim-a-Fim do Rekeying (E2E Rekey Latency)
    Tempo desde decisão até confirmação do túnel
    """
    print("Gerando Fig: E2E Rekey Latency...")
    df = load_metrics_csv(METRICS_CSV)
    if df is None:
        print("ERRO: Não foi possível carregar metrics CSV")
        return
    
    df_e2e = df[df['event_type'] == 'E2E_REKEY_LATENCY'].copy()
    
    if df_e2e.empty:
        print("AVISO: Nenhuma métrica E2E_REKEY_LATENCY encontrada")
        return
    
    connections = df_e2e['connection'].unique()
    colors = {'alice-bob': '#2ecc71', 'carol-dave': '#3498db'}
    
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    
    # (a) CDF
    ax1 = axes[0]
    for conn in connections:
        values = df_e2e[df_e2e['connection'] == conn]['value_ms'].sort_values()
        if len(values) > 0:
            cdf = np.arange(1, len(values) + 1) / len(values)
            ax1.plot(values, cdf, label=conn, color=colors.get(conn, '#95a5a6'), linewidth=2)
    
    ax1.set_xlabel('Latência E2E (ms)')
    ax1.set_ylabel('CDF')
    ax1.set_title('(a) Distribuição Cumulativa')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(left=0)
    
    # (b) Violin Plot
    ax2 = axes[1]
    violin_data = [df_e2e[df_e2e['connection'] == c]['value_ms'].values for c in connections]
    violin_data = [v for v in violin_data if len(v) > 0]
    
    if violin_data:
        parts = ax2.violinplot(violin_data, positions=range(len(connections)), showmeans=True, showmedians=True)
        for i, pc in enumerate(parts['bodies']):
            pc.set_facecolor(colors.get(list(connections)[i], '#95a5a6'))
            pc.set_alpha(0.7)
        
        ax2.set_xticks(range(len(connections)))
        ax2.set_xticklabels(connections)
        
        # Estatísticas
        for i, conn in enumerate(connections):
            vals = df_e2e[df_e2e['connection'] == conn]['value_ms']
            if len(vals) > 0:
                ax2.text(i, vals.max() + 10, f'μ={vals.mean():.0f}\nσ={vals.std():.0f}', 
                        ha='center', fontsize=9)
    
    ax2.set_ylabel('Latência (ms)')
    ax2.set_title('(b) Distribuição por Conexão')
    ax2.grid(True, alpha=0.3, axis='y')
    
    # (c) Série Temporal
    ax3 = axes[2]
    for conn in connections:
        conn_data = df_e2e[df_e2e['connection'] == conn].copy()
        if 'elapsed_sec' in conn_data.columns and len(conn_data) > 1:
            conn_data = conn_data.sort_values('elapsed_sec')
            ax3.scatter(conn_data['elapsed_sec'] / 60, conn_data['value_ms'], 
                       label=conn, color=colors.get(conn, '#95a5a6'), alpha=0.7, s=30)
    
    ax3.set_xlabel('Tempo (minutos)')
    ax3.set_ylabel('Latência (ms)')
    ax3.set_title('(c) Evolução Temporal')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('fig_e2e_rekey_latency.png', dpi=300, bbox_inches='tight')
    print("   Salvo: fig_e2e_rekey_latency.png")


def plot_throughput_during_rekey():
    """
    MÉTRICA 4: Throughput e Jitter durante o Rekeying
    Marca os momentos de troca de chave sobre os dados de iperf3
    """
    print("Gerando Fig: Throughput/Jitter durante Rekeying...")
    df_metrics = load_metrics_csv(METRICS_CSV)
    data_tp = load_json_data(THROUGHPUT_JSON)
    data_vid = load_json_data(VIDEO_JSON)
    
    session_tp = get_representative_session(data_tp)
    session_vid = get_representative_session(data_vid)
    
    if not session_tp:
        print("ERRO: Dados de throughput não encontrados")
        return
    
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    
    # Obter tempos de rekey
    rekey_times = []
    if df_metrics is not None and 'timestamp' in df_metrics.columns:
        rekeys = df_metrics[df_metrics['event_type'] == 'E2E_REKEY_LATENCY']
        rekey_times = rekeys['timestamp'].values
    
    # (a) Throughput
    ax1 = axes[0]
    tp_start = session_tp['start']['timestamp']['timesecs']
    t_tp = []
    y_tp = []
    for interval in session_tp['intervals']:
        t_tp.append(interval['sum']['start'])
        y_tp.append(interval['sum']['bits_per_second'] / 1e6)
    
    ax1.plot(t_tp, y_tp, color='#1f77b4', linewidth=1.5, label='Throughput')
    
    # Marcar rekeys como triângulos
    rekey_rel = [r - tp_start for r in rekey_times if 0 <= r - tp_start <= max(t_tp)]
    if rekey_rel:
        # Interpolar valores de throughput nos pontos de rekey
        y_rekey = np.interp(rekey_rel, t_tp, y_tp)
        ax1.scatter(rekey_rel, y_rekey, marker='v', color='red', s=80, 
                   zorder=5, label='Troca de Chave')
    
    ax1.set_ylabel('Throughput (Mbps)')
    ax1.set_title('(a) Throughput durante Rekeying')
    ax1.legend(loc='upper right')
    ax1.grid(True, alpha=0.3)
    
    # (b) Jitter (se disponível)
    ax2 = axes[1]
    if session_vid:
        vid_start = session_vid['start']['timestamp']['timesecs']
        t_vid = []
        y_jit = []
        for interval in session_vid['intervals']:
            t_vid.append(interval['sum']['start'])
            y_jit.append(interval['sum'].get('jitter_ms', 0))
        
        ax2.plot(t_vid, y_jit, color='#9467bd', linewidth=1.5, label='Jitter')
        
        rekey_rel_vid = [r - vid_start for r in rekey_times if 0 <= r - vid_start <= max(t_vid)]
        if rekey_rel_vid:
            y_rekey_jit = np.interp(rekey_rel_vid, t_vid, y_jit)
            ax2.scatter(rekey_rel_vid, y_rekey_jit, marker='v', color='red', s=80, 
                       zorder=5, label='Troca de Chave')
        
        ax2.set_ylabel('Jitter (ms)')
        ax2.set_title('(b) Jitter durante Rekeying')
        ax2.legend(loc='upper right')
    else:
        ax2.text(0.5, 0.5, 'Dados de vídeo não disponíveis', transform=ax2.transAxes, 
                ha='center', fontsize=12)
    
    ax2.set_xlabel('Tempo (s)')
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('fig_throughput_during_rekey.png', dpi=300, bbox_inches='tight')
    print("   Salvo: fig_throughput_during_rekey.png")


def plot_summary_table():
    """
    Tabela resumo com estatísticas das 4 métricas principais
    """
    print("Gerando Fig: Summary Table...")
    df = load_metrics_csv(METRICS_CSV)
    if df is None:
        print("ERRO: Não foi possível carregar metrics CSV")
        return
    
    connections = df['connection'].unique()
    
    summary = []
    for conn in connections:
        conn_data = df[df['connection'] == conn]
        
        # Key Availability
        avail = conn_data[conn_data['event_type'] == 'KEY_AVAILABILITY']
        avail_rate = (avail['value_ms'] == 1).mean() * 100 if len(avail) > 0 else 0
        
        # Hybridization Overhead
        hybrid = conn_data[conn_data['event_type'] == 'HYBRIDIZATION_OVERHEAD']['value_ms']
        hybrid_mean = hybrid.mean() if len(hybrid) > 0 else 0
        hybrid_p99 = hybrid.quantile(0.99) if len(hybrid) > 0 else 0
        
        # E2E Rekey Latency
        e2e = conn_data[conn_data['event_type'] == 'E2E_REKEY_LATENCY']['value_ms']
        e2e_mean = e2e.mean() if len(e2e) > 0 else 0
        e2e_p99 = e2e.quantile(0.99) if len(e2e) > 0 else 0
        
        # QKD Fetch (para comparar latência da rede quântica)
        qkd = conn_data[conn_data['event_type'] == 'QKD_FETCH']['value_ms']
        qkd_mean = qkd.mean() if len(qkd) > 0 else 0
        
        summary.append({
            'Conexão': conn,
            'Disp. QKD (%)': f'{avail_rate:.1f}',
            'QKD Lat. (ms)': f'{qkd_mean:.1f}',
            'Hybrid Overhead (ms)': f'{hybrid_mean:.1f}',
            'Hybrid P99 (ms)': f'{hybrid_p99:.1f}',
            'E2E Lat. (ms)': f'{e2e_mean:.1f}',
            'E2E P99 (ms)': f'{e2e_p99:.1f}',
            'Nº Rekeys': len(e2e)
        })
    
    fig, ax = plt.subplots(figsize=(14, 3))
    ax.axis('off')
    
    df_summary = pd.DataFrame(summary)
    table = ax.table(cellText=df_summary.values, colLabels=df_summary.columns,
                     cellLoc='center', loc='center',
                     colColours=['#3498db']*len(df_summary.columns))
    
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.5)
    
    for key, cell in table.get_celld().items():
        if key[0] == 0:
            cell.set_text_props(color='white', fontweight='bold')
    
    plt.title('Resumo das Métricas do Experimento', fontsize=14, fontweight='bold', pad=20)
    plt.tight_layout()
    plt.savefig('fig_summary_table.png', dpi=300, bbox_inches='tight')
    print("   Salvo: fig_summary_table.png")


if __name__ == "__main__":
    print("="*60)
    print("GERADOR DE GRÁFICOS - Métricas Acadêmicas Focadas")
    print("="*60)
    
    # Gráficos originais (mantidos para compatibilidade)
    plot_throughput()
    plot_video()
    plot_resources()
    plot_keygen()
    plot_zooms()
    
    print("\n" + "-"*60)
    print("MÉTRICAS ACADÊMICAS (Foco do Orientador)")
    print("-"*60)
    
    # Novas métricas focadas
    plot_key_availability()        # Métrica 1: Key Availability Ratio
    plot_hybridization_overhead()  # Métrica 2: Hybridization Overhead
    plot_e2e_rekey_latency()       # Métrica 3: E2E Rekey Latency
    plot_throughput_during_rekey() # Métrica 4: Throughput/Jitter durante rekey
    plot_summary_table()           # Tabela resumo
    
    print("\n" + "="*60)
    print("[CONCLUÍDO] Todos os gráficos foram gerados na pasta atual.")
    print("="*60)