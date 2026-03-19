#!/bin/bash
set -e

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info() { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() {
  echo -e "${RED}[ERROR]${NC} $*"
  exit 1
}

REPO_URL="https://github.com/securo-finance/securo.git"
COMPOSE_FILE="docker-compose.prod.yml"
HEALTH_URL="http://localhost:8000/api/health"
HEALTH_TIMEOUT=60
APP_URL="http://localhost:3000"

# ── OS Detection ─────────────────────────────────────────────────────────────
detect_os() {
  OS="$(uname -s)"
  case "$OS" in
  Linux)
    if [ -f /etc/os-release ]; then
      . /etc/os-release
      DISTRO="$ID"
    else
      error "Cannot detect Linux distribution. /etc/os-release not found."
    fi
    ;;
  Darwin)
    DISTRO="macos"
    ;;
  *)
    error "Unsupported operating system: $OS"
    ;;
  esac
  info "Detected OS: $OS ($DISTRO)"
}

# ── Docker Installation ──────────────────────────────────────────────────────
install_docker_linux() {
  echo ""
  echo -e "${BOLD}Docker is not installed. Install it now?${NC}"
  read -r -p "  [y/N] " response
  case "$response" in
  [yY][eE][sS] | [yY]) ;;
  *) error "Docker is required. Install it manually: https://docs.docker.com/engine/install/" ;;
  esac

  info "Installing Docker..."

  case "$DISTRO" in
  ubuntu | debian | linuxmint | pop)
    sudo apt-get update -qq
    sudo apt-get install -y -qq ca-certificates curl gnupg
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$DISTRO/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$DISTRO \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" |
      sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ;;
  fedora)
    sudo dnf -y install dnf-plugins-core
    sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
    sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo systemctl start docker
    sudo systemctl enable docker
    ;;
  centos | rhel | rocky | almalinux)
    sudo yum install -y yum-utils
    sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    sudo yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo systemctl start docker
    sudo systemctl enable docker
    ;;
  *)
    error "Automatic Docker install not supported for $DISTRO. Install manually: https://docs.docker.com/engine/install/"
    ;;
  esac

  # Add current user to docker group
  if ! groups "$USER" | grep -q docker; then
    sudo usermod -aG docker "$USER"
    warn "Added $USER to docker group. You may need to log out and back in for this to take effect."
  fi

  success "Docker installed"
}

check_docker() {
  if ! command -v docker &>/dev/null; then
    case "$DISTRO" in
    macos)
      error "Docker Desktop is not installed. Download it from https://www.docker.com/products/docker-desktop/ and re-run this script."
      ;;
    *)
      install_docker_linux
      ;;
    esac
  else
    success "Docker is installed"
  fi

  # Verify docker compose is available
  if ! docker compose version &>/dev/null; then
    error "docker compose plugin not found. Please install docker-compose-plugin."
  fi
}

# ── Wait for Docker Daemon ───────────────────────────────────────────────────
wait_for_docker() {
  info "Checking Docker daemon..."
  local retries=0
  local max_retries=15

  while ! docker info &>/dev/null; do
    retries=$((retries + 1))
    if [ "$retries" -ge "$max_retries" ]; then
      error "Docker daemon is not running. Please start Docker and re-run this script."
    fi
    warn "Docker daemon not ready, retrying ($retries/$max_retries)..."
    sleep 2
  done

  success "Docker daemon is running"
}

# ── Repository Setup ─────────────────────────────────────────────────────────
setup_repo() {
  if [ -f "$COMPOSE_FILE" ]; then
    info "Found $COMPOSE_FILE in current directory"
    return
  fi

  info "Cloning Securo repository..."
  git clone "$REPO_URL" securo
  cd securo
  success "Repository cloned"
}

# ── Generate .env ────────────────────────────────────────────────────────────
generate_env() {
  if [ -f .env ]; then
    info ".env file already exists, skipping generation"
    return
  fi

  info "Generating .env file..."

  if command -v openssl &>/dev/null; then
    SECRET_KEY=$(openssl rand -hex 32)
  else
    SECRET_KEY=$(head -c 32 /dev/urandom | xxd -p | tr -d '\n')
  fi

  cat >.env <<EOF
SECRET_KEY=$SECRET_KEY
PLUGGY_CLIENT_ID=
PLUGGY_CLIENT_SECRET=
EOF

  success ".env file created with a random SECRET_KEY"
}

# ── Start Services ───────────────────────────────────────────────────────────
start_services() {
  info "Pulling latest images..."
  docker compose -f "$COMPOSE_FILE" pull

  info "Starting Securo..."
  docker compose -f "$COMPOSE_FILE" up -d

  success "Containers started"
}

# ── Health Check ─────────────────────────────────────────────────────────────
wait_for_health() {
  info "Waiting for Securo to be ready (up to ${HEALTH_TIMEOUT}s)..."
  local elapsed=0

  while [ "$elapsed" -lt "$HEALTH_TIMEOUT" ]; do
    if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
      success "Securo is healthy"
      return
    fi
    sleep 3
    elapsed=$((elapsed + 3))
    printf "  %ds / %ds\r" "$elapsed" "$HEALTH_TIMEOUT"
  done

  echo ""
  warn "Health check timed out after ${HEALTH_TIMEOUT}s."
  warn "The app may still be starting. Check logs with: docker compose -f $COMPOSE_FILE logs -f"
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
  echo ""
  echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
  echo -e "${BOLD}║         Securo Installer             ║${NC}"
  echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"
  echo ""

  detect_os
  check_docker
  wait_for_docker
  setup_repo
  generate_env
  start_services
  wait_for_health

  echo ""
  echo -e "${GREEN}${BOLD}════════════════════════════════════════${NC}"
  echo -e "${GREEN}${BOLD}  Securo is running!${NC}"
  echo -e "${GREEN}${BOLD}  Open ${APP_URL}${NC}"
  echo -e "${GREEN}${BOLD}════════════════════════════════════════${NC}"
  echo ""
  echo -e "  Useful commands:"
  echo -e "    ${BLUE}docker compose -f $COMPOSE_FILE logs -f${NC}    # View logs"
  echo -e "    ${BLUE}docker compose -f $COMPOSE_FILE ps${NC}         # Container status"
  echo -e "    ${BLUE}docker compose -f $COMPOSE_FILE down${NC}       # Stop Securo"
  echo ""
}

main
