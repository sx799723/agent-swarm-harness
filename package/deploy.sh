#!/bin/bash
#===============================================================================
# MonoSwarm 部署脚本
# 用法: ./deploy.sh [install|start|stop|status|restart]
#===============================================================================

set -e

APP_NAME="monoswarm"
INSTALL_DIR="${HOME}/.hermes/${APP_NAME}"
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${CURRENT_DIR}/src"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 创建安装目录
mkdir -p "${INSTALL_DIR}"

install() {
    log_info "安装 MonoSwarm 到 ${INSTALL_DIR}"
    
    # 复制源代码
    cp -r "${SRC_DIR}/"* "${INSTALL_DIR}/"
    
    # 确保可执行权限
    chmod +x "${INSTALL_DIR}/run.py"
    chmod +x "${INSTALL_DIR}/ceo_brain.py"
    
    # 创建数据目录
    mkdir -p "${INSTALL_DIR}/data"
    
    log_info "安装完成!"
    echo "使用方式:"
    echo "  python3 ${INSTALL_DIR}/run.py status <task_id>  # 查看任务状态"
    echo "  python3 ${INSTALL_DIR}/run.py tasks             # 列出所有任务"
    echo "  python3 ${INSTALL_DIR}/run.py \"任务描述\"        # 执行任务"
}

uninstall() {
    log_warn "卸载 MonoSwarm (数据目录 ${INSTALL_DIR} 将被删除)"
    read -p "确认删除? (y/N): " confirm
    if [ "$confirm" = "y" ]; then
        rm -rf "${INSTALL_DIR}"
        log_info "卸载完成"
    fi
}

# 主逻辑
case "${1:-}" in
    install)
        install
        ;;
    uninstall)
        uninstall
        ;;
    *)
        echo "用法: $0 {install|uninstall}"
        echo ""
        echo "命令:"
        echo "  install    - 安装 MonoSwarm 到 ${INSTALL_DIR}"
        echo "  uninstall  - 卸载 MonoSwarm"
        exit 1
        ;;
esac
