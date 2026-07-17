"""Classification: heuristic categorization of playlists into main/extras/menus.

Uses MPLS metadata and clip properties to classify playlists.
Main movie = longest HD playlist(s) (possibly with seamless branching variants).
Extras = secondary movies (shorter, lower res, or lower bitrate).
Menus = MPLS type 1, or very short clips (<120s), or clips with low/no video.
"""

from dataclasses import dataclass

from bd_shrink.inventory import Inventory, PlaylistMetadata


@dataclass
class Classification:
    """Playlist classification results."""
    main_playlists: list[str]  # playlist IDs
    extras_playlists: list[str]
    menu_playlists: list[str]


def is_menu_type(playlist_meta: PlaylistMetadata) -> bool:
    """Check if playlist is MPLS type 1 (menu/interactive).
    
    Args:
        playlist_meta: Playlist metadata
    
    Returns:
        True if playlist is flagged as menu type
    """
    return playlist_meta.playlist_type == 1


def is_short_clip(playlist_meta: PlaylistMetadata, duration_floor_sec: float = 120.0) -> bool:
    """Check if playlist is very short (likely intro/logo/menu).
    
    Args:
        playlist_meta: Playlist metadata
        duration_floor_sec: Threshold in seconds (default 120s = 2 min)
    
    Returns:
        True if duration <= threshold
    """
    return playlist_meta.duration_sec <= duration_floor_sec


def is_low_res(inventory: Inventory, playlist_meta: PlaylistMetadata) -> bool:
    """Check if all clips in playlist are <720p (likely extras/menus).
    
    Args:
        inventory: Full inventory
        playlist_meta: Playlist metadata
    
    Returns:
        True if all clips have height < 720
    """
    for clip_id in playlist_meta.clips:
        if clip_id not in inventory.clips:
            continue
        clip = inventory.clips[clip_id]
        if clip.video and clip.video.height >= 720:
            return False
    return True


def has_video(inventory: Inventory, playlist_meta: PlaylistMetadata) -> bool:
    """Check if playlist has at least one clip with video.
    
    Args:
        inventory: Full inventory
        playlist_meta: Playlist metadata
    
    Returns:
        True if any clip has video stream
    """
    for clip_id in playlist_meta.clips:
        if clip_id not in inventory.clips:
            continue
        clip = inventory.clips[clip_id]
        if clip.video is not None:
            return True
    return False


def classify_playlists(
    inventory: Inventory,
    menu_duration_floor: float = 120.0,
) -> Classification:
    """Classify all playlists into main/extras/menus using heuristics.
    
    Algorithm:
    1. Mark any MPLS type 1 as menu
    2. Mark very short clips (<120s) with low/no video as menu
    3. Group main candidates by clip count + branching pattern
    4. Select longest main playlist(s) by total duration
    5. Remaining HD playlists are extras
    6. Remaining low-res/short clips are menus
    
    Args:
        inventory: Full inventory
        menu_duration_floor: Threshold for short clips (seconds)
    
    Returns:
        Classification with main/extras/menu lists
    """
    menu_pls = []
    main_candidates = []
    extras_candidates = []
    
    for pl_id, playlist_meta in inventory.playlists.items():
        # Rule 1: MPLS type 1 is always menu
        if is_menu_type(playlist_meta):
            menu_pls.append(pl_id)
            continue
        
        # Rule 2: Short + low/no video = menu
        if is_short_clip(playlist_meta, menu_duration_floor) and not has_video(inventory, playlist_meta):
            menu_pls.append(pl_id)
            continue
        
        # Rule 3: Short + low-res = menu
        if is_short_clip(playlist_meta, menu_duration_floor) and is_low_res(inventory, playlist_meta):
            menu_pls.append(pl_id)
            continue
        
        # Remaining HD/long clips are main or extras candidates
        if has_video(inventory, playlist_meta) and not is_low_res(inventory, playlist_meta):
            main_candidates.append((pl_id, playlist_meta))
        else:
            extras_candidates.append(pl_id)
    
    # Select main: longest playlist(s) by duration
    main_pls = []
    if main_candidates:
        # Sort by duration (descending)
        main_candidates.sort(key=lambda x: x[1].duration_sec, reverse=True)
        max_duration = main_candidates[0][1].duration_sec
        
        # All playlists within 5% of max are considered main (handles alternate cuts)
        # Use 5% window as per AGENTS.md
        min_duration = max_duration * 0.95
        for pl_id, meta in main_candidates:
            if meta.duration_sec >= min_duration:
                main_pls.append(pl_id)
    
    # Move unclassified main_candidates to extras if not selected
    for pl_id, _ in main_candidates:
        if pl_id not in main_pls and pl_id not in menu_pls:
            extras_candidates.append(pl_id)
    
    return Classification(
        main_playlists=main_pls,
        extras_playlists=extras_candidates,
        menu_playlists=menu_pls,
    )


def is_seamless_branching(inventory: Inventory, playlist_id: str) -> bool:
    """Check if playlist exhibits seamless branching (multiple playlists share clips).
    
    Seamless branching = different playlists contain overlapping clip sets.
    Indicates alternate cuts/angles, not separate content.
    
    Args:
        inventory: Full inventory
        playlist_id: Playlist to check
    
    Returns:
        True if this playlist's clips appear in other main playlists
    """
    if playlist_id not in inventory.playlists:
        return False
    
    main_playlist = inventory.playlists[playlist_id]
    main_clips = set(main_playlist.clips)
    
    # Check if any other playlist shares clips
    for other_id, other_pl in inventory.playlists.items():
        if other_id == playlist_id:
            continue
        
        other_clips = set(other_pl.clips)
        if main_clips & other_clips:  # Intersection
            return True
    
    return False


def count_main_clips_unique(
    inventory: Inventory,
    main_playlists: list[str],
) -> tuple[int, float]:
    """Count unique main clips across all main playlists.
    
    For seamless branching (multiple main playlists sharing clips),
    count each unique clip only once.
    
    Args:
        inventory: Full inventory
        main_playlists: List of main playlist IDs
    
    Returns:
        Tuple of (unique clip count, total duration in seconds)
    """
    unique_clips = set()
    total_duration = 0.0
    
    for pl_id in main_playlists:
        if pl_id not in inventory.playlists:
            continue
        
        playlist = inventory.playlists[pl_id]
        
        for clip_id in playlist.clips:
            if clip_id not in unique_clips:
                unique_clips.add(clip_id)
                
                # Add duration of this clip (only once)
                if clip_id in inventory.clips:
                    total_duration += inventory.clips[clip_id].duration_sec
    
    return len(unique_clips), total_duration
