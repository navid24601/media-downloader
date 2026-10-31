# Instagram & YouTube Local for Windows

A personal downloader and offline library for one Windows user. It runs only on `127.0.0.1`, with an English, dark interface. Downloads, history and session storage are local; there is no hosted backend or third-party downloader service.

**Read this first:** individual downloads and the offline Library are implemented. Large YouTube playlists have a known first-50-entry bug; see [status and roadmap](#status-and-roadmap). Source support does not guarantee that Instagram or YouTube will allow a particular account or video.

[Start](#start-and-stop) · [Download](#download-media) · [Library](#use-the-offline-library) · [Sessions](#sessions-and-connection) · [Files](#files-and-recovery) · [Design](#design-guide-and-logbook) · [Roadmap](#status-and-roadmap)

## Start and stop

Requires Windows 11, Python 3.11+, Node.js 22+ on PATH, and a browser such as Chrome. Setup installs pinned dependencies and bundled FFmpeg in `.venv`.

1. Double-click **[Setup.cmd](Setup.cmd)** for first installation, dependency updates, or after moving the project. Python environments are not portable.
2. Double-click **[Launch.cmd](Launch.cmd)**. Keep its console open; the browser opens when the server is ready.
3. In **Connection & settings**, save an absolute local download folder. The default is the project's `Downloads` folder. Set up your VPN/TUN or proxy as needed.
4. Add downloads, then choose **Resume · next 50**. The app always opens on **Downloader**, with network work paused after a restart.

Launch reopens the existing instance, trying ports 8765–8795 if needed. Use it rather than a bare localhost URL: it supplies interface authorization.

**Controls:** Pause finishes the current item/request; Resume continues; Cancel remaining keeps completed files; Retry failed requeues failures and waits for Resume. Processing pauses after 50 items, including duplicate skips; source scans can also pause. Counts cover discovered work, not an assumed total.

Closing the browser does not stop the worker. Pause and wait, then **Shut down** or Ctrl+C for a clean stop. When updating, stop the old server first and preserve `.local` and media folders.

## Download media

### Links and file imports

Paste one link per line, mixing Instagram and YouTube if needed, or import UTF-8 TXT/CSV. Limits: 4 MB per file and 20,000 links per request. CSV selects an unambiguous link column or asks you to choose one. Blank lines and repeated identities are ignored; malformed links are reported individually.

Use canonical Instagram post/Reel links (`/p/`, `/reel/`, `/reels/`, legacy `/tv/`); open share redirects in Instagram first. YouTube accepts video, Shorts and playlist links. For a link containing both video and playlist, explicitly select **This video** or **This playlist**.

Instagram selects the highest exposed image/video quality, which may differ from the original upload. There are no artificial lower-quality options.

### Instagram profiles and Saved

- **Profile:** username or profile URL; Latest N or All. Timeline and Reels are scanned together. Latest N uses publication times, accounts for pinned posts, and may need several scan batches before downloads begin.
- **Saved:** requires imported Instagram cookies. Choose All Saved posts or a named collection, then Latest N or All. For a collection, paste its URL, such as `https://www.instagram.com/you/saved/name/123456/`; automatic collection discovery is unavailable.
- **Ordering:** reliable recently-saved order/timestamps are unavailable. Latest N requires accepting **Latest N in available order**; publication order is never substituted. All needs no ordering acceptance.

If Saved/collection access fails, paste individual post links; this does not enumerate Saved. An expired source cursor may require canceling/re-adding it; existing downloads skip.

### YouTube output and audio

Choose options **before queuing**; they apply only to new YouTube jobs:

| Choice | Behavior |
| --- | --- |
| MP4 video | Best usable quality, or a ceiling of 1080p, 720p, 480p or 360p. Uses the best available lower resolution; fails if none fits. Library shows actual resolution. |
| MP3 audio | Converts the selected audio locally. Conversion is lossy; the video ceiling is ignored. |
| Subtitles / thumbnail | Optional files, off by default. Requests available English VTT subtitles, not automatic captions. |
| Start / end | Optional individual-video/Short segment. Enter both as seconds or `HH:MM:SS`, optionally with milliseconds; start must precede end, within the known duration and 24-hour input limit. Playlists reject ranges. |

**Audio identity comes before bitrate.** Both modes prefer the identified original, whatever its language, over dubbed or dynamic-range-compressed alternatives. Ambiguous multilingual audio or an unusable original fails instead of substituting a dub. Older/single-language sources can download without an original marker; Library shows the uncertainty.

MP4 uses H.264/AV1 video and AAC audio without video re-encoding; HLS-only, DRM and incompatible formats are refused. MP4 segments follow nearby keyframes, possibly extending beyond the range; MP3 cuts happen during conversion. Segments may require a full source download. Subtitles retain full-video timing.

Playlist membership/positions are separate from media, so a video can belong to several playlists without another copy. The Library filter uses recorded order. **Complete enumeration beyond 50 entries is unreliable**, despite the configured 10,000-entry ceiling. Membership does not automatically synchronize with playlist edits.

## Use the offline Library

Switching tabs preserves unsaved Downloader input and leaves its worker running. Library browsing, search, previews, Favorites and playback need only the local server/files, without Internet or account sessions. Clicking an original source link goes online.

- **Browse:** 48 tiles per page, newest downloaded first; filter by platform, known Instagram account, source, YouTube playlist, Favorites or hidden state. Use Refresh for newly indexed files.
- **Search:** download date (`YYYY-MM-DD`), original link, shortcode/video ID or filename; also YouTube title/channel.
- **Viewer:** ordered carousel slider, standard playback controls and known metadata. Left/right moves media; up/down moves posts unless native controls have focus. Back or Escape restores the grid position.
- **Favorites / Hide:** local database changes only. Hide keeps files and history; enable Hidden items to restore them. Favorites do not change Instagram Saved.
- **Delete local file:** a separate permanent-delete confirmation lists exact recorded media and sidecars. History remains; a later download is allowed once the file is missing.
- **Fetch missing details:** Instagram only, one selected post, with online confirmation. Queues missing caption/account data without downloading media again. Never runs on browsing; YouTube has no equivalent.

Old completed paths are indexed in repeatable batches without moving/downloading media. Records, matching filenames and sidecars/ZIP captions supply available metadata; other values stay unknown. New Instagram captions enter Library metadata even with caption-file output disabled.

Missing/moved/corrupt files remain visible. Restore their recorded paths or download again; folder settings do not relocate old outputs. Preview failure leaves originals accessible. ZIPs are read directly without duplicate extracted folders.

## Sessions and connection

### Import account cookies

Public access is attempted without cookies; Instagram may still require login. Import only sessions authorized to view the content. Platform imports/Clear actions are separate; passwords are never collected.

1. Log in normally in Chrome and resolve any account challenge there.
2. Export site cookies locally as Netscape `cookies.txt` or JSON using a trusted exporter. Instagram needs HTTP-only `sessionid`; `document.cookie` omits it. JSON needs `domain`, `name`, `value`, optional expiry. Netscape needs seven tab-separated fields and `#HttpOnly_` prefixes.
3. Import into the matching session section in **Connection & settings**. **Verify Saved access** checks one Instagram Saved page; import alone proves nothing about session validity. For YouTube, test one queued video.
4. Remove the plaintext export when finished; never share, commit or sync it. Clear session removes the app's copy, not Chrome's login. Revoke exposed sessions through the site's account controls.

**Instagram without an exporter:** Chrome F12 → Application → Storage → Cookies → Instagram. Copy raw Value/Domain for `sessionid`, plus `csrftoken` and `ds_user_id` if present; leave Show URL-decoded off. Save entries like the following as `instagram-cookies.json` in Notepad using **All files**, then import:

```json
[{"domain":".instagram.com","name":"sessionid","value":"PASTE_RAW_VALUE_HERE"}]
```

Chrome cookies are not extracted automatically. For YouTube's cookie rotation and incognito export procedure, follow the [official engine guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies).

### VPN and proxy

Use your existing VPN/TUN, a detected static Windows/environment proxy, or an explicit `http://host:port`, `https://host:port`, `socks5://host:port` or `socks5h://host:port`. SOCKS5h delegates DNS to the proxy. Browser-only proxy extensions and PAC/WPAD scripts are unsupported. Windows bypass-list behavior is not reproduced by the explicit engine mapping. Local app requests bypass proxies.

Pause and wait before connection/session changes. **Test connection** checks Instagram reachability, not login or every media host; **Verify Saved access** checks authentication. Explicit proxy credentials are encrypted; clearing returns to detected/direct access. TLS verification stays enabled.

Temporary failures get at most three attempts with backoff; ordinary failures are recorded and processing continues. Account restrictions/challenges/rate limits pause work. Respect server waits, resolve issues in Chrome, then manually Resume. There is no evasion or guarantee against account restrictions.

## Files and recovery

| Platform | Output |
| --- | --- |
| Instagram | `username-shortcode-YYYY-MM-DD`; one image/video, or one ordered carousel ZIP (`001.jpg`, `002.mp4`, etc.). Optional caption/link text goes beside single media or inside ZIP as `caption.txt`. |
| YouTube | `video_title_in_snake_case-channel_name-video_id-YYYY-MM-DD.mp4` or `.mp3`. Title/channel are lowercase snake_case, capped at 80/40 characters; ID case is preserved. Missing values use `untitled`/`unknown`. Optional sidecars share the base. |

Dates use the computer's local download day, not publication/save dates. Instagram fixes its filename date when media staging begins. Windows names are sanitized; collisions get ` (2)`, ` (3)`, etc. Existing files are not bulk-renamed. Example: `most_epic_deaths_in_history-serious_history-Yctn_C_6cF8-2026-10-28.mp3`.

Duplicates are skipped across input sources. Instagram identity is its shortcode; YouTube identity includes video ID, MP4/MP3 mode and full/segment range. Quality ceilings are not separate identities: an existing lower-resolution file may satisfy a higher ceiling. Missing media or recorded sidecars permits another download. **Download again** explicitly saves another copy; active queue duplicates still skip. Use it after restarting to correct an older dubbed YouTube download. Old files/history remain intact.

**Back up with the server stopped:** copy `.local` and every media folder. `.local/queue.sqlite3` contains queue/history/Library/settings; `.local/credentials.bin` holds Windows-protected sessions/proxy. Media is plaintext. Session blobs may not transfer to another Windows user/computer; rerun Setup and reimport sessions when moving installations.

Incomplete work is under `<download-folder>/.incomplete/<queue-id>/`, outside the Library. Retry reuses verified staged Instagram items and matching YouTube partials. Track changes invalidate YouTube staging; legacy audio-finalization jobs instruct you to cancel/requeue rather than commit an old dub. Successful jobs remove staging. With the server stopped, remove only unwanted canceled/failed staging subfolders if needed.

Previews are cached under `.local/thumbnails`, limited to 128 MiB / 1,000 files, and invalidate when sources change. YouTube also caps individual streams around 3.3 GiB, monitors staging at 10 GiB, and refuses new jobs once retained incomplete work exceeds 20 GiB per download folder. An in-flight transfer/merge can temporarily exceed thresholds; these are safeguards, not a disk-space guarantee.

## Design guide and logbook

The browser calls a local Python server. Download jobs go through a persisted queue and one worker into a platform adapter; Library reads recorded files and SQLite directly, without extraction. These boundaries keep browsing offline and platform changes isolated.

| Decision | Reason / tradeoff |
| --- | --- |
| Standard-library HTTP server, SQLite, vanilla HTML/CSS/JS | Small Windows setup, no hosted services or frontend framework/build. Node is only a YouTube runtime dependency. |
| Separate maintained engines | gallery-dl provides Instagram posts/profiles/Saved/collection extraction; yt-dlp handles YouTube and Instagram DASH support. Adapter boundaries avoid maintaining private platform APIs ourselves. |
| SQLite checkpoints and manual Resume | Unfinished work survives restarts without automatic account traffic. Bounded queries/enumeration keep thousands of records manageable. |
| Sequential work, pacing, batches of 50 | Favor cautious account access and review over aggressive speed. Restricted endpoints are never repeatedly retried automatically. |
| Temporary files and completion receipts | A partial carousel or failed conversion must not become a successful download. Collision checks preserve unrelated files. |
| ZIP-only carousels and local previews | Preserve media order in one output without duplicate extraction folders; cache small previews rather than loading originals into the grid. |
| Original audio before bitrate | The old selector chose Indonesian/French dubs with slightly higher bitrates. Resolve track identity first, then quality; record uncertainty rather than force English. |
| Native HTTPS/DASH, no automatic video encoding | Keep metadata/media on the same proxy/TLS path and avoid hidden quality loss. HLS-only sources and exact-frame video cuts remain limitations. |
| Cookie import, DPAPI, authenticated local routes | Avoid passwords and brittle Chrome decryption. Restrict local API/media access as well as LAN access. |

**Security boundary:** loopback-only binding, Host/Origin checks and per-run authorization protect API/media routes. Media IDs replace client-supplied paths; ranges support seeking, while traversal/staging/symlinks/junctions are refused. DPAPI/restricted `.local` permissions protect credentials, not media/history. Raw diagnostics and secrets are not logged; no telemetry is sent. Malware, administrators, browser extensions and external sync tools remain outside these protections.

**Project map:**

| Files | Responsibility |
| --- | --- |
| [app.py](app.py), [Setup.ps1](Setup.ps1), [Launch.cmd](Launch.cmd) | Environment, single-instance launch, authorized browser opening, shutdown. |
| [server.py](instagram_local/server.py), [static/](instagram_local/static) | Local API/media security and browser interface. |
| [core.py](instagram_local/core.py), [routing.py](instagram_local/routing.py), [security.py](instagram_local/security.py) | Validation, names/imports, platform routing, cookie/proxy storage. |
| [store.py](instagram_local/store.py), [worker.py](instagram_local/worker.py) | SQLite state, queue execution, retries, finalization. |
| [engine.py](instagram_local/engine.py), [youtube.py](instagram_local/youtube.py) | Platform-specific extraction/download adapters. |
| [library.py](instagram_local/library.py), [tests/](tests) | Offline indexing/streaming/previews; repeatable fixtures and regressions. |

**Milestones:** Instagram engine research/design on 2026-10-25; offline Library verification on 2026-10-27; YouTube support, original-audio correction and title-first naming on 2026-10-28. Detailed evidence stays in [Instagram engine research](ENGINE_RESEARCH.md), [YouTube engine research](YOUTUBE_RESEARCH.md) and [verification history](VERIFICATION.md), rather than duplicating every test in this manual.

## Status and roadmap

Reviewed 2026-11-01. These are remaining work or source constraints, not promised release dates.

| Priority / status | Requested capability and next step |
| --- | --- |
| **Required fix: large YouTube playlists** | Current extraction caps an iterator at 50 entries; the worker can mistake its exhaustion for playlist completion. A local 120-item fixture enumerated 50 and marked the source done. Advance to the next page/range before declaring completion; add integrated tests beyond 100 items, restart, ordering and duplicate membership. Until fixed, paste individual links for the full batch. |
| **Required verification: authenticated sources** | Recorded checks do not establish current Instagram private/profile/Saved/collection access or account-only YouTube availability. Run a small account-authorized smoke test, including six varied Instagram links, Saved/collection pagination and a small YouTube playlist; fix source-specific failures. Public YouTube download and reported-video audio metadata were checked live; optional subtitle/artwork retrieval was not. |
| **Source-blocked: recently-saved ordering** | Prefer true Recently saved / Latest N only when a maintained source reliably provides that order. Keep the explicit available-order fallback and never invent saved timestamps. |
| **Partial experience: named Saved collections** | Contents are supported through a pasted collection URL. Add collection discovery/a selector only when the engine exposes a maintainable method. |
| **Preferred convenience: browser-session reuse** | Investigate a supported Chrome-compatible local import method if Windows protections permit it. Explicit cookie import remains the working fallback; do not bypass browser security or collect passwords. |

**Deliberately deferred or outside scope:** date-range filtering; Stories, Highlights and archived/live Instagram broadcasts; whole YouTube channels and active livestream recording; richer tags/custom collections; standalone executable packaging. These were mentioned as exclusions or optional future work, not shipped features. Completed YouTube livestream recordings may work as ordinary video URLs. Instagram lower-quality choices are offered only if usable source alternatives are actually supported; currently there is no quality selector. Frame-accurate MP4 segments and retimed segment subtitles are also unavailable.

## Troubleshooting and maintenance

| Symptom | Action |
| --- | --- |
| Setup/startup fails | Check Python/Node versions, PyPI connectivity, writable project folder and same Windows account. Rerun Setup; normal admin installation is not required. |
| Unauthorized action / lost tab | Reopen with Launch.cmd. Do not force a second instance or delete its lock while running. |
| Connection/session/challenge failure | Pause; verify the source in Chrome through VPN/proxy, resolve challenges, import fresh cookies and respect waits. Do not disable TLS. |
| Download skipped / wrong older audio | Enable Download again; choose output options and requeue. |
| Profile scan has no downloads yet | Latest N can require more timeline/Reels scanning; Resume when prompted. |
| Missing Library file / failed preview | Restore its recorded path or download again. A preview/codec failure may still allow playback in a desktop player. |
| Disk/ZIP/conversion failure | Check free space/permissions, then Retry failed. Review only unwanted staging folders with the server stopped. Never delete `.local` as a reset shortcut. |

From the project folder, local regression checks are:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check instagram_local/static/app.js
node --check instagram_local/static/library.js
.\.venv\Scripts\python.exe -m pip check
```

Last recorded full run: **77 passing tests, 2026-10-28**, including Windows DPAPI/launcher and real local FFmpeg pipelines. Windows checks need normal user access and can fail in a restricted sandbox. That suite missed the large-playlist boundary bug identified in the 2026-11-01 documentation audit; passing fixtures do not prove live account availability. No large live/account stress test is part of routine verification.

[requirements.txt](requirements.txt) pins the installed dependency set, not a promise of the latest releases. Engine upgrades require reviewing adapter behavior, rerunning regressions and a small relevant live check. Third-party packages keep their own licenses; this is a personal source checkout, not a redistributed executable.
