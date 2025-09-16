#!/usr/bin/env sh
set -eu

echo "[entrypoint] 🚀 Starting MUM container…"

# ───────── 1) Create or reuse the chosen UID/GID ──────────
DEFAULT_UID='1000'
DEFAULT_GID='1000'
PUID="${PUID:-$DEFAULT_UID}"
PGID="${PGID:-$DEFAULT_GID}"

# Only attempt user/group changes if running as root
if [ "$(id -u)" = "0" ]; then
  echo "[entrypoint] 👤 Wanted UID=$PUID GID=$PGID"

  # Figure out which *names* already map to those numeric IDs
  EXISTING_USER="$(getent passwd "$PUID" | cut -d: -f1 || true)"
  EXISTING_GRP="$(getent group "$PGID" | cut -d: -f1 || true)"

  # Handle group creation/assignment
  if [ -z "$EXISTING_GRP" ]; then
    echo "[entrypoint] Creating group 'mumgroup' (GID: $PGID)"
    addgroup -S -g "$PGID" mumgroup
    TARGET_GRP="mumgroup"
  else
    echo "[entrypoint] Group GID $PGID already exists as '$EXISTING_GRP'."
    TARGET_GRP="$EXISTING_GRP"
  fi

  # Handle user creation/assignment  
  if [ -z "$EXISTING_USER" ]; then
    echo "[entrypoint] Creating user 'mumuser' (UID: $PUID) in group '$TARGET_GRP'."
    adduser -S -G "$TARGET_GRP" -u "$PUID" mumuser
    TARGET_USER="mumuser"
  else
    echo "[entrypoint] User UID $PUID already exists as '$EXISTING_USER'."
    TARGET_USER="$EXISTING_USER"
    
    # Ensure existing user is in the target group
    if ! groups "$EXISTING_USER" | grep -q "$TARGET_GRP"; then
      echo "[entrypoint] Adding user '$EXISTING_USER' to group '$TARGET_GRP'."
      adduser "$EXISTING_USER" "$TARGET_GRP" 2>/dev/null || {
        echo "[entrypoint] ⚠️  Could not add user to group, but continuing..."
      }
    fi
  fi

  # Handle the special case where mumuser was created in Dockerfile with wrong UID
  # This occurs when upgrading from older container versions
  DOCKERFILE_USER="$(getent passwd mumuser | cut -d: -f3 || true)"
  if [ -n "$DOCKERFILE_USER" ] && [ "$DOCKERFILE_USER" != "$PUID" ]; then
    echo "[entrypoint] ⚠️  Found existing 'mumuser' with wrong UID ($DOCKERFILE_USER), removing and recreating..."
    
    # Remove the incorrectly created user from Dockerfile
    deluser mumuser 2>/dev/null || true
    
    # Remove group if it exists and is empty
    if getent group mumgroup >/dev/null 2>&1; then
      delgroup mumgroup 2>/dev/null || true
    fi
    
    # Now create with correct UID/GID
    if [ -z "$EXISTING_GRP" ]; then
      echo "[entrypoint] Creating group 'mumgroup' (GID: $PGID)"
      addgroup -S -g "$PGID" mumgroup
      TARGET_GRP="mumgroup"
    fi
    
    echo "[entrypoint] Creating user 'mumuser' (UID: $PUID) in group '$TARGET_GRP'."
    adduser -S -G "$TARGET_GRP" -u "$PUID" mumuser
    TARGET_USER="mumuser"
  fi

  # ───────── Fix ownership for bind mounts ──────────
  echo "[entrypoint] ⚙️ Fixing ownership for bind mounts to $TARGET_USER:$TARGET_GRP ($PUID:$PGID)…"
  
  # Create directories if they don't exist and fix ownership
  mkdir -p /app/instance /.cache /tmp
  
  # Fix ownership with better error handling
  if ! chown -R "$PUID":"$PGID" /app/instance /.cache /tmp 2>/dev/null; then
    echo "[entrypoint] ⚠️  Some ownership changes failed, but continuing..."
    # Try individual directories in case one fails
    chown -R "$PUID":"$PGID" /app/instance 2>/dev/null || echo "[entrypoint] ⚠️  Could not chown /app/instance"
    chown -R "$PUID":"$PGID" /.cache 2>/dev/null || echo "[entrypoint] ⚠️  Could not chown /.cache"  
    chown -R "$PUID":"$PGID" /tmp 2>/dev/null || echo "[entrypoint] ⚠️  Could not chown /tmp"
  fi

  # Optional: If you copied files with --chown to 1000:1000 and the user provides different PUID/PGID
  # this section ensures existing files inside /app are re-chowned.
  if [ "$PUID:$PGID" != "$DEFAULT_UID:$DEFAULT_GID" ]; then
    echo "[entrypoint] ⚙️ Re-fixing ownership for custom UID/GID on /app..."
    # Use more efficient approach with better error handling
    if ! find /app -not -user "$PUID" -exec chown "$PUID":"$PGID" {} + 2>/dev/null; then
      echo "[entrypoint] ⚠️  Some app file ownership changes failed, but continuing..."
    fi
  else
    echo "[entrypoint] ⚙️ Default UID/GID detected; assuming ownership is correct."
  fi

  # Verify we have a valid target user before su-exec
  if [ -z "$TARGET_USER" ]; then
    echo "[entrypoint] ❌ TARGET_USER is empty, falling back to mumuser"
    TARGET_USER="mumuser"
  fi
  
  if [ -z "$TARGET_GRP" ]; then
    echo "[entrypoint] ❌ TARGET_GRP is empty, falling back to mumgroup"  
    TARGET_GRP="mumgroup"
  fi

  echo "[entrypoint] 🔄 Switching to user $TARGET_USER:$TARGET_GRP..."
  # Re-exec as that user with better error handling
  if ! exec su-exec "$TARGET_USER":"$TARGET_GRP" "$0" "$@"; then
    echo "[entrypoint] ❌ Failed to switch to user $TARGET_USER:$TARGET_GRP"
    echo "[entrypoint] 🔄 Attempting fallback to numeric IDs $PUID:$PGID..."
    exec su-exec "$PUID":"$PGID" "$0" "$@"
  fi
fi

# This part runs *after* su-exec has dropped privileges and re-executed the script
echo "[entrypoint] 👍 Running as $(id -un):$(id -gn) ($(id -u):$(id -g))"

# Verify we're running as the expected user
CURRENT_UID="$(id -u)"
CURRENT_GID="$(id -g)"
if [ "$CURRENT_UID" != "$PUID" ]; then
  echo "[entrypoint] ⚠️  Warning: Running as UID $CURRENT_UID but expected $PUID"
fi
if [ "$CURRENT_GID" != "$PGID" ]; then
  echo "[entrypoint] ⚠️  Warning: Running as GID $CURRENT_GID but expected $PGID"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2) Database migrations
# ─────────────────────────────────────────────────────────────────────────────
echo "[entrypoint] 🔧 Applying Alembic migrations for MUM…"

# Ensure you are in the correct directory if 'flask' needs it, usually /app
cd /app || {
  echo "[entrypoint] ❌ Could not change to /app directory"
  exit 1
}

# Apply migrations with error handling
if ! flask db upgrade; then
  echo "[entrypoint] ❌ Database migration failed"
  exit 1
fi

echo "[entrypoint] ✅ Database migrations completed successfully"

# ─────────────────────────────────────────────────────────────────────────────
# 3) Hand off to your CMD (e.g. gunicorn)
# ─────────────────────────────────────────────────────────────────────────────
echo "[entrypoint] 🚀 Starting application: $*"
exec "$@"