import pandas as pd
import json
import matplotlib.pyplot as plt
import numpy as np
import os

# --- CONFIGURAÇÕES DE ESTILO ---
plt.rcParams.update({'font.size': 11, 'font.family': 'serif', 'axes.grid': True})

# --- CAMINHOS ---
PATH_BASELINE = "./baseline_test/metrics_baseline_real"
PATH_SDKM = "./metrics_sdn_real/wan-fiber"

def load_json(path, filename):
    with open(os.path.join(path, filename), 'r') as f:
        return json.load(f)

# --- 1. EXTRAÇÃO DE DADOS EXPANDIDA ---
def get_extended_stats(path):
    tp_data = load_json(path, 'throughput_combined.json')
    vid_data = load_json(path, 'video_combined.json')
    
    tps = [d['end']['sum_received']['bits_per_second'] * 1e-6 for d in tp_data if 'sum_received' in d['end']]
    jitters = []
    losses = []
    
    for d in vid_data:
        res = d['end'].get('sum_received', d['end'].get('sum', {}))
        if 'jitter_ms' in res: jitters.append(res['jitter_ms'])
        if 'lost_percent' in res: losses.append(res['lost_percent'])
    
    return np.mean(tps), np.mean(jitters), np.mean(losses)

# Coletando métricas
b_tp, b_jit, b_loss = get_extended_stats(PATH_BASELINE)
s_tp, s_jit, s_loss = get_extended_stats(PATH_SDKM)

# --- FIGURA 2: DESEMPENHO E CONFIABILIDADE (Agora com Perda de Pacotes) ---
fig2, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

# --- FIGURA 2: COMPARATIVO DE DESEMPENHO ---
labels = ['Vazão (Mbps)', 'Jitter (ms)']
x = np.arange(len(labels))
width = 0.35

fig2, ax = plt.subplots(figsize=(8, 5))
ax.bar(x - width/2, [b_tp, b_jit], width, label='Baseline (IPsec)', color='#90caf9', edgecolor='black', hatch='//')
ax.bar(x + width/2, [s_tp, s_jit], width, label='SDKM (Híbrido)', color='#ffab91', edgecolor='black', hatch='..')

ax.set_ylabel('Valor Médio')
ax.set_title('Fig 3. Impacto da Segurança Híbrida no Desempenho')
ax.set_xticks(x)
ax.set_xticklabels(labels)
ax.legend()
ax.grid(axis='y', linestyle='--', alpha=0.6)

for i, v in enumerate([b_tp, b_jit]): ax.text(i - width/2, v + 0.5, f"{v:.2f}", ha='center', fontweight='bold')
for i, v in enumerate([s_tp, s_jit]): ax.text(i + width/2, v + 0.5, f"{v:.2f}", ha='center', fontweight='bold')

plt.tight_layout()
plt.savefig('fig3_comparativo_final.pdf', format='pdf', dpi=300)

# --- FIGURA 2: DECOMPOSIÇÃO DA LATÊNCIA (Visualizando a Tabela 2) ---
df_exp = pd.read_csv(os.path.join(PATH_SDKM, 'experiment_metrics.csv'), 
                     names=['timestamp', 'iteration', 'pair', 'operation', 'latency_ms', 'status', 'extra'])

ops_map = {
    'QKD_FETCH': 'Busca QKD',
    'PQC_GEN': 'Geração PQC',
    'HKDF_MIX': 'Hibridização',
    'PUSH_KEY': 'Distribuição',
    'TUNNEL_ESTABLISH': 'IKE Rekey'
}
lat_data = df_exp[df_exp['operation'].isin(ops_map.keys())].groupby('operation')['latency_ms'].mean().reindex(ops_map.keys())

plt.figure(figsize=(9, 5))
colors = plt.cm.Paired(np.linspace(0, 1, len(lat_data)))
bars = plt.bar([ops_map[k] for k in lat_data.index], lat_data.values, color=colors, edgecolor='black')
plt.yscale('log') # Escala logarítmica para ver PQC (0.1ms) e QKD (700ms) no mesmo gráfico
plt.ylabel('Latência Média (ms) - Escala Log')
plt.title('Fig 2. Decomposição do Overhead de Orquestração')

for bar in bars:
    yval = bar.get_height()
    plt.text(bar.get_x() + bar.get_width()/2, yval, f"{yval:.2f}ms", va='bottom', ha='center', fontsize=9)

plt.tight_layout()
plt.savefig('fig2_latency_breakdown.pdf', format='pdf', dpi=300)


# --- 2. FIGURA 4: ESTABILIDADE E CONCORRÊNCIA (Versão Melhorada) ---
df_exp = pd.read_csv(os.path.join(PATH_SDKM, 'experiment_metrics.csv'), 
                     names=['timestamp', 'iteration', 'pair', 'operation', 'latency_ms', 'status', 'extra'])

# Carrega o primeiro teste de vazão do SDKM
sdkm_tests = load_json(PATH_SDKM, 'throughput_combined.json')
test = sdkm_tests[0]
json_start = test['start']['timestamp']['timesecs']
json_end = json_start + test['end']['sum_received']['seconds']

# Filtra eventos de rotação que ocorreram DURANTE este teste específico
rekeys = df_exp[(df_exp['timestamp'] >= json_start) & (df_exp['timestamp'] <= json_end) & (df_exp['operation'] == 'E2E_REKEY_LATENCY')]

# Dados de vazão Alice-Bob
times = [i['streams'][0]['end'] for i in test['intervals']]
bps = [i['streams'][0]['bits_per_second'] * 1e-6 for i in test['intervals']]
df_tp = pd.DataFrame({'time': times, 'tp': bps})
df_tp['smooth'] = df_tp['tp'].rolling(window=5, center=True).mean() # Média móvel para clareza

fig3, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), gridspec_kw={'height_ratios': [3, 1]}, sharex=True)

# Painel Superior: Vazão Alice-Bob
ax1.plot(df_tp['time'], df_tp['tp'], color='#1f77b4', alpha=0.2, label='Vazão Bruta')
ax1.plot(df_tp['time'], df_tp['smooth'], color='#1f77b4', linewidth=2, label='Média Móvel (5s)')
# Marcadores de Rekey no topo
ab_rekeys = rekeys[rekeys['pair'] == 'alice-bob']['timestamp'] - json_start
ax1.scatter(ab_rekeys, [max(bps)*1.1]*len(ab_rekeys), marker='v', color='red', label='Rekey Alice-Bob', zorder=5)

ax1.set_ylabel('Vazão (Mbps)')
ax1.set_title('Fig 3. Estabilidade da Vazão com Rotações de Chaves Sincronizadas')
ax1.legend(loc='lower left', ncol=3, fontsize='small')
ax1.set_ylim(0, max(bps)*1.3)

# Painel Inferior: Linha do Tempo (Concorrência Carol-Dave)
ax2.scatter(ab_rekeys, [1]*len(ab_rekeys), marker='|', s=250, color='red', label='Alice-Bob')
cd_rekeys = rekeys[rekeys['pair'] == 'carol-dave']['timestamp'] - json_start
ax2.scatter(cd_rekeys, [0]*len(cd_rekeys), marker='|', s=250, color='orange', label='Carol-Dave')

ax2.set_yticks([0, 1])
ax2.set_yticklabels(['Carol-Dave', 'Alice-Bob'])
ax2.set_xlabel('Tempo de Experimento (s)')
ax2.set_title('Eventos de Rotação por Túnel (Plano de Controle)', fontsize=10)
ax2.set_ylim(-0.5, 1.5)

plt.tight_layout()
plt.savefig('fig4_estabilidade_melhorada.pdf', format='pdf', dpi=300)



print("Novos gráficos gerados: fig2 (atualizada), fig5 e fig6.")