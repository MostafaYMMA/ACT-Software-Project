"""
Resolves a SharePoint/OneDrive web link (the kind you'd copy from a
browser address bar or a "Copy Link" button) to the LOCAL folder path
where the OneDrive desktop client already syncs that library on this PC.

Deliberately NOT the Graph API -- no network call, no OAuth, no Azure AD
app registration. Everything here comes from OneDrive's own local
bookkeeping: when OneDrive syncs a SharePoint library, it records that
library's URL and the local mount point it chose for it in the Windows
registry (HKCU\\SOFTWARE\\SyncEngines\\Providers\\OneDrive). This module
just reads that mapping back -- see SHAREPOINT_SYNC_SPEC.md sec 13.

Only works for a library OneDrive has ALREADY synced to this machine (it
must already show as synced in File Explorer) -- this cannot make
OneDrive start syncing something new, only find where it already put it.
"""

import os
from urllib.parse import parse_qs, unquote, urlparse

_REGISTRY_BASE = r"SOFTWARE\SyncEngines\Providers\OneDrive"


class OneDriveLinkResolutionError(RuntimeError):
    """Raised with a message meant to be shown to the user directly --
    always ends with a pointer back to the manual Browse fallback."""


def _read_registry_value(key, name):
    import winreg

    try:
        value, _type = winreg.QueryValueEx(key, name)
        return value
    except FileNotFoundError:
        return None


def list_onedrive_sync_registrations():
    """Every SharePoint/OneDrive library this Windows account has ever
    synced, straight from OneDrive's own registry bookkeeping. Each entry
    is {"url": <library's SharePoint URL>, "mount_point": <local synced
    folder path>, "display_name": <library display name>}. Returns []
    (never raises) if OneDrive has no sync registrations yet, or if this
    isn't Windows -- callers turn an empty list into a clear message
    rather than a stack trace.
    """
    import winreg

    registrations = []
    try:
        base = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_BASE)
    except (FileNotFoundError, OSError):
        return registrations

    with base:
        index = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(base, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(base, subkey_name) as subkey:
                    url = _read_registry_value(subkey, "UrlNamespace")
                    mount_point = _read_registry_value(subkey, "MountPoint")
                    display_name = _read_registry_value(subkey, "DisplayName") or subkey_name
                    if url and mount_point:
                        registrations.append({
                            "url": url, "mount_point": mount_point, "display_name": display_name,
                        })
            except OSError:
                continue

    return registrations


def _score_by_path_prefix(registration, link_path_lower):
    """How well a registration's own URL path matches the pasted link's
    path -- longer matching prefix wins, so the right library gets picked
    when a tenant has several synced side by side. -1 means no match at
    all (the two paths share no common site/library segment)."""
    reg_path = urlparse(registration["url"]).path.lower().rstrip("/")
    if reg_path and link_path_lower.startswith(reg_path):
        return len(reg_path)
    return -1


def _extract_subfolder_path(parsed_link, registration):
    """Best-effort only: the SharePoint "library view" link shape
    (.../Forms/AllItems.aspx?id=%2Fsites%2F...%2FSubFolder) encodes the
    full server-relative folder path in the 'id' query parameter, which
    lets us resolve all the way down to a specific sub-folder, not just
    the library root. A "Copy Link" share link's path segment is an
    opaque resource id instead (no folder path in it at all) -- for that
    shape this returns None and the caller falls back to the library's
    top-level synced folder, which is still correct, just less precise.
    """
    query = parse_qs(parsed_link.query)
    raw_id = (query.get("id") or [None])[0]
    if not raw_id:
        return None
    decoded = unquote(raw_id)

    reg_path = urlparse(registration["url"]).path
    if not decoded.lower().startswith(reg_path.lower()):
        return None

    remainder = decoded[len(reg_path):].strip("/")
    if not remainder:
        return None
    parts = remainder.split("/", 1)  # parts[0] is the document library name itself (e.g. "Shared Documents")
    if len(parts) < 2 or not parts[1]:
        return None
    return parts[1].replace("/", os.sep)


def resolve_local_path_from_link(link):
    """The main entry point: given a pasted SharePoint/OneDrive URL,
    returns the local folder path OneDrive already syncs it to on this
    PC. Raises OneDriveLinkResolutionError with a user-facing message on
    any failure -- unknown library, OneDrive not signed in, folder not
    actually synced yet, ambiguous match, etc. Never guesses silently:
    if it can't be confident, it says so instead of returning a wrong
    path (see SHAREPOINT_SYNC_SPEC.md sec 13).
    """
    link = (link or "").strip()
    if not link:
        raise OneDriveLinkResolutionError("Paste a SharePoint/OneDrive link first.")

    parsed = urlparse(link)
    if not parsed.scheme or not parsed.netloc:
        raise OneDriveLinkResolutionError(
            "That doesn't look like a full link (it should start with https://). "
            "Use Browse below to pick the folder manually instead."
        )

    registrations = list_onedrive_sync_registrations()
    if not registrations:
        raise OneDriveLinkResolutionError(
            "No synced SharePoint/OneDrive libraries were found on this PC. Make sure OneDrive is "
            "installed, signed in, and this library already shows as synced in File Explorer, then try "
            "again -- or use Browse below to pick the folder manually."
        )

    host = parsed.netloc.lower()
    same_host = [r for r in registrations if urlparse(r["url"]).netloc.lower() == host]
    candidates = same_host or registrations

    link_path_lower = unquote(parsed.path).lower()
    best = max(candidates, key=lambda r: _score_by_path_prefix(r, link_path_lower))
    best_score = _score_by_path_prefix(best, link_path_lower)

    if best_score < 0 and len(candidates) != 1:
        names = ", ".join(r["display_name"] for r in candidates)
        raise OneDriveLinkResolutionError(
            f"Found {len(candidates)} synced libraries on this account but couldn't tell which one this "
            f"link points to ({names}). Use Browse below to pick the folder manually instead."
        )

    mount_point = best["mount_point"]
    if not os.path.isdir(mount_point):
        raise OneDriveLinkResolutionError(
            f"OneDrive says '{best['display_name']}' should be synced to {mount_point}, but that folder "
            "doesn't exist on disk right now (OneDrive may still be syncing, paused, or signed out). Wait "
            "for OneDrive to finish, or use Browse below to pick the folder manually."
        )

    sub_path = _extract_subfolder_path(parsed, best)
    if sub_path:
        candidate_path = os.path.join(mount_point, sub_path)
        if os.path.isdir(candidate_path):
            return candidate_path
        # The guessed sub-folder doesn't exist locally -- fall back to
        # the library root rather than returning a path that doesn't exist.

    return mount_point
