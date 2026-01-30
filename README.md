<<<<<<< HEAD
# SDKM-PoC
# SDKM-PoC

# 🔐 Quantum VPN - Key Injection Daemon

Sistema de daemon HTTP para injeção de chaves híbridas (PQC + QKD) em túneis IPsec/StrongSwan, totalmente dockerizado.

## 🚀 Quick Start

**Executa o fluxo COMPLETO automaticamente:**

```bash
make docker-full-workflow
```

Isso faz:
1. ✅ Sobe containers Alice, Bob, daemons e orchestrador
2. ✅ Executa `swanctl --load-all` (carrega configurações)
3. ✅ Executa `swanctl --initiate` (inicia túnel)
4. ✅ Aguarda túnel estar ESTABLISHED
5. ✅ Inicia orchestrador para injetar chaves e fazer rekeys

Ou de forma **manual/controlada**:

```bash
docker-compose up -d alice bob daemon-alice daemon-bob
docker exec alice swanctl --load-all && docker exec bob swanctl --load-all
docker exec alice swanctl --initiate --child net-traffic
docker-compose up orchestrator  # Aguarda tunnel ativo automaticamente
```

## 📋 Fluxo de Operação

```
1. Containers iniciam (Alice, Bob, Daemons)
   ↓
2. swanctl --load-all carrega configurações
   ↓
3. swanctl --initiate inicia negociação IKE
   ↓
4. Tunnel estabelecido (IKE_SA + CHILD_SA)
   ↓
5. Orquestrador AGUARDA tunnel estar ativo
   ↓
6. Orquestrador injeta chaves híbridas (PQC + QKD)
   ↓
7. Daemons injetam no socket Unix do StrongSwan
   ↓
8. Rekeys a cada 30s com novas chaves híbridas
   ↓
9. Verifica continuamente se tunnel permanece ativo
```

📖 **Leia a documentação completa:** [docs/ENTENDIMENTO_DO_FLUXO.md](docs/ENTENDIMENTO_DO_FLUXO.md)

## 📋 Comandos Principais

| Comando | Descrição |
|---------|-----------|
| `make docker-full-workflow` | **RECOMENDADO**: Executa tudo automaticamente |
| `make docker-up` | Iniciar containers |
| `make docker-down` | Parar containers |
| `make docker-tunnel-activate` | Ativar túnel manualmente |
| `make docker-tunnel-status` | Ver status do túnel |
| `make docker-health` | Verificar saúde dos daemons |
| `make help` | Lista todos os comandos |

## 📁 Estrutura do Projeto

```
quantum_vpn/
├── docker-compose.yml          # Orquestração dos 6 containers
├── Dockerfile                  # Imagem base com StrongSwan
├── Makefile                    # Comandos (25+)
├── requirements.txt            # Dependências Python
│
├── scripts/                    # Scripts Python principais
│   ├── key_injection_daemon.py (HTTP server)
│   ├── orchestrator_with_daemon.py (coordena fluxo)
│   ├── hybrid_key_gen.py (mistura PQC+QKD)
│   └── test_daemon.py
│
├── scripts_helper/             # Scripts auxiliares (bash)
│   ├── full_workflow.sh (fluxo completo automatizado)
│   ├── activate_tunnel.sh
│   ├── health_check_docker.sh
│   └── ...
│
├── docs/                       # Documentação detalhada
│   ├── ENTENDIMENTO_DO_FLUXO.md (este é o importante!)
│   ├── FLUXO_CORRETO.md
│   ├── README_DAEMON.md
│   └── ...
│
├── config/, alice/, bob/, sockets/, metrics/
```

## 🔍 Status Atual

✅ **Túnel VPN**: Totalmente funcional, aguarda ser iniciado  
✅ **Daemons**: Alice (8000) e Bob (8001), aguardando requisições  
✅ **Orquestrador**: Aguarda túnel ativo para iniciar injeção de chaves  
✅ **Fluxo**: Implementado corretamente com espera e verificação contínua

## 📚 Documentação

Veja a documentação completa em [docs/README_DAEMON.md](docs/README_DAEMON.md)

## 📊 Serviços Docker

| Serviço | IP | Porta | Status |
|---------|----|----|--------|
| alice | 10.100.1.10 | - | ✅ |
| bob | 10.100.2.10 | - | ✅ |
| daemon-alice | 10.5.0.10 | 8000 | ✅ |
| daemon-bob | 10.5.0.11 | 8001 | ✅ |
| orchestrator | 10.5.0.99 | - | ✅ |

## 🎯 Workflow Típico

```bash
# 1. Setup inicial
make docker-build
make docker-up

# 2. Validar
make validate

# 3. Ativar túnel
make docker-tunnel-activate

# 4. Monitorar
make docker-logs

# 5. Testar injeção de chaves
make docker-run-orchestrator

# 6. Parar
make docker-down
```

## 🐛 Troubleshooting

```bash
# Ver logs detalhados
make docker-logs

# Debug de um container específico
docker exec -it alice bash

# Recriar sistema
make docker-clean
make docker-build
make docker-up
```

---

**Desenvolvido para Quantum VPN - Post-Quantum Cryptography**
>>>>>>> 6022eee (terminado)
