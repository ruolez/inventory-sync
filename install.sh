#!/bin/bash

################################################################################
# Inventory Sync Installer
# For Ubuntu 24 LTS Server
#
# Usage: sudo ./install.sh [install|update|status|remove]
#
# Options:
#   1. Install - Fresh installation
#   2. Update  - Pull latest, rebuild containers, preserve DB
#   3. Status  - Show container and service status
#   4. Remove  - Complete removal with data preservation prompts
################################################################################

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
APP_NAME="Inventory Sync"
INSTALL_DIR="/opt/inventory-sync"
REPO_URL="https://github.com/ruolez/inventory-sync.git"
APP_PORT="80"
COMPOSE_PROJECT="inventory-sync"

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo -e "\n${BLUE}================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}================================================${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This script must be run as root"
        echo "Please run: sudo $0"
        exit 1
    fi
}

check_ubuntu_version() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        if [[ "$ID" != "ubuntu" ]]; then
            print_warning "This script is designed for Ubuntu 24 LTS"
            print_warning "Detected: $ID $VERSION_ID"
            read -p "Continue anyway? (y/N): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                exit 1
            fi
        fi
    else
        print_warning "Cannot detect OS version"
    fi
}

validate_ip() {
    local ip=$1
    local stat=1

    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        OIFS=$IFS
        IFS='.'
        ip=($ip)
        IFS=$OIFS
        [[ ${ip[0]} -le 255 && ${ip[1]} -le 255 && ${ip[2]} -le 255 && ${ip[3]} -le 255 ]]
        stat=$?
    fi
    return $stat
}

get_local_ip() {
    print_info "Please enter the local IP address where this application will be accessible"

    LOCAL_IP=$(hostname -I | awk '{print $1}')

    while true; do
        if [ -n "$LOCAL_IP" ]; then
            read -p "Local IP address [$LOCAL_IP]: " INPUT_IP
            IP_ADDRESS=${INPUT_IP:-$LOCAL_IP}
        else
            read -p "Local IP address: " IP_ADDRESS
        fi

        if validate_ip "$IP_ADDRESS"; then
            print_success "IP address set to: $IP_ADDRESS"
            break
        else
            print_error "Invalid IP address format. Please try again."
        fi
    done
}

################################################################################
# Installation Functions
################################################################################

install_docker() {
    print_header "Installing Docker"

    if command -v docker &> /dev/null; then
        print_info "Docker is already installed"
        docker --version
        return 0
    fi

    print_info "Updating package index..."
    apt-get update -qq

    print_info "Installing prerequisites..."
    apt-get install -y -qq \
        ca-certificates \
        curl \
        gnupg \
        lsb-release

    print_info "Adding Docker's official GPG key..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    print_info "Setting up Docker repository..."
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null

    print_info "Installing Docker..."
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    print_info "Starting Docker service..."
    systemctl start docker
    systemctl enable docker

    print_success "Docker installed successfully"
    docker --version
}

install_git() {
    print_header "Installing Git"

    if command -v git &> /dev/null; then
        print_info "Git is already installed"
        git --version
        return 0
    fi

    print_info "Installing Git..."
    apt-get install -y -qq git

    print_success "Git installed successfully"
    git --version
}

clone_repository() {
    print_header "Cloning Repository"

    if [ -d "$INSTALL_DIR" ]; then
        print_warning "Installation directory already exists: $INSTALL_DIR"
        read -p "Remove and re-clone? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$INSTALL_DIR"
        else
            print_error "Installation cancelled"
            exit 1
        fi
    fi

    print_info "Cloning from $REPO_URL..."
    git clone "$REPO_URL" "$INSTALL_DIR"

    print_success "Repository cloned successfully"
}

build_and_start() {
    print_header "Building and Starting Application"

    cd "$INSTALL_DIR"

    print_info "Building Docker images (this may take a few minutes)..."
    docker compose build --no-cache

    print_info "Starting containers..."
    docker compose up -d

    print_info "Waiting for PostgreSQL health check and Flask init (10s)..."
    sleep 10

    verify_containers
}

verify_containers() {
    cd "$INSTALL_DIR"

    print_info "Checking container status..."
    if ! docker compose ps | grep -q "running"; then
        print_error "Containers failed to start properly"
        echo ""
        print_info "Showing last 50 lines of logs:"
        docker compose logs --tail=50
        echo ""
        print_error "Check logs above for errors."
        exit 1
    fi

    print_success "Containers are running!"
    echo ""
    docker compose ps
    echo ""

    print_info "Checking health endpoint..."
    if curl -sf http://localhost:${APP_PORT}/health > /dev/null 2>&1; then
        print_success "Health check passed"
    else
        print_warning "Health endpoint not responding yet (app may still be starting)"
        print_info "Try: curl http://localhost:${APP_PORT}/health"
    fi
}

################################################################################
# Main Install
################################################################################

main_install() {
    print_header "Starting Fresh Installation"

    check_ubuntu_version
    get_local_ip
    install_docker
    install_git
    clone_repository

    cd "$INSTALL_DIR"
    build_and_start

    print_header "Installation Complete!"
    echo ""
    print_success "Application is running at: http://$IP_ADDRESS"
    echo ""
    print_info "Next steps:"
    echo "  1. Open http://$IP_ADDRESS in your browser"
    echo "  2. Go to Settings"
    echo "  3. Add stores and configure SQL Server connections"
    echo "  4. Start syncing inventory!"
    echo ""
    print_info "Useful commands:"
    echo "  - View logs:  cd $INSTALL_DIR && docker compose logs -f"
    echo "  - Restart:    cd $INSTALL_DIR && docker compose restart"
    echo "  - Stop:       cd $INSTALL_DIR && docker compose down"
    echo "  - Start:      cd $INSTALL_DIR && docker compose up -d"
    echo ""
}

################################################################################
# Update
################################################################################

update_application() {
    print_header "Updating Application"

    if [ ! -d "$INSTALL_DIR" ]; then
        print_error "Application is not installed at $INSTALL_DIR"
        print_info "Please run installation first"
        exit 1
    fi

    cd "$INSTALL_DIR"

    print_info "Current container status:"
    docker compose ps || true
    echo ""

    print_info "Stopping containers (database volume will be preserved)..."
    docker compose down --remove-orphans

    print_info "Pulling latest changes from GitHub..."
    git pull origin main
    print_success "Code updated from GitHub"

    print_info "Pruning old Docker images..."
    docker image prune -f || true

    print_info "Cleaning Docker builder cache..."
    docker builder prune -f || true

    print_info "Rebuilding containers..."
    docker compose build --no-cache

    print_info "Starting containers..."
    docker compose up -d

    print_info "Waiting for PostgreSQL health check and Flask init (10s)..."
    sleep 10

    verify_containers

    echo ""
    print_success "Application updated successfully!"
    print_success "Access the app at: http://$(hostname -I | awk '{print $1}')"
    echo ""
    print_info "All stores, SQL configs, sync history, and product logs have been preserved."
    print_info "To view logs: cd $INSTALL_DIR && docker compose logs -f"
    echo ""
}

################################################################################
# Status
################################################################################

show_status() {
    print_header "Application Status"

    if [ ! -d "$INSTALL_DIR" ]; then
        print_error "Application is not installed at $INSTALL_DIR"
        return 1
    fi

    cd "$INSTALL_DIR"

    print_info "Docker Compose Status:"
    docker compose ps
    echo ""

    print_info "Running Containers:"
    docker ps --filter "name=${COMPOSE_PROJECT}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    echo ""

    print_info "Database Volume:"
    docker volume inspect ${COMPOSE_PROJECT}_pgdata --format "  Name: {{.Name}}\n  Created: {{.CreatedAt}}\n  Mountpoint: {{.Mountpoint}}" 2>/dev/null || print_warning "Volume ${COMPOSE_PROJECT}_pgdata not found"
    echo ""

    print_info "Health Check:"
    if curl -sf http://localhost:${APP_PORT}/health > /dev/null 2>&1; then
        print_success "Health endpoint responding at http://localhost:${APP_PORT}/health"
    else
        print_warning "Health endpoint not responding"
    fi
    echo ""

    print_info "Tip: To view live logs run: cd $INSTALL_DIR && docker compose logs -f"
    echo ""
}

################################################################################
# Remove
################################################################################

remove_application() {
    print_header "Removing Application"

    print_warning "This will remove the application and all containers"
    read -p "Are you sure you want to continue? (y/N): " -n 1 -r
    echo

    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "Removal cancelled"
        exit 0
    fi

    if [ -d "$INSTALL_DIR" ]; then
        cd "$INSTALL_DIR"

        print_info "Stopping and removing containers..."
        docker compose down --remove-orphans || true

        # Prompt about database volume
        echo ""
        print_warning "The database volume contains all settings, stores, sync history, and product logs."
        read -p "Remove database volume (all data will be lost)? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            docker volume rm ${COMPOSE_PROJECT}_pgdata 2>/dev/null && \
                print_info "Database volume removed" || \
                print_warning "Database volume not found or already removed"
        else
            print_success "Database volume preserved (${COMPOSE_PROJECT}_pgdata)"
            print_info "To remove later: docker volume rm ${COMPOSE_PROJECT}_pgdata"
        fi

        print_info "Removing Docker images for the project..."
        docker compose down --rmi all 2>/dev/null || true

        cd /

        print_info "Removing application directory..."
        rm -rf "$INSTALL_DIR"

        print_info "Pruning unused Docker images..."
        docker image prune -f || true

        print_success "Application removed successfully"
    else
        print_warning "Application directory not found: $INSTALL_DIR"
    fi

    echo ""
    read -p "Remove Docker entirely? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        print_info "Removing Docker..."
        apt-get remove -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        apt-get autoremove -y
        print_success "Docker removed"
    else
        print_info "Docker preserved"
    fi

    echo ""
    print_success "Removal complete"
}

################################################################################
# Main Menu
################################################################################

show_menu() {
    print_header "$APP_NAME Installer"

    echo "Please select an option:"
    echo ""
    echo "  1) Install - Fresh installation on clean system"
    echo "  2) Update  - Pull latest changes, rebuild, preserve DB"
    echo "  3) Status  - Check application and container status"
    echo "  4) Remove  - Complete removal of application"
    echo "  5) Exit"
    echo ""
}

################################################################################
# Main Script
################################################################################

check_root

if [ $# -eq 0 ]; then
    while true; do
        show_menu
        read -p "Enter your choice [1-5]: " choice

        case $choice in
            1)
                main_install
                break
                ;;
            2)
                update_application
                break
                ;;
            3)
                show_status
                read -p "Press Enter to continue..."
                ;;
            4)
                remove_application
                break
                ;;
            5)
                print_info "Exiting..."
                exit 0
                ;;
            *)
                print_error "Invalid option. Please try again."
                ;;
        esac
    done
else
    case "$1" in
        install)
            main_install
            ;;
        update)
            update_application
            ;;
        status)
            show_status
            ;;
        remove)
            remove_application
            ;;
        *)
            echo "Usage: $0 [install|update|status|remove]"
            echo "Or run without arguments for interactive menu"
            exit 1
            ;;
    esac
fi
