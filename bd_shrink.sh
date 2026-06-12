#!/usr/bin/env zsh
set -euo pipefail
# Make word splitting on unquoted $var behave like bash (split on IFS)
setopt SH_WORD_SPLIT
# Don't error when a glob matches nothing (like bash shopt -s nullglob)
setopt NULL_GLOB

VERSION="0.1.0"
SCRIPT_DIR="$(cd "$(dirname "${0}")" && pwd)"

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

# ─── helpers ─────────────────────────────────────────────────────────────────
die()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN:  $*" >&2; }
log()  { printf '[%02d:%02d:%02d] %s\n' $((SECONDS/3600)) $(((SECONDS%3600)/60)) $((SECONDS%60)) "$*"; }
info() { echo "       $*"; }
trap 'echo "FATAL: line $LINENO exit $?" >&2' ERR

# Run ffmpeg via systemd-run --user --wait.
# The command runs as a transient systemd service, NOT as a child of bash.
# Even if the shell crashes, the service continues to completion.
run_ff() {
    systemd-run --user --wait -q -u "bd_ff.${RANDOM}.$$" -- "$@"
}

usage() {
    cat <<EOF
bd_shrink.sh v${VERSION} — shrink BD50 → BD25 with menu preservation

Usage:  bd_shrink.sh -s <source> -o <output> [options]

Required:
  -s, --source DIR|FILE   Source BDMV folder (must contain index.bdmv) or .mkv file
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
  -h, --help             Show this help
EOF
    exit 1
}

check_deps() {
    local missing=()
    for cmd in run_ff ffmpeg ffprobe tsMuxeR bc python3; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Missing required tools: ${missing[*]}"
    fi
    if ! python3 -c "import json, struct, os, sys" 2>/dev/null; then
        die "Python3 missing standard library modules"
    fi
}

# ─── arg parsing ─────────────────────────────────────────────────────────────
SOURCE=""
OUTPUT=""
WORK_DIR=""
MAIN_AUDIO_BITRATE="640k"
NO_EXTRAS=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--source)       SOURCE="$2"; shift 2 ;;
        -o|--output)       OUTPUT="$2"; shift 2 ;;
        -t|--target)       TARGET_GB="$2"; shift 2 ;;
        --no-extras)       NO_EXTRAS=true; shift ;;
        --keep-one)        KEEP_ONE=true; shift ;;
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
        -h|--help)         usage ;;
        *)                 die "Unknown option: $1" ;;
    esac
done

# --movie-only implies --keep-one (only the first main playlist is encoded)
if $MOVIE_ONLY; then
    KEEP_ONE=true
fi

[[ -z "$SOURCE" ]] && die "Source folder required (-s)"
[[ -z "$OUTPUT" ]] && die "Output folder required (-o)"

MKV_INPUT=false
if [[ -f "$SOURCE" ]] && [[ "$SOURCE" == *.mkv ]]; then
    MKV_INPUT=true
    MOVIE_ONLY=true
elif [[ -f "$SOURCE" ]]; then
    die "Source must be a BDMV folder (with index.bdmv) or a .mkv file"
fi

if ! $MKV_INPUT; then
    [[ ! -f "$SOURCE/index.bdmv" ]] && die "Source must contain index.bdmv (point to the BDMV folder)"
    [[ ! -d "$SOURCE/PLAYLIST" ]] && die "Source must contain PLAYLIST/ directory"
    [[ ! -d "$SOURCE/STREAM" ]] && die "Source must contain STREAM/ directory"
fi

if [[ -d "$OUTPUT" ]] && ! $FORCE; then
    die "Output directory exists. Use -f to overwrite."
fi

if [[ -z "$WORK_DIR" ]]; then
    WORK_DIR="${OUTPUT}.work"
fi
mkdir -p "$WORK_DIR"

# Detect BD-J
HAS_BDJ=false
if ! $MKV_INPUT && [[ -d "$SOURCE/BDJO" || -d "$SOURCE/JAR" ]]; then
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
    # MKV: create synthetic playlists.json with single clip "00000"
    python3 -c "
import json
# Get MKV duration via ffprobe
import subprocess
r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', '$SOURCE'], capture_output=True, text=True, timeout=30)
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
" > "$WORK_DIR/playlists.json"
else
    parse_mpls "$SOURCE/PLAYLIST" > "$WORK_DIR/playlists.json"
fi

# Probe all unique clips
declare -A PROBED_CLIPS

all_clips=$(python3 -c "
import json, sys
with open('$WORK_DIR/playlists.json') as f:
    data = json.load(f)
clips = set()
for pl in data.values():
    for item in pl['playitems']:
        clips.add(item['clip'])
for c in sorted(clips):
    print(c)
")

log "Found $(echo "$all_clips" | wc -l) unique M2TS clips"

# Convert bash booleans to Python-safe strings
if $KEEP_ONE; then PY_KEEP_ONE="True"; else PY_KEEP_ONE="False"; fi
if $MOVIE_ONLY; then PY_MOVIE_ONLY="True"; else PY_MOVIE_ONLY="False"; fi
if $NO_EXTRAS; then PY_NO_EXTRAS="True"; else PY_NO_EXTRAS="False"; fi
if $FORCE; then PY_FORCE="True"; else PY_FORCE="False"; fi

CLIPS_DIR="$WORK_DIR/clips"
mkdir -p "$CLIPS_DIR"

if $MKV_INPUT; then
    # MKV: ffprobe the source file directly as clip "00000"
    python3 -c "
import json, subprocess
r = subprocess.run(['ffprobe', '-v', 'error',
    '-show_entries', 'stream=index,codec_type,codec_name,width,height,duration,r_frame_rate,channels,channel_layout,sample_rate,bit_rate',
    '-show_entries', 'format=size,duration,bit_rate',
    '-of', 'json', '$SOURCE'],
    capture_output=True, text=True, timeout=60)
data = json.loads(r.stdout) if r.stdout else {'streams': [], 'format': {}}
with open('$CLIPS_DIR/00000.json', 'w') as f:
    json.dump(data, f)
"
else
    python3 - "$SOURCE" "$CLIPS_DIR" << 'PYEOF'
import json, os, subprocess, sys

source = sys.argv[1]
clips_dir = sys.argv[2]

# Read playlists to get clip IDs
playlists = json.load(open(os.path.join(os.path.dirname(clips_dir), 'playlists.json')))
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
    with open(out_path, 'w') as f:
        json.dump(data, f)
PYEOF
fi

# Build combined inventory
python3 << PYEOF > "$INVENTORY_FILE"
import json, os, sys

playlists = json.load(open('$WORK_DIR/playlists.json'))
clips_dir = '$CLIPS_DIR'

# Summarize each unique clip
clip_summaries = {}
for clip_name in os.listdir(clips_dir):
    if not clip_name.endswith('.json'):
        continue
    clip_id = clip_name.replace('.json', '')
    path = os.path.join(clips_dir, clip_name)
    try:
        data = json.load(open(path))
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
    }
    # Calculate total size from unique clips
    total_size = 0
    seen_clips = set()
    for item in pl_data['playitems']:
        cid = item['clip']
        if cid in seen_clips:
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

log "Inventory complete: $(python3 -c "import json; d=json.load(open('$INVENTORY_FILE')); print(f\"{d['disc_size_gb']} GB, {len(d['clips'])} clips, {len(d['playlists'])} playlists\")")"

# ─── Phase 2: Classify ───────────────────────────────────────────────────────

log "Phase 2: Classifying content..."

CLASSIFY_FILE="$WORK_DIR/classify.json"
python3 << PYEOF > "$CLASSIFY_FILE"
import json, sys

inv = json.load(open('$INVENTORY_FILE'))
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

    # Check if any referenced clip has video
    has_video = False
    is_hd = False
    for c in clip_list:
        cs = clips.get(c, {})
        for v in cs.get('video', []):
            has_video = True
            if v.get('height', 0) and v['height'] >= 720:
                is_hd = True

    if dur < 5 and not has_video:
        menu_pls.append(pl_name)
    elif dur >= 30:
        extras_pls.append(pl_name)
    else:
        # Short clips with video -> menu animations/transitions
        menu_pls.append(pl_name)

# Re-classify: the LARGEST long playlist(s) are the main movie.
# Real movies have both long duration AND large size. Some discs contain
# bogus playlists that repeat a short clip many times, giving them a huge
# duration but tiny size; these must not be selected as the main movie.
# Playlists with similar durations to the size-based main movie are treated
# as alternate cuts or angles.
if extras_pls:
    movie_candidates = [pl_name for pl_name in extras_pls
                        if playlists[pl_name]['duration'] >= 600]
    if movie_candidates:
        # Pick the candidate with the largest total size as the main movie
        main_movie_pls = [max(movie_candidates,
                              key=lambda x: playlists[x].get('total_size_mb', 0))]
        main_dur = playlists[main_movie_pls[0]]['duration']

        # Include other candidates with similar duration as alternate cuts/angles
        new_extras = []
        for pl_name in extras_pls:
            if pl_name == main_movie_pls[0]:
                continue
            pl_dur = playlists[pl_name]['duration']
            # Similar duration to main movie -> alternate cut/angle
            if main_dur * 0.70 <= pl_dur <= main_dur * 1.30:
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
for pl_name in pl_sorted:
    pl_data = pl_name[1] if isinstance(pl_name, tuple) else playlists[pl_name]
    if isinstance(pl_name, tuple):
        pl_name, pl_data = pl_name
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

if $PY_KEEP_ONE:
    classification['main_movie'] = main_movie_pls[:1]
    # Move the others to extras (they'll be skipped or encoded)
    for pl in main_movie_pls[1:]:
        classification['extras'].append(pl)
        details[pl]['category'] = 'extras'

json.dump(classification, sys.stdout, indent=2)
PYEOF

MAIN_PLAYLISTS=($(python3 -c "import json; d=json.load(open('$CLASSIFY_FILE')); print(' '.join(d['main_movie']))"))
EXTRAS_PLAYLISTS=($(python3 -c "import json; d=json.load(open('$CLASSIFY_FILE')); print(' '.join(d['extras']))"))
MENU_PLAYLISTS=($(python3 -c "import json; d=json.load(open('$CLASSIFY_FILE')); print(' '.join(d['menus']))"))

log "Main movie: ${#MAIN_PLAYLISTS[@]} playlist(s)"
for pl in "${MAIN_PLAYLISTS[@]}"; do
    info "$pl — $(python3 -c "import json; d=json.load(open('$CLASSIFY_FILE')); print(d['details']['$pl']['duration_str'])" 2>/dev/null || echo '?')"
done
log "Extras: ${#EXTRAS_PLAYLISTS[@]} playlist(s)"
log "Menus: ${#MENU_PLAYLISTS[@]} playlist(s) + $(python3 -c "import json; d=json.load(open('$CLASSIFY_FILE')); print(len(d['orphans']))") orphan clips"

# ─── Phase 3: Budget ─────────────────────────────────────────────────────────

log "Phase 3: Calculating space budget..."

BUDGET_FILE="$WORK_DIR/budget.json"
python3 << PYEOF > "$BUDGET_FILE"
import json, sys

inv = json.load(open('$INVENTORY_FILE'))
clf = json.load(open('$CLASSIFY_FILE'))
clips = inv['clips']

target_bytes = $TARGET_GB * 1073741824
overhead_bytes = $OVERHEAD_MB * 1048576
target_available = target_bytes - overhead_bytes
extras_scale = "$EXTRAS_SCALE"

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
extras_clips = clips_for_playlists(extras_pls)
menu_clips = clips_for_playlists(menu_pls)

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

    audio_bitrate = int('$EXTRAS_AUDIO_BITRATE'.replace('k', '')) * 1000
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

# Available for main movie
if $PY_MOVIE_ONLY:
    # Movie-only: all space goes to main movie
    available_for_main = target_available
else:
    available_for_main = target_available - total_menu_size - extras_reencoded_size

# Calculate main movie bitrate
total_main_dur = 0
for pl_name in main_pls:
    total_main_dur += clf['details'][pl_name]['duration']
    if $PY_MOVIE_ONLY:
        break  # movie-only only encodes the first playlist

main_audio_size = 0
main_audio_tracks = 0
if total_main_dur > 0:
    # Audio overhead: extraction always attempts 8 tracks
    # (matching seq 0 7 in Phase 4), 1 at MAIN, 7 at COMMENTARY bitrate
    for i in range(8):
        if i == 0:
            rate = int('$MAIN_AUDIO_BITRATE'.replace('k', '')) * 1000
        else:
            main_audio_tracks += 1
            rate = int('$COMMENTARY_AUDIO_BITRATE'.replace('k', '')) * 1000
        main_audio_size += int((rate * total_main_dur) / 8)

main_bitrate = 0
if total_main_dur > 0:
    # Subtract audio overhead + 5% safety margin for x264 overshoot
    main_video_available = int((available_for_main - main_audio_size) * 0.95)
    main_bitrate = max(1000000, int((main_video_available * 8) / total_main_dur))

budget = {
    'target_gb': $TARGET_GB,
    'target_bytes': target_bytes,
    'overhead_mb': $OVERHEAD_MB,
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
    'main_audio_tracks': main_audio_tracks + 1,
    'commentary_tracks': main_audio_tracks,
    'commentary_bitrate': int('$COMMENTARY_AUDIO_BITRATE'.replace('k', '')),
    'extras_details': extras_clip_details,
}

json.dump(budget, sys.stdout, indent=2)
PYEOF

log "Budget summary:"
python3 -c "
import json
b = json.load(open('$BUDGET_FILE'))
print(f\"  Target:            {b['target_gb']} GB\")
print(f\"  Menu (passthru):   {b['menu_size_mb']} MB\")
print(f\"  Extras original:   {b['extras_original_mb']} MB\")
print(f\"  Extras estimated:  {b['extras_estimated_mb']} MB\")
print(f\"  Main original:     {b['main_original_mb']} MB\")
print(f\"  Main available:    {b['main_available_mb']} MB\")
print(f\"  Main bitrate:      {b['main_bitrate_mbps']} Mbps\")
print(f\"  Main duration:     {b['main_duration_str']}\")
" 2>/dev/null

if $DRY_RUN; then
    log "Dry run complete. Summary above."
    log "Work dir: $WORK_DIR"
    exit 0
fi

# ─── Phase 4: Encode ─────────────────────────────────────────────────────────

log "Phase 4: Encoding..."

ENCODE_DIR="$WORK_DIR/encode"
mkdir -p "$ENCODE_DIR"

# Pre-compute ALL data for Phase 4 + Phase 5 in a single systemd-run call (no shell child processes)
systemd-run --user --wait -q -u "bd_pre.${RANDOM}.$$" -- python3 -c "
import json, os

data = json.load(open('$INVENTORY_FILE'))
budget = json.load(open('$BUDGET_FILE'))
classify = json.load(open('$CLASSIFY_FILE'))
base = int(budget['main_bitrate'])

# --- Phase 4: clip metadata ---
with open('$WORK_DIR/.clip_precompute.txt', 'w') as f:
    for cid, c in data.get('clips', {}).items():
        aud = len(c.get('audio', []))
        sub = len(c.get('subtitles', []))
        vs = c.get('video', [{}])[0] if c.get('video') else {}
        h = vs.get('height', 1080) or 1080
        w = vs.get('width', 1920) or 1920
        f.write(f'{cid}|{aud}|{sub}|{h}|{w}\n')

# --- Phase 4: budget values ---
with open('$WORK_DIR/.budget_values.txt', 'w') as f:
    f.write(f'{base}\n{int(base * 1.1)}\n{int(base * 1.5)}\n')

# --- Phase 4: extras clips ---
with open('$WORK_DIR/.extras_clips.txt', 'w') as f:
    for cid, cd in budget['extras_details'].items():
        if cd['will_reencode']:
            f.write(f'{cid}\n')

# --- Phase 4: main clips + Phase 5 data ---
main_clips = []
for pl_name in classify['main_movie']:
    pd = classify['details'].get(pl_name, {})
    main_clips = pd.get('clips', [])
    break  # first main playlist

with open('$WORK_DIR/.main_clips.txt', 'w') as f:
    for cid in main_clips:
        f.write(f'{cid}\n')

# --- Phase 5: main playlist name ---
main_pl_name = classify['main_movie'][0] if classify['main_movie'] else ''
with open('$WORK_DIR/.main_playlist.txt', 'w') as f:
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
with open('$WORK_DIR/.main_chapters.txt', 'w') as f:
    f.write(';'.join(chapters) + '\n')

# --- Phase 5: FPS of all clips (surgical mode needs per-clip FPS) ---
with open('$WORK_DIR/.clip_fps.txt', 'w') as f:
    for cid in data.get('clips', {}):
        fps = '24000/1001'
        clip_json = os.path.join('$CLIPS_DIR', f'{cid}.json')
        try:
            d = json.load(open(clip_json))
            for s in d.get('streams', []):
                if s.get('codec_type') == 'video':
                    fps = s.get('r_frame_rate', '24000/1001')
                    break
        except:
            pass
        f.write(f'{cid}|{fps}\n')

# --- Phase 5: main FPS (from first main clip) ---
main_fps = '24000/1001'
if main_clips:
    first_cid = main_clips[0]
    clip_json = os.path.join('$CLIPS_DIR', f'{first_cid}.json')
    try:
        d = json.load(open(clip_json))
        for s in d.get('streams', []):
            if s.get('codec_type') == 'video':
                main_fps = s.get('r_frame_rate', '24000/1001')
                break
    except:
        pass
with open('$WORK_DIR/.main_fps.txt', 'w') as f:
    f.write(f'{main_fps}\n')

# --- Phase 5: all clip IDs (surgical mode needs the full list) ---
with open('$WORK_DIR/.all_clips.txt', 'w') as f:
    for cid in sorted(data.get('clips', {}).keys()):
        f.write(f'{cid}\n')

# --- Phase 5: main clip count, max audio, max subtitle ---
with open('$WORK_DIR/.main_counts.txt', 'w') as f:
    f.write(f'{len(main_clips)}\n')
    f.write('0\n0\n')  # placeholder, recalculated in Phase 5 after encoding
"

# Read clip metadata into associative arrays (builtins only, no child processes)
declare -A CLIP_AUD CLIP_SUB CLIP_HEIGHT CLIP_WIDTH
while IFS='|' read -r cid aud sub h w; do
    CLIP_AUD[$cid]=$aud
    CLIP_SUB[$cid]=$sub
    CLIP_HEIGHT[$cid]=$h
    CLIP_WIDTH[$cid]=$w
done < "$WORK_DIR/.clip_precompute.txt"

# Read budget values (builtins only)
{
    read MAIN_BITRATE
    read MAIN_MAXRATE
    read MAIN_BUFSIZE
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

# Common x264 BD-compat opts
BD_X264_OPTS="bluray-compat=1:vbv-maxrate=40000:vbv-bufsize=30000"

# Encode extras (single-pass CRF, downscale to 720p)
if [[ -n "$EXTRAS_CLIPS" ]] && ! $NO_EXTRAS && ! $MOVIE_ONLY; then
    log "Encoding extras..."
    # Trim trailing newline from clip list
    EXTRAS_CLIPS="${EXTRAS_CLIPS%$'\n'}"
fi

# Encode main movie (two-pass VBR)
if [[ -n "$MAIN_CLIPS" ]]; then
    log "Encoding main movie..."
    # Trim trailing newline from clip list
    MAIN_CLIPS="${MAIN_CLIPS%$'\n'}"
    log "  Bitrate: $(( MAIN_BITRATE / 1000000 )) Mbps, preset: $MAIN_PRESET"
fi

# Run ALL encoding in a single Python process.
# Single child process instead of dozens — drastically reduces SIGCHLD crash risk.
python3 -u << PYEOF
import json, os, subprocess, sys

src_dir = '$SOURCE/STREAM'
mkv_src = '$SOURCE'  # unused for BDMV input
mkv_input = '$MKV_INPUT' == 'true'
encode_dir = '$ENCODE_DIR'
work_dir = '$WORK_DIR'
clips_dir = '$CLIPS_DIR'
inventory_file = '$INVENTORY_FILE'
bd_x264_opts = '$BD_X264_OPTS'
main_preset = '$MAIN_PRESET'
main_bitrate = '$MAIN_BITRATE'
main_maxrate = '$MAIN_MAXRATE'
main_bufsize = '$MAIN_BUFSIZE'
main_audio_bitrate = '$MAIN_AUDIO_BITRATE'
commentary_audio_bitrate = '$COMMENTARY_AUDIO_BITRATE'
extras_audio_bitrate = '$EXTRAS_AUDIO_BITRATE'
extras_crf = '$EXTRAS_CRF'
extras_scale = '$EXTRAS_SCALE'
extras_clips_str = """$EXTRAS_CLIPS"""
main_clips_str = """$MAIN_CLIPS"""
no_extras = '$NO_EXTRAS' == 'true'
movie_only = '$MOVIE_ONLY' == 'true'

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
        d = json.load(open(cpath))
        subs = []
        for s in d.get('streams', []):
            if s.get('codec_type') == 'subtitle':
                subs.append(s.get('codec_name', '?'))
        return subs
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
        import glob
        matches = glob.glob(pass_log_base + '*')
        if matches:
            return True
    try:
        r = subprocess.run(cmd, timeout=None, capture_output=False)
        if r.returncode == 0:
            return True
        if out_file and os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
            return True
        if pass_log_base:
            import glob
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
with open('{}/.clip_precompute.txt'.format(work_dir)) as f:
    for line in f:
        line = line.strip()
        if not line: continue
        parts = line.split('|')
        clip_data[parts[0]] = {'aud': int(parts[1]), 'sub': int(parts[2]), 'h': int(parts[3]), 'w': int(parts[4])}

# --- Encode extras ---
if not no_extras and not movie_only:
    for i, clip in enumerate(extras_clips):
        src = clip_source(clip)
        out_video = os.path.join(encode_dir, '{}_video.h264'.format(clip))
        cd = clip_data.get(clip, {'aud':0, 'sub':0, 'h':1080})
        src_aud, src_sub, src_height = cd['aud'], cd['sub'], cd['h']

        sys.stderr.write('  Extra: {}.m2ts\n'.format(clip))

        # Audio extraction
        audio_tracks = 0
        if src_aud > 0:
            audio_args = ['ffmpeg', '-y', '-v', 'error', '-i', src]
            for ai in range(src_aud):
                audio_args += ['-map', '0:a:{}'.format(ai), '-c:a', 'ac3',
                               '-b:a', extras_audio_bitrate,
                               os.path.join(encode_dir, '{}_audio_{}.ac3'.format(clip, ai))]
            if run_ff(audio_args):
                audio_tracks = src_aud
                # Verify all files exist
                for ai in range(src_aud):
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
            # Copy original clip
            subprocess.run(['cp', src, os.path.join(encode_dir, '{}.m2ts'.format(clip))], timeout=300)
            sys.stderr.write('  (copied original)\n')
            continue

        # Video encoding
        video_filter = []
        if src_height > 720:
            video_filter = ['-vf', 'scale={}'.format(extras_scale)]

        x264_full_opts = '{}:vbv-maxrate=12000:vbv-bufsize=12000'.format(bd_x264_opts)
        for attempt in range(3):
            cmd = ['ffmpeg', '-y', '-v', 'error', '-i', src,
                   '-map', '0:v:0', '-c:v', 'libx264', '-preset', 'medium',
                   '-crf', str(extras_crf)] + video_filter + [
                   '-x264opts', x264_full_opts, out_video]
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
        out_video = os.path.join(encode_dir, '{}_video.h264'.format(clip))
        pass_log = os.path.join(work_dir, 'x264_{}.log'.format(clip))
        cd = clip_data.get(clip, {'aud':0, 'sub':0, 'h':1080})
        src_aud, src_sub = cd['aud'], cd['sub']

        sys.stderr.write('  Main: {}.m2ts\n'.format(clip))

        # Audio extraction
        audio_tracks = 0
        if src_aud > 0:
            audio_args = ['ffmpeg', '-y', '-v', 'error', '-i', src,
                          '-map', '0:a:0', '-c:a', 'ac3', '-b:a', main_audio_bitrate,
                          os.path.join(encode_dir, '{}_audio_0.ac3'.format(clip))]
            for ai in range(1, src_aud):
                audio_args += ['-map', '0:a:{}'.format(ai), '-c:a', 'ac3',
                               '-b:a', commentary_audio_bitrate,
                               os.path.join(encode_dir, '{}_audio_{}.ac3'.format(clip, ai))]
            if run_ff(audio_args):
                audio_tracks = src_aud

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

        # Pass 1
        pass_log_actual = pass_log + '-0.log'
        # Clean orphaned passlogs
        import glob
        for f in glob.glob(pass_log + '*'):
            try: os.remove(f)
            except: pass

        for attempt in range(3):
            cmd = ['ffmpeg', '-y', '-v', 'error', '-i', src,
                   '-map', '0:v:0', '-c:v', 'libx264', '-preset', main_preset,
                   '-b:v', main_bitrate, '-x264opts', bd_x264_opts,
                   '-pass', '1', '-passlogfile', pass_log,
                   '-an', '-f', 'null', '/dev/null']
            try:
                r = subprocess.run(cmd, timeout=None, capture_output=False)
                if r.returncode == 0:
                    break
            except:
                pass
            if os.path.isfile(pass_log_actual):
                break  # stats file exists = pass 1 actually finished
            sys.stderr.write('  Pass 1 attempt {} failed - retrying\n'.format(attempt + 1))

        if not os.path.isfile(pass_log_actual):
            sys.stderr.write('  Pass 1 failed - skipping\n')
            continue

        # Pass 2
        pass2_ok = False
        for attempt in range(3):
            cmd = ['ffmpeg', '-y', '-v', 'error', '-i', src,
                   '-map', '0:v:0', '-c:v', 'libx264', '-preset', main_preset,
                   '-b:v', main_bitrate, '-maxrate', main_maxrate, '-bufsize', main_bufsize,
                   '-x264opts', bd_x264_opts,
                   '-pass', '2', '-passlogfile', pass_log,
                   '-an', out_video]
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

    # Write video tracks
    first=true
    for clip in $MAIN_CLIPS; do
        vf="$ENCODE_DIR/${clip}_video.h264"
        [[ -f "$vf" && -s "$vf" ]] || { warn "Missing/empty video for clip ${clip}"; continue; }
        if $first; then
            echo "V_MPEG4/ISO/AVC, \"$vf\", fps=$fps, insertSEI, contSPS" >> "$META_FILE"
        else
            echo "+V_MPEG4/ISO/AVC, \"$vf\", fps=$fps, insertSEI, contSPS" >> "$META_FILE"
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
    tsMuxeR "$META_FILE" "$DST" > "$WORK_DIR/.tsmuxer_out.txt" 2>&1 || {
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

    # Build fresh BD structure (don't rm -rf "$DST" — that deletes run.log!)
    mkdir -p "$DST/BDMV/PLAYLIST" "$DST/BDMV/CLIPINF" "$DST/BDMV/STREAM"
    mkdir -p "$DST/BDMV/BACKUP/PLAYLIST" "$DST/BDMV/BACKUP/CLIPINF"
    mkdir -p "$DST/CERTIFICATE"

    # Copy source BD metadata
    cp "$SOURCE/index.bdmv" "$DST/BDMV/"
    cp "$SOURCE/MovieObject.bdmv" "$DST/BDMV/"
    [[ -d "$SOURCE/../CERTIFICATE" ]] && cp -r "$SOURCE/../CERTIFICATE/"* "$DST/CERTIFICATE/" 2>/dev/null || true
    [[ -d "$SOURCE/CERTIFICATE" ]] && cp -r "$SOURCE/CERTIFICATE/"* "$DST/CERTIFICATE/" 2>/dev/null || true

    # Discover which clips were re-encoded (builtins only)
    ENCODED_CLIP_IDS=""
    encode_dir="$WORK_DIR/encode"
    for f in "$encode_dir/"*_video.h264; do
        [[ -f "$f" ]] || continue
        fname="${f##*/}"
        cid="${fname%_video.h264}"
        ENCODED_CLIP_IDS+="$cid "
    done

    encoded_count=0
    log "Re-encoded clips:"
    for cid in $ENCODED_CLIP_IDS; do
        [[ -n "$cid" ]] || continue
        [[ -f "$encode_dir/${cid}_video.h264" ]] || continue
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
            echo "V_MPEG4/ISO/AVC, \"$encode_dir/${cid}_video.h264\", fps=$fps, insertSEI, contSPS"
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

        # Copy remuxed output to destination (use zsh glob, no ls/head)
        local new_m2ts=("$tmpout/BDMV/STREAM/"*.m2ts(N))
        local new_clpi=("$tmpout/BDMV/CLIPINF/"*.clpi(N))
        if [[ -n "${new_m2ts[1]:-}" ]] && [[ -n "${new_clpi[1]:-}" ]]; then
            run_ff cp "$new_m2ts" "$DST/BDMV/STREAM/${cid}.m2ts" || true
            run_ff cp "$new_clpi" "$DST/BDMV/CLIPINF/${cid}.clpi" || true
            log "    done: ${cid}.m2ts"
        else
            warn "tsMuxeR produced no output for ${cid}"
        fi
    done

    # Copy un-encoded clips verbatim (builtins only)
    log "Copying un-encoded clips..."
    for cid in $ALL_CLIP_IDS; do
        [[ -n "$cid" ]] || continue
        [[ -f "$DST/BDMV/STREAM/${cid}.m2ts" ]] && continue
        # Check if this clip was encoded (remux already placed it)
        local was_encoded=false
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

    # Copy all MPLS files
    cp "$SOURCE/PLAYLIST/"*.mpls "$DST/BDMV/PLAYLIST/" 2>/dev/null || true

    # Copy extra metadata directories
    for dir in AUXDATA META BDJO JAR; do
        [[ -d "$SOURCE/$dir" ]] && cp -r "$SOURCE/$dir" "$DST/BDMV/" 2>/dev/null || true
    done

    # Copy BACKUP
    cp "$SOURCE/index.bdmv" "$DST/BDMV/BACKUP/"
    cp "$SOURCE/MovieObject.bdmv" "$DST/BDMV/BACKUP/"
    cp "$SOURCE/BACKUP/PLAYLIST/"*.mpls "$DST/BDMV/BACKUP/PLAYLIST/" 2>/dev/null || true
    cp "$DST/BDMV/CLIPINF/"*.clpi "$DST/BDMV/BACKUP/CLIPINF/" 2>/dev/null || true

fi  # end if MOVIE_ONLY

# Create ISO if requested (wraps the BDMV folder created above)
if $OUTPUT_ISO; then
    ISO_OUT="${OUTPUT%.iso}.iso"
    log "Creating ISO: ${ISO_OUT}..."

    if command -v xorriso &>/dev/null; then
        xorriso -outdev "$ISO_OUT" -volid "BD_SHRINK" -map "$DST" / -commit 2>/dev/null || {
            warn "ISO creation failed with xorriso"
        }
    elif command -v genisoimage &>/dev/null; then
        genisoimage -udf -V "BD_SHRINK" -o "$ISO_OUT" "$DST" 2>/dev/null || {
            warn "ISO creation failed with genisoimage"
        }
    elif command -v mkisofs &>/dev/null; then
        mkisofs -udf -V "BD_SHRINK" -o "$ISO_OUT" "$DST" 2>/dev/null || {
            warn "ISO creation failed with mkisofs"
        }
    else
        warn "No ISO creation tool found (xorriso/genisoimage/mkisofs). Install one and run:"
        warn "  xorriso -outdev ${ISO_OUT} -volid 'BD_SHRINK' -map ${DST} / -commit"
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

# ─── Done ─────────────────────────────────────────────────────────────────────
log "Done! Output: $DST"
if $HAS_BDJ && ! $MOVIE_ONLY; then
    warn "BD-J disc — test in a software player (VLC/mpv) before burning."
elif $MOVIE_ONLY; then
    info "Movie-only — no BD-J concerns."
fi
log "Working files retained at: $WORK_DIR (delete manually when satisfied)"
log "IMPORTANT: Test the output in VLC or mpv before burning to disc!"

# Cleanup temp directories
rm -rf "${REBUILD_DIR:-}" 2>/dev/null || true
