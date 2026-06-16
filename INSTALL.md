# bd_shrink.sh — Installation & Dependencies

## Quick Check

Run this first — it prints which tools are missing and the exact commands to install them:

```bash
./bd_shrink.sh --install-deps
```

## Required Tools

| Tool | Where to get it | Check |
|------|----------------|-------|
| `ffmpeg` + `ffprobe` | `sudo dnf install ffmpeg` (from rpmfusion-free) | `ffmpeg -version` |
| `tsMuxeR` | Download binary from [justdan96/tsMuxer](https://github.com/justdan96/tsMuxer/releases/tag/2.7.0) | `tsMuxeR --version` |
| `bc` | `sudo dnf install bc` | `bc --version` |
| `python3` | Usually pre-installed | `python3 --version` |
| `systemd-run` | Part of `systemd` (always present on Fedora) | `systemd-run --version` |
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

## Optional: TUI mode

| Tool | Where to get it | Check |
|------|----------------|-------|
| `gum` | `sudo dnf install gum` (Fedora 42+, EPEL 10) | `gum --version` |

## Optional: BD-R burning (`--burn`)

| Tool | Where to get it | Check |
|------|----------------|-------|
| `growisofs` | `sudo dnf install dvd+rw-tools` | `growisofs --version` |
| `genisoimage` | `sudo dnf install genisoimage` | `genisoimage --version` |
| `eject` | Part of `util-linux` (always pre-installed on Fedora) | `eject --version` |

`growisofs` with `genisoimage` burns directly to BD-R with proper UDF 2.50. This is the preferred method for player compatibility.
`xorriso` is pre-installed on Fedora and used by the script for MD5 verification and ISO creation, but is not a manual install dependency.

## Optional: Playback & Testing

| Tool | Where to get it | Check |
|------|----------------|-------|
| `vlc` | `sudo dnf install vlc` | `vlc --version` |
| `mpv` | `sudo dnf install mpv` | `mpv --version` |
| `libbluray` | `sudo dnf install libbluray` | (dependency, no direct command) |

Test before burning: `vlc /path/to/BDMV` or `mpv bd:// --bluray-device=/path/to/output`.

## Verification

Run the dry-run to confirm everything works:

```bash
./bd_shrink.sh -s /path/to/BDMV -o /tmp/test -n -f --movie-only
```
