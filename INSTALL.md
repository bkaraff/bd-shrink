# bd_shrink.sh — Installation & Dependencies

## Required Tools

| Tool | Where to get it | Check |
|------|----------------|-------|
| `ffmpeg` + `ffprobe` | `sudo dnf install ffmpeg` (from rpmfusion-free) | `ffmpeg -version` |
| `tsMuxeR` | Download binary from [justdan96/tsMuxer](https://github.com/justdan96/tsMuxer/releases/tag/2.7.0) | `tsMuxeR --version` |
| `bc` | `sudo dnf install bc` | `bc --version` |
| `python3` | Usually pre-installed | `python3 --version` |
| `libbluray-utils` | `sudo dnf install libbluray-utils` | `bd_info --help` |

## tsMuxeR Setup

```bash
# Download and install the Linux binary (single file, no deps)
wget https://github.com/justdan96/tsMuxeR/releases/download/2.7.0/tsMuxer-2.7.0-linux.zip
unzip tsMuxer-2.7.0-linux.zip
sudo cp tsMuxer/tsMuxeR /usr/local/bin/
# Verify
tsMuxeR --version
```

## ffmpeg Setup (Fedora)

```bash
# rpmfusion-free should already be enabled (check with: dnf repolist)
sudo dnf install ffmpeg libbluray-utils
```

## Verification

Run the dry-run to confirm everything works:

```bash
./bd_shrink.sh -s /path/to/BDMV -o /tmp/test -n -f --movie-only
```
