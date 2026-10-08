#!/usr/bin/env bash
# Validate persistent storage against systemd home and private-tmp isolation.

validate_storage_root() {
    # Return a canonical storage path accessible to all panel services.
    local requested="$1"
    local canonical candidate
    [[ "$requested" =~ ^/[A-Za-z0-9._/-]+$ ]] || return 1
    canonical="$(realpath -m -- "$requested")" || return 1
    for candidate in "$requested" "$canonical"; do
        case "$candidate" in
            /|/root|/root/*|/home|/home/*|/run/user|/run/user/*|/tmp|/tmp/*|/var/tmp|/var/tmp/*)
                return 1
                ;;
        esac
    done
    printf '%s\n' "$canonical"
}
