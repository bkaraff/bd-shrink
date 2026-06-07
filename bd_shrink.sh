#!/usr/bin/env bash
set -euo pipefail

VERSION="0.1.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
log()  { echo "[$(date +%H:%M:%S)] $*"; }
info() { echo "       $*"; }

usage() {
    cat <<EOF
bd_shrink.sh v${VERSION} — shrink BD50 → BD25 with menu preservation

Usage:  bd_shrink.sh -s <source> -o <output> [options]

Required:
  -s, --source DIR       Source BDMV folder (must contain index.bdmv)
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
  --iso                  Output ISO instead of BDMV folder (combine with --movie-only)
  -f, --force            Overwrite output directory if it exists
  -n, --dry-run          Show what would be done without encoding
  -w, --work DIR         Working directory (default: /tmp/bd-shrink-XXXXXX)
  -h, --help             Show this help
EOF
    exit 1
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

[[ -z "$SOURCE" ]] && die "Source folder required (-s)"
[[ -z "$OUTPUT" ]] && die "Output folder required (-o)"
[[ ! -f "$SOURCE/index.bdmv" ]] && die "Source must contain index.bdmv (point to the BDMV folder)"
[[ ! -d "$SOURCE/PLAYLIST" ]] && die "Source must contain PLAYLIST/ directory"
[[ ! -d "$SOURCE/STREAM" ]] && die "Source must contain STREAM/ directory"

if [[ -d "$OUTPUT" ]] && ! $FORCE; then
    die "Output directory exists. Use -f to overwrite."
fi

if [[ -z "$WORK_DIR" ]]; then
    WORK_DIR=$(mktemp -d -t bd-shrink-XXXXXX)
else
    mkdir -p "$WORK_DIR"
fi

# Detect BD-J
HAS_BDJ=false
if [[ -d "$SOURCE/BDJO" || -d "$SOURCE/JAR" ]]; then
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
parse_mpls "$SOURCE/PLAYLIST" > "$WORK_DIR/playlists.json"

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

for clip in $all_clips; do
    clip_file="$SOURCE/STREAM/${clip}.m2ts"
    if [[ ! -f "$clip_file" ]]; then
        warn "Referenced clip not found: ${clip}.m2ts"
        continue
    fi
    ffprobe -v error -show_entries stream=index,codec_type,codec_name,width,height,\
duration,r_frame_rate,channels,channel_layout,sample_rate,bit_rate \
        -show_entries format=size,duration,bit_rate \
        -of json "$clip_file" > "$CLIPS_DIR/${clip}.json" 2>/dev/null || true
done

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
    summary = {
        'filename': pl_name,
        'duration': round(pl_data['duration'], 1),
        'chapters': pl_data['chapters'],
        'chapter_times': pl_data.get('chapter_times', []),
        'subpaths': pl_data['subpaths'],
        'clips': [item['clip'] for item in pl_data['playitems']],
        'total_clip_dur': round(sum(item['duration'] for item in pl_data['playitems']), 1),
    }
    # Calculate total size from clips
    total_size = 0
    for item in pl_data['playitems']:
        cid = item['clip']
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

# Re-classify: the LONGEST playlist(s) are the main movie.
# If multiple playlists have similar durations (within 30% of longest),
# they are likely alternate cuts or angles of the same film.
if extras_pls:
    first_name = extras_pls[0]
    longest_dur = playlists[first_name]['duration']
    movie_threshold = longest_dur * 0.70

    new_extras = []
    for pl_name in extras_pls:
        pl_dur = playlists[pl_name]['duration']
        if pl_dur >= movie_threshold and pl_dur >= 600:
            main_movie_pls.append(pl_name)
        else:
            new_extras.append(pl_name)
    extras_pls = new_extras
elif not main_movie_pls and extras_pls:
    # If nothing classified as main movie, promote the longest extra
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

# Check main movie clips for lossless audio to re-encode
main_duration = 0
main_audio_size = 0
main_original_size = 0
main_audio_tracks = 0
for c in main_clips:
    cs = clips.get(c, {})
    main_original_size += cs.get('size_bytes', 0)
    dur = cs.get('duration_sec', 0)
    if dur > main_duration:
        main_duration = dur
    audios = cs.get('audio', [])
    for i, a in enumerate(audios):
        codec = a.get('codec', '')
        # First track = main audio bitrate, rest = commentary bitrate
        if i == 0:
            rate = int('$MAIN_AUDIO_BITRATE'.replace('k', '')) * 1000
        else:
            main_audio_tracks += 1
            rate = int('$COMMENTARY_AUDIO_BITRATE'.replace('k', '')) * 1000
        if codec in ('dts', 'truehd', 'dts_hd_ma', 'pcm_s16le', 'pcm_s24le', 'pcm_s32le', 'flac'):
            main_audio_size += (rate * dur) / 8  # bits to bytes

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

main_bitrate = 0
if total_main_dur > 0:
    # Subtract audio overhead
    main_video_available = available_for_main - main_audio_size
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

# Read budget to get bitrate
MAIN_BITRATE=$(python3 -c "import json; print(json.load(open('$BUDGET_FILE'))['main_bitrate'])")
MAIN_MAXRATE=$(python3 -c "print(int($MAIN_BITRATE * 1.1))")
MAIN_BUFSIZE=$(python3 -c "print(int($MAIN_BITRATE * 1.5))")

# Common x264 BD-compat opts
BD_X264_OPTS="bluray-compat=1:vbv-maxrate=40000:vbv-bufsize=30000"

# Get clips to re-encode
EXTRAS_CLIPS=$(python3 -c "
import json
b = json.load(open('$BUDGET_FILE'))
for cid, cd in b['extras_details'].items():
    if cd['will_reencode']:
        print(cid)
")

MAIN_CLIPS=$(python3 -c "
import json
clf = json.load(open('$CLASSIFY_FILE'))
main_pls = clf['main_movie']
clips = set()
for pl_name in main_pls:
    pd = clf['details'].get(pl_name, {})
    for c in pd.get('clips', []):
        clips.add(c)
for c in sorted(clips):
    print(c)
")

# Encode extras (single-pass CRF, downscale to 720p)
if [[ -n "$EXTRAS_CLIPS" ]] && ! $NO_EXTRAS && ! $MOVIE_ONLY; then
    log "Encoding extras ($(echo "$EXTRAS_CLIPS" | wc -l) clips)..."

    IFS=$'\n'
    for clip in $EXTRAS_CLIPS; do
        log "  Extra: ${clip}.m2ts"
        src="$SOURCE/STREAM/${clip}.m2ts"
        out_video="$ENCODE_DIR/${clip}_video.h264"

        # Get stream counts for this clip
        num_audio=$(ffprobe -v error -show_entries stream=codec_type \
            -of default=noprint_wrappers=1:nokey=1 "$src" 2>/dev/null | grep -c "^audio" || echo "0")
        num_subs=$(ffprobe -v error -show_entries stream=codec_type \
            -of default=noprint_wrappers=1:nokey=1 "$src" 2>/dev/null | grep -c "^subtitle" || echo "0")

        # Extract ALL audio tracks
        audio_tracks=0
        if [[ "$num_audio" -gt 0 ]]; then
            for i in $(seq 0 $((num_audio - 1))); do
                out_a="$ENCODE_DIR/${clip}_audio_${i}.ac3"
                ab="$EXTRAS_AUDIO_BITRATE"
                ffmpeg -y -v error -i "$src" \
                    -map "0:a:${i}" -c:a ac3 -b:a "$ab" \
                    "$out_a" 2>/dev/null && ((audio_tracks++)) || warn "  Failed to extract audio track $i"
            done
        fi

        # Extract ALL subtitle tracks (PGS passthrough)
        sub_tracks=0
        if [[ "$num_subs" -gt 0 ]]; then
            for i in $(seq 0 $((num_subs - 1))); do
                out_s="$ENCODE_DIR/${clip}_sub_${i}.sup"
                ffmpeg -y -v error -i "$src" \
                    -map "0:s:${i}" -c copy -f sup \
                    "$out_s" 2>/dev/null && ((sub_tracks++)) || warn "  Failed to extract subtitle track $i"
            done
        fi

        if [[ $audio_tracks -eq 0 ]]; then
            warn "  No audio extracted from ${clip}.m2ts — copying original"
            cp "$src" "$ENCODE_DIR/${clip}.m2ts"
            continue
        fi

        # Encode video to 720p
        video_filter=""
        src_height=$(python3 -c "
import json
cs = json.load(open('$CLIPS_DIR/${clip}.json'))
for s in cs.get('streams',[]):
    if s.get('codec_type')=='video':
        print(s.get('height',0))
        break
" 2>/dev/null || echo "1080")

        if [[ "$src_height" -gt 720 ]]; then
            video_filter="-vf scale=$EXTRAS_SCALE"
        fi

        ffmpeg -y -v error -i "$src" \
            -map 0:v:0 -c:v libx264 -preset medium -crf "$EXTRAS_CRF" \
            $video_filter \
            -x264opts "$BD_X264_OPTS:vbv-maxrate=12000:vbv-bufsize=12000" \
            "$out_video" 2>/dev/null || {
                warn "Failed to encode video for ${clip}.m2ts"
                continue
            }

        log "    done (${audio_tracks} audio, ${sub_tracks} subtitle, video: $(du -h "$out_video" | cut -f1))"
    done
    unset IFS
else
    log "No extras to encode."
fi

# Encode main movie (two-pass VBR)
if [[ -n "$MAIN_CLIPS" ]]; then
    log "Encoding main movie ($(echo "$MAIN_CLIPS" | wc -l) clips)..."
    log "  Bitrate: $(( MAIN_BITRATE / 1000000 )) Mbps, preset: $MAIN_PRESET"

    for clip in $MAIN_CLIPS; do
        log "  Main: ${clip}.m2ts"
        src="$SOURCE/STREAM/${clip}.m2ts"
        out_video="$ENCODE_DIR/${clip}_video.h264"
        pass_log="$WORK_DIR/x264_${clip}.log"

        # Get stream counts
        # Get stream counts
        num_audio=$(ffprobe -v error -show_entries stream=codec_type \
            -of default=noprint_wrappers=1:nokey=1 "$src" 2>/dev/null | grep -c "^audio" || echo "0")
        num_subs=$(ffprobe -v error -show_entries stream=codec_type \
            -of default=noprint_wrappers=1:nokey=1 "$src" 2>/dev/null | grep -c "^subtitle" || echo "0")

        # Extract audio tracks — track 0 at MAIN, rest at COMMENTARY bitrate
        audio_tracks=0
        if [[ "$num_audio" -gt 0 ]]; then
            for i in $(seq 0 $((num_audio - 1))); do
                out_a="$ENCODE_DIR/${clip}_audio_${i}.ac3"
                if [[ $i -eq 0 ]]; then
                    ab="$MAIN_AUDIO_BITRATE"
                else
                    ab="$COMMENTARY_AUDIO_BITRATE"
                fi
                ffmpeg -y -v error -i "$src" \
                    -map "0:a:${i}" -c:a ac3 -b:a "$ab" \
                    "$out_a" 2>/dev/null && ((audio_tracks++)) || warn "  Failed to extract audio track $i"
            done
        fi

        # Extract subtitle tracks (PGS passthrough)
        sub_tracks=0
        if [[ "$num_subs" -gt 0 ]]; then
            for i in $(seq 0 $((num_subs - 1))); do
                out_s="$ENCODE_DIR/${clip}_sub_${i}.sup"
                ffmpeg -y -v error -i "$src" \
                    -map "0:s:${i}" -c copy -f sup \
                    "$out_s" 2>/dev/null && ((sub_tracks++)) || warn "  Failed to extract subtitle track $i"
            done
        fi

        if [[ $audio_tracks -eq 0 ]]; then
            warn "  No audio extracted from ${clip}.m2ts — skipping"
            continue
        fi

        # Pass 1
        ffmpeg -y -v error -i "$src" \
            -map 0:v:0 -c:v libx264 -preset "$MAIN_PRESET" \
            -b:v "$MAIN_BITRATE" \
            -x264opts "$BD_X264_OPTS" \
            -pass 1 -passlogfile "$pass_log" \
            -an -f null /dev/null 2>/dev/null || {
                warn "Pass 1 failed for ${clip}.m2ts"
                continue
            }

        # Pass 2
        ffmpeg -y -v error -i "$src" \
            -map 0:v:0 -c:v libx264 -preset "$MAIN_PRESET" \
            -b:v "$MAIN_BITRATE" -maxrate "$MAIN_MAXRATE" -bufsize "$MAIN_BUFSIZE" \
            -x264opts "$BD_X264_OPTS" \
            -pass 2 -passlogfile "$pass_log" \
            -an "$out_video" 2>/dev/null || {
                warn "Pass 2 failed for ${clip}.m2ts"
                continue
            }

        log "    done (${audio_tracks} audio, ${sub_tracks} subtitle, video: $(du -h "$out_video" | cut -f1))"
        rm -f "${pass_log}" "${pass_log}.mbtree" "${pass_log}.cuted"
    done
fi

# ─── Phase 5: Rebuild ────────────────────────────────────────────────────────

log "Phase 5: Rebuilding BD structure..."

DST="$OUTPUT"

if $MOVIE_ONLY; then
    # ────────────────────────────────────────────────────────────────────
    # Movie-only mode: fresh BD authoring with tsMuxeR (no menus, no extras)
    # ────────────────────────────────────────────────────────────────────
    log "  Movie-only mode: authoring fresh BD..."

    # Identify the main movie playlist
    main_pl=$(python3 -c "
import json
clf = json.load(open('$CLASSIFY_FILE'))
print(clf['main_movie'][0])")

    main_clips=$(python3 -c "
import json
clf = json.load(open('$CLASSIFY_FILE'))
for c in clf['details']['$main_pl']['clips']:
    print(c)")

    # Get chapter timestamps from the original playlist
    main_chapters=$(python3 -c "
import json
inv = json.load(open('$INVENTORY_FILE'))
pl = inv['playlists']['$main_pl']
ct = pl.get('chapter_times', [])
parts = ['00:00:00']
for t in ct:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    parts.append(f'{h:02d}:{m:02d}:{s:02d}')
# Remove duplicates and keep unique sorted
seen = set()
unique = []
for p in parts:
    if p not in seen:
        unique.append(p)
        seen.add(p)
print(';'.join(unique))")

    # Read fps from the first clip's probe
    first_clip=$(echo "$main_clips" | head -1)
    fps=$(python3 -c "
import json
with open('$CLIPS_DIR/${first_clip}.json') as f:
    d = json.load(f)
for s in d.get('streams', []):
    if s.get('codec_type') == 'video':
        print(s.get('r_frame_rate', '24000/1001'))
        break
" 2>/dev/null || echo "24000/1001")

    clip_count=$(echo "$main_clips" | wc -l)

    META_DIR="$WORK_DIR/meta"
    mkdir -p "$META_DIR"
    META_FILE="$META_DIR/movie.meta"

    # Write MUXOPT header with chapters
    cat > "$META_FILE" << META
MUXOPT --no-pcr-on-video-pid --new-audio-pes --vbr --blu-ray --custom-chapters=${main_chapters}
META

    # Count max audio/subtitle tracks across all clips
    max_audio=0
    max_subs=0
    for clip in $main_clips; do
        a=0; while [[ -f "$ENCODE_DIR/${clip}_audio_${a}.ac3" ]]; do ((a++)); done
        ((a > max_audio)) && max_audio=$a
        s=0; while [[ -f "$ENCODE_DIR/${clip}_sub_${s}.sup" ]]; do ((s++)); done
        ((s > max_subs)) && max_subs=$s
    done

    # Write video tracks
    first=true
    for clip in $main_clips; do
        vf="$ENCODE_DIR/${clip}_video.h264"
        [[ -f "$vf" ]] || { warn "Missing video for clip ${clip}"; continue; }
        if $first; then
            echo "V_MPEG4/ISO/AVC, \"$vf\", fps=$fps, insertSEI, contSPS" >> "$META_FILE"
        else
            echo "+V_MPEG4/ISO/AVC, \"$vf\", fps=$fps, insertSEI, contSPS" >> "$META_FILE"
        fi
        first=false
    done

    # Write audio tracks (grouped by track index across clips)
    for aidx in $(seq 0 $((max_audio - 1))); do
        first=true
        for clip in $main_clips; do
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
    for sidx in $(seq 0 $((max_subs - 1))); do
        first=true
        for clip in $main_clips; do
            sf="$ENCODE_DIR/${clip}_sub_${sidx}.sup"
            [[ -f "$sf" ]] || continue
            if $first; then
                echo "S_HDMV/PGS, \"$sf\"" >> "$META_FILE"
            else
                echo "+S_HDMV/PGS, \"$sf\"" >> "$META_FILE"
            fi
            first=false
        done
    done

    log "  Running tsMuxeR ($(echo "$main_clips" | wc -l) clip(s), ${max_audio} audio, ${max_subs} subtitle)..."

    if $OUTPUT_ISO; then
        ISO_OUTPUT="${OUTPUT%.iso}.iso"
        DST="$ISO_OUTPUT"
        tsMuxeR "$META_FILE" "$ISO_OUTPUT" > /dev/null 2>&1 || {
            die "tsMuxeR authoring failed"
        }
        log "  ISO created: $ISO_OUTPUT"
    else
        rm -rf "$DST" 2>/dev/null || true
        tsMuxeR "$META_FILE" "$DST" > /dev/null 2>&1 || {
            die "tsMuxeR authoring failed"
        }
        log "  BDMV folder created: $DST"
    fi

    log "  Fresh BD structure complete"
else
    # ────────────────────────────────────────────────────────────────────
    # Surgical replacement mode: preserve original menus, files, structure
    # ────────────────────────────────────────────────────────────────────

rm -rf "$DST" 2>/dev/null || true
mkdir -p "$DST/BDMV/PLAYLIST" "$DST/BDMV/CLIPINF" "$DST/BDMV/STREAM"
mkdir -p "$DST/BDMV/BACKUP/PLAYLIST" "$DST/BDMV/BACKUP/CLIPINF"
mkdir -p "$DST/CERTIFICATE"

# Copy CERTIFICATE if it exists
if [[ -d "$SOURCE/../CERTIFICATE" ]]; then
    cp -r "$SOURCE/../CERTIFICATE/"* "$DST/CERTIFICATE/" 2>/dev/null || true
elif [[ -d "$SOURCE/CERTIFICATE" ]]; then
    cp -r "$SOURCE/CERTIFICATE/"* "$DST/CERTIFICATE/" 2>/dev/null || true
fi

# Copy index.bdmv and MovieObject.bdmv
cp "$SOURCE/index.bdmv" "$DST/BDMV/"
cp "$SOURCE/MovieObject.bdmv" "$DST/BDMV/"

# Determine which clips were re-encoded
REBUILD_DIR="$WORK_DIR/rebuild"
mkdir -p "$REBUILD_DIR"

# Function: remux a re-encoded clip with tsMuxeR
remux_clip() {
    local clip_id="$1"
    local video_file="$ENCODE_DIR/${clip_id}_video.h264"
    local temp_meta="$REBUILD_DIR/${clip_id}.meta"
    local temp_output="$REBUILD_DIR/${clip_id}_output"

    if [[ ! -f "$video_file" ]]; then
        return 1
    fi

    # Determine FPS from original probe
    local fps="24000/1001"
    local src_json="$CLIPS_DIR/${clip_id}.json"
    if [[ -f "$src_json" ]]; then
        fps=$(python3 -c "
import json
with open('$src_json') as f:
    d = json.load(f)
for s in d.get('streams', []):
    if s.get('codec_type') == 'video':
        print(s.get('r_frame_rate', '24000/1001'))
        break
" 2>/dev/null || echo "24000/1001")
    fi

    # Build META with video + all audio + all subtitle tracks
    cat > "$temp_meta" << METAEOF
MUXOPT --no-pcr-on-video-pid --new-audio-pes --vbr --blu-ray
V_MPEG4/ISO/AVC, "$video_file", fps=$fps, insertSEI, contSPS
METAEOF

    local a=0
    while [[ -f "$ENCODE_DIR/${clip_id}_audio_${a}.ac3" ]]; do
        echo "A_AC3, \"$ENCODE_DIR/${clip_id}_audio_${a}.ac3\"" >> "$temp_meta"
        ((a++))
    done

    local s=0
    while [[ -f "$ENCODE_DIR/${clip_id}_sub_${s}.sup" ]]; do
        echo "S_HDMV/PGS, \"$ENCODE_DIR/${clip_id}_sub_${s}.sup\"" >> "$temp_meta"
        ((s++))
    done

    mkdir -p "$temp_output"
    tsMuxeR "$temp_meta" "$temp_output" > /dev/null 2>&1 || {
        warn "tsMuxeR failed for clip ${clip_id}"
        return 1
    }

    # Copy resulting M2TS and CLPI to output
    local new_m2ts=$(ls "$temp_output/BDMV/STREAM/"*.m2ts 2>/dev/null | head -1)
    local new_clpi=$(ls "$temp_output/BDMV/CLIPINF/"*.clpi 2>/dev/null | head -1)

    if [[ -f "$new_m2ts" ]] && [[ -f "$new_clpi" ]]; then
        cp "$new_m2ts" "$DST/BDMV/STREAM/${clip_id}.m2ts"
        cp "$new_clpi" "$DST/BDMV/CLIPINF/${clip_id}.clpi"
        log "    remuxed: ${clip_id}.m2ts (${a} audio, ${s} subtitle)"
        return 0
    fi
    return 1
}

# Get all clip IDs from the inventory
ALL_CLIP_IDS=$(python3 -c "
import json
inv = json.load(open('$INVENTORY_FILE'))
for cid in sorted(inv['clips'].keys()):
    print(cid)
")

# Get encoded clip IDs
ENCODED_CLIP_IDS=""
for f in "$ENCODE_DIR/"*_video.h264; do
    [[ -f "$f" ]] || continue
    cid=$(basename "$f" | sed 's/_video.h264//')
    ENCODED_CLIP_IDS="$ENCODED_CLIP_IDS $cid"
done

log "Clips re-encoded: $(echo "$ENCODED_CLIP_IDS" | wc -w)"
log "Clips copied verbatim: $(python3 -c "
import json
inv = json.load(open('$INVENTORY_FILE'))
encoded = set('$ENCODED_CLIP_IDS'.split())
verbatim = [c for c in inv['clips'] if c not in encoded]
print(len(verbatim))
")"

# Remux encoded clips
for cid in $ENCODED_CLIP_IDS; do
    log "  Remuxing: ${cid}.m2ts"
    remux_clip "$cid" || warn "Remux failed for ${cid}, will try fallback"
done

# Copy un-encoded clips verbatim
for cid in $ALL_CLIP_IDS; do
    if [[ -f "$DST/BDMV/STREAM/${cid}.m2ts" ]]; then
        continue
    fi
    if echo "$ENCODED_CLIP_IDS" | grep -qw "$cid"; then
        if [[ ! -f "$DST/BDMV/STREAM/${cid}.m2ts" ]]; then
            warn "Falling back to original for ${cid}.m2ts"
            cp "$SOURCE/STREAM/${cid}.m2ts" "$DST/BDMV/STREAM/${cid}.m2ts"
            cp "$SOURCE/CLIPINF/${cid}.clpi" "$DST/BDMV/CLIPINF/${cid}.clpi"
        fi
        continue
    fi
    cp "$SOURCE/STREAM/${cid}.m2ts" "$DST/BDMV/STREAM/${cid}.m2ts"
    cp "$SOURCE/CLIPINF/${cid}.clpi" "$DST/BDMV/CLIPINF/${cid}.clpi"
done

# Copy all MPLS files
cp "$SOURCE/PLAYLIST/"*.mpls "$DST/BDMV/PLAYLIST/" 2>/dev/null || true

# Copy any extra metadata directories
for dir in AUXDATA META BDJO JAR; do
    if [[ -d "$SOURCE/$dir" ]]; then
        cp -r "$SOURCE/$dir" "$DST/BDMV/" 2>/dev/null || true
    fi
done

# Copy BACKUP
cp "$SOURCE/index.bdmv" "$DST/BDMV/BACKUP/"
cp "$SOURCE/MovieObject.bdmv" "$DST/BDMV/BACKUP/"
cp "$SOURCE/BACKUP/PLAYLIST/"*.mpls "$DST/BDMV/BACKUP/PLAYLIST/" 2>/dev/null || true
cp "$DST/BDMV/CLIPINF/"*.clpi "$DST/BDMV/BACKUP/CLIPINF/" 2>/dev/null || true

fi  # end if MOVIE_ONLY

# ─── Phase 6: Validate ───────────────────────────────────────────────────────

log "Phase 6: Validating output..."

# Check total size
OUTPUT_SIZE=$(du -sb "$DST" 2>/dev/null | cut -f1)
OUTPUT_GB=$(python3 -c "print(round(${OUTPUT_SIZE:-0} / 1073741824, 2))")
log "Output size: ${OUTPUT_GB} GB"

if [[ ${OUTPUT_SIZE:-0} -gt $(( TARGET_GB * 1073741824 )) ]]; then
    warn "Output ($OUTPUT_GB GB) exceeds target ($TARGET_GB GB)!"
fi

if [[ ! -e "$DST" ]]; then
    die "Output does not exist: $DST"
fi

# For folder-based output (not ISO), verify BD structure
if [[ -d "$DST" ]] && [[ -d "$DST/BDMV" ]]; then
    log "Verifying BDMV structure..."

    # Verify all MPLS clips exist (surgical mode only — checks original playlists)
    if ! $MOVIE_ONLY; then
        MISSING_CLIPS=0
        for mpls in "$DST/BDMV/PLAYLIST/"*.mpls; do
            python3 -c "
import json, sys, os
result = json.load(open('$WORK_DIR/playlists.json'))
pl_name = os.path.basename('$mpls')
if pl_name in result:
    for item in result[pl_name]['playitems']:
        expected = os.path.join('$DST/BDMV/STREAM', item['clip'] + '.m2ts')
        if not os.path.exists(expected):
            print(f'MISSING: {item[\"clip\"]}.m2ts referenced by {pl_name}')
            sys.exit(1)
" 2>/dev/null || MISSING_CLIPS=1
        done

        if [[ $MISSING_CLIPS -eq 1 ]]; then
            warn "Some MPLS references point to missing M2TS files!"
        else
            log "All playlist references verified."
        fi
    fi

    # Verify CLPI files exist for all M2TS
    for m2ts in "$DST/BDMV/STREAM/"*.m2ts; do
        cid=$(basename "$m2ts" .m2ts)
        if [[ ! -f "$DST/BDMV/CLIPINF/${cid}.clpi" ]]; then
            warn "Missing CLPI for ${cid}.m2ts"
        fi
    done
fi

# ─── Done ─────────────────────────────────────────────────────────────────────
log "Done! Output: $DST"
log "Size: ${OUTPUT_GB} GB / ${TARGET_GB} GB target"
if $HAS_BDJ && ! $MOVIE_ONLY; then
    warn "BD-J disc — test in a software player (VLC/mpv) before burning."
elif $MOVIE_ONLY; then
    info "Movie-only — no BD-J concerns."
fi
log "Working files retained at: $WORK_DIR (delete manually when satisfied)"
log "IMPORTANT: Test the output in VLC or mpv before burning to disc!"

# Cleanup temp directories
rm -rf "${REBUILD_DIR:-}" 2>/dev/null || true
