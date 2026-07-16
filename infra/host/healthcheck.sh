#!/usr/bin/env bash
# EVA-VM 宿主健康检查脚本
#
# 检查项：
#   1. VM 是否在运行
#   2. eva-core 端口是否响应
#   3. 磁盘使用率
#   4. 内存使用情况
#
# 使用方法：
#   ./healthcheck.sh          # 输出到 stdout
#   ./healthcheck.sh --json   # JSON 输出（适合监控系统）
#   ./healthcheck.sh --quiet  # 静默模式，退出码表示健康状态

set -euo pipefail

VM_NAME="eva-vm"
EVA_PORT=8000
VM_IP="192.168.122.100"   # EVA-VM 内网地址
TIMEOUT=5

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC} $*"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $*"; }

all_ok=true

# ── 1. VM 存活检查 ────────────────────────────────────────
check_vm_alive() {
    local state
    state=$(virsh domstate "$VM_NAME" 2>/dev/null || echo "undefined")
    if [[ "$state" == "running" ]]; then
        ok "VM $VM_NAME is running"
    else
        fail "VM $VM_NAME state: $state"
        all_ok=false
    fi
}

# ── 2. eva-core 端口检查 ──────────────────────────────────
check_eva_port() {
    if timeout "$TIMEOUT" bash -c "echo >/dev/tcp/$VM_IP/$EVA_PORT" 2>/dev/null; then
        ok "eva-core port $EVA_PORT reachable"
    else
        fail "eva-core port $EVA_PORT NOT reachable on $VM_IP"
        all_ok=false
    fi
}

# ── 3. EVA API 健康端点 ───────────────────────────────────
check_eva_api() {
    local resp
    resp=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" \
        "http://$VM_IP:$EVA_PORT/health/live" 2>/dev/null || echo "000")
    if [[ "$resp" == "200" ]]; then
        ok "EVA health endpoint returns 200"
    else
        fail "EVA health endpoint returns $resp"
        all_ok=false
    fi
}

# ── 4. 磁盘检查 ───────────────────────────────────────────
check_disk() {
    local usage
    usage=$(df -h /var/lib/libvirt/images/ | awk 'NR==2 {print $5}' | tr -d '%')
    if [[ "$usage" -lt 80 ]]; then
        ok "Disk usage: ${usage}%"
    elif [[ "$usage" -lt 95 ]]; then
        warn "Disk usage: ${usage}% (nearing capacity)"
    else
        fail "Disk usage: ${usage}% (critical)"
        all_ok=false
    fi
}

# ── 5. 内存检查 ───────────────────────────────────────────
check_memory() {
    local total used free pct
    read -r total used free <<< "$(free -m | awk 'NR==2 {print $2, $3, $4}')"
    pct=$((used * 100 / total))
    if [[ "$pct" -lt 80 ]]; then
        ok "Host memory: ${used}M / ${total}M (${pct}%)"
    elif [[ "$pct" -lt 95 ]]; then
        warn "Host memory: ${used}M / ${total}M (${pct}%)"
    else
        fail "Host memory: ${used}M / ${total}M (${pct}%)"
        all_ok=false
    fi
}

# ── JSON 输出 ──────────────────────────────────────────────
output_json() {
    local vm_state eva_status
    vm_state=$(virsh domstate "$VM_NAME" 2>/dev/null || echo "undefined")
    eva_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" \
        "http://$VM_IP:$EVA_PORT/health/live" 2>/dev/null || echo "000")
    local disk_usage
    disk_usage=$(df -h /var/lib/libvirt/images/ | awk 'NR==2 {print $5}' | tr -d '%')
    local mem_total mem_used
    read -r mem_total mem_used <<< "$(free -m | awk 'NR==2 {print $2, $3}')"
    local healthy="false"
    [[ "$vm_state" == "running" && "$eva_status" == "200" && "$disk_usage" -lt 95 ]] && healthy="true"

    cat <<EOF
{
  "healthy": $healthy,
  "vm": "$vm_state",
  "eva_api": $eva_status,
  "disk_usage_pct": $disk_usage,
  "memory_mb": {"total": $mem_total, "used": $mem_used}
}
EOF
}

# ── 主入口 ────────────────────────────────────────────────
case "${1:-}" in
    --json)
        output_json
        ;;
    --quiet)
        check_vm_alive >/dev/null 2>&1
        check_eva_port >/dev/null 2>&1
        check_eva_api >/dev/null 2>&1
        $all_ok && exit 0 || exit 1
        ;;
    *)
        echo "=== EVA-VM Host Health Check ($(date)) ==="
        echo ""
        check_vm_alive
        check_eva_port
        check_eva_api
        check_disk
        check_memory
        echo ""
        if $all_ok; then
            echo -e "${GREEN}All checks passed.${NC}"
            exit 0
        else
            echo -e "${RED}Some checks failed.${NC}"
            exit 1
        fi
        ;;
esac
