#!/usr/bin/env bash
# EVA-VM 宿主备份/恢复脚本
#
# 功能：
#   backup  - 创建 VM 快照 + 备份数据目录
#   restore - 从备份恢复 VM 和数据
#   list    - 列出可用备份
#
# 使用方法：
#   ./backup-restore.sh backup
#   ./backup-restore.sh restore 2026-07-16_03-00
#   ./backup-restore.sh list

set -euo pipefail

VM_NAME="eva-vm"
VM_DISK="/var/lib/libvirt/images/eva-vm.qcow2"
DATA_DISK="/var/lib/libvirt/images/eva-data.qcow2"
DATA_DIR="/mnt/eva-data"           # 数据盘挂载点
BACKUP_ROOT="/backup/eva"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M)

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date +%T)]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── 依赖检查 ──────────────────────────────────────────────
check_deps() {
    for cmd in virsh qemu-img rsync; do
        command -v "$cmd" >/dev/null || err "Missing dependency: $cmd"
    done
}

# ── VM 状态管理 ───────────────────────────────────────────
vm_stop() {
    log "Stopping VM: $VM_NAME"
    virsh shutdown "$VM_NAME" 2>/dev/null || true
    for i in $(seq 1 30); do
        local state
        state=$(virsh domstate "$VM_NAME" 2>/dev/null || echo "shut off")
        [[ "$state" == "shut off" ]] && return 0
        sleep 2
    done
    log "Force destroying VM: $VM_NAME"
    virsh destroy "$VM_NAME" 2>/dev/null || true
}

vm_start() {
    log "Starting VM: $VM_NAME"
    virsh start "$VM_NAME"
}

# ── 备份 ──────────────────────────────────────────────────
do_backup() {
    local backup_dir="$BACKUP_ROOT/$TIMESTAMP"
    mkdir -p "$backup_dir"

    log "=== EVA Backup $TIMESTAMP ==="

    # 1. 停止 VM
    vm_stop

    # 2. 快照系统盘
    log "Snapshotting VM disk..."
    qemu-img snapshot -c "backup-$TIMESTAMP" "$VM_DISK"

    # 3. 复制数据目录
    log "Copying data directory..."
    rsync -a --delete "$DATA_DIR/" "$backup_dir/data/"

    # 4. 备份 VM XML
    log "Backing up VM definition..."
    virsh dumpxml "$VM_NAME" > "$backup_dir/eva-vm.xml"

    # 5. 元数据
    cat > "$backup_dir/backup.json" <<EOF
{
  "timestamp": "$TIMESTAMP",
  "vm": "$VM_NAME",
  "vm_disk": "$VM_DISK",
  "data_disk": "$DATA_DISK",
  "backup_root": "$BACKUP_ROOT"
}
EOF

    # 6. 重启 VM
    vm_start

    # 7. 清理旧备份
    log "Cleaning old backups (keeping last 7 daily)..."
    ls -1d "$BACKUP_ROOT"/*/ 2>/dev/null | sort -r | tail -n +8 | xargs rm -rf 2>/dev/null || true

    log "Backup complete: $backup_dir"
}

# ── 恢复 ──────────────────────────────────────────────────
do_restore() {
    local restore_point="$1"
    local restore_dir="$BACKUP_ROOT/$restore_point"

    [[ -d "$restore_dir" ]] || err "Backup not found: $restore_dir"

    log "=== EVA Restore from $restore_point ==="

    # 1. 确认操作
    echo -n "This will STOP the VM and overwrite data. Continue? (yes/no): "
    read -r confirm
    [[ "$confirm" == "yes" ]] || err "Aborted by user."

    # 2. 停止 VM
    vm_stop

    # 3. 恢复数据
    log "Restoring data..."
    rsync -a --delete "$restore_dir/data/" "$DATA_DIR/"

    # 4. 恢复磁盘快照
    log "Restoring VM disk snapshot..."
    qemu-img snapshot -a "backup-$restore_point" "$VM_DISK"

    # 5. 重新定义 VM
    log "Redefining VM..."
    virsh define "$restore_dir/eva-vm.xml"

    # 6. 启动 VM
    vm_start

    log "Restore complete. EVA-VM is now running from $restore_point."
}

# ── 列表 ──────────────────────────────────────────────────
do_list() {
    log "Available backups:"
    if ! ls -1d "$BACKUP_ROOT"/*/ 2>/dev/null; then
        echo "  (no backups found)"
    fi
}

# ── 主入口 ────────────────────────────────────────────────
check_deps

case "${1:-}" in
    backup)  do_backup ;;
    restore) do_restore "${2:?Usage: $0 restore <timestamp>}" ;;
    list)    do_list ;;
    *)       echo "Usage: $0 {backup|restore <ts>|list}" >&2; exit 1 ;;
esac
