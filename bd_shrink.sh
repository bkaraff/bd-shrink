#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

VERSION="0.2.0"

# ─── defaults ────────────────────────────────────────────────────────────────
TARGET_GB=23
EXTRAS_SCALE="1280:720"
EXTRAS_AUDIO_BITRATE="128k"
EXTRAS_CRF=22
MAIN_PRESET="slow"
OVERHEAD_MB=200
DRY_RUN=false
FORCE=false
KEEP_ONE=false
MOVIE_ONLY=false
OUTPUT_ISO=false
COMMENTARY_AUDIO_BITRATE="128k"
USE_TUI=false
INSTALL_DEPS=false
BURN=false
BURN_DEVICE=""
BURN_SPEED=""
CLEAN_WORK=false
CODEC="h264"
NICE=0

# Default output directory when -o is omitted
MOVIES_DIR="${HOME}/Movies"

# Derive a clean title from the source path for auto-naming output
get_source_title() {
    local src="$1"
    if [[ -d "$src" ]]; then
        local dir="$src"
        dir="${dir%/}"                 # strip trailing slash
        [[ "$dir" == */BDMV ]] && dir="${dir%/BDMV}"
        dir="${dir%/}"                 # strip any remaining trailing slash
        local title="${dir##*/}"
        title="${title//:/ -}"
        printf '%s\n' "$title"
    elif [[ -f "$src" ]]; then
        local fname="${src##*/}"
        printf '%s\n' "${fname%.*}"
    else
        printf '%s\n' "output"
    fi
}

# Catppuccin Mocha true-color ANSI codes for TUI styling
CCTP_RESET=$'\e[0m'
CCTP_BLUE=$'\e[38;2;137;180;250m'       # #89b4fa accent
CCTP_GREEN=$'\e[38;2;166;227;161m'      # #a6e3a1
CCTP_YELLOW=$'\e[38;2;249;226;175m'     # #f9e2af
CCTP_RED=$'\e[38;2;243;139;168m'        # #f38ba8
CCTP_TEXT=$'\e[38;2;205;214;244m'       # #cdd6f4 foreground
CCTP_SUBTEXT1=$'\e[38;2;186;194;222m'   # #bac2de
CCTP_SUBTEXT0=$'\e[38;2;166;173;200m'   # #a6adc8
CCTP_OVERLAY1=$'\e[38;2;127;132;156m'   # #7f849c
CCTP_BOLD=$'\e[1m'

# ─── config / logging / source-root defaults ─────────────────────────────────
# Source root for TUI file browser persistence.
# Can be seeded via environment variable or saved to ~/.config/bd-shrink/source_root.
SOURCE_ROOT="${SOURCE_ROOT:-}"
CONFIG_DIR="${HOME}/.config/bd-shrink"
SOURCE_ROOT_FILE="${CONFIG_DIR}/source_root"
[[ -f "$SOURCE_ROOT_FILE" ]] && SOURCE_ROOT=$(<"$SOURCE_ROOT_FILE")

# Default log directory: /var/log/bd-shrink if writable without root,
# otherwise fall back to ~/.local/share/bd-shrink/logs.
if mkdir -p /var/log/bd-shrink 2>/dev/null && [[ -w /var/log/bd-shrink ]]; then
    LOG_DIR="/var/log/bd-shrink"
else
    LOG_DIR="${HOME}/.local/share/bd-shrink/logs"
    mkdir -p "$LOG_DIR"
fi
LOG_FILE="${LOG_DIR}/bd_shrink_$(date +%Y%m%d_%H%M%S).log"

# ─── helpers ─────────────────────────────────────────────────────────────────
die()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN:  $*" >&2; }
log()  { printf '[%02d:%02d:%02d] %s\n' $((SECONDS/3600)) $(((SECONDS%3600)/60)) $((SECONDS%60)) "$*"; }
info() { echo "       $*"; }

# Cleanup function for ISO mounts/extracts
cleanup_iso() {
    [[ -z "${ISO_MOUNT_POINT:-}" ]] && return 0
    [[ ! -e "$ISO_MOUNT_POINT" ]] && return 0
    
    # Try to unmount first (if mounted)
    if mountpoint -q "$ISO_MOUNT_POINT" 2>/dev/null; then
        umount "$ISO_MOUNT_POINT" 2>/dev/null || true
    fi
    
    # Clean up the mount/extract directory
    rm -rf "$ISO_MOUNT_POINT" 2>/dev/null || true
}

trap 'rc=$?; cleanup_iso; echo "FATAL: line $LINENO exit $rc" >&2' ERR

# Run ffmpeg via systemd-run --user --wait.
# The command runs as a transient systemd service, NOT as a child of bash.
# Even if the shell crashes, the service continues to completion.
# Use --pipe to forward output/error to caller's fds (for redirection/capture).
run_ff() {
     local nice_prop=()
     if [[ "${NICE:-0}" -gt 0 ]]; then
         nice_prop=(--property=Nice="$NICE")
     fi
     systemd-run --user --wait --pipe -q -u "bd_ff.${RANDOM}.$$" --setenv=PATH=/usr/local/bin:/usr/bin:/bin "${nice_prop[@]}" -- "$@"
}

usage() {
    cat <<EOF
bd_shrink.sh v${VERSION} — shrink BD50 → BD25 with menu preservation

Usage:  bd_shrink.sh [-s <source> -o <output>] [options]

When source/output are omitted and gum is available, an interactive TUI
is launched automatically. Pass --tui to force TUI mode even with args.

Required:
  -s, --source DIR|FILE   Source BDMV folder (must contain index.bdmv) or video file (.mkv/.mp4/.m4v)
  -o, --output DIR       Output directory (must not exist unless -f)

Options:
  -t, --target NUM       Target size in GB (default: 23 for BD25)
  --no-extras            Skip extras entirely (movie-only backup)
  --keep-one             Only keep the longest movie playlist
  --extras-scale WxH     Extras downscale resolution (default: 1280:720)
  --extras-ab BITRATE    Extras audio bitrate (default: 128k)
  --extras-crf NUM       Extras CRF value (default: 22)
  --main-preset NAME     x264 preset for main movie (default: slow)
  --main-audio BITRATE   Main movie audio re-encode bitrate (default: 640k)
  --commentary-ab BITRATE Commentary/secondary audio bitrate (default: 128k)
  --movie-only           Movie-only backup (no menus, no extras, fresh BD author)
  --iso                  Output ISO instead of BDMV folder (works with any mode)
  -f, --force            Overwrite output directory if it exists
  -n, --dry-run          Show what would be done without encoding
  -w, --work DIR         Working directory (default: <output>.work)
      --tui              Interactive TUI mode (requires gum)
      --install-deps     Show required tools and install commands, then exit
      --burn              Burn output to BD-R after validation
      --burn-device DEV   Optical drive device path (auto-detected if omitted)
      --burn-speed N      BD-R write speed multiplier (default: drive max)
      --clean-work        Remove working directory on success
      --codec CODEC       Video codec: h264 or hevc (default: h264)
      --nice [N]          Run encoder/rebuild at low CPU priority (default N=19)

Classification overrides (comma-separated, .mpls optional):
      --main-playlist LIST Force playlist(s) as the main movie (e.g., 00000.mpls)
      --extra LIST         Force playlist(s) as extras (re-encoded to 720p)
      --menu LIST          Force playlist(s) as menus (copied verbatim)
      --not-extra LIST     Exclude playlist(s) from extras (treat as menu)
  -h, --help             Show this help
EOF
    exit 1
}

# Interactive TUI mode using charmbracelet/gum
run_tui() {
    command -v gum &>/dev/null || die "gum is required for --tui mode (https://github.com/charmbracelet/gum)"

    local orig_keep_one=$KEEP_ONE

    while true; do
        clear

        gum style --border double --align center --width 60 --padding "1 2" \
            --border-foreground "#89b4fa" --foreground "#cdd6f4" \
            "bd_shrink" "" "Shrink BD50 → BD25"
        # ── Source selection ──
        if [[ -z "$SOURCE" ]]; then
            gum style --foreground "#89b4fa" --bold "Select source"
            gum style --foreground "#cdd6f4" \
                "Choose the movie folder that contains the BDMV directory or video/ISO file."
            gum confirm --default=true --prompt.foreground "#89b4fa" \
                --affirmative "Continue" --negative "Exit" \
                "Continue to source selection?" || exit 0

            # Retry until a valid source is selected
            while true; do
                local selected=""

                # Quick-pick filter only if SOURCE_ROOT has recognizable movie folders/files
                if [[ -n "$SOURCE_ROOT" ]] && [[ -d "$SOURCE_ROOT" ]]; then
                    declare -a candidates=()
                    for d in "$SOURCE_ROOT"/*/; do
                        [[ -d "$d" ]] || continue
                        d="${d%/}"
                        local base="${d##*/}"
                        if [[ -f "$d/index.bdmv" ]] || [[ -f "$d/BDMV/index.bdmv" ]]; then
                            candidates+=("$base")
                        elif ls "$d"/*.mkv "$d"/*.mp4 "$d"/*.m4v "$d"/*.m2ts "$d"/*.ts "$d"/*.iso &>/dev/null; then
                            candidates+=("$base")
                        fi
                    done
                    for f in "$SOURCE_ROOT"/*.mkv "$SOURCE_ROOT"/*.mp4 "$SOURCE_ROOT"/*.m4v "$SOURCE_ROOT"/*.m2ts "$SOURCE_ROOT"/*.ts "$SOURCE_ROOT"/*.iso; do
                        [[ -f "$f" ]] && candidates+=("${f##*/}")
                    done

                    if [[ ${#candidates[@]} -gt 0 ]]; then
                        gum style --foreground "#7f849c" "Looking in: ${SOURCE_ROOT}"
                        local choice=$(printf '%s\n' "${candidates[@]}" \
                            | gum filter --header="SELECT SOURCE" --placeholder "Search sources (esc = browse file system)..." || true)
                        [[ -n "$choice" ]] && selected="$SOURCE_ROOT/$choice"
                    fi
                fi

                # Fall back to the file browser.
                if [[ -z "$selected" ]]; then
                    local start_dir="$SOURCE_ROOT"
                    [[ -z "$start_dir" || ! -d "$start_dir" ]] && start_dir="/data-nvme1"
                    [[ -z "$start_dir" || ! -d "$start_dir" ]] && start_dir="${HOME}"
                    selected=$(gum file --directory --cursor="▸ " "$start_dir" \
                        --header="SELECT SOURCE — ↑↓ move, → enter dir, ← go up, enter select") || exit 1
                fi

                # Detect the actual source inside the selected movie folder.
                local movie_folder=""
                if [[ -f "$selected/index.bdmv" ]]; then
                    SOURCE="$selected"
                    movie_folder="$(dirname "$selected")"
                elif [[ -f "$selected/BDMV/index.bdmv" ]]; then
                    SOURCE="$selected/BDMV"
                    movie_folder="$selected"
                elif [[ -f "$selected" ]]; then
                    SOURCE="$selected"
                    movie_folder="$(dirname "$selected")"
                    MOVIE_ONLY=true
                else
                    declare -a found_bdmv
                    found_bdmv=("$selected"/*/BDMV)
                    if [[ -n "${found_bdmv[0]:-}" ]] && [[ -f "${found_bdmv[0]}/index.bdmv" ]]; then
                        SOURCE="${found_bdmv[0]}"
                        movie_folder="$(dirname "${found_bdmv[0]}")"
                    fi
                fi

                # If no BDMV, look for a video/ISO file directly inside the selected folder.
                if [[ -z "$SOURCE" ]]; then
                    local videos=("$selected"/*.mkv "$selected"/*.mp4 "$selected"/*.m4v "$selected"/*.m2ts "$selected"/*.ts "$selected"/*.iso)
                    if [[ -n "${videos[0]:-}" ]]; then
                        SOURCE="${videos[0]}"
                        movie_folder="$selected"
                        MOVIE_ONLY=true
                    fi
                fi

                 if [[ -n "$SOURCE" ]]; then
                     # Validate that video/ISO files have supported extensions
                     if [[ -f "$SOURCE" ]] && ! [[ "${SOURCE,,}" =~ \.(mkv|mp4|m4v|iso)$ ]]; then
                         warn "Unsupported file format: ${SOURCE##*/}"
                         warn "Supported: .mkv, .mp4, .m4v, .iso (or BDMV folders)"
                         SOURCE=""  # reset so we loop and retry
                     else
                         break  # valid source found
                     fi
                 fi

                warn "No BDMV or video file found directly under: $selected"
                warn "Browse deeper and select the folder containing BDMV/ or a video file."
                SOURCE_ROOT="$selected"  # next browser starts here
            done

            # Remember the parent of the movie folder as SOURCE_ROOT.
            if [[ -n "$movie_folder" ]]; then
                SOURCE_ROOT="$(dirname "$movie_folder")"
                mkdir -p "$CONFIG_DIR"
                printf '%s\n' "$SOURCE_ROOT" > "$SOURCE_ROOT_FILE"
            fi
        fi

        # ── Output ──
        if [[ -z "$OUTPUT" ]]; then
            local source_title=$(get_source_title "$SOURCE")
            local default_out="${MOVIES_DIR}/${source_title}"
            local out_choice=$(printf '%s\n' \
                    "Use default: $default_out" \
                    "Browse for folder..." \
                    "Type custom path..." \
                | gum choose --limit=1 --height=3 --header="SELECT OUTPUT" || true)
            case "$out_choice" in
                *Browse*)
                    local start_dir="$MOVIES_DIR"
                    [[ -d "$start_dir" ]] || start_dir="${HOME}"
                    OUTPUT=$(gum file --directory --cursor="▸ " "$start_dir" \
                        --header="SELECT OUTPUT FOLDER — ↑↓ move, → enter dir, ← go up, enter select") || OUTPUT="$default_out"
                    ;;
                *Type*)
                    OUTPUT=$(gum input --placeholder "$default_out" --prompt "Output: ") || OUTPUT="$default_out"
                    ;;
                *)
                    OUTPUT="$default_out"
                    ;;
            esac
            [[ -z "$OUTPUT" ]] && OUTPUT="$default_out"
        fi

        # ── Mode (Full disc vs Movie-only) ──
        local mode_default="Full disc (keep menus, extras)"
        $MOVIE_ONLY && mode_default="Movie-only (no menus, fresh BD)"

        local mode_choice=$(printf '%s\n' \
                "Full disc (keep menus, extras)" \
                "Movie-only (no menus, fresh BD)" \
            | gum choose --limit=1 --height=2 \
                --header="SELECT MODE" --selected="$mode_default" || true)

        if [[ "$mode_choice" == *Movie-only* ]]; then
            MOVIE_ONLY=true
            KEEP_ONE=true
        elif [[ "$mode_choice" == *Full* ]]; then
            MOVIE_ONLY=false
            KEEP_ONE=$orig_keep_one
        fi

        # ── Output format (Folder vs ISO) ──
        local iso_option_default="Folder (BDMV)"
        $OUTPUT_ISO && iso_option_default="ISO (.iso file)"

        local iso_option_choice=$(printf '%s\n' \
                "Folder (BDMV)" \
                "ISO (.iso file)" \
            | gum choose --limit=1 --height=2 \
                --header="OUTPUT FORMAT" --selected="$iso_option_default" || true)

        if [[ "$iso_option_choice" == *ISO* ]]; then
            OUTPUT_ISO=true
        elif [[ "$iso_option_choice" == *Folder* ]]; then
            OUTPUT_ISO=false
        fi

        # ── Codec ──
        local codec_default="h264 (AVC, BD-compatible)"
        [[ "$CODEC" == "hevc" ]] && codec_default="hevc (x265, slower)"

        local codec_choice=$(printf '%s\n' \
                "h264 (AVC, BD-compatible)" \
                "hevc (x265, slower)" \
            | gum choose --limit=1 --height=2 \
                --header="VIDEO CODEC" --selected="$codec_default" || true)

        if [[ "$codec_choice" == *hevc* ]]; then
            CODEC="hevc"
        elif [[ "$codec_choice" == *h264* ]]; then
            CODEC="h264"
        fi

        # ── Encoding options ──
        local preset_default="$MAIN_PRESET"

        local preset_choice=$(printf '%s\n' \
                "slow" "medium" "fast" "slower" "veryslow" \
            | gum choose --limit=1 --height=5 \
                --header="ENCODING PRESET" --selected="$preset_default" || true)
        if [[ -n "$preset_choice" ]]; then
            MAIN_PRESET="$preset_choice"
        fi

        # ── Additional options ──
        local opt_labels=() opt_selected=()
        if [[ -d "$OUTPUT" ]]; then
            opt_labels+=("Overwrite existing output")
            $FORCE && opt_selected+=("Overwrite existing output")
        fi

        if [[ ${#opt_labels[@]} -gt 0 ]]; then
            local selected_str=$(IFS=,; echo "${opt_selected[*]}")
            local choose_flags=(--no-limit --height=${#opt_labels[@]} --header="SELECT OPTIONS")
            [[ -n "$selected_str" ]] && choose_flags+=(--selected="$selected_str")
            local chosen=$(printf '%s\n' "${opt_labels[@]}" \
                | gum choose "${choose_flags[@]}" || true)

            if [[ -n "$chosen" ]]; then
                FORCE=false
                if [[ "$chosen" == *Overwrite* ]]; then FORCE=true; fi
            fi
        fi

        # ── Summary ──
        local source_size
        if [[ -d "$SOURCE" ]]; then
            source_size=$(du -sh "$SOURCE" 2>/dev/null | cut -f1)
        elif [[ -f "$SOURCE" ]]; then
            source_size=$(du -h "$SOURCE" 2>/dev/null | cut -f1)
        else
            source_size="unknown"
        fi

        local c_movie="${CCTP_RED}"
        $MOVIE_ONLY && c_movie="${CCTP_GREEN}"
        local iso_label="Folder"
        $OUTPUT_ISO && iso_label="ISO"

        gum style --border rounded --padding "1 2" --width 64 \
            --margin "1 0" --border-foreground "#89b4fa" \
            "${CCTP_BLUE}${CCTP_BOLD}  ▶ Ready to start${CCTP_RESET}" \
            "" \
            "${CCTP_SUBTEXT1}  Source:${CCTP_RESET}      ${CCTP_TEXT}${SOURCE}${CCTP_RESET}" \
            "${CCTP_SUBTEXT1}  Source size:${CCTP_RESET} ${CCTP_TEXT}${source_size}${CCTP_RESET}" \
            "${CCTP_SUBTEXT1}  Output:${CCTP_RESET}      ${CCTP_TEXT}${OUTPUT}${CCTP_RESET}" \
            "${CCTP_SUBTEXT1}  Movie-only:${CCTP_RESET}  ${c_movie}${MOVIE_ONLY}${CCTP_RESET}" \
            "${CCTP_SUBTEXT1}  Format:${CCTP_RESET}      ${CCTP_TEXT}${iso_label}${CCTP_RESET}" \
            "${CCTP_SUBTEXT1}  Preset:${CCTP_RESET}      ${CCTP_TEXT}${MAIN_PRESET}${CCTP_RESET}"

        local action=$(gum choose --height=5 \
            --header="SELECT ACTION" \
            "Start" "Edit source" "Edit output" "Edit options" "Cancel" || true)
        case "$action" in
            Start) break ;;
            "Edit source") SOURCE=""; continue ;;
            "Edit output") OUTPUT=""; continue ;;
            "Edit options") continue ;;
            *) exit 0 ;;
        esac
    done
}

show_install_deps() {
    # Detect required and optional tools, print install commands for missing ones.
    # Does NOT auto-install anything — just tells the user what to run.
    local found=0 missing=0

    echo "bd_shrink.sh — dependency check"
    echo ""

    # ── Required tools ──────────────────────────────────────────────────────
    echo "Required tools:"
    echo ""

    # ffmpeg / ffprobe (usually from the same package)
    local ffmpeg_ok=true
    command -v ffmpeg &>/dev/null || ffmpeg_ok=false
    command -v ffprobe &>/dev/null || ffmpeg_ok=false
    if $ffmpeg_ok; then
        echo "  ✓ ffmpeg + ffprobe"
        ((++found))
    else
        echo "  ✗ ffmpeg / ffprobe"
        echo "    sudo dnf install ffmpeg"
        echo "    Note: ffmpeg requires the rpmfusion-free repo."
        echo "    Enable it: sudo dnf install rpmfusion-free-release"
        ((++missing))
    fi

    # tsMuxeR (GitHub binary only)
    if command -v tsMuxeR &>/dev/null; then
        echo "  ✓ tsMuxeR"
        ((++found))
    else
        echo "  ✗ tsMuxeR"
        echo "    # Download and install (GitHub binary, no dnf package):"
        echo "    wget https://github.com/justdan96/tsMuxer/releases/download/2.7.0/tsMuxer-2.7.0-linux.zip"
        echo "    unzip tsMuxer-2.7.0-linux.zip"
        echo "    sudo cp tsMuxer/tsMuxeR /usr/local/bin/"
        ((++missing))
    fi

    # bc
    if command -v bc &>/dev/null; then
        echo "  ✓ bc"
        ((++found))
    else
        echo "  ✗ bc"
        echo "    sudo dnf install bc"
        ((++missing))
    fi

    # python3 + stdlib modules
    if command -v python3 &>/dev/null && python3 -c "import json, struct, os, sys" 2>/dev/null; then
        echo "  ✓ python3 (with json, struct, os, sys)"
        ((++found))
    else
        echo "  ✗ python3"
        echo "    sudo dnf install python3"
        ((++missing))
    fi

    # systemd-run (should always be present)
    if command -v systemd-run &>/dev/null; then
        echo "  ✓ systemd-run"
        ((++found))
    else
        echo "  ✗ systemd-run"
        echo "    sudo dnf install systemd"
        ((++missing))
    fi

    echo ""
    echo "Optional tools:"
    echo ""

    # gum (TUI mode)
    if command -v gum &>/dev/null; then
        echo "  ✓ gum  (TUI mode)"
    else
        echo "  ✗ gum  (TUI mode) — sudo dnf install gum"
    fi

    # genisoimage (--iso output, --burn UDF filesystem)
    if command -v genisoimage &>/dev/null; then
        echo "  ✓ genisoimage  (--iso output, --burn UDF)"
    else
        echo "  ✗ genisoimage  (--iso output, --burn) — sudo dnf install genisoimage"
    fi

    # xorriso (--iso output fallback, no UDF)
    if command -v xorriso &>/dev/null; then
        echo "  ✓ xorriso  (--iso output, fallback)"
    else
        echo "  ✗ xorriso  (--iso output, fallback) — sudo dnf install xorriso"
    fi

    # growisofs (--burn, preferred for UDF bridge)
    if command -v growisofs &>/dev/null; then
        echo "  ✓ growisofs  (--burn, from dvd+rw-tools)"
    else
        echo "  ✗ growisofs  (--burn) — sudo dnf install dvd+rw-tools"
    fi

    # eject (--burn, disc ejection after verification)
    if command -v eject &>/dev/null; then
        echo "  ✓ eject  (--burn)"
    else
        echo "  ✗ eject  (--burn) — part of util-linux (usually pre-installed on Linux; not needed on macOS)"
    fi

    # Playback tools (vlc / mpv + libbluray)
    if command -v vlc &>/dev/null || command -v mpv &>/dev/null; then
        echo "  ✓ vlc / mpv  (playback)"
    else
        echo "  - vlc / mpv  (playback) — sudo dnf install vlc (or mpv)"
    fi

    echo ""
    echo "Summary: ${found} of $((found + missing)) required tools found."
    if [[ ${missing} -gt 0 ]]; then
        echo "Install the missing tools using the commands above, then re-run this script."
    fi

    exit 0
}

check_deps() {
    local missing=()
    for cmd in ffmpeg ffprobe tsMuxeR bc python3; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Missing required tools: ${missing[*]}"
    fi
    if ! python3 -c "import json, struct, os, sys" 2>/dev/null; then
        die "Python3 missing standard library modules"
    fi
}

burn_output() {
    # Burn BDMV to BD-R. Two modes:
    #   --iso: burn from pre-created ISO file (for archival + burn)
    #   no --iso: pipe genisoimage -udf directly to growisofs (no temp ISO)

    local burn_dev="$BURN_DEVICE"

    # ── Auto-detect burn device if not specified ────────────────────────────
    if [[ -z "$burn_dev" ]]; then
        for dev in /dev/sr*; do
            if [[ -b "$dev" ]]; then
                burn_dev="$dev"
                break
            fi
        done
        if [[ -z "$burn_dev" ]]; then
            die "No optical drive found. Use --burn-device to specify one (e.g., /dev/sr0)"
        fi
    fi

    # ── Validate burn device ─────────────────────────────────────────────────
    if [[ ! -b "$burn_dev" ]]; then
        die "Burn device not found or not a block device: $burn_dev"
    fi

    # ── Burn from ISO (--iso mode) or direct pipe ───────────────────────────
    if $OUTPUT_ISO; then
        # Burn from pre-created ISO file
        if [[ ! -f "$ISO_OUT" ]]; then
            die "No ISO file found for burning (expected: $ISO_OUT)"
        fi
        log "Burning ISO to $burn_dev..."
        local growisofs_bin
        growisofs_bin="$(command -v growisofs 2>/dev/null || true)"
        if [[ -n "$growisofs_bin" ]]; then
            local burn_args=(-dvd-compat)
            [[ -n "$BURN_SPEED" ]] && burn_args+=(-speed "$BURN_SPEED")
            run_ff "$growisofs_bin" "${burn_args[@]}" -Z "${burn_dev}=${ISO_OUT}" || {
                die "Burn failed with growisofs"
            }
        else
            warn "growisofs not found — falling back to xorriso cdrecord (no UDF bridge)."
            warn "Some standalone BD players may not read this disc."
            run_ff xorriso -as cdrecord -v -sao dev="$burn_dev" "$ISO_OUT" || {
                die "Burn failed with xorriso"
            }
        fi
    else
        # Direct pipe: genisoimage -udf → growisofs, no temp ISO
        local growisofs_bin genisoimage_bin
        growisofs_bin="$(command -v growisofs 2>/dev/null || true)"
        genisoimage_bin="$(command -v genisoimage 2>/dev/null || true)"
        if [[ -z "$growisofs_bin" ]]; then
            die "growisofs is required for --burn (install: sudo dnf install dvd+rw-tools)"
        fi
        if [[ -z "$genisoimage_bin" ]]; then
            die "genisoimage is required for --burn (install: sudo dnf install genisoimage)"
        fi
        log "Piping to $burn_dev via growisofs (no temp ISO)..."
        # Only stream BDMV/CERTIFICATE to the drive, not .work or other siblings
        local direct_burn_args=(-dvd-compat)
        [[ -n "$BURN_SPEED" ]] && direct_burn_args+=(-speed "$BURN_SPEED")
        run_ff env MKISOFS="$genisoimage_bin" "$growisofs_bin" "${direct_burn_args[@]}" -Z "$burn_dev" -udf -allow-limited-size -V "$ISO_LABEL" \
            -graft-points BDMV="$DST/BDMV" CERTIFICATE="$DST/CERTIFICATE" || {
            die "Burn failed with growisofs"
        }
    fi

    # ── Eject ────────────────────────────────────────────────────────────────
    log "Ejecting $burn_dev..."
    eject "$burn_dev" 2>/dev/null || true
}

# ─── arg parsing ─────────────────────────────────────────────────────────────
SOURCE=""
OUTPUT=""
WORK_DIR=""
MAIN_AUDIO_BITRATE="640k"
NO_EXTRAS=false
OVERRIDE_MAIN_PLAYLISTS=""  # CSV list of playlist names to force as main
OVERRIDE_EXTRAS=""           # CSV list of playlist names to force as extras
OVERRIDE_NOT_EXTRAS=""       # CSV list of playlist names to exclude from extras
OVERRIDE_MENUS=""            # CSV list of playlist names to force as menus

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--source)       SOURCE="$2"; shift 2 ;;
        -o|--output)       OUTPUT="$2"; shift 2 ;;
        -t|--target)       TARGET_GB="$2"; shift 2 ;;
        --no-extras)       NO_EXTRAS=true; shift ;;
        --keep-one)        KEEP_ONE=true; shift ;;
        --main-playlist)   OVERRIDE_MAIN_PLAYLISTS="$2"; shift 2 ;;
        --extra)           OVERRIDE_EXTRAS="$2"; shift 2 ;;
        --not-extra)       OVERRIDE_NOT_EXTRAS="$2"; shift 2 ;;
        --menu)            OVERRIDE_MENUS="$2"; shift 2 ;;
        --extras-scale)    EXTRAS_SCALE="$2"; shift 2 ;;
        --extras-ab)       EXTRAS_AUDIO_BITRATE="$2"; shift 2 ;;
        --extras-crf)      EXTRAS_CRF="$2"; shift 2 ;;
        --main-preset)     MAIN_PRESET="$2"; shift 2 ;;
        --main-audio)      MAIN_AUDIO_BITRATE="$2"; shift 2 ;;
        --commentary-ab)   COMMENTARY_AUDIO_BITRATE="$2"; shift 2 ;;
        --movie-only)      MOVIE_ONLY=true; shift ;;
        --iso)             OUTPUT_ISO=true; shift ;;
        -f|--force)        FORCE=true; shift ;;
        -n|--dry-run)      DRY_RUN=true; shift ;;
        -w|--work)         WORK_DIR="$2"; shift 2 ;;
        --tui)             USE_TUI=true; shift ;;
        --install-deps)    INSTALL_DEPS=true; shift ;;
        --burn)            BURN=true; shift ;;
        --burn-device)     BURN_DEVICE="$2"; shift 2 ;;
        --burn-speed)      BURN_SPEED="$2"; shift 2 ;;
        --clean-work)      CLEAN_WORK=true; shift ;;
        --codec)           CODEC="$2"; shift 2 ;;
        --nice)
            if [[ -n "${2:-}" && ! "$2" =~ ^- ]]; then
                NICE="$2"; shift 2
            else
                NICE=19; shift
            fi
            ;;
        -h|--help)         usage ;;
        *)                 die "Unknown option: $1" ;;
    esac
done

# Validate codec
[[ "$CODEC" =~ ^(h264|hevc)$ ]] || die "Invalid --codec $CODEC — use h264 or hevc"

# Validate target GB
[[ "$TARGET_GB" =~ ^[0-9]+$ ]] || die "Invalid --target $TARGET_GB — must be a positive integer (GB)"

# Validate audio bitrate arguments
[[ "$MAIN_AUDIO_BITRATE" =~ ^[0-9]+k?$ ]] || die "Invalid --main-audio $MAIN_AUDIO_BITRATE — use format: 640k or 640"
[[ "$EXTRAS_AUDIO_BITRATE" =~ ^[0-9]+k?$ ]] || die "Invalid --extras-ab $EXTRAS_AUDIO_BITRATE — use format: 128k or 128"
[[ "$COMMENTARY_AUDIO_BITRATE" =~ ^[0-9]+k?$ ]] || die "Invalid --commentary-ab $COMMENTARY_AUDIO_BITRATE — use format: 128k or 128"

# Validate nice value
[[ "$NICE" =~ ^[0-9]+$ ]] || die "Invalid --nice value $NICE — must be a non-negative integer"
[[ "$NICE" -le 19 ]] || die "Invalid --nice value $NICE — must be 0-19"

# Validate override playlist names (comma-separated 5-digit hex names, .mpls optional)
for override_list in "$OVERRIDE_MAIN_PLAYLISTS" "$OVERRIDE_EXTRAS" "$OVERRIDE_NOT_EXTRAS" "$OVERRIDE_MENUS"; do
    if [[ -n "$override_list" ]]; then
        for pl_name in $(echo "$override_list" | tr ',' '\n'); do
            [[ "$pl_name" =~ ^[0-9A-Fa-f]{5}(\.mpls)?$ ]] || die "Invalid playlist name: $pl_name — must be 5 hex digits, optionally with .mpls (e.g., 00000 or 00000.mpls)"
        done
    fi
done

# Validate burn speed
if [[ -n "$BURN_SPEED" ]]; then
    [[ "$BURN_SPEED" =~ ^[0-9]+$ ]] || die "Invalid --burn-speed $BURN_SPEED — must be a positive integer"
fi

# --install-deps: show dependency info and exit (no source/output required)
if $INSTALL_DEPS; then
    show_install_deps
fi

# Launch interactive TUI if requested, or if required args are missing and we
# have an interactive terminal with gum available.
if $USE_TUI || { [[ -z "$SOURCE" || -z "$OUTPUT" ]] && [[ -t 1 ]] && command -v gum &>/dev/null; }; then
    USE_TUI=true
    run_tui
fi

# Derive codec-specific variables (after TUI, which may have changed CODEC)
if [[ "$CODEC" == "hevc" ]]; then
    VIDEO_EXT="hevc"
    MUX_VIDEO_TYPE="V_MPEGH/ISO/HEVC"
else
    VIDEO_EXT="h264"
    MUX_VIDEO_TYPE="V_MPEG4/ISO/AVC"
fi

# Auto-derive output path from source if not provided
if [[ -z "$OUTPUT" ]]; then
    source_title=$(get_source_title "$SOURCE")
    OUTPUT="${MOVIES_DIR}/${source_title}"
    log "Output not specified — defaulting to ${OUTPUT}"
fi

# --movie-only implies --keep-one (only the first main playlist is encoded)
if $MOVIE_ONLY; then
    KEEP_ONE=true
fi

[[ -z "$SOURCE" ]] && die "Source folder required (-s)"

MKV_INPUT=false
ISO_INPUT=false
ISO_MOUNT_POINT=""

# Check for ISO input file
if [[ -f "$SOURCE" ]] && [[ "${SOURCE,,}" =~ \.iso$ ]]; then
    ISO_INPUT=true
    # Create a temporary mount point for the ISO
    ISO_MOUNT_POINT=$(mktemp -d "/tmp/bd_shrink_iso.XXXXXX")
    
    # Try to mount the ISO (read-only)
    if ! mount -o loop,ro "$SOURCE" "$ISO_MOUNT_POINT" 2>/dev/null; then
        # Fallback: try extracting with bsdtar or 7z
        if command -v bsdtar &>/dev/null; then
            log "Extracting ISO with bsdtar..."
            bsdtar -xf "$SOURCE" -C "$ISO_MOUNT_POINT" 2>/dev/null || die "Failed to extract ISO file"
        elif command -v 7z &>/dev/null; then
            log "Extracting ISO with 7z..."
            7z x "$SOURCE" -o"$ISO_MOUNT_POINT" -y >/dev/null 2>&1 || die "Failed to extract ISO file"
        else
            rm -rf "$ISO_MOUNT_POINT"
            die "Cannot mount or extract ISO. Install bsdtar or 7z, or use --mount to pre-mount the ISO"
        fi
    fi
    
    # Find BDMV folder inside extracted/mounted ISO
    if [[ -d "$ISO_MOUNT_POINT/BDMV" ]]; then
        SOURCE="$ISO_MOUNT_POINT/BDMV"
        log "ISO extracted/mounted: $SOURCE"
    elif [[ -d "$ISO_MOUNT_POINT/VIDEO_TS" ]]; then
        die "DVD structure detected (VIDEO_TS), not Blu-ray (BDMV). This tool only handles BD discs."
    else
        rm -rf "$ISO_MOUNT_POINT"
        die "No BDMV folder found inside ISO file"
    fi
elif [[ -f "$SOURCE" ]] && [[ "${SOURCE,,}" =~ \.(mkv|mp4|m4v)$ ]]; then
    MKV_INPUT=true
    MOVIE_ONLY=true
fi

# Auto-detect BDMV inside a parent directory, or video file inside a directory
if [[ -d "$SOURCE" ]] && [[ ! -f "$SOURCE/index.bdmv" ]] && ! $MKV_INPUT; then
    if [[ -f "$SOURCE/BDMV/index.bdmv" ]]; then
        SOURCE="$SOURCE/BDMV"
        log "Auto-detected BDMV at: $SOURCE"
    else
        detected=false
        for bdmv_candidate in "$SOURCE"/*/BDMV/index.bdmv; do
            [[ -f "$bdmv_candidate" ]] || continue
            SOURCE="${bdmv_candidate%/index.bdmv}"
            log "Auto-detected BDMV at: $SOURCE"
            detected=true
            break
        done
        if ! $detected; then
            for vid in "$SOURCE"/*.mkv "$SOURCE"/*.mp4 "$SOURCE"/*.m4v; do
                [[ -f "$vid" ]] || continue
                SOURCE="$vid"
                MKV_INPUT=true
                log "Auto-detected video file: $SOURCE"
                break
            done
        fi
    fi
fi

if [[ ! -d "$SOURCE" ]] && ! $MKV_INPUT && ! $ISO_INPUT; then
    die "Source must be a BDMV folder (with index.bdmv), video file (.mkv/.mp4/.m4v), or ISO image (.iso)"
fi

if ! $MKV_INPUT; then
    [[ ! -f "$SOURCE/index.bdmv" ]] && die "Source must contain index.bdmv (point to the BDMV folder)"
    [[ ! -d "$SOURCE/PLAYLIST" ]] && die "Source must contain PLAYLIST/ directory"
    [[ ! -d "$SOURCE/STREAM" ]] && die "Source must contain STREAM/ directory"
fi

# If OUTPUT points to an existing parent directory (not already a BD output),
# create/use a source-named subdirectory so the actual output is self-contained
# and the work directory remains a sibling in the output root.
if [[ -d "$OUTPUT" ]] && [[ ! -d "$OUTPUT/BDMV" ]]; then
    source_title=$(get_source_title "$SOURCE")
    output_candidate="${OUTPUT%/}/${source_title}"
    if [[ -d "$output_candidate" ]]; then
        OUTPUT="$output_candidate"
        log "Output directory is a parent folder — using existing ${OUTPUT}"
    elif $FORCE; then
        mkdir -p "$output_candidate" || die "Cannot create output subdirectory: ${output_candidate}"
        OUTPUT="$output_candidate"
        log "Output directory is a parent folder — creating ${OUTPUT}"
    fi
fi

if [[ -d "$OUTPUT" ]] && ! $FORCE; then
    die "Output directory exists. Use -f to overwrite."
fi

check_deps

if [[ -z "$WORK_DIR" ]]; then
    WORK_DIR="${OUTPUT}.work"
fi
mkdir -p "$WORK_DIR"

# Start logging to file while still printing to terminal
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE" "$WORK_DIR/bd_shrink.log") 2>&1
log "Logging to $LOG_FILE"

# Detect BD-J
HAS_BDJ=false
if ! $MOVIE_ONLY && ! $MKV_INPUT && [[ -d "$SOURCE/BDJO" || -d "$SOURCE/JAR" ]]; then
    HAS_BDJ=true
    warn "BD-J (Java) menus detected. Menu preservation may fail on this disc."
    warn "Proceeding anyway, but test the output carefully in a software player first."
fi

# ─── Phase 1: Inventory ──────────────────────────────────────────────────────

log "Phase 1: Scanning BD structure..."

parse_mpls() {
    python3 - "$1" << 'PYEOF'
import struct, sys, os, json

def parse_mpls(path):
    with open(path, 'rb') as f:
        data = f.read()
    if len(data) < 20 or data[:4] != b'MPLS':
        return None

    pl_offset = struct.unpack_from('>I', data, 8)[0]
    pm_offset = struct.unpack_from('>I', data, 12)[0]
    num_playitems = struct.unpack_from('>H', data, pl_offset + 6)[0]
    num_subpaths = struct.unpack_from('>H', data, pl_offset + 8)[0]
    num_marks = struct.unpack_from('>H', data, pm_offset + 6)[0]

    items = []
    off = pl_offset + 10
    for _ in range(num_playitems):
        plen = struct.unpack_from('>H', data, off)[0]
        clip = data[off+2:off+7].decode('ascii')
        codec = data[off+7:off+11].decode('ascii')
        in_time = struct.unpack_from('>I', data, off + 14)[0]
        out_time = struct.unpack_from('>I', data, off + 18)[0]
        items.append({
            'clip': clip,
            'codec': codec,
            'in_time': in_time,
            'out_time': out_time,
            'duration': (out_time - in_time) / 45000.0,
        })
        off += 2 + plen

    # Parse SubPaths: extract SubPlayItem clips, then advance to AppInfoPlayList.
    # BDMV spec: SubPath length is a 4-byte uint (not 2-byte as previously coded).
    # SubPath layout:
    #   length(4) | type(1) | repeat(1) | reserved(2) | num_sub_playitems(2) | SubPlayItems...
    # SubPlayItem layout:
    #   length(2) | clip_id(5) | codec(4) | flags... | IN_time(4) | OUT_time(4) | ...
    subpath_items = []
    for _ in range(num_subpaths):
        splen = struct.unpack_from('>I', data, off)[0]
        sub_end = off + 4 + splen
        sub_off = off + 4
        if sub_off + 6 <= sub_end and sub_off + 6 <= len(data):
            num_spi = struct.unpack_from('>H', data, sub_off + 4)[0]
            spi_off = sub_off + 6
            for _ in range(num_spi):
                if spi_off + 11 > sub_end or spi_off + 11 > len(data):
                    break
                spi_len = struct.unpack_from('>H', data, spi_off)[0]
                clip = data[spi_off+2:spi_off+7].decode('ascii', errors='replace')
                codec = data[spi_off+7:spi_off+11].decode('ascii', errors='replace')
                subpath_items.append({
                    'clip': clip,
                    'codec': codec,
                    'in_time': 0,
                    'out_time': 0,
                    'duration': 0,
                })
                spi_off += 2 + spi_len
        off = sub_end

    items.extend(subpath_items)

    # Read PlayList_type from AppInfoPlayList (1 = menu/interactive)
    # Bounds-check appinfo_len to avoid garbage playlist_type from misaligned offset
    playlist_type = 0
    if off + 4 < len(data):
        try:
            appinfo_len = struct.unpack_from('>I', data, off)[0]
            # AppInfoPlayList must be reasonable size (at least 5 bytes)
            if appinfo_len >= 5 and appinfo_len < 10000 and off + 4 + appinfo_len <= len(data):
                playlist_type = data[off + 4 + 1]  # offset 4 (length) + 1 byte reserved = playlist_type
        except (struct.error, IndexError):
            pass

    marks = []
    if num_marks > 0:
        moff = pm_offset + 8
        for _ in range(num_marks):
            mark_type = data[moff + 1] if moff + 1 < len(data) else 0
            ref_to_playitem = struct.unpack_from('>H', data, moff + 2)[0] if moff + 3 < len(data) else 0
            mark_time = struct.unpack_from('>I', data, moff + 4)[0] if moff + 7 < len(data) else 0
            marks.append({
                'type': mark_type,
                'time': mark_time / 45000.0,
                'playitem': ref_to_playitem,
            })
            moff += 14

    chapters = [m for m in marks if m['type'] == 1]
    chapter_times = sorted([m['time'] for m in chapters])

    return {
        'playitems': items,
        'subpaths': num_subpaths,
        'duration': sum(i['duration'] for i in items),
        'chapters': len(chapters),
        'chapter_times': [round(t, 2) for t in chapter_times],
        'playlist_type': playlist_type,
    }

playlist_dir = sys.argv[1]
results = {}
for fname in sorted(os.listdir(playlist_dir)):
    if fname.endswith('.mpls'):
        fpath = os.path.join(playlist_dir, fname)
        parsed = parse_mpls(fpath)
        if parsed:
            results[fname] = parsed

print(json.dumps(results, indent=2))
PYEOF
}

INVENTORY_FILE="$WORK_DIR/inventory.json"

if $MKV_INPUT; then
    # Single-file video: create synthetic playlists.json with single clip "00000"
    python3 - "$SOURCE" << 'PYEOF' > "$WORK_DIR/playlists.json"
import json, subprocess, sys
# Get single-file video duration via ffprobe
source = sys.argv[1]
r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', source], capture_output=True, text=True, timeout=30)
dur = float(r.stdout.strip()) if r.stdout.strip() else 0
data = {
    '00000.mpls': {
        'playitems': [{'clip': '00000', 'codec': 'H.264', 'in_time': 0, 'out_time': int(dur * 45000), 'duration': dur}],
        'subpaths': 0,
        'duration': dur,
        'chapters': 1,
        'chapter_times': [0],
    }
}
print(json.dumps(data, indent=2))
PYEOF
else
    parse_mpls "$SOURCE/PLAYLIST" > "$WORK_DIR/playlists.json"
fi

# Probe all unique clips
all_clips=$(python3 - "$WORK_DIR" << 'PYEOF'
import json, os, sys
with open(os.path.join(sys.argv[1], 'playlists.json'), encoding='utf-8') as f:
    data = json.load(f)
clips = set()
for pl in data.values():
    for item in pl['playitems']:
        clips.add(item['clip'])
for c in sorted(clips):
    print(c)
PYEOF
)

clip_count=$(echo "$all_clips" | wc -l)
[[ -z "$all_clips" ]] && clip_count=0
log "Found $clip_count unique M2TS clips"

# Convert bash booleans to Python-safe strings
if $KEEP_ONE; then PY_KEEP_ONE="True"; else PY_KEEP_ONE="False"; fi
if $MOVIE_ONLY; then PY_MOVIE_ONLY="True"; else PY_MOVIE_ONLY="False"; fi
if $NO_EXTRAS; then PY_NO_EXTRAS="True"; else PY_NO_EXTRAS="False"; fi
if $FORCE; then PY_FORCE="True"; else PY_FORCE="False"; fi

CLIPS_DIR="$WORK_DIR/clips"
mkdir -p "$CLIPS_DIR"

if $MKV_INPUT; then
    # Single-file video: ffprobe the source file directly as clip "00000"
    python3 - "$SOURCE" "$CLIPS_DIR" << 'PYEOF'
import json, os, subprocess, sys
source = sys.argv[1]
clips_dir = sys.argv[2]
r = subprocess.run(['ffprobe', '-v', 'error',
    '-show_entries', 'stream=index,codec_type,codec_name,width,height,duration,r_frame_rate,channels,channel_layout,sample_rate,bit_rate',
    '-show_entries', 'format=size,duration,bit_rate',
    '-of', 'json', source],
    capture_output=True, text=True, timeout=60)
data = json.loads(r.stdout) if r.stdout else {'streams': [], 'format': {}}
with open(os.path.join(clips_dir, '00000.json'), 'w', encoding='utf-8') as f:
    json.dump(data, f)
PYEOF
else
    python3 - "$SOURCE" "$CLIPS_DIR" << 'PYEOF'
import json, os, subprocess, sys

source = sys.argv[1]
clips_dir = sys.argv[2]

# Read playlists to get clip IDs
playlists = json.load(open(os.path.join(os.path.dirname(clips_dir), 'playlists.json'), encoding='utf-8'))
all_clips = set()
for pl in playlists.values():
    for item in pl['playitems']:
        all_clips.add(item['clip'])

for clip in sorted(all_clips):
    clip_path = os.path.join(source, 'STREAM', f'{clip}.m2ts')
    out_path = os.path.join(clips_dir, f'{clip}.json')
    if not os.path.exists(clip_path):
        continue
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error',
             '-show_entries', 'stream=index,codec_type,codec_name,width,height,duration,r_frame_rate,channels,channel_layout,sample_rate,bit_rate',
             '-show_entries', 'format=size,duration,bit_rate',
             '-of', 'json', clip_path],
            capture_output=True, text=True, timeout=60)
        data = json.loads(r.stdout) if r.stdout else {'streams': [], 'format': {}}
    except Exception:
        data = {'streams': [], 'format': {}}
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f)
PYEOF
fi

# Build combined inventory
python3 - "$WORK_DIR" "$CLIPS_DIR" << 'PYEOF' > "$INVENTORY_FILE"
import json, os, sys

work_dir = sys.argv[1]
playlists = json.load(open(os.path.join(work_dir, 'playlists.json'), encoding='utf-8'))
clips_dir = sys.argv[2]

# Summarize each unique clip
clip_summaries = {}
for clip_name in os.listdir(clips_dir):
    if not clip_name.endswith('.json'):
        continue
    clip_id = clip_name.replace('.json', '')
    path = os.path.join(clips_dir, clip_name)
    try:
        data = json.load(open(path, encoding='utf-8'))
    except:
        clip_summaries[clip_id] = {'error': 'failed to parse ffprobe output'}
        continue

    streams = data.get('streams', [])
    fmt = data.get('format', {})

    video_streams = []
    audio_streams = []
    subtitle_streams = []

    for s in streams:
        ct = s.get('codec_type', 'unknown')
        if ct == 'video':
            video_streams.append({
                'codec': s.get('codec_name', '?'),
                'width': s.get('width'),
                'height': s.get('height'),
                'fps': s.get('r_frame_rate', '?'),
                'duration': float(s.get('duration', 0) or 0),
                'bit_rate': int(s.get('bit_rate', 0) or 0),
            })
        elif ct == 'audio':
            audio_streams.append({
                'codec': s.get('codec_name', '?'),
                'channels': s.get('channels', 0),
                'layout': s.get('channel_layout', '?'),
                'sample_rate': int(s.get('sample_rate', 0) or 0),
                'bit_rate': int(s.get('bit_rate', 0) or 0),
                'duration': float(s.get('duration', 0) or 0),
            })
        elif ct == 'subtitle':
            subtitle_streams.append({
                'codec': s.get('codec_name', '?'),
                'width': s.get('width'),
                'height': s.get('height'),
            })

    clip_size = int(fmt.get('size', 0) or 0)
    clip_duration = float(fmt.get('duration', 0) or 0)
    if clip_duration == 0 and video_streams:
        clip_duration = video_streams[0].get('duration', 0)

    clip_summaries[clip_id] = {
        'size_bytes': clip_size,
        'size_mb': round(clip_size / 1048576, 1),
        'duration_sec': round(clip_duration, 1),
        'video': video_streams,
        'audio': audio_streams,
        'subtitles': subtitle_streams,
    }

# Combine into full inventory
inventory = {
    'playlists': {},
    'clips': clip_summaries,
}

for pl_name, pl_data in playlists.items():
    # Deduplicate clips while preserving order
    playlist_clips = list(dict.fromkeys(item['clip'] for item in pl_data['playitems']))
    summary = {
        'filename': pl_name,
        'duration': round(pl_data['duration'], 1),
        'chapters': pl_data['chapters'],
        'chapter_times': pl_data.get('chapter_times', []),
        'subpaths': pl_data['subpaths'],
        'clips': playlist_clips,
        'total_clip_dur': round(sum(item['duration'] for item in pl_data['playitems']), 1),
        'playlist_type': pl_data.get('playlist_type', 0),
    }
    # Calculate total size from unique clips (exclude zero-duration SubPath clips)
    total_size = 0
    seen_clips = set()
    for item in pl_data['playitems']:
        cid = item['clip']
        if cid in seen_clips:
            continue
        # Skip zero-duration clips (SubPath items): they're menu/PiP, not real content
        if item.get('duration', 0) == 0:
            continue
        seen_clips.add(cid)
        if cid in clip_summaries:
            total_size += clip_summaries[cid].get('size_bytes', 0)
    summary['total_size_mb'] = round(total_size / 1048576, 1)
    inventory['playlists'][pl_name] = summary

# Total disc size
disc_size = sum(c.get('size_bytes', 0) for c in clip_summaries.values())
inventory['disc_size_gb'] = round(disc_size / 1073741824, 2)
inventory['disc_size_mb'] = round(disc_size / 1048576, 1)

json.dump(inventory, sys.stdout, indent=2)
PYEOF

log "Inventory complete: $(python3 -c 'import json,sys;d=json.load(open(sys.argv[1],encoding="utf-8"));print(f"{d["disc_size_gb"]} GB, {len(d["clips"])} clips, {len(d["playlists"])} playlists")' "$INVENTORY_FILE")"

# ─── Phase 2: Classify ───────────────────────────────────────────────────────

log "Phase 2: Classifying content..."

CLASSIFY_FILE="$WORK_DIR/classify.json"
python3 - "$INVENTORY_FILE" "$PY_KEEP_ONE" << 'PYEOF' > "$CLASSIFY_FILE"
import json, sys

inv = json.load(open(sys.argv[1],encoding="utf-8"))
playlists = inv['playlists']
clips = inv['clips']

# Find the longest playlist -> main movie
pl_sorted = sorted(playlists.items(), key=lambda x: x[1]['duration'], reverse=True)

# A playlist qualifies as a potential movie if:
# - duration > 600 seconds (10 min)
# - has at least one video stream
# - not purely short menu animations

main_movie_pls = []
extras_pls = []
menu_pls = []
orphan_clips = set(clips.keys())

for pl_name, pl_data in pl_sorted:
    dur = pl_data['duration']
    clip_list = pl_data['clips']

    for c in clip_list:
        orphan_clips.discard(c)

    # PlayList_type 1 = menu (from MPLS AppInfoPlayList)
    if pl_data.get('playlist_type') == 1:
        menu_pls.append(pl_name)
        continue

    # Check if any referenced clip has video
    has_video = False
    is_hd = False
    for c in clip_list:
        cs = clips.get(c, {})
        for v in cs.get('video', []):
            has_video = True
            if v.get('height', 0) and v['height'] >= 720:
                is_hd = True

    # Compute unique size (excluding zero-duration SubPath clips)
    unique_size_mb = 0
    seen_clips = set()
    for item in pl_data['playitems']:
        cid = item['clip']
        if cid in seen_clips or item.get('duration', 0) == 0:
            continue
        seen_clips.add(cid)
        if cid in clips:
            unique_size_mb += clips[cid].get('size_bytes', 0) / 1048576.0

    if dur < 5 and not has_video:
        menu_pls.append(pl_name)
    elif dur >= 30 and unique_size_mb >= 50:  # Minimum 50 MB to avoid looping/warning playlists
        extras_pls.append(pl_name)
    else:
        # Short clips with video -> menu animations/transitions
        menu_pls.append(pl_name)

# Second pass: catch menu-adjacent playlists.
# Collect clips referenced by known menus, then scan extras for:
#   - shared clips (e.g. menu background also used by a warning screen)
#   - zero-chapter + short duration (logos, warnings, transitions)
menu_clip_set = set()
for pl_name in menu_pls:
    for c in playlists.get(pl_name, {}).get('clips', []):
        menu_clip_set.add(c)

adjacent_extras = []
for pl_name in extras_pls:
    pl = playlists.get(pl_name, {})
    if any(c in menu_clip_set for c in pl.get('clips', [])):
        menu_pls.append(pl_name)
    elif pl.get('chapters', 0) == 0 and pl.get('duration', 0) < 120:
        menu_pls.append(pl_name)
    else:
        adjacent_extras.append(pl_name)
extras_pls = adjacent_extras

# Re-classify: the LARGEST long playlist(s) are the main movie.
# Real movies have both long duration AND large size. Some discs contain
# bogus playlists that repeat a short clip many times, giving them a huge
# duration but tiny size; these must not be selected as the main movie.
# Playlists with similar durations AND comparable sizes/chapters are treated
# as alternate cuts or angles (stricter than duration-only window).
if extras_pls:
    movie_candidates = [pl_name for pl_name in extras_pls
                        if playlists[pl_name]['duration'] >= 600]
    if movie_candidates:
        # Pick the candidate with the largest total size as the main movie
        main_movie_pls = [max(movie_candidates,
                              key=lambda x: playlists[x].get('total_size_mb', 0))]
        main_dur = playlists[main_movie_pls[0]]['duration']
        main_size = playlists[main_movie_pls[0]].get('total_size_mb', 0)
        main_chapters = playlists[main_movie_pls[0]].get('chapters', 0)

        # Include other candidates as alternate cuts/angles ONLY if:
        # 1. Duration within ±25% of main (tighter than the old ±30%)
        # 2. AND size within a 2x ratio of main (to catch legitimate longer/shorter versions)
        # 3. AND chapters count is similar (both have many or both have few)
        new_extras = []
        for pl_name in extras_pls:
            if pl_name == main_movie_pls[0]:
                continue
            pl_dur = playlists[pl_name]['duration']
            pl_size = playlists[pl_name].get('total_size_mb', 0)
            pl_chapters = playlists[pl_name].get('chapters', 0)
            
            # Duration similarity: pl within ±25% of main
            dur_match = main_dur * 0.75 <= pl_dur <= main_dur * 1.25
            # Size similarity: within a 2x ratio of each other (main between 0.5x and 2x of pl)
            size_match = main_size > 0 and (pl_size * 0.5 <= main_size <= pl_size * 2.0)
            # Chapters similarity: both have chapters or both don't
            chapters_match = (main_chapters > 0 and pl_chapters > 0) or (main_chapters == 0 and pl_chapters == 0)
            
            # Strict alternate-cut rule: all three must match
            if dur_match and size_match and chapters_match:
                main_movie_pls.append(pl_name)
            else:
                new_extras.append(pl_name)
        extras_pls = new_extras
    else:
        # No long playlists; promote the longest extra
        main_movie_pls.append(extras_pls.pop(0))

# Handle orphan clips (referenced by no playlist — usually menu graphics/sounds)
orphan_info = {}
for cid in sorted(orphan_clips):
    cs = clips.get(cid, {})
    orphan_info[cid] = {
        'size_mb': cs.get('size_mb', 0),
        'duration': cs.get('duration_sec', 0),
        'has_video': len(cs.get('video', [])) > 0,
    }

classification = {
    'main_movie': main_movie_pls,
    'extras': extras_pls,
    'menus': menu_pls,
    'orphans': orphan_info,
}

# Detailed per-playlist breakdown
details = {}
for pl_name, pl_data in pl_sorted:
    dur_str = f"{int(pl_data['duration']//60)}m{int(pl_data['duration']%60)}s"
    size_mb = pl_data.get('total_size_mb', 0)
    cat = 'main' if pl_name in main_movie_pls else ('extras' if pl_name in extras_pls else 'menu')
    details[pl_name] = {
        'duration': pl_data['duration'],
        'duration_str': dur_str,
        'size_mb': size_mb,
        'category': cat,
        'clips': pl_data['clips'],
        'chapters': pl_data.get('chapters', 0),
    }

classification['details'] = details

if sys.argv[2] == 'True':
    classification['main_movie'] = main_movie_pls[:1]
    # Move the others to extras (they'll be skipped or encoded)
    for pl in main_movie_pls[1:]:
        classification['extras'].append(pl)
        details[pl]['category'] = 'extras'

json.dump(classification, sys.stdout, indent=2)
PYEOF

script_output=$(python3 - "$CLASSIFY_FILE" << 'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
main_list = d.get('main_movie', [])
print(' '.join(main_list))
print(' '.join(d.get('extras', [])))
print(' '.join(d.get('menus', [])))
print(len(d.get('orphans', [])))
for pl in main_list:
    detail = d.get('details', {}).get(pl, {})
    print(detail.get('duration_str', '?'))
PYEOF
) || die "Failed to parse classify data"

mapfile -t lines <<< "$script_output"

main_line="${lines[0]:-}"
extras_line="${lines[1]:-}"
menu_line="${lines[2]:-}"
orphan_count="${lines[3]:-0}"
dur_lines=("${lines[@]:4}")

IFS=' ' read -ra MAIN_PLAYLISTS <<< "$main_line"
IFS=' ' read -ra EXTRAS_PLAYLISTS <<< "$extras_line"
IFS=' ' read -ra MENU_PLAYLISTS <<< "$menu_line"

log "Main movie: ${#MAIN_PLAYLISTS[@]} playlist(s)"
idx=0
for pl in "${MAIN_PLAYLISTS[@]}"; do
    dur="${dur_lines[$idx]:-?}"
    info "$pl — $dur"
    ((idx++)) || true
done
log "Extras: ${#EXTRAS_PLAYLISTS[@]} playlist(s)"
log "Menus: ${#MENU_PLAYLISTS[@]} playlist(s) + ${orphan_count} orphan clips"

# Emit detailed per-playlist classification table for diagnostics
log ""
log "=== Detailed Playlist Classification ==="
python3 - "$CLASSIFY_FILE" << 'CLASS_TABLE'
import json, sys
try:
    clf = json.load(open(sys.argv[1], encoding='utf-8'))
    details = clf.get('details', {})
    
    print("Playlist | Duration | Size(MB) | Chapters | Category")
    print("-" * 65)
    for pl_name in sorted(details.keys()):
        d = details[pl_name]
        duration_str = d.get('duration_str', '?')
        size_mb = d.get('size_mb', 0)
        chapters = d.get('chapters', 0)
        category = d.get('category', '?')
        print(f"{pl_name:12} | {duration_str:>6} | {size_mb:>7.1f} | {chapters:>8} | {category}")
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
CLASS_TABLE
log "=== End Classification Table ==="
log ""

# Apply classification overrides (if provided)
if [[ -n "$OVERRIDE_MAIN_PLAYLISTS" ]] || [[ -n "$OVERRIDE_EXTRAS" ]] || [[ -n "$OVERRIDE_MENUS" ]] || [[ -n "$OVERRIDE_NOT_EXTRAS" ]]; then
    log "Applying classification overrides..."
    python3 - "$CLASSIFY_FILE" "$OVERRIDE_MAIN_PLAYLISTS" "$OVERRIDE_EXTRAS" "$OVERRIDE_MENUS" "$OVERRIDE_NOT_EXTRAS" << 'APPLY_OVERRIDES'
import json, sys
try:
    clf = json.load(open(sys.argv[1], encoding='utf-8'))
    details = clf.get('details', {})
    
    override_main = sys.argv[2].split(',') if sys.argv[2] else []
    override_extras = sys.argv[3].split(',') if sys.argv[3] else []
    override_menus = sys.argv[4].split(',') if sys.argv[4] else []
    override_not_extras = sys.argv[5].split(',') if sys.argv[5] else []
    
    # Normalize: remove .mpls and empty strings
    override_main = [p.replace('.mpls', '') for p in override_main if p.strip()]
    override_extras = [p.replace('.mpls', '') for p in override_extras if p.strip()]
    override_menus = [p.replace('.mpls', '') for p in override_menus if p.strip()]
    override_not_extras = [p.replace('.mpls', '') for p in override_not_extras if p.strip()]
    
    # Apply overrides
    for pl_name in details:
        if pl_name in override_main:
            clf['main_movie'] = [pl_name]  # Force as only main
            clf['extras'] = [e for e in clf.get('extras', []) if e != pl_name]
            clf['menus'] = [m for m in clf.get('menus', []) if m != pl_name]
            details[pl_name]['category'] = 'main'
            print(f"  Forced main: {pl_name}.mpls")
        elif pl_name in override_menus:
            clf['menus'].append(pl_name)
            clf['extras'] = [e for e in clf.get('extras', []) if e != pl_name]
            clf['main_movie'] = [m for m in clf.get('main_movie', []) if m != pl_name]
            details[pl_name]['category'] = 'menu'
            print(f"  Forced menu: {pl_name}.mpls")
        elif pl_name in override_extras:
            clf['extras'].append(pl_name)
            clf['menus'] = [m for m in clf.get('menus', []) if m != pl_name]
            clf['main_movie'] = [m for m in clf.get('main_movie', []) if m != pl_name]
            details[pl_name]['category'] = 'extras'
            print(f"  Forced extra: {pl_name}.mpls")
        elif pl_name in override_not_extras:
            if pl_name in clf.get('extras', []):
                clf['extras'].remove(pl_name)
            clf['menus'].append(pl_name)
            details[pl_name]['category'] = 'menu'
            print(f"  Excluded from extras: {pl_name}.mpls")
    
    # Remove duplicates and maintain order
    clf['main_movie'] = list(dict.fromkeys(clf['main_movie']))
    clf['extras'] = list(dict.fromkeys(clf['extras']))
    clf['menus'] = list(dict.fromkeys(clf['menus']))
    
    json.dump(clf, open(sys.argv[1], 'w', encoding='utf-8'), indent=2)
except Exception as e:
    print(f"Error applying overrides: {e}", file=sys.stderr)
    sys.exit(1)
APPLY_OVERRIDES

    # Re-read classification after overrides
    override_output=$(python3 - "$CLASSIFY_FILE" << 'REREAD'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
main_list = d.get('main_movie', [])
print(' '.join(main_list))
print(' '.join(d.get('extras', [])))
print(' '.join(d.get('menus', [])))
print(len(d.get('orphans', [])))
for pl in main_list:
    detail = d.get('details', {}).get(pl, {})
    print(detail.get('duration_str', '?'))
REREAD
) || die "Failed to re-parse classify data after overrides"
    
    mapfile -t lines <<< "$override_output"
    main_line="${lines[0]:-}"
    extras_line="${lines[1]:-}"
    menu_line="${lines[2]:-}"
    orphan_count="${lines[3]:-0}"
    dur_lines=("${lines[@]:4}")
    
    IFS=' ' read -ra MAIN_PLAYLISTS <<< "$main_line"
    IFS=' ' read -ra EXTRAS_PLAYLISTS <<< "$extras_line"
    IFS=' ' read -ra MENU_PLAYLISTS <<< "$menu_line"
    log "After overrides: Main=${#MAIN_PLAYLISTS[@]}, Extras=${#EXTRAS_PLAYLISTS[@]}, Menus=${#MENU_PLAYLISTS[@]}"
fi

# Verify a main movie was identified
[[ ${#MAIN_PLAYLISTS[@]} -gt 0 ]] || die "No main movie playlist identified. Check the source disc structure."

# Preflight: warn if surgical mode may break navigation (menus + lossless audio change)
if ! $MOVIE_ONLY && [[ ${#MENU_PLAYLISTS[@]} -gt 0 ]]; then
    # Check if main audio is lossless (requires transcode in surgical mode)
    main_audio_is_lossless=false
    if [[ -n "${MAIN_PLAYLISTS[0]}" ]]; then
        main_pl="${MAIN_PLAYLISTS[0]}"
        # Get the first clip of the main movie
        first_clip=$(python3 - "$CLASSIFY_FILE" "$main_pl" << 'AUDIO_CHECK'
import json, sys
try:
    clf = json.load(open(sys.argv[1], encoding='utf-8'))
    clips = clf['details'].get(sys.argv[2], {}).get('clips', [])
    print(clips[0] if clips else '')
except:
    print('')
AUDIO_CHECK
)
        if [[ -n "$first_clip" ]]; then
            # Check clip audio codecs from inventory
            has_lossless=$(python3 - "$INVENTORY_FILE" "$first_clip" << 'LOSSLESS_CHECK'
import json, sys
try:
    inv = json.load(open(sys.argv[1], encoding='utf-8'))
    clip = inv['clips'].get(sys.argv[2], {})
    audio = clip.get('audio', [])
    for a in audio:
        codec = a.get('codec', '')
        if codec in ('dts', 'truehd', 'pcm_s16be', 'pcm_s24be'):
            print('true')
            exit(0)
    print('false')
except:
    print('false')
LOSSLESS_CHECK
)
            [[ "$has_lossless" == "true" ]] && main_audio_is_lossless=true
        fi
    fi
    
    if $main_audio_is_lossless; then
        warn "=== Surgical Mode Navigation Warning ==="
        warn "This disc has menus (${#MENU_PLAYLISTS[@]} playlists) and lossless audio tracks."
        warn "Re-encoding requires transcoding audio (lossless → AC3) and changing stream PIDs."
        warn "This may cause menus to freeze or navigation to fail (IGS/HDMV compositions won't tear down)."
        warn "Recommendation: use --movie-only mode instead, or test thoroughly in VLC/mpv before burning."
        warn "============================================"
    fi
fi

# ─── Phase 3: Budget ─────────────────────────────────────────────────────────

log "Phase 3: Calculating space budget..."

BUDGET_FILE="$WORK_DIR/budget.json"
python3 - "$INVENTORY_FILE" "$CLASSIFY_FILE" "$TARGET_GB" "$OVERHEAD_MB" "$EXTRAS_SCALE" "$PY_MOVIE_ONLY" "$PY_NO_EXTRAS" "$EXTRAS_AUDIO_BITRATE" "$MAIN_AUDIO_BITRATE" "$COMMENTARY_AUDIO_BITRATE" << 'PYEOF' > "$BUDGET_FILE"
import json, sys

inventory_file = sys.argv[1]
classify_file = sys.argv[2]
target_gb = int(sys.argv[3])
overhead_mb = int(sys.argv[4])
extras_scale = sys.argv[5]
py_movie_only = sys.argv[6] == 'True'
py_no_extras = sys.argv[7] == 'True'
extras_audio_bitrate_str = sys.argv[8]
main_audio_bitrate_str = sys.argv[9]
commentary_audio_bitrate_str = sys.argv[10]

inv = json.load(open(inventory_file, encoding='utf-8'))
clf = json.load(open(classify_file, encoding='utf-8'))
clips = inv['clips']

target_bytes = target_gb * 1073741824
overhead_bytes = overhead_mb * 1048576
target_available = target_bytes - overhead_bytes

main_pls = clf['main_movie']
extras_pls = clf['extras']
menu_pls = clf['menus']
orphans = clf['orphans']

# Collect all unique clips by category
def clips_for_playlists(pl_list):
    result = set()
    for pl_name in pl_list:
        pd = clf['details'].get(pl_name, {})
        for c in pd.get('clips', []):
            result.add(c)
    return result

main_clips = clips_for_playlists(main_pls)
extras_clips = clips_for_playlists(extras_pls) - main_clips
menu_clips = clips_for_playlists(menu_pls)
extras_clips -= menu_clips  # never re-encode menu-adjacent clips

# Menu clips: copy verbatim
menu_size = sum(clips.get(c, {}).get('size_bytes', 0) for c in menu_clips)
orphan_size = sum(clips.get(c, {}).get('size_bytes', 0) for c in orphans)
total_menu_size = menu_size + orphan_size

# Compute main movie original size for reporting
main_original_size = sum(clips.get(c, {}).get('size_bytes', 0) for c in main_clips)

# Estimate extras re-encoded size
extras_reencoded_size = 0
extras_original_size = 0
extras_clip_details = {}
for c in extras_clips:
    cs = clips.get(c, {})
    extras_original_size += cs.get('size_bytes', 0)
    dur = cs.get('duration_sec', 0)
    # Check if HD (1080p or 720p) vs already SD
    is_hd = False
    resolution = None
    for v in cs.get('video', []):
        h = v.get('height', 0) or 0
        w = v.get('width', 0) or 0
        if h > 576:  # PAL SD threshold
            is_hd = True
            resolution = f"{w}x{h}"

    audio_bitrate = int(extras_audio_bitrate_str.replace('k', '')) * 1000
    num_audio = len(cs.get('audio', []))
    total_audio_bitrate = audio_bitrate * max(1, num_audio)  # all audio tracks at same bitrate

    if is_hd and dur > 0:
        # Estimate: 720p CRF 22 ~ 2-4 Mbps video
        est_video_bitrate = 3_000_000  # 3 Mbps conservative estimate
        est_size = dur * (est_video_bitrate + total_audio_bitrate) / 8
        extras_reencoded_size += est_size
    else:
        # Already small or SD, keep original
        extras_reencoded_size += cs.get('size_bytes', 0)

    extras_clip_details[c] = {
        'duration': dur,
        'original_mb': round(cs.get('size_bytes', 0) / 1048576, 1),
        'resolution': resolution,
        'is_hd': is_hd,
        'will_reencode': is_hd and dur > 0,
    }

if py_no_extras:
    extras_reencoded_size = 0
    extras_original_size = 0
    extras_clip_details = {}

# Available for main movie
if py_movie_only:
    # Movie-only: all space goes to main movie
    available_for_main = target_available
else:
    available_for_main = target_available - total_menu_size - extras_reencoded_size

# Calculate main movie bitrate from the UNIQUE set of clips that will
# actually be encoded. Summing per-playlist durations double-counts clips
# shared between multiple main playlists (seamless branching / alternate
# cuts) and produces a too-low bitrate, undersizing the output.
if py_movie_only and main_pls:
    # Movie-only encodes only the first main playlist (largest by size).
    total_main_dur = clf['details'][main_pls[0]]['duration']
else:
    total_main_dur = sum(clips.get(c, {}).get('duration_sec', 0) for c in main_clips)

main_audio_size = 0
main_audio_tracks = 0
main_audio_count = 0  # Initialize to avoid NameError if duration is 0
if total_main_dur > 0:
    # Use actual audio track count from the first main clip, capped at 8
    first_cid = next(iter(main_clips)) if main_clips else None
    first_clip = inv['clips'].get(first_cid, {}) if first_cid else {}
    main_audio_count = min(8, max(1, len(first_clip.get('audio', []))))
    for i in range(main_audio_count):
        if i == 0:
            rate = int(main_audio_bitrate_str.replace('k', '')) * 1000
        else:
            main_audio_tracks += 1
            rate = int(commentary_audio_bitrate_str.replace('k', '')) * 1000
        main_audio_size += int((rate * total_main_dur) / 8)

main_bitrate = 0
if total_main_dur > 0:
    # Subtract audio overhead + 5% safety margin for x264 overshoot
    main_video_available = int((available_for_main - main_audio_size) * 0.95)
    main_bitrate = max(1000000, int((main_video_available * 8) / total_main_dur))

budget = {
    'target_gb': target_gb,
    'target_bytes': target_bytes,
    'overhead_mb': overhead_mb,
    'available_bytes': int(available_for_main),
    'menu_size_mb': round(total_menu_size / 1048576, 1),
    'extras_original_mb': round(extras_original_size / 1048576, 1),
    'extras_estimated_mb': round(extras_reencoded_size / 1048576, 1),
    'main_original_mb': round(main_original_size / 1048576, 1),
    'main_available_mb': round(available_for_main / 1048576, 1),
    'main_bitrate': main_bitrate,
    'main_bitrate_mbps': round(main_bitrate / 1000000, 2),
    'main_duration_sec': total_main_dur,
    'main_duration_str': f"{int(total_main_dur//3600)}h{int((total_main_dur%3600)//60)}m",
    'main_audio_tracks': main_audio_count,
    'commentary_tracks': main_audio_tracks,
    'commentary_bitrate': int(commentary_audio_bitrate_str.replace('k', '')),
    'extras_details': extras_clip_details,
}

json.dump(budget, sys.stdout, indent=2)
PYEOF

log "Budget summary:"
python3 - "$BUDGET_FILE" << 'PYEOF' 2>/dev/null
import json, sys
b = json.load(open(sys.argv[1],encoding="utf-8"))
print(f"  Target:            {b['target_gb']} GB")
print(f"  Menu (passthru):   {b['menu_size_mb']} MB")
print(f"  Extras original:   {b['extras_original_mb']} MB")
print(f"  Extras estimated:  {b['extras_estimated_mb']} MB")
print(f"  Main original:     {b['main_original_mb']} MB")
print(f"  Main available:    {b['main_available_mb']} MB")
print(f"  Main bitrate:      {b['main_bitrate_mbps']} Mbps")
print(f"  Main duration:     {b['main_duration_str']}")
PYEOF

if $DRY_RUN; then
    log "Dry run complete. Summary above."
    log "Work dir: $WORK_DIR"
    exit 0
fi

# ─── Phase 4: Encode ─────────────────────────────────────────────────────────

log "Phase 4: Encoding..."

ENCODE_DIR="$WORK_DIR/encode"
mkdir -p "$ENCODE_DIR"

# Pre-compute ALL data for Phase 4 + Phase 5 in a single systemd-run call
PRECOMPUTE_PY="$WORK_DIR/precompute.py"
cat > "$PRECOMPUTE_PY" << 'PYEOF'
import json, os, sys

inventory_file = sys.argv[1]
budget_file = sys.argv[2]
classify_file = sys.argv[3]
work_dir = sys.argv[4]
clips_dir = sys.argv[5]

data = json.load(open(inventory_file, encoding='utf-8'))
budget = json.load(open(budget_file, encoding='utf-8'))
classify = json.load(open(classify_file, encoding='utf-8'))
base = int(budget['main_bitrate'])

# --- Phase 4: clip metadata ---
with open(os.path.join(work_dir, '.clip_precompute.txt'), 'w', encoding='utf-8') as f:
    for cid, c in data.get('clips', {}).items():
        aud = len(c.get('audio', []))
        sub = len(c.get('subtitles', []))
        vs = c.get('video', [{}])[0] if c.get('video') else {}
        h = vs.get('height', 1080) or 1080
        w = vs.get('width', 1920) or 1920
        f.write(f'{cid}|{aud}|{sub}|{h}|{w}\n')

# --- Phase 4: budget values ---
with open(os.path.join(work_dir, '.budget_values.txt'), 'w', encoding='utf-8') as f:
    # Cap maxrate/bufsize at BD-compatible peaks to avoid encoder rejects on tiny sources
    maxrate = min(int(base * 1.1), 40000000)
    bufsize = min(int(base * 1.5), 30000000)
    f.write(f'{base}\n{maxrate}\n{bufsize}\n')

# --- Phase 4: extras clips ---
with open(os.path.join(work_dir, '.extras_clips.txt'), 'w', encoding='utf-8') as f:
    for cid, cd in budget['extras_details'].items():
        if cd['will_reencode']:
            f.write(f'{cid}\n')

# --- Phase 4: main clips + Phase 5 data ---
main_clips = []
for pl_name in classify['main_movie']:
    pd = classify['details'].get(pl_name, {})
    for c in pd.get('clips', []):
        if c not in main_clips:
            main_clips.append(c)

with open(os.path.join(work_dir, '.main_clips.txt'), 'w', encoding='utf-8') as f:
    for cid in main_clips:
        f.write(f'{cid}\n')

# --- Phase 5: main playlist name ---
main_pl_name = classify['main_movie'][0] if classify['main_movie'] else ''
with open(os.path.join(work_dir, '.main_playlist.txt'), 'w', encoding='utf-8') as f:
    f.write(f'{main_pl_name}\n')

# --- Phase 5: chapter timestamps ---
pl = data['playlists'].get(main_pl_name, {})
ct = pl.get('chapter_times', [])
seen = {0}
chapters = ['00:00:00']
for t in ct:
    h, m, s = int(t//3600), int((t%3600)//60), int(t%60)
    ch = f'{h:02d}:{m:02d}:{s:02d}'
    if t not in seen:
        chapters.append(ch)
        seen.add(t)
with open(os.path.join(work_dir, '.main_chapters.txt'), 'w', encoding='utf-8') as f:
    f.write(';'.join(chapters) + '\n')

# --- Phase 5: FPS of all clips (surgical mode needs per-clip FPS) ---
with open(os.path.join(work_dir, '.clip_fps.txt'), 'w', encoding='utf-8') as f:
    for cid in data.get('clips', {}):
        fps = '24000/1001'
        clip_json = os.path.join(clips_dir, f'{cid}.json')
        try:
            d = json.load(open(clip_json, encoding='utf-8'))
            for s in d.get('streams', []):
                if s.get('codec_type') == 'video':
                    fps = s.get('avg_frame_rate') or '24000/1001'
                    if fps in ('0/0', '0', ''):
                        fps = '24000/1001'
                    break
        except:
            pass
        f.write(f'{cid}|{fps}\n')

# --- Phase 5: main FPS (from first main clip) ---
main_fps = '24000/1001'
if main_clips:
    first_cid = main_clips[0]
    clip_json = os.path.join(clips_dir, f'{first_cid}.json')
    try:
        d = json.load(open(clip_json, encoding='utf-8'))
        for s in d.get('streams', []):
            if s.get('codec_type') == 'video':
                main_fps = s.get('avg_frame_rate') or '24000/1001'
                if main_fps in ('0/0', '0', ''):
                    main_fps = '24000/1001'
                break
    except:
        pass
with open(os.path.join(work_dir, '.main_fps.txt'), 'w', encoding='utf-8') as f:
    f.write(f'{main_fps}\n')

# --- Phase 5: all clip IDs (surgical mode needs the full list) ---
with open(os.path.join(work_dir, '.all_clips.txt'), 'w', encoding='utf-8') as f:
    for cid in sorted(data.get('clips', {}).keys()):
        f.write(f'{cid}\n')

# --- Phase 5: main clip count, max audio, max subtitle ---
with open(os.path.join(work_dir, '.main_counts.txt'), 'w', encoding='utf-8') as f:
    f.write(f'{len(main_clips)}\n')
    f.write('0\n0\n')  # placeholder, recalculated in Phase 5 after encoding
PYEOF

pre_nice_prop=()
if [[ "${NICE:-0}" -gt 0 ]]; then
    pre_nice_prop=(--property=Nice="$NICE")
fi
systemd-run --user --wait -q -u "bd_pre.${RANDOM}.$$" "${pre_nice_prop[@]}" -- python3 "$PRECOMPUTE_PY" "$INVENTORY_FILE" "$BUDGET_FILE" "$CLASSIFY_FILE" "$WORK_DIR" "$CLIPS_DIR"
rm -f "$PRECOMPUTE_PY"

# Read budget values (builtins only)
{
    read MAIN_BITRATE || true
    read MAIN_MAXRATE || true
    read MAIN_BUFSIZE || true
} < "$WORK_DIR/.budget_values.txt"
[[ -z "$MAIN_BITRATE" ]] && MAIN_BITRATE=20000000
[[ -z "$MAIN_MAXRATE" ]] && MAIN_MAXRATE=22000000
[[ -z "$MAIN_BUFSIZE" ]] && MAIN_BUFSIZE=30000000

# Read clip lists (builtins only, use read loop for line-separated lists)
EXTRAS_CLIPS=""
while read -r cid; do
    EXTRAS_CLIPS+="$cid"$'\n'
done < "$WORK_DIR/.extras_clips.txt"

MAIN_CLIPS=""
while read -r cid; do
    MAIN_CLIPS+="$cid"$'\n'
done < "$WORK_DIR/.main_clips.txt"

# Read FPS for all clips + all-clip-ID list (builtins only)
declare -A CLIP_FPS
while IFS='|' read -r cid fps; do
    CLIP_FPS[$cid]="$fps"
done < "$WORK_DIR/.clip_fps.txt"

ALL_CLIP_IDS=""
while read -r cid; do
    ALL_CLIP_IDS+="$cid"$'\n'
done < "$WORK_DIR/.all_clips.txt"

# Trim trailing newlines from clip lists (consistent for reuse)
EXTRAS_CLIPS="${EXTRAS_CLIPS%$'\n'}"
MAIN_CLIPS="${MAIN_CLIPS%$'\n'}"
ALL_CLIP_IDS="${ALL_CLIP_IDS%$'\n'}"

# Common x264 BD-compat opts
BD_X264_OPTS="bluray-compat=1:vbv-maxrate=40000:vbv-bufsize=30000"

# Encode extras (single-pass CRF, downscale to 720p)
if [[ -n "$EXTRAS_CLIPS" ]] && ! $NO_EXTRAS && ! $MOVIE_ONLY; then
    log "Encoding extras..."
fi

# Encode main movie (two-pass VBR)
if [[ -n "$MAIN_CLIPS" ]]; then
    log "Encoding main movie..."
    # Trim trailing newline from clip list
    MAIN_CLIPS="${MAIN_CLIPS%$'\n'}"
    log "  Bitrate: $(( MAIN_BITRATE / 1000000 )) Mbps, preset: $MAIN_PRESET"
fi

# Run ALL encoding in a single Python process.
python3 -u - "$SOURCE" "$MKV_INPUT" "$ENCODE_DIR" "$WORK_DIR" "$CLIPS_DIR" "$INVENTORY_FILE" "$BD_X264_OPTS" "$MAIN_PRESET" "$MAIN_BITRATE" "$MAIN_MAXRATE" "$MAIN_BUFSIZE" "$MAIN_AUDIO_BITRATE" "$COMMENTARY_AUDIO_BITRATE" "$EXTRAS_AUDIO_BITRATE" "$EXTRAS_CRF" "$EXTRAS_SCALE" "$EXTRAS_CLIPS" "$MAIN_CLIPS" "$NO_EXTRAS" "$MOVIE_ONLY" "$CODEC" "$NICE" << 'PYEOF'
import glob, json, os, subprocess, sys

_source = sys.argv[1]
src_dir = os.path.join(_source, 'STREAM')
mkv_src = _source  # unused for BDMV input
mkv_input = sys.argv[2] == 'true'
encode_dir = sys.argv[3]
work_dir = sys.argv[4]
clips_dir = sys.argv[5]
inventory_file = sys.argv[6]
bd_x264_opts = sys.argv[7]
main_preset = sys.argv[8]
main_bitrate = sys.argv[9]
main_maxrate = sys.argv[10]
main_bufsize = sys.argv[11]
main_audio_bitrate = sys.argv[12]
commentary_audio_bitrate = sys.argv[13]
extras_audio_bitrate = sys.argv[14]
extras_crf = sys.argv[15]
extras_scale = sys.argv[16]
extras_clips_str = sys.argv[17]
main_clips_str = sys.argv[18]
no_extras = sys.argv[19] == 'true'
movie_only = sys.argv[20] == 'true'
codec = sys.argv[21]
nice = int(sys.argv[22])
enc_lib = 'libx265' if codec == 'hevc' else 'libx264'
video_ext = 'hevc' if codec == 'hevc' else 'h264'
pass_prefix = 'x265' if codec == 'hevc' else 'x264'
is_hevc = codec == 'hevc'

def apply_nice(cmd):
    if nice > 0:
        return ['nice', '-n', str(nice)] + cmd
    return cmd

def clip_source(clip):
    if mkv_input:
        return mkv_src
    return os.path.join(src_dir, '{}.m2ts'.format(clip))

def get_sub_codecs(clip):
    """Return list of subtitle codec names (e.g. 'hdmv_pgs', 'subrip') for a clip."""
    cpath = os.path.join(clips_dir, '{}.json'.format(clip))
    if not os.path.isfile(cpath):
        return []
    try:
        d = json.load(open(cpath, encoding='utf-8'))
        subs = []
        for s in d.get('streams', []):
            if s.get('codec_type') == 'subtitle':
                subs.append(s.get('codec_name', '?'))
        return subs
    except:
        return []

def get_audio_codecs(clip):
    """Return list of audio codec names (e.g. 'ac3', 'dts', 'mp3') for a clip."""
    cpath = os.path.join(clips_dir, '{}.json'.format(clip))
    if not os.path.isfile(cpath):
        return []
    try:
        d = json.load(open(cpath, encoding='utf-8'))
        codecs = []
        for s in d.get('streams', []):
            if s.get('codec_type') == 'audio':
                codecs.append(s.get('codec_name', '?'))
        return codecs
    except:
        return []

def sub_ext(codec):
    if codec in ('subrip', 'srt', 'text'):
        return 'srt'
    return 'sup'

def sub_format(codec):
    if codec in ('subrip', 'srt', 'text'):
        return None  # no -f needed for SRT text
    return 'sup'

def run_ff(cmd, out_file=None, pass_log_base=None):
    """Run ffmpeg via subprocess. Returns True on success, False on failure."""
    # Skip if output already exists (resumability)
    if out_file and os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
        return True
    if pass_log_base:
        matches = glob.glob(pass_log_base + '*')
        if matches:
            return True
    cmd = apply_nice(cmd)
    try:
        r = subprocess.run(cmd, timeout=None, capture_output=False)
        if r.returncode == 0:
            return True
        if out_file and os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
            return True
        if pass_log_base:
            matches = glob.glob(pass_log_base + '*')
            if matches:
                return True
        return False
    except Exception as e:
        sys.stderr.write('  ffmpeg exception: {}\n'.format(e))
        return False

# Parse clip lists
extras_clips = [c.strip() for c in extras_clips_str.split('\n') if c.strip()]
main_clips = [c.strip() for c in main_clips_str.split('\n') if c.strip()]

# Read pre-computed clip metadata
clip_data = {}
with open('{}/.clip_precompute.txt'.format(work_dir), encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        parts = line.split('|')
        clip_data[parts[0]] = {'aud': int(parts[1]), 'sub': int(parts[2]), 'h': int(parts[3]), 'w': int(parts[4])}

# --- Encode extras ---
total_clips = len(extras_clips) + len(main_clips)
clip_idx = 0
if not no_extras and not movie_only:
    for i, clip in enumerate(extras_clips):
        src = clip_source(clip)
        out_video = os.path.join(encode_dir, '{}_video.{}'.format(clip, video_ext))
        cd = clip_data.get(clip, {'aud':0, 'sub':0, 'h':1080})
        src_aud, src_sub, src_height = cd['aud'], cd['sub'], cd['h']

        clip_idx += 1
        sys.stderr.write('  [{}/{}] Extra: {}.m2ts\n'.format(clip_idx, total_clips, clip))

        # Audio extraction
        audio_tracks = 0
        if src_aud > 0:
            audio_codecs = get_audio_codecs(clip)
            audio_args = ['ffmpeg', '-y', '-v', 'error', '-fflags', '+genpts', '-i', src]
            actual_audio_idx = 0
            for ai in range(src_aud):
                codec = audio_codecs[ai] if ai < len(audio_codecs) else '?'
                if codec in ('mp3', 'mp3float', 'mp2', 'mp2float'):
                    sys.stderr.write('    skipping MPEG audio track {}\n'.format(ai))
                    continue
                out_path = os.path.join(encode_dir, '{}_audio_{}.ac3'.format(clip, actual_audio_idx))
                if codec in ('ac3', 'eac3'):
                    audio_args += ['-map', '0:a:{}'.format(ai), '-c:a', 'copy', out_path]
                else:
                    audio_args += ['-map', '0:a:{}'.format(ai), '-c:a', 'ac3',
                                   '-b:a', extras_audio_bitrate, out_path]
                actual_audio_idx += 1
            first_audio = os.path.join(encode_dir, '{}_audio_0.ac3'.format(clip))
            if actual_audio_idx > 0 and run_ff(audio_args, out_file=first_audio):
                audio_tracks = actual_audio_idx
                for ai in range(actual_audio_idx):
                    if not os.path.isfile(os.path.join(encode_dir, '{}_audio_{}.ac3'.format(clip, ai))):
                        audio_tracks = 0
                        break

        # Subtitle extraction
        sub_tracks = 0
        if src_sub > 0:
            sub_codecs = get_sub_codecs(clip)
            sb_args = ['ffmpeg', '-y', '-v', 'error', '-i', src]
            actual_sub_idx = 0
            for si in range(src_sub):
                codec = sub_codecs[si] if si < len(sub_codecs) else 'hdmv_pgs'
                # Skip non-BD-compatible subtitle codecs (DVD/VobSub, etc.)
                if codec in ('dvd_subtitle', 'dvdsub', 'dvd_sub', 'dvd'):
                    continue
                ext = sub_ext(codec)
                fmt = sub_format(codec)
                sb_args += ['-map', '0:s:{}'.format(si), '-c', 'copy']
                if fmt:
                    sb_args += ['-f', fmt]
                sb_args += [os.path.join(encode_dir, '{}_sub_{}.{}'.format(clip, actual_sub_idx, ext))]
                actual_sub_idx += 1
            if len(sb_args) > 6:  # have subtitle mappings beyond initial ['ffmpeg', '-y', '-v', 'error', '-i', src]
                if run_ff(sb_args):
                    sub_tracks = actual_sub_idx

        if audio_tracks == 0:
            sys.stderr.write('  (video-only)\n')

        # Video encoding
        video_filter = []
        if src_height > 720:
            video_filter = ['-vf', 'scale={}'.format(extras_scale)]

        x264_full_opts = '{}:vbv-maxrate=12000:vbv-bufsize=12000'.format(bd_x264_opts)
        for attempt in range(3):
            cmd = ['ffmpeg', '-y', '-v', 'error', '-stats', '-i', src,
                   '-map', '0:v:0', '-c:v', enc_lib, '-preset', 'medium',
                   '-crf', str(extras_crf)] + video_filter
            if is_hevc:
                cmd += ['-maxrate', '12000k', '-bufsize', '12000k', out_video]
            else:
                cmd += ['-x264opts', x264_full_opts, out_video]
            if run_ff(cmd, out_file=out_video):
                break
            if attempt < 2:
                sys.stderr.write('    retry {}\n'.format(attempt + 1))

        if os.path.isfile(out_video) and os.path.getsize(out_video) > 0:
            sys.stderr.write('    done ({} audio, {} subtitle)\n'.format(audio_tracks, sub_tracks))

# --- Encode main movie ---
if main_clips:
    for clip in main_clips:
        src = clip_source(clip)
        out_video = os.path.join(encode_dir, '{}_video.{}'.format(clip, video_ext))
        pass_log = os.path.join(work_dir, pass_prefix + '_{}.log'.format(clip))
        cd = clip_data.get(clip, {'aud':0, 'sub':0, 'h':1080})
        src_aud, src_sub = cd['aud'], cd['sub']

        clip_idx += 1
        sys.stderr.write('  [{}/{}] Main: {}.m2ts\n'.format(clip_idx, total_clips, clip))

        # Audio extraction
        audio_tracks = 0
        if src_aud > 0:
            audio_codecs = get_audio_codecs(clip)
            audio_args = ['ffmpeg', '-y', '-v', 'error', '-fflags', '+genpts', '-i', src]
            actual_audio_idx = 0
            for ai in range(src_aud):
                codec = audio_codecs[ai] if ai < len(audio_codecs) else '?'
                if codec in ('mp3', 'mp3float', 'mp2', 'mp2float'):
                    sys.stderr.write('    skipping MPEG audio track {}\n'.format(ai))
                    continue
                out_path = os.path.join(encode_dir, '{}_audio_{}.ac3'.format(clip, actual_audio_idx))
                if codec in ('ac3', 'eac3'):
                    audio_args += ['-map', '0:a:{}'.format(ai), '-c:a', 'copy', out_path]
                else:
                    bitrate = main_audio_bitrate if actual_audio_idx == 0 else commentary_audio_bitrate
                    audio_args += ['-map', '0:a:{}'.format(ai), '-c:a', 'ac3',
                                   '-b:a', bitrate, out_path]
                actual_audio_idx += 1
            first_audio = os.path.join(encode_dir, '{}_audio_0.ac3'.format(clip))
            if actual_audio_idx > 0 and run_ff(audio_args, out_file=first_audio):
                audio_tracks = actual_audio_idx
                # Verify all expected tracks exist (resume safety: catch partial/missing tracks on re-run)
                for ai in range(actual_audio_idx):
                    if not os.path.isfile(os.path.join(encode_dir, '{}_audio_{}.ac3'.format(clip, ai))):
                        audio_tracks = 0
                        break

        # Subtitle extraction
        sub_tracks = 0
        if src_sub > 0:
            sub_codecs = get_sub_codecs(clip)
            sb_args = ['ffmpeg', '-y', '-v', 'error', '-i', src]
            actual_sub_idx = 0
            for si in range(src_sub):
                codec = sub_codecs[si] if si < len(sub_codecs) else 'hdmv_pgs'
                # Skip non-BD-compatible subtitle codecs (DVD/VobSub, etc.)
                if codec in ('dvd_subtitle', 'dvdsub', 'dvd_sub', 'dvd'):
                    continue
                ext = sub_ext(codec)
                fmt = sub_format(codec)
                sb_args += ['-map', '0:s:{}'.format(si), '-c', 'copy']
                if fmt:
                    sb_args += ['-f', fmt]
                sb_args += [os.path.join(encode_dir, '{}_sub_{}.{}'.format(clip, actual_sub_idx, ext))]
                actual_sub_idx += 1
            if len(sb_args) > 6:  # have subtitle mappings
                if run_ff(sb_args):
                    sub_tracks = actual_sub_idx

        if audio_tracks == 0:
            sys.stderr.write('  (video-only)\n')

        # Skip the 2-pass video encode if output already exists (resumability —
        # passlogs may have been cleaned up by a prior run, but the video file
        # is valid). Audio/sub extraction above still runs so partial state
        # gets retried; only the expensive video passes are skipped.
        if os.path.isfile(out_video) and os.path.getsize(out_video) > 0:
            sys.stderr.write('    video already exists, skipping 2-pass\n')
            continue

        # Pass 1
        # Helper to detect passlog for any encoder (x264 uses -0.log, x265 may vary)
        def passlog_exists():
            return bool(glob.glob(pass_log + '*'))
        
        # Clean orphaned passlogs
        for f in glob.glob(pass_log + '*'):
            try: os.remove(f)
            except: pass
        
        sys.stderr.write('    Pass 1/2...\n')
        for attempt in range(3):
            cmd = ['ffmpeg', '-y', '-v', 'error', '-stats', '-i', src,
                   '-map', '0:v:0', '-c:v', enc_lib, '-preset', main_preset,
                   '-b:v', main_bitrate]
            if not is_hevc:
                cmd += ['-x264opts', bd_x264_opts]
            cmd += ['-pass', '1', '-passlogfile', pass_log,
                    '-an', '-f', 'null', '/dev/null']
            cmd = apply_nice(cmd)
            try:
                r = subprocess.run(cmd, timeout=None, capture_output=False)
                if r.returncode == 0:
                    break
            except:
                pass
            if passlog_exists():
                break  # stats file exists = pass 1 actually finished
            sys.stderr.write('  Pass 1 attempt {} failed - retrying\n'.format(attempt + 1))
        
        if not passlog_exists():
            sys.stderr.write('  Pass 1 failed - skipping\n')
            continue

        # Pass 2
        pass2_ok = False
        sys.stderr.write('    Pass 2/2...\n')
        for attempt in range(3):
            cmd = ['ffmpeg', '-y', '-v', 'error', '-stats', '-i', src,
                   '-map', '0:v:0', '-c:v', enc_lib, '-preset', main_preset,
                   '-b:v', main_bitrate, '-maxrate', main_maxrate, '-bufsize', main_bufsize]
            if not is_hevc:
                cmd += ['-x264opts', bd_x264_opts]
            cmd += ['-pass', '2', '-passlogfile', pass_log,
                    '-an', out_video]
            cmd = apply_nice(cmd)
            try:
                r = subprocess.run(cmd, timeout=None, capture_output=False)
                if r.returncode == 0:
                    pass2_ok = True
                    break
                # Negative returncode = killed by signal (partial output may be usable)
                if r.returncode < 0 and os.path.isfile(out_video) and os.path.getsize(out_video) > 0:
                    pass2_ok = True
                    break
            except:
                # subprocess.run itself failed (e.g. shell crash); partial output may be usable
                if os.path.isfile(out_video) and os.path.getsize(out_video) > 0:
                    pass2_ok = True
                    break
            sys.stderr.write('  Pass 2 attempt {} failed - retrying\n'.format(attempt + 1))
            for f in glob.glob(pass_log + '*'):
                try: os.remove(f)
                except: pass

        # Validate output is a valid H.264 raw stream before accepting
        if pass2_ok and os.path.isfile(out_video) and os.path.getsize(out_video) > 0:
            try:
                with open(out_video, 'rb') as vf:
                    magic = vf.read(4)
                if len(magic) >= 3 and magic[:3] not in (b'\x00\x00\x00', b'\x00\x00\x01'):
                    sys.stderr.write('  WARNING: {} may be corrupt (bad H.264 stream)\n'.format(clip))
                    pass2_ok = False
            except:
                pass2_ok = False

        if pass2_ok:
            sys.stderr.write('    done ({} audio, {} subtitle)\n'.format(audio_tracks, sub_tracks))
            for f in glob.glob(pass_log + '*'):
                try: os.remove(f)
                except: pass
        else:
            sys.stderr.write('  Pass 2 failed after 3 attempts\n')
            for f in glob.glob(pass_log + '*'):
                try: os.remove(f)
                except: pass
            # Remove corrupt output so meta construction doesn't pick it up
            try: os.remove(out_video)
            except: pass

PYEOF
log "  Encoding complete."

# ─── Phase 5: Rebuild ────────────────────────────────────────────────────────

log "Phase 5: Rebuilding BD structure..."

DST="$OUTPUT"

if $MOVIE_ONLY; then
    # ────────────────────────────────────────────────────────────────────
    # Movie-only mode: fresh BD authoring with tsMuxeR (no menus, no extras)
    # ────────────────────────────────────────────────────────────────────
    log "  Movie-only mode: authoring fresh BD..."

    # Read pre-computed Phase 5 data (builtins only, no child processes)
    read main_pl < "$WORK_DIR/.main_playlist.txt"
    read fps < "$WORK_DIR/.main_fps.txt"
    read -r main_chapters < "$WORK_DIR/.main_chapters.txt"
    main_chapters="${main_chapters%$'\n'}"

    META_DIR="$WORK_DIR/meta"
    mkdir -p "$META_DIR"
    META_FILE="$META_DIR/movie.meta"

    # Write MUXOPT header with chapters (use exec to avoid subshell)
    {
        echo "MUXOPT --no-pcr-on-video-pid --new-audio-pes --vbr --blu-ray --custom-chapters=${main_chapters}"
    } > "$META_FILE"

     # Count max audio/subtitle tracks across all clips (builtins only)
     max_audio=0
     max_subs=0
     for clip in $MAIN_CLIPS; do
         a=0; while [[ -f "$ENCODE_DIR/${clip}_audio_${a}.ac3" ]]; do ((++a)); done
         ((a > max_audio)) && max_audio=$a
         s=0
         while [[ -f "$ENCODE_DIR/${clip}_sub_${s}.sup" || -f "$ENCODE_DIR/${clip}_sub_${s}.srt" ]]; do ((++s)); done
         ((s > max_subs)) && max_subs=$s
     done
     
     # Warn if clips have differing track counts (seamless branching edge case)
     clip_count=0
     for clip in $MAIN_CLIPS; do ((++clip_count)); done
     if [[ $clip_count -gt 1 ]]; then
         audio_mismatch=false sub_mismatch=false
         for clip in $MAIN_CLIPS; do
             a=0; while [[ -f "$ENCODE_DIR/${clip}_audio_${a}.ac3" ]]; do ((++a)); done
             [[ $a -ne $max_audio ]] && audio_mismatch=true
             s=0; while [[ -f "$ENCODE_DIR/${clip}_sub_${s}.sup" || -f "$ENCODE_DIR/${clip}_sub_${s}.srt" ]]; do ((++s)); done
             [[ $s -ne $max_subs ]] && sub_mismatch=true
         done
         if $audio_mismatch || $sub_mismatch; then
             warn "Multi-clip title with track count mismatches detected (seamless branching)"
             warn "  Some clips may have gaps in audio/subtitle tracks — test playback carefully"
         fi
     fi

    # Write video tracks
    first=true
    for clip in $MAIN_CLIPS; do
        vf="$ENCODE_DIR/${clip}_video.$VIDEO_EXT"
        [[ -f "$vf" && -s "$vf" ]] || { warn "Missing/empty video for clip ${clip}"; continue; }
        if [[ "$MUX_VIDEO_TYPE" == "V_MPEGH/ISO/HEVC" ]]; then
            # tsMuxeR 2.7.0 asserts on fps= + insertSEI/contSPS for HEVC streams.
            # Let it auto-detect from the stream VPS/SPS metadata instead.
            if $first; then
                echo "$MUX_VIDEO_TYPE, \"$vf\"" >> "$META_FILE"
            else
                echo "+$MUX_VIDEO_TYPE, \"$vf\"" >> "$META_FILE"
            fi
        else
            if $first; then
                echo "$MUX_VIDEO_TYPE, \"$vf\", fps=$fps, insertSEI, contSPS" >> "$META_FILE"
            else
                echo "+$MUX_VIDEO_TYPE, \"$vf\", fps=$fps, insertSEI, contSPS" >> "$META_FILE"
            fi
        fi
        first=false
    done
    if $first; then
        die "No valid video tracks found for any main clip — check encoding output in $ENCODE_DIR"
    fi

    # Write audio tracks (grouped by track index across clips)
    for ((aidx = 0; aidx < max_audio; aidx++)); do
        first=true
        for clip in $MAIN_CLIPS; do
            af="$ENCODE_DIR/${clip}_audio_${aidx}.ac3"
            [[ -f "$af" ]] || continue
            if $first; then
                echo "A_AC3, \"$af\"" >> "$META_FILE"
            else
                echo "+A_AC3, \"$af\"" >> "$META_FILE"
            fi
            first=false
        done
    done

    # Write subtitle tracks (grouped by track index across clips)
    srt_skipped=false
    for ((sidx = 0; sidx < max_subs; sidx++)); do
        first=true
        for clip in $MAIN_CLIPS; do
            sf_sup="$ENCODE_DIR/${clip}_sub_${sidx}.sup"
            sf_srt="$ENCODE_DIR/${clip}_sub_${sidx}.srt"
            if [[ -f "$sf_sup" ]]; then
                sf="$sf_sup"; scodec="S_HDMV/PGS"
            elif [[ -f "$sf_srt" ]]; then
                # Skip SRT subtitles — tsMuxeR 2.7.0 lacks font rendering on Linux
                srt_skipped=true
                continue
            else
                continue
            fi
            if $first; then
                echo "${scodec}, \"$sf\"" >> "$META_FILE"
            else
                echo "+${scodec}, \"$sf\"" >> "$META_FILE"
            fi
            first=false
        done
    done
    if $srt_skipped; then
        warn "SRT subtitles skipped — tsMuxeR font rendering unsupported. Use PGS subs instead."
    fi

    # Dump meta file for debugging
    log "  tsMuxeR meta file:"
    while read -r meta_line; do log "    $meta_line"; done < "$META_FILE"

    log "  Running tsMuxeR..."
    run_ff tsMuxeR "$META_FILE" "$DST" > "$WORK_DIR/.tsmuxer_out.txt" 2>&1 || {
        while IFS= read -r tline; do log "    tsMuxeR: $tline"; done < "$WORK_DIR/.tsmuxer_out.txt"
        die "tsMuxeR authoring failed"
    }
    while IFS= read -r tline; do log "    tsMuxeR: $tline"; done < "$WORK_DIR/.tsmuxer_out.txt"
    log "  BDMV folder created: $DST"

    log "  Fresh BD structure complete"
else
    # ────────────────────────────────────────────────────────────────────
    # Surgical replacement mode: preserve original menus, files, structure
    # ────────────────────────────────────────────────────────────────────

    REBUILD_DIR="$WORK_DIR/rebuild"
    mkdir -p "$REBUILD_DIR"
    
    # Initialize nav-unsafe clip tracking (appended to during remuxing)
    > "$WORK_DIR/.nav_unsafe_clips.txt"

    # Build fresh BD structure (don't rm -rf "$DST" — that deletes run.log!)
    mkdir -p "$DST/BDMV/PLAYLIST" "$DST/BDMV/CLIPINF" "$DST/BDMV/STREAM"
    mkdir -p "$DST/BDMV/BACKUP/PLAYLIST" "$DST/BDMV/BACKUP/CLIPINF"
    mkdir -p "$DST/CERTIFICATE"

     # Copy source BD metadata
     run_ff cp "$SOURCE/index.bdmv" "$DST/BDMV/"
     if [[ -f "$SOURCE/MovieObject.bdmv" ]]; then run_ff cp "$SOURCE/MovieObject.bdmv" "$DST/BDMV/"; fi
    [[ -d "$SOURCE/../CERTIFICATE" ]] && run_ff cp -r "$SOURCE/../CERTIFICATE/"* "$DST/CERTIFICATE/" 2>/dev/null || true
    [[ -d "$SOURCE/CERTIFICATE" ]] && run_ff cp -r "$SOURCE/CERTIFICATE/"* "$DST/CERTIFICATE/" 2>/dev/null || true

    # Discover which clips were re-encoded (builtins only)
    ENCODED_CLIP_IDS=""
    encode_dir="$WORK_DIR/encode"
    for f in "$encode_dir/"*_video.$VIDEO_EXT; do
        [[ -f "$f" ]] || continue
        fname="${f##*/}"
        cid="${fname%_video.$VIDEO_EXT}"
        ENCODED_CLIP_IDS+="$cid "
    done

    encoded_count=0
    log "Re-encoded clips:"
    for cid in $ENCODED_CLIP_IDS; do
        [[ -n "$cid" ]] || continue
        [[ -f "$encode_dir/${cid}_video.$VIDEO_EXT" ]] || continue
        [[ -f "$DST/BDMV/STREAM/${cid}.m2ts" ]] && continue
        ((++encoded_count))
        fps=${CLIP_FPS[$cid]:-24000/1001}

        # Count audio / subtitle tracks in encoded output
        a=0; while [[ -f "$encode_dir/${cid}_audio_${a}.ac3" ]]; do ((++a)); done
        s=0; while [[ -f "$encode_dir/${cid}_sub_${s}.sup" ]]; do ((++s)); done

        # Build tsMuxeR meta file (builtins only)
        meta="$REBUILD_DIR/${cid}.meta"
        tmpout="$REBUILD_DIR/${cid}_output"
        {
            echo "MUXOPT --no-pcr-on-video-pid --new-audio-pes --vbr --blu-ray"
            if [[ "$MUX_VIDEO_TYPE" == "V_MPEGH/ISO/HEVC" ]]; then
                # tsMuxeR 2.7.0 asserts on fps= + insertSEI/contSPS for HEVC streams.
                echo "$MUX_VIDEO_TYPE, \"$encode_dir/${cid}_video.$VIDEO_EXT\""
            else
                echo "$MUX_VIDEO_TYPE, \"$encode_dir/${cid}_video.$VIDEO_EXT\", fps=$fps, insertSEI, contSPS"
            fi
            aidx=0
            while [[ -f "$encode_dir/${cid}_audio_${aidx}.ac3" ]]; do
                echo "A_AC3, \"$encode_dir/${cid}_audio_${aidx}.ac3\""
                ((++aidx))
            done
            sidx=0
            while [[ -f "$encode_dir/${cid}_sub_${sidx}.sup" ]]; do
                echo "S_HDMV/PGS, \"$encode_dir/${cid}_sub_${sidx}.sup\""
                ((++sidx))
            done
        } > "$meta"

        log "  Remuxing: ${cid}.m2ts ($a audio, $s subtitle)"
        mkdir -p "$tmpout" 2>/dev/null || true
        run_ff tsMuxeR "$meta" "$tmpout" || {
            warn "tsMuxeR failed for clip ${cid}"
            continue
        }

         # Copy remuxed output to destination
         new_m2ts=("$tmpout/BDMV/STREAM/"*.m2ts)
         new_clpi=("$tmpout/BDMV/CLIPINF/"*.clpi)
          if [[ -n "${new_m2ts[0]:-}" ]] && [[ -n "${new_clpi[0]:-}" ]]; then
              # Hard-fail if tsMuxeR split the clip into multiple parts (unsupported in surgical mode)
              if [[ ${#new_m2ts[@]} -gt 1 ]]; then
                  die "tsMuxeR split clip ${cid} into ${#new_m2ts[@]} parts. Surgical mode cannot handle multi-part clips. Use --movie-only mode instead."
              fi
              run_ff cp "$new_m2ts" "$DST/BDMV/STREAM/${cid}.m2ts" || true
              run_ff cp "$new_clpi" "$DST/BDMV/CLIPINF/${cid}.clpi" || true
              
              # Check if remux changed CLPI PIDs or stream coding types (navigation safety)
              python3 - "$SOURCE/CLIPINF/${cid}.clpi" "$DST/BDMV/CLIPINF/${cid}.clpi" "$WORK_DIR/.nav_unsafe_clips.txt" "$cid" << 'CLPI_DIFF'
import sys, struct, os
try:
    src_clpi = sys.argv[1]
    dst_clpi = sys.argv[2]
    unsafe_file = sys.argv[3]
    clip_id = sys.argv[4]
    
    def parse_clpi_streams(clpi_path):
        """Extract PIDs and stream_coding_types from CLPI ProgramInfo."""
        if not os.path.isfile(clpi_path):
            return set()
        try:
            with open(clpi_path, 'rb') as f:
                data = f.read()
            if len(data) < 20 or data[:4] != b'CLPI':
                return set()
            # ProgramInfo offset at bytes 8-11 (big-endian)
            if len(data) < 12:
                return set()
            prog_offset = struct.unpack_from('>I', data, 8)[0]
            if prog_offset < 20 or prog_offset + 10 > len(data):
                return set()
            # StreamCodingInfo loop: offset at prog_offset+8
            sdi_offset = struct.unpack_from('>H', data, prog_offset + 8)[0]
            if sdi_offset + prog_offset + 2 > len(data):
                return set()
            sdi_base = prog_offset + sdi_offset
            num_streams = struct.unpack_from('>B', data, sdi_base)[0] if sdi_base < len(data) else 0
            streams = set()
            off = sdi_base + 1
            for _ in range(num_streams):
                if off + 5 > len(data):
                    break
                pid = struct.unpack_from('>H', data, off)[0]
                coding_type = data[off + 2] if off + 2 < len(data) else 0
                streams.add((pid, coding_type))
                sdi_len = struct.unpack_from('>B', data, off + 3)[0] if off + 3 < len(data) else 0
                off += 4 + sdi_len
            return streams
        except Exception:
            return set()
    
    src_streams = parse_clpi_streams(src_clpi)
    dst_streams = parse_clpi_streams(dst_clpi)
    
    # If PID or coding-type sets differ, flag as navigation-unsafe
    if src_streams and dst_streams and src_streams != dst_streams:
        with open(unsafe_file, 'a', encoding='utf-8') as f:
            f.write(f'{clip_id}\n')
except Exception:
    pass
CLPI_DIFF
              
              log "    done: ${cid}.m2ts"
         else
             warn "tsMuxeR produced no output for ${cid}"
         fi
    done

    # Build map of extra clip IDs for --no-extras filtering
    declare -A EXTRA_CLIP_MAP
    if $NO_EXTRAS; then
        for cid in $EXTRAS_CLIPS; do
            [[ -n "$cid" ]] && EXTRA_CLIP_MAP["$cid"]=1
        done
    fi

    # Copy un-encoded clips verbatim (builtins only)
    log "Copying un-encoded clips..."
    for cid in $ALL_CLIP_IDS; do
        [[ -n "$cid" ]] || continue
        [[ -f "$DST/BDMV/STREAM/${cid}.m2ts" ]] && continue
        # Skip extras when --no-extras is set
        if $NO_EXTRAS && [[ -n "${EXTRA_CLIP_MAP[$cid]:-}" ]]; then
            continue
        fi
        # Check if this clip was encoded (remux already placed it)
        was_encoded=false
        for ecid in $ENCODED_CLIP_IDS; do
            [[ "$ecid" == "$cid" ]] && was_encoded=true && break
        done
        if $was_encoded; then
            # Fallback: copy original
            [[ -f "$DST/BDMV/STREAM/${cid}.m2ts" ]] && continue
            warn "  Falling back to original for ${cid}.m2ts"
        fi
        run_ff cp "$SOURCE/STREAM/${cid}.m2ts" "$DST/BDMV/STREAM/${cid}.m2ts" || true
        run_ff cp "$SOURCE/CLIPINF/${cid}.clpi" "$DST/BDMV/CLIPINF/${cid}.clpi" || true
    done

    # Copy orphan clips — clips in source STREAM/CLIPINF not referenced by any
    # playlist but still part of the BD structure (e.g. FirstPlayback assets
    # referenced directly by MovieObjects or BD-J objects)
    for m2ts in "$SOURCE/STREAM/"*.m2ts; do
        [[ -f "$m2ts" ]] || continue
        fname="${m2ts##*/}"
        cid="${fname%.m2ts}"
        [[ -f "$DST/BDMV/STREAM/${cid}.m2ts" ]] && continue
        log "  Copying orphan clip: ${cid}.m2ts"
        run_ff cp "$m2ts" "$DST/BDMV/STREAM/${cid}.m2ts" || true
        [[ -f "$SOURCE/CLIPINF/${cid}.clpi" ]] && run_ff cp "$SOURCE/CLIPINF/${cid}.clpi" "$DST/BDMV/CLIPINF/${cid}.clpi" || true
    done

    # Copy all MPLS files
    run_ff cp "$SOURCE/PLAYLIST/"*.mpls "$DST/BDMV/PLAYLIST/" 2>/dev/null || true

    # Copy extra metadata directories
    for dir in AUXDATA META BDJO JAR; do
        [[ -d "$SOURCE/$dir" ]] && run_ff cp -r "$SOURCE/$dir" "$DST/BDMV/" 2>/dev/null || true
    done

     # Copy BACKUP
     run_ff cp "$SOURCE/index.bdmv" "$DST/BDMV/BACKUP/" || true
     if [[ -f "$SOURCE/MovieObject.bdmv" ]]; then run_ff cp "$SOURCE/MovieObject.bdmv" "$DST/BDMV/BACKUP/"; fi
    run_ff cp "$SOURCE/BACKUP/PLAYLIST/"*.mpls "$DST/BDMV/BACKUP/PLAYLIST/" 2>/dev/null || true
    run_ff cp "$DST/BDMV/CLIPINF/"*.clpi "$DST/BDMV/BACKUP/CLIPINF/" 2>/dev/null || true

fi  # end if MOVIE_ONLY

# Post-rebuild check: if surgical mode produced nav-unsafe clips, fail loudly
if ! $MOVIE_ONLY && [[ -f "$WORK_DIR/.nav_unsafe_clips.txt" ]]; then
    nav_unsafe_clips=$(grep -v '^$' "$WORK_DIR/.nav_unsafe_clips.txt" | sort -u)
    if [[ -n "$nav_unsafe_clips" ]]; then
        die "Surgical rebuild produced stream layout changes that will break menu navigation.
Affected clips: $(echo $nav_unsafe_clips | tr '\n' ' ')

Re-encoding lossless audio and changing stream PIDs requires rewriting MPLS metadata.
This feature is not yet implemented in surgical mode.

SOLUTION: Use --movie-only mode instead:
  ./bd_shrink.sh -s /path/to/BDMV -o /output -f --movie-only

The --movie-only mode creates a fresh BD structure with working menus and all content.
It is the recommended and tested path for discs with complex menu systems."
    fi
fi

# Compute ISO/disc volume label from source title (ISO9660 rules: uppercase, no spaces)
ISO_LABEL=$(get_source_title "$SOURCE")
ISO_LABEL="${ISO_LABEL^^}"
ISO_LABEL="${ISO_LABEL// /_}"
ISO_LABEL="${ISO_LABEL//[^A-Za-z0-9_]/}"
ISO_LABEL="${ISO_LABEL:0:32}"
[[ -z "$ISO_LABEL" ]] && ISO_LABEL="BD_SHRINK"

# Ensure CERTIFICATE exists before ISO/burn phases (tsMuxeR creates it with --blu-ray,
# but this guarantees it for all paths including edge cases)
mkdir -p "$DST/CERTIFICATE"

# Create ISO if explicitly requested (--iso)
if $OUTPUT_ISO; then
    if [[ "$OUTPUT" == *.iso ]]; then
        ISO_OUT="$OUTPUT"
    else
        iso_title=$(get_source_title "$SOURCE")
        ISO_OUT="${OUTPUT%/}/${iso_title}.iso"
    fi
    log "Creating ISO: ${ISO_OUT}..."

    # Only include BDMV and CERTIFICATE in the ISO; never include the .work directory
    if command -v genisoimage &>/dev/null; then
        run_ff genisoimage -udf -allow-limited-size -V "$ISO_LABEL" -o "$ISO_OUT" \
            -graft-points BDMV="$DST/BDMV" CERTIFICATE="$DST/CERTIFICATE" 2>/dev/null || {
            warn "ISO creation failed with genisoimage"
        }
    elif command -v mkisofs &>/dev/null; then
        run_ff mkisofs -udf -allow-limited-size -V "$ISO_LABEL" -o "$ISO_OUT" \
            -graft-points BDMV="$DST/BDMV" CERTIFICATE="$DST/CERTIFICATE" 2>/dev/null || {
            warn "ISO creation failed with mkisofs"
        }
    elif command -v xorriso &>/dev/null; then
        run_ff xorriso -outdev "$ISO_OUT" -volid "$ISO_LABEL" \
            -map "$DST/BDMV" /BDMV -map "$DST/CERTIFICATE" /CERTIFICATE -commit 2>/dev/null || {
            warn "ISO creation failed with xorriso"
        }
    else
        warn "No ISO creation tool found (genisoimage/mkisofs/xorriso). Install one and run:"
        warn "  genisoimage -udf -allow-limited-size -V '${ISO_LABEL}' -o ${ISO_OUT} \\"
        warn "    -graft-points BDMV=${DST}/BDMV CERTIFICATE=${DST}/CERTIFICATE"
    fi

    if [[ ! -f "$ISO_OUT" ]]; then
        die "ISO creation failed"
    fi
fi

# ─── Phase 6: Validate ───────────────────────────────────────────────────────

log "Phase 6: Validating output..."

# Check output exists
[[ ! -e "$DST" ]] && die "Output does not exist: $DST"

if [[ -d "$DST" ]] && [[ -d "$DST/BDMV" ]]; then
    log "Verifying BDMV structure..."

    # Count and verify files (builtins only)
    m2ts_count=0
    missing_clpi=0
    for m2ts in "$DST/BDMV/STREAM/"*.m2ts; do
        [[ -f "$m2ts" ]] || continue
        ((++m2ts_count))
        fname="${m2ts##*/}"
        cid="${fname%.m2ts}"
        if [[ ! -f "$DST/BDMV/CLIPINF/${cid}.clpi" ]]; then
            warn "Missing CLPI for ${cid}.m2ts"
            ((++missing_clpi))
        fi
    done

    if [[ $m2ts_count -eq 0 ]]; then
        warn "No M2TS files found in STREAM/"
    else
        log "STREAM: $m2ts_count file(s) — ${missing_clpi} missing CLPI"
    fi
fi

# Final output size check
if $OUTPUT_ISO && [[ -f "$ISO_OUT" ]]; then
    # For ISO, check the actual ISO file size
    output_bytes=$(du -sb "$ISO_OUT" | cut -f1)
    oversized=$(echo "$output_bytes > $TARGET_GB * 1073741824" | bc)
    if [[ "$oversized" -eq 1 ]]; then
        output_gb=$(echo "scale=2; $output_bytes / 1073741824" | bc)
        warn "ISO file size (${output_gb} GB) exceeds target (${TARGET_GB} GB)"
    fi
elif [[ -e "$DST" ]]; then
    # For BDMV folder, check the folder size
    output_bytes=$(du -sb "$DST" | cut -f1)
    oversized=$(echo "$output_bytes > $TARGET_GB * 1073741824" | bc)
    if [[ "$oversized" -eq 1 ]]; then
        output_gb=$(echo "scale=2; $output_bytes / 1073741824" | bc)
        warn "Output size (${output_gb} GB) exceeds target (${TARGET_GB} GB)"
    fi
fi

# ─── Burn ──────────────────────────────────────────────────────────────────────
if $BURN; then
    burn_output
fi

# ─── Done ─────────────────────────────────────────────────────────────────────
log "Done! Output: $DST"
if $HAS_BDJ && ! $MOVIE_ONLY; then
    warn "BD-J disc — test in a software player (VLC/mpv) before burning."
elif $MOVIE_ONLY; then
    info "Movie-only — no BD-J concerns."
fi

# Cleanup
if $CLEAN_WORK; then
    rm -rf "$WORK_DIR" 2>/dev/null || true
    log "Working directory removed: $WORK_DIR"
else
    [[ -n "${REBUILD_DIR:-}" ]] && rm -rf "$REBUILD_DIR" 2>/dev/null || true
    log "Working files retained at: $WORK_DIR (delete manually when satisfied)"
fi

# Clean up ISO mount/extract if applicable
cleanup_iso

if ! $BURN; then
    log "IMPORTANT: Test the output in VLC or mpv before burning to disc!"
fi
