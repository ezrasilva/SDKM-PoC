#!/bin/bash
set -e

#echo "[BOOT] Configurando latência de rede..."
# O "|| true" impede que o script pare se o comando falhar (comum em containers sem privilégios totais)
#tc qdisc add dev eth0 root netem delay 50ms || echo "Aviso: Não foi possível aplicar NetEm (falta --cap-add=NET_ADMIN?)"

echo "[BOOT] Iniciando StrongSwan Charon..."
# Iniciamos o Charon em background, mas forçamos logs para stdout para debugging
/usr/lib/ipsec/charon --debug-ike 2 --debug-knl 2 --debug-cfg 2 &
CHARON_PID=$!

# Espera um pouco para garantir que o processo não morreu imediatamente
sleep 2

if ! kill -0 $CHARON_PID > /dev/null 2>&1; then
    echo "[ERRO] O processo Charon morreu imediatamente! Verifique os logs acima."
    exit 1
fi

echo "[BOOT] Charon rodando (PID $CHARON_PID). Carregando configurações swanctl..."
sleep 1
swanctl --load-all
echo "[BOOT] Configurações carregadas. Iniciando Agente VPN..."

# Mantém o container rodando mesmo se o Python falhar
python3 /scripts/vpn_agent.py &
AGENT_PID=$!

# Mantém o container vivo e monitora os processos
while true; do
    # Verifica se o Charon ainda está rodando
    if ! kill -0 $CHARON_PID > /dev/null 2>&1; then
        echo "[ERRO] Charon morreu! Reiniciando..."
        /usr/lib/ipsec/charon --debug-ike 2 --debug-knl 2 --debug-cfg 2 &
        CHARON_PID=$!
        sleep 2
        swanctl --load-all
    fi
    
    # Verifica se o agente ainda está rodando
    if ! kill -0 $AGENT_PID > /dev/null 2>&1; then
        echo "[AVISO] Agente VPN morreu! Reiniciando..."
        python3 /scripts/vpn_agent.py &
        AGENT_PID=$!
    fi
    
    sleep 10
done