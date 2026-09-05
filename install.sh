#!/bin/bash
set -e

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

REPO_URL="https://github.com/securo-finance/securo.git"
COMPOSE_FILE="docker-compose.prod.yml"
HEALTH_URL="http://localhost:8000/api/health"
HEALTH_TIMEOUT=60
APP_URL="http://localhost:3000"
RUNTIME="docker"
COMPOSE_BACKEND="docker-compose-plugin"
COMPOSE_CMD=(docker compose)
COMPOSE_CMD_DISPLAY="docker compose"

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
    [yY][eE][sS]|[yY]) ;;
    *) error "Docker or Podman is required. Install one manually and re-run this script." ;;
  esac

  info "Installing Docker..."

  case "$DISTRO" in
    ubuntu|debian|linuxmint|pop)
      sudo apt-get update -qq
      sudo apt-get install -y -qq ca-certificates curl gnupg
      sudo install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/$DISTRO/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      sudo chmod a+r /etc/apt/keyrings/docker.gpg
      echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$DISTRO \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
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
    centos|rhel|rocky|almalinux)
      sudo yum install -y yum-utils
      sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
      sudo yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      sudo systemctl start docker
      sudo systemctl enable docker
      ;;
    *)
      error "Automatic Docker install not supported for $DISTRO. Install Docker manually (https://docs.docker.com/engine/install/) or install Podman with podman-compose."
      ;;
  esac

  # Add current user to docker group
  if ! groups "$USER" | grep -q docker; then
    sudo usermod -aG docker "$USER"
    warn "Added $USER to docker group. You may need to log out and back in for this to take effect."
  fi

  success "Docker installed"
}

is_docker_compose_v2() {
  command -v docker-compose > /dev/null 2>&1 && docker-compose version 2>/dev/null | grep -Eq 'Docker Compose version v?2|v2'
}

version_at_least() {
  local version="${1%%[-+]*}"
  local minimum="${2%%[-+]*}"
  local IFS=.
  local -a version_parts minimum_parts
  local version_part minimum_part i

  [ -n "$version" ] || return 1

  read -r -a version_parts <<< "$version"
  read -r -a minimum_parts <<< "$minimum"

  for i in 0 1 2; do
    version_part="${version_parts[$i]:-0}"
    minimum_part="${minimum_parts[$i]:-0}"
    version_part="${version_part//[^0-9]/}"
    minimum_part="${minimum_part//[^0-9]/}"
    version_part="${version_part:-0}"
    minimum_part="${minimum_part:-0}"

    if ((10#$version_part > 10#$minimum_part)); then
      return 0
    fi
    if ((10#$version_part < 10#$minimum_part)); then
      return 1
    fi
  done

  return 0
}

check_podman_compose_version() {
  local pc_version

  # podman-compose --version prints both the Podman version and the
  # podman-compose version; target the compose line explicitly.
  pc_version=$(podman-compose --version 2>&1 | grep -i 'podman-compose version' | awk '{print $NF}' || true)
  info "podman-compose ${pc_version:-unknown} found"

  # 1.6.0 is the first release that resolves nested ${VAR:-${OTHER:-x}}
  # interpolation. Below it, FRONTEND_URL is handed to the containers with the
  # inner ${FRONTEND_PORT:-3000} left as literal text, which silently breaks
  # CORS, passkeys, and the OAuth callbacks.
  if [ -n "$pc_version" ] && ! version_at_least "$pc_version" "1.6.0"; then
    warn "podman-compose $pc_version is old. Upgrade to >= 1.6.0: earlier versions leave FRONTEND_URL unresolved and break login callbacks."
  fi
}

resolve_compose_command() {
  case "$RUNTIME" in
    podman)
      # Prefer Docker Compose v2 if present; otherwise call podman-compose
      # directly instead of going through the Docker CLI shim.
      if is_docker_compose_v2; then
        COMPOSE_BACKEND="docker-compose-v2"
        COMPOSE_CMD=(docker-compose)
        COMPOSE_CMD_DISPLAY="docker-compose"
        success "docker-compose v2 found — best Podman compatibility"
      elif command -v podman-compose > /dev/null 2>&1; then
        COMPOSE_BACKEND="podman-compose"
        COMPOSE_CMD=(podman-compose)
        COMPOSE_CMD_DISPLAY="podman-compose"
        check_podman_compose_version
      elif command -v docker > /dev/null 2>&1 && docker compose version > /dev/null 2>&1; then
        COMPOSE_BACKEND="docker-compose-plugin"
        COMPOSE_CMD=(docker compose)
        COMPOSE_CMD_DISPLAY="docker compose"
        success "docker compose provider found"
      else
        error "No compose provider found. Install podman-compose or Docker Compose v2."
      fi
      ;;
    *)
      if command -v docker > /dev/null 2>&1 && docker compose version > /dev/null 2>&1; then
        COMPOSE_BACKEND="docker-compose-plugin"
        COMPOSE_CMD=(docker compose)
        COMPOSE_CMD_DISPLAY="docker compose"
      elif is_docker_compose_v2; then
        COMPOSE_BACKEND="docker-compose-v2"
        COMPOSE_CMD=(docker-compose)
        COMPOSE_CMD_DISPLAY="docker-compose"
      else
        error "docker compose plugin not found. Please install docker-compose-plugin."
      fi
      ;;
  esac

  success "Using compose command: $COMPOSE_CMD_DISPLAY"
}

check_container_runtime() {
  if command -v docker > /dev/null 2>&1; then
    if docker version 2>&1 | grep -qi podman; then
      RUNTIME="podman"
      PODMAN_VERSION=$(podman --version 2>/dev/null | awk '{print $NF}')
      success "Podman is installed${PODMAN_VERSION:+ ($PODMAN_VERSION)}"
      warn "Detected Podman behind the Docker-compatible CLI"
    else
      RUNTIME="docker"
      success "Docker is installed"
    fi
  elif command -v podman > /dev/null 2>&1; then
    RUNTIME="podman"
    PODMAN_VERSION=$(podman --version 2>/dev/null | awk '{print $NF}')
    success "Podman is installed${PODMAN_VERSION:+ ($PODMAN_VERSION)}"
    warn "Docker CLI not found; using Podman directly"
  else
    case "$DISTRO" in
      macos)
        error "Docker Desktop is not installed. Download it from https://www.docker.com/products/docker-desktop/ and re-run this script."
        ;;
      *)
        install_docker_linux
        RUNTIME="docker"
        ;;
    esac
  fi

  resolve_compose_command
}

# ── Wait for Container Runtime ───────────────────────────────────────────────
ensure_podman_socket() {
  [ "$COMPOSE_BACKEND" = "docker-compose-v2" ] || return 0

  local socket_path
  socket_path="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/podman/podman.sock"

  if [ ! -S "$socket_path" ]; then
    info "Starting Podman socket for Docker Compose v2..."
    systemctl --user start podman.socket > /dev/null 2>&1 || true
    sleep 1
  fi

  if [ ! -S "$socket_path" ]; then
    error "Podman socket could not be started. Run: systemctl --user start podman.socket"
  fi

  if [ -z "${DOCKER_HOST:-}" ]; then
    export DOCKER_HOST="unix://$socket_path"
  fi

  success "Podman socket is active"
}

wait_for_container_runtime() {
  if [ "$RUNTIME" = "podman" ]; then
    info "Checking Podman..."
    if ! podman info > /dev/null 2>&1; then
      error "Podman is not available. Please check your Podman installation and re-run this script."
    fi
    ensure_podman_socket
    success "Podman is ready"
    return
  fi

  info "Checking Docker daemon..."
  local retries=0
  local max_retries=15

  while ! docker info &> /dev/null; do
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

  if command -v openssl &> /dev/null; then
    SECRET_KEY=$(openssl rand -hex 32)
  else
    SECRET_KEY=$(head -c 32 /dev/urandom | xxd -p | tr -d '\n')
  fi

  cat > .env <<EOF
SECRET_KEY=$SECRET_KEY
PLUGGY_CLIENT_ID=
PLUGGY_CLIENT_SECRET=
EOF

  success ".env file created with a random SECRET_KEY"
}

# ── Start Services ───────────────────────────────────────────────────────────
start_services() {
  info "Pulling latest images..."
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" pull

  info "Starting Securo..."
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" up -d

  success "Containers started"
}

# ── Health Check ─────────────────────────────────────────────────────────────
wait_for_health() {
  info "Waiting for Securo to be ready (up to ${HEALTH_TIMEOUT}s)..."
  local elapsed=0

  while [ "$elapsed" -lt "$HEALTH_TIMEOUT" ]; do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
      success "Securo is healthy"
      return
    fi
    sleep 3
    elapsed=$((elapsed + 3))
    printf "  %ds / %ds\r" "$elapsed" "$HEALTH_TIMEOUT"
  done

  echo ""
  warn "Health check timed out after ${HEALTH_TIMEOUT}s."
  warn "The app may still be starting. Check logs with: $COMPOSE_CMD_DISPLAY -f $COMPOSE_FILE logs -f"
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
  echo ""
  echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
  echo -e "${BOLD}║         Securo Installer             ║${NC}"
  echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"
  echo ""

  detect_os
  check_container_runtime
  wait_for_container_runtime
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
  echo -e "    ${BLUE}$COMPOSE_CMD_DISPLAY -f $COMPOSE_FILE logs -f${NC}    # View logs"
  echo -e "    ${BLUE}$COMPOSE_CMD_DISPLAY -f $COMPOSE_FILE ps${NC}         # Container status"
  echo -e "    ${BLUE}$COMPOSE_CMD_DISPLAY -f $COMPOSE_FILE down${NC}       # Stop Securo"
  echo ""
}

main
